import json
from datetime import date, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from requests.structures import CaseInsensitiveDict

from apps.quickentry import ai
from apps.quickentry.ai import (
    ExtractionError,
    RateLimitedError,
    _normalize_decorative_unicode,
    _post_with_retry,
)
from apps.quickentry.models import ManualBatch
from apps.quickentry.views import _current_batch_slot, _period_sort_key, amend_eligible

MANILA = ZoneInfo("Asia/Manila")


def _manila(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=MANILA)


class CurrentBatchSlotTests(TestCase):
    """
    Alpha-test bug #1 (client feedback, 2026-08-25): production stayed on
    the AM batch past 6:00 AM instead of opening the PM window. Root
    cause was the boundary constant itself (`hour < 12`, i.e. noon), not
    a UTC/local mixup — _current_batch_slot() already receives an
    already-localized datetime, so these tests pass Manila-zoned
    datetimes directly rather than mocking timezone.now().
    """

    def test_just_before_6am_is_still_overnight_am_window(self):
        got = _current_batch_slot(_manila(2026, 8, 25, 5, 59))
        self.assertEqual(got, (date(2026, 8, 25), ManualBatch.Shift.AM))

    def test_exactly_6am_flips_to_pm_window(self):
        got = _current_batch_slot(_manila(2026, 8, 25, 6, 0))
        self.assertEqual(got, (date(2026, 8, 25), ManualBatch.Shift.PM))

    def test_mid_morning_is_pm_window_not_stuck_on_am(self):
        # This is the exact bug: under the old `hour < 12` check, 9 AM
        # was still the AM window. It must now be PM.
        got = _current_batch_slot(_manila(2026, 8, 25, 9, 0))
        self.assertEqual(got, (date(2026, 8, 25), ManualBatch.Shift.PM))

    def test_just_before_6pm_is_still_pm_window(self):
        got = _current_batch_slot(_manila(2026, 8, 25, 17, 59))
        self.assertEqual(got, (date(2026, 8, 25), ManualBatch.Shift.PM))

    def test_exactly_6pm_flips_to_next_days_am_window(self):
        # The overnight window starting tonight belongs to TOMORROW's
        # 0600H report, so the date rolls forward.
        got = _current_batch_slot(_manila(2026, 8, 25, 18, 0))
        self.assertEqual(got, (date(2026, 8, 26), ManualBatch.Shift.AM))

    def test_late_evening_is_next_days_am_window(self):
        got = _current_batch_slot(_manila(2026, 8, 25, 23, 30))
        self.assertEqual(got, (date(2026, 8, 26), ManualBatch.Shift.AM))

    def test_just_after_midnight_is_same_days_am_window(self):
        # Continuing the window that started the evening before —
        # already the correct date, no further rollover.
        got = _current_batch_slot(_manila(2026, 8, 26, 0, 30))
        self.assertEqual(got, (date(2026, 8, 26), ManualBatch.Shift.AM))


