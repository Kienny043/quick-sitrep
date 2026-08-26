from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase

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
