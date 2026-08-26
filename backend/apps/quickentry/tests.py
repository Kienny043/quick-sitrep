import json
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

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
from apps.quickentry.views import _current_batch_slot

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