class CurrentBatchViewOpensNewPeriodTests(TestCase):
    """
    End-to-end: a FINALIZED AM batch from earlier in the night must not
    block a fresh PM batch from opening once the clock passes 6:00 AM —
    this is the exact "stuck on FINALIZED · 4/41" symptom the client hit
    in production. TestCase wraps each test in a transaction that's
    rolled back afterward, so this never touches real data.
    """

    def setUp(self):
        from django.contrib.auth import get_user_model

        self.user = get_user_model().objects.create_user(
            username="alpha_test_user", password="testpass123!"
        )
        self.client.force_login(self.user)

    def test_new_pm_batch_opens_after_6am_despite_finalized_am_batch(self):
        stuck_am_batch = ManualBatch.objects.create(
            date=date(2026, 8, 25),
            shift=ManualBatch.Shift.AM,
            status=ManualBatch.Status.FINALIZED,
        )

        fake_now = _manila(2026, 8, 25, 8, 0)  # 8 AM — well past the 6 AM flip
        with patch("apps.quickentry.views.timezone.now", return_value=fake_now):
            response = self.client.get("/api/current-batch/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["batch"]["date"], "2026-08-25")
        self.assertEqual(body["batch"]["shift"], "PM")
        self.assertEqual(body["batch"]["status"], "DRAFT")
        self.assertNotEqual(body["batch"]["id"], stuck_am_batch.id)


class FakeResp:
    """
    Per CLAUDE.md's documented testing gotcha: headers MUST be a real
    CaseInsensitiveDict, never a plain dict -- Groq sends lowercase
    header names and ai.py reads them titlecased, which only round-trips
    correctly against the real case-insensitive type requests actually
    returns.
    """

    def __init__(self, status_code, headers=None, body=None):
        self.status_code = status_code
        self.headers = CaseInsensitiveDict(headers or {})
        self._body = body if body is not None else {"choices": [{"message": {"content": "{}"}}]}
        self.text = json.dumps(self._body)
        self.ok = status_code < 400

    def json(self):
        return self._body


def _reset_rate_limit_state():
    with ai._rate_limit_lock:
        ai._rate_limit_state.clear()


class Groq413RateLimitTests(TestCase):
    """
    Alpha-test bug #4 (client feedback, 2026-08-25): reproduced live
    against real Groq -- a report with enough decorative Unicode pushed a
    single request's token estimate over the account's per-minute (TPM)
    budget, and Groq reports that specific case as 413 with a
    "rate_limit_exceeded" body code (still carrying a real Retry-After
    header), not the 429 the app already knew how to handle. These tests
    mock the actual response shape captured from that real call rather
    than a guessed one.
    """

    def setUp(self):
        _reset_rate_limit_state()
        self.addCleanup(_reset_rate_limit_state)

    def test_413_with_rate_limit_exceeded_code_is_treated_as_rate_limited(self):
        resp = FakeResp(
            413,
            headers={"Retry-After": "2", "x-ratelimit-remaining-tokens": "8000", "x-ratelimit-reset-tokens": "1ms"},
            body={"error": {"message": "Request too large ... TPM: Limit 8000, Requested 8073",
                             "type": "tokens", "code": "rate_limit_exceeded"}},
        )
        with patch("apps.quickentry.ai.requests.post", return_value=resp):
            with self.assertRaises(RateLimitedError) as ctx:
                _post_with_retry({"test": "payload"})
        self.assertEqual(ctx.exception.retry_after_seconds, 2.0)

    def test_413_without_rate_limit_code_stays_a_hard_extraction_error(self):
        # A genuinely different 413 (unrelated to rate limiting) must NOT
        # be misreported as a waitable rate limit.
        resp = FakeResp(413, body={"error": {"message": "payload malformed", "code": "invalid_request"}})
        with patch("apps.quickentry.ai.requests.post", return_value=resp):
            with self.assertRaises(ExtractionError) as ctx:
                _post_with_retry({"test": "payload"})
        self.assertNotIsInstance(ctx.exception, RateLimitedError)

    def test_non_429_non_413_groq_error_becomes_extraction_error_not_raw_httperror(self):
        # This is the actual client-facing bug: resp.raise_for_status()
        # used to leak a bare requests.HTTPError here, which views.py
        # doesn't catch, producing an uncaught 500. Any Groq-side failure
        # must always come back as ExtractionError instead.
        resp = FakeResp(400, body={"error": {"message": "bad request", "code": "invalid_request"}})
        with patch("apps.quickentry.ai.requests.post", return_value=resp):
            with self.assertRaises(ExtractionError) as ctx:
                _post_with_retry({"test": "payload"})
        self.assertIn("400", str(ctx.exception))

    def test_ordinary_success_is_unaffected(self):
        resp = FakeResp(200, headers={"x-ratelimit-remaining-tokens": "8000", "x-ratelimit-reset-tokens": "1s"})
        with patch("apps.quickentry.ai.requests.post", return_value=resp):
            got = _post_with_retry({"test": "payload"})
        self.assertEqual(got.status_code, 200)


class DecorativeUnicodeNormalizationTests(SimpleTestCase):
    """
    Alpha-test bug #4: NFKC normalization is what actually fixes the
    token-bloat root cause -- confirmed against the real saved report
    text that triggered this (Polillo, entry id 47 in production).
    """

    def test_mathematical_alphanumeric_bold_italic_folds_to_plain_ascii(self):
        decorative = "\U0001d648\U0001d63f\U0001d64d\U0001d64d\U0001d648\U0001d64a: POLILLO QUEZON"
        self.assertEqual(_normalize_decorative_unicode(decorative), "MDRRMO: POLILLO QUEZON")

    def test_genuine_emoji_and_content_are_left_alone(self):
        text = "Hotline ☎️ : Smart 09989966909  Email \U0001f4e9 : mdrrmpolillo@gmail.com"
        self.assertEqual(_normalize_decorative_unicode(text), text)

    def test_plain_ascii_report_is_unchanged(self):
        text = "WHAT: Road Crash\nWHEN: February 10, 2026 | 1400H\nWHERE: Brgy. Test"
        self.assertEqual(_normalize_decorative_unicode(text), text)


def _sequential_periods(start_date, count):
    """(date, shift) pairs in strict period order: AM, PM, AM, PM, ...
    starting at start_date's AM window. Used so tests never have to hand-
    pick dates and can't accidentally collide with ManualBatch's
    unique_together=(date, shift)."""
    d = start_date
    shift = ManualBatch.Shift.AM
    for _ in range(count):
        yield (d, shift)
        if shift == ManualBatch.Shift.AM:
            shift = ManualBatch.Shift.PM
        else:
            shift = ManualBatch.Shift.AM
            d = d + timedelta(days=1)


class AmendEligibleTests(TestCase):
    """
    Last-5-period amend-restriction eligibility. Dates are built relative
    to settings.AMEND_RESTRICTION_CUTOFF (not hardcoded) so these stay
    correct if the cutoff value is ever revisited.
    """

    CUTOFF = settings.AMEND_RESTRICTION_CUTOFF

    def _make(self, d, shift, finalized_at):
        return ManualBatch.objects.create(
            date=d, shift=shift, status=ManualBatch.Status.FINALIZED,
            finalized_at=finalized_at,
        )

    def test_period_sort_key_orders_chronologically_not_alphabetically(self):
        # Deliberately NOT relying on "AM" < "PM" string comparison --
        # spans two dates so a same-date AM/PM mixup or a date/shift
        # priority mixup would both be caught.
        aug_n_pm = ManualBatch(date=date(2026, 1, 10), shift=ManualBatch.Shift.PM)
        aug_n1_am = ManualBatch(date=date(2026, 1, 11), shift=ManualBatch.Shift.AM)
        aug_n1_pm = ManualBatch(date=date(2026, 1, 11), shift=ManualBatch.Shift.PM)
        ordered = sorted([aug_n1_pm, aug_n_pm, aug_n1_am], key=_period_sort_key)
        self.assertEqual(
            [(b.date, b.shift) for b in ordered],
            [(date(2026, 1, 10), "PM"), (date(2026, 1, 11), "AM"), (date(2026, 1, 11), "PM")],
        )

    def test_non_finalized_batch_is_never_eligible(self):
        draft = ManualBatch.objects.create(date=date(2026, 1, 1), shift=ManualBatch.Shift.AM)
        self.assertFalse(amend_eligible(draft))

    def test_pre_cutoff_batch_always_eligible_regardless_of_window_size(self):
        grandfathered = self._make(
            self.CUTOFF.date() - timedelta(days=30), ManualBatch.Shift.AM,
            self.CUTOFF - timedelta(days=30),
        )
        # Finalize far more than 5 post-cutoff batches -- the grandfathered
        # one must remain eligible no matter how the post-cutoff window
        # fills up.
        for d, shift in _sequential_periods(self.CUTOFF.date(), 10):
            self._make(d, shift, self.CUTOFF + timedelta(hours=1))

        self.assertTrue(amend_eligible(grandfathered))

    def test_pre_cutoff_batches_do_not_consume_post_cutoff_window_slots(self):
        pre_cutoff_batches = [
            self._make(
                self.CUTOFF.date() - timedelta(days=10 + i), ManualBatch.Shift.AM,
                self.CUTOFF - timedelta(days=10 + i),
            )
            for i in range(3)
        ]
        # Exactly 5 post-cutoff batches -- if pre-cutoff batches wrongly
        # ate window slots, some of these would incorrectly fall out.
        post_cutoff_batches = [
            self._make(d, shift, self.CUTOFF + timedelta(hours=1))
            for d, shift in _sequential_periods(self.CUTOFF.date(), 5)
        ]

        for b in pre_cutoff_batches:
            self.assertTrue(amend_eligible(b), f"pre-cutoff {b} should always be eligible")
        for b in post_cutoff_batches:
            self.assertTrue(amend_eligible(b), f"post-cutoff {b} should fit within the 5-slot window")

    def test_sixth_post_cutoff_batch_pushes_the_oldest_period_out(self):
        # Six post-cutoff PERIODS in strict chronological order p1..p6.
        periods = list(_sequential_periods(self.CUTOFF.date(), 6))

        # finalized_at is deliberately scrambled OUT of period order --
        # p1 (the chronologically OLDEST period) is finalized LAST (as if
        # OPS caught up on a missed period days later), while p2 is
        # finalized right away. This is the real thing being tested:
        # ranking must follow period identity (date, shift), never
        # finalized_at wall-clock order -- if the implementation used
        # finalized_at instead, p2 (not p1) would be the one pushed out.
        finalized_ats = [
            self.CUTOFF + timedelta(days=10),  # p1 -- finalized very late
            self.CUTOFF + timedelta(hours=1),  # p2
            self.CUTOFF + timedelta(hours=2),  # p3
            self.CUTOFF + timedelta(hours=3),  # p4
            self.CUTOFF + timedelta(hours=4),  # p5
            self.CUTOFF + timedelta(hours=5),  # p6 -- newest period, finalized promptly
        ]
        batches = [
            self._make(d, shift, fa)
            for (d, shift), fa in zip(periods, finalized_ats)
        ]

        p1, p2, p3, p4, p5, p6 = batches
        self.assertFalse(amend_eligible(p1), "oldest period must be pushed out by the 6th")
        for b in (p2, p3, p4, p5, p6):
            self.assertTrue(amend_eligible(b), f"{b} is among the 5 most recent periods")


