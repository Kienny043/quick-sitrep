"""
Quick SitRep — API endpoints. See docs/quick-report-entry-spec.md
Section 6. PDF compilation (Step 6) isn't built yet — finalize/download
are stubbed accordingly.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count
from django.http import FileResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .ai import (
    extract_incident_data,
    generate_synopsis,
    generate_weather_summary,
    seconds_until_available,
    ExtractionError,
    RateLimitedError,
)
from .models import (
    MUNICIPALITY_CHOICES,
    ManualBatch,
    BatchAmendment,
    ManualEntry,
    LifelinesStatus,
    SitRepSignatoryConfig,
)
from .pdf import generate_batch_pdf, compute_summary
from .serializers import (
    ManualBatchSerializer,
    ManualEntrySummarySerializer,
    ExtractRequestSerializer,
    EntrySaveRequestSerializer,
    FinalizeBatchRequestSerializer,
    AmendBatchRequestSerializer,
    LifelinesStatusSerializer,
    INCIDENT_SERIALIZERS,
    get_incident_schema,
)


# ── / (the OPS-facing page — Step 5) ───────────────────────────────────
@ensure_csrf_cookie
@login_required
def main_page(request):
    """
    Server-rendered shell; all the actual work happens client-side in
    static/quickentry/js/main.js against the API views below. Session
    auth + CSRF cookie only — no JWT (see spec Section 3's Auth note).
    incident_schema drives the frontend's required-field blocking UI
    (Step 9) — computed fresh from the live serializers on every page
    load, never hardcoded client-side. Rendered via json_script for safe
    embedding rather than a raw |safe JSON dump.
    """
    return render(
        request,
        "quickentry/main.html",
        {"incident_schema": get_incident_schema()},
    )


# ── /settings/ (SitRep signatories — Step 12) ──────────────────────────
SIGNATORY_FIELDS = [
    "prepared_by",
    "noted_by",
    "noted_by_title",
    "approved_by",
    "approved_by_title",
]


@login_required
def signatory_settings(request):
    """
    Any logged-in user can edit these — small internal office tool, no
    separate permission tier for now (per the spec's Auth note, same
    reasoning as everywhere else in this app).
    """
    config = SitRepSignatoryConfig.get_config()
    saved = False

    if request.method == "POST":
        for field in SIGNATORY_FIELDS:
            setattr(config, field, request.POST.get(field, "").strip())
        config.save()
        saved = True

    return render(
        request,
        "quickentry/settings.html",
        {"config": config, "saved": saved},
    )


# ── /history/ (Batch History — Step 13) ────────────────────────────────
@login_required
def batch_history(request):
    """
    Every batch, most recent first — how OPS reaches anything that isn't
    today's current-shift batch (main_page's default). A DRAFT row here
    links back into the normal entry-editing view for that specific
    batch (via ?batch_id=), even if its shift window has technically
    passed; a FINALIZED row links straight to the (always freshly
    regenerated) download endpoint.
    """
    # Distinct municipalities, not raw entry rows — a municipality can now
    # have several separate entries per batch, so Count("entries") alone
    # would count reports rather than "how many municipalities reported"
    # (and could exceed total_municipalities, which used to be impossible
    # back when one entry per municipality was the most a batch could have).
    batches = ManualBatch.objects.annotate(
        municipality_count=Count("entries__municipality", distinct=True)
    ).order_by("-date", "-shift")
    return render(
        request,
        "quickentry/history.html",
        {"batches": batches, "total_municipalities": len(MUNICIPALITY_CHOICES)},
    )


# ── GET /api/ai-status/ ─────────────────────────────────────────────────
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def ai_status(request):
    """
    Lets the frontend show a countdown and disable "Process with AI"
    *before* OPS clicks into a call that would just block server-side —
    see ai.seconds_until_available(). Cheap: reads in-memory state, never
    calls Groq.
    """
    return Response({"cooldown_seconds": round(seconds_until_available(), 1)})


# ── GET /api/current-batch/ ────────────────────────────────────────────
def _current_batch_slot(now_local):
    """
    Maps a tz-aware, ALREADY-LOCALIZED (Asia/Manila) datetime to the
    (date, shift) of the batch window it falls into.

    Real PDRRMO SitRep convention, confirmed against client feedback
    (alpha test, 2026-08-25 — see CLAUDE.md Known Gaps history): the two
    12-hour windows are bounded by 6:00 AM and 6:00 PM, not midnight and
    noon. A prior version of this function checked `hour < 12`, which
    doesn't flip until noon — the batch was still showing as the AM
    window for a full 6 hours after 6:00 AM, blocking new submissions
    for the whole client office during that gap. That bug was NOT a
    UTC-vs-local mixup (the caller already converts via
    timezone.localtime() before calling this) — it was simply the wrong
    boundary constant.

    - 06:00–17:59 -> PM window (daytime), released as the "1800H"
      report, dated today.
    - 18:00–23:59 -> AM window (overnight), building toward TOMORROW
      morning's "0600H" report — dated tomorrow, since that's the date
      the eventual report carries.
    - 00:00–05:59 -> AM window (overnight), finishing last night's
      window that started before midnight — dated today, same
      reasoning (this is the morning the "0600H" report it belongs to
      actually gets released).
    """
    hour = now_local.hour
    if 6 <= hour < 18:
        return now_local.date(), ManualBatch.Shift.PM
    if hour < 6:
        return now_local.date(), ManualBatch.Shift.AM
    return now_local.date() + timedelta(days=1), ManualBatch.Shift.AM


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def current_batch(request):
    """
    No ?batch_id= — simple wall-clock check via _current_batch_slot(),
    independent of the main system's period logic (and of the
    reopen-bug found there). get_or_create never touches an existing
    batch's status, so a FINALIZED batch can never get silently
    reopened by someone loading this page later in the same window.

    With ?batch_id= — Batch History's "Open" link for a DRAFT batch that
    isn't today's current-shift one (e.g. its window has passed but it
    was never finalized). Same response shape either way, so the
    frontend's entry-editing UI doesn't need to know or care which path
    it came from.
    """
    batch_id = request.query_params.get("batch_id")
    if batch_id:
        batch = get_object_or_404(ManualBatch, pk=batch_id)
    else:
        now = timezone.localtime(timezone.now())
        batch_date, shift = _current_batch_slot(now)

        batch, _created = ManualBatch.objects.get_or_create(
            date=batch_date,
            shift=shift,
            defaults={"status": ManualBatch.Status.DRAFT},
        )

    # A municipality can have several separate entries per batch now (one
    # paste per incident is how some LGUs — Lucban in particular — submit
    # reports in practice), so this groups rather than assumes one entry
    # each. batch.entries.all() is already ordered municipality-first via
    # ManualEntry.Meta.ordering, so grouping here doesn't need its own
    # extra query or re-sort.
    entries_by_muni = {}
    for e in batch.entries.all():
        entries_by_muni.setdefault(e.municipality, []).append(e)

    def _preview(raw_text):
        snippet = " ".join(raw_text.split())
        return snippet[:80] + ("…" if len(snippet) > 80 else "")

    municipalities = [
        {
            "id": code,
            "name": name,
            "entry_count": len(entries_by_muni.get(code, [])),
            "entries": [
                {
                    "id": e.id,
                    "status": e.status,
                    "processed_at": e.processed_at,
                    "preview": _preview(e.raw_text),
                }
                for e in entries_by_muni.get(code, [])
            ],
        }
        for code, name in MUNICIPALITY_CHOICES
    ]

    return Response(
        {
            "batch": ManualBatchSerializer(batch).data,
            "municipalities": municipalities,
            # Same compute_summary() the PDF's Section III uses (pdf.py) —
            # single source of truth, never a second counting
            # implementation for the dashboard cards.
            "summary": compute_summary(batch),
        }
    )


# ── POST /api/entries/extract/ ─────────────────────────────────────────
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def extract_entry(request):
    """Preview only — never touches the database."""
    req = ExtractRequestSerializer(data=request.data)
    req.is_valid(raise_exception=True)

    try:
        result = extract_incident_data(
            req.validated_data["municipality"], req.validated_data["raw_text"]
        )
    except RateLimitedError as exc:
        # Fast refusal, not a blocking sleep — see ai.RateLimitedError.
        # retry_after_seconds lets the frontend's cooldown UI take over
        # instead of the request hanging on the server's one worker.
        return Response(
            {"detail": str(exc), "retry_after_seconds": exc.retry_after_seconds},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except ExtractionError as exc:
        # Upstream (Groq) failure or unparseable response — 502, not a 500,
        # since our own code didn't crash; the AI call/response did.
        return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

    return Response(result)


# ── GET /api/entries/<id>/ ──────────────────────────────────────────────
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def entry_detail(request, pk):
    """
    Step 8 — re-open a saved entry for editing. Returns raw_text
    (rendered read-only client-side — immutable once an entry exists per
    the accountability rule) alongside the CURRENT state of every child
    record, serialized through the same serializers /save/ validates
    against, so the frontend can populate the identical editable form it
    already uses for a fresh extraction. ai_output is returned as-is —
    it's the frozen audit-trail copy and must stay untouched by re-edits.
    """
    entry = get_object_or_404(
        ManualEntry.objects.select_related("batch", "lifelines").prefetch_related(
            "road_crashes__victims", "medical_cases", "fire_incidents",
            "water_incidents", "trauma_incidents",
        ),
        pk=pk,
    )
    related_querysets = {
        "road_crashes": entry.road_crashes.all(),
        "medical_assistance": entry.medical_cases.all(),
        "fire_incidents": entry.fire_incidents.all(),
        "water_incidents": entry.water_incidents.all(),
        "trauma_emergencies": entry.trauma_incidents.all(),
    }
    lifelines = getattr(entry, "lifelines", None)

    return Response(
        {
            "id": entry.id,
            "batch_id": entry.batch_id,
            "municipality": entry.municipality,
            "municipality_name": entry.get_municipality_display(),
            "status": entry.status,
            "raw_text": entry.raw_text,
            "ai_output": entry.ai_output,
            "unmapped_notes": entry.unmapped_notes,
            "weather_condition": entry.weather_condition,
            **{
                key: INCIDENT_SERIALIZERS[key](qs, many=True).data
                for key, qs in related_querysets.items()
            },
            "lifelines_status": LifelinesStatusSerializer(lifelines).data if lifelines else {},
        }
    )


# ── POST /api/entries/save/ ────────────────────────────────────────────
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def save_entry(request):
    envelope = EntrySaveRequestSerializer(data=request.data)
    envelope.is_valid(raise_exception=True)
    data = envelope.validated_data

    batch = get_object_or_404(ManualBatch, pk=data["batch_id"])
    if batch.is_locked:
        return Response(
            {"detail": "This batch has already been finalized and is locked."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    edited = data["edited_json"]
    if not isinstance(edited, dict):
        return Response(
            {"detail": "edited_json must be an object matching the extraction schema."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # entry_id present = the re-open-for-edit flow (GET /api/entries/<id>/
    # then save back to that exact row) — a municipality can have several
    # separate entries per batch now, so there's no other way to know
    # which one an edit belongs to. entry_id absent = always a brand new
    # entry; see the create/update branch below.
    entry_id = data.get("entry_id")
    existing_entry = None
    if entry_id:
        existing_entry = get_object_or_404(ManualEntry, pk=entry_id, batch=batch)
        if existing_entry.municipality != data["municipality"]:
            return Response(
                {"detail": "municipality does not match the entry being edited."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # raw_text is the accountability record — never silently
        # overwritten once this entry exists.
        if existing_entry.raw_text != data["raw_text"]:
            return Response(
                {
                    "detail": (
                        "raw_text cannot be changed after the first save for this "
                        "entry — it's the accountability record. "
                        "Only edited_json may be updated."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    # ── Validate everything before touching the database ──────────────
    errors = {}
    validated_lists = {}

    for key, serializer_class in INCIDENT_SERIALIZERS.items():
        items = edited.get(key) or []
        if not isinstance(items, list):
            errors[key] = ["Expected a list."]
            continue

        item_serializers = []
        item_errors = []
        any_error = False
        for item in items:
            s = serializer_class(data=item)
            ok = s.is_valid()
            item_serializers.append(s)
            item_errors.append(s.errors if not ok else None)
            any_error = any_error or not ok

        if any_error:
            errors[key] = item_errors
        else:
            validated_lists[key] = item_serializers

    lifelines_input = edited.get("lifelines_status") or {}
    lifelines_serializer = None
    if lifelines_input:
        lifelines_serializer = LifelinesStatusSerializer(data=lifelines_input)
        if not lifelines_serializer.is_valid():
            errors["lifelines_status"] = lifelines_serializer.errors

    if errors:
        return Response(
            {"detail": "Validation failed.", "errors": errors},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # ── Everything validated — now write ───────────────────────────────
    ai_output = data.get("ai_output", edited)
    entry_status = (
        ManualEntry.Status.MANUALLY_EDITED
        if ai_output != edited
        else ManualEntry.Status.PROCESSED
    )

    entry_fields = {
        "raw_text": data["raw_text"],
        "ai_output": ai_output,
        "unmapped_notes": edited.get("unmapped_notes") or "",
        "weather_condition": edited.get("weather_condition") or "",
        "status": entry_status,
        "processed_by": request.user,
        "processed_at": timezone.now(),
    }

    with transaction.atomic():
        if existing_entry:
            for field, value in entry_fields.items():
                setattr(existing_entry, field, value)
            existing_entry.save()
            entry = existing_entry
        else:
            entry = ManualEntry.objects.create(
                batch=batch, municipality=data["municipality"], **entry_fields
            )

        # edited_json is the full current state for this entry — replace
        # rather than diff.
        entry.road_crashes.all().delete()
        entry.medical_cases.all().delete()
        entry.fire_incidents.all().delete()
        entry.water_incidents.all().delete()
        entry.trauma_incidents.all().delete()
        LifelinesStatus.objects.filter(entry=entry).delete()

        counts = {}
        for key, item_serializers in validated_lists.items():
            for s in item_serializers:
                s.save(entry=entry)
            counts[key] = len(item_serializers)

        if lifelines_serializer is not None:
            lifelines_serializer.save(entry=entry)

    return Response(
        {
            "entry": ManualEntrySummarySerializer(entry).data,
            "counts": counts,
            "lifelines_saved": lifelines_serializer is not None,
        },
        status=status.HTTP_201_CREATED,
    )


# ── POST /api/batches/<id>/generate-synopsis/ ──────────────────────────
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def generate_synopsis_view(request, pk):
    """
    Draft-only — never saves anything, and deliberately works on a DRAFT
    batch (not just FINALIZED ones), since this is meant for OPS to use
    while still filling out the finalize panel, before locking the batch.
    The returned text still has to be explicitly submitted with the
    finalize request afterward, same as if OPS had typed it by hand — this
    endpoint doesn't touch batch.synopsis itself.
    """
    batch = get_object_or_404(ManualBatch, pk=pk)
    try:
        synopsis = generate_synopsis(batch)
    except RateLimitedError as exc:
        return Response(
            {"detail": str(exc), "retry_after_seconds": exc.retry_after_seconds},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except ExtractionError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    return Response({"synopsis": synopsis})


# ── POST /api/batches/<id>/generate-weather/ ────────────────────────────
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def generate_weather_view(request, pk):
    """
    Same shape as generate_synopsis_view — draft-only, works on a DRAFT
    batch, doesn't touch batch.weather_conditions itself.
    """
    batch = get_object_or_404(ManualBatch, pk=pk)
    try:
        weather = generate_weather_summary(batch)
    except RateLimitedError as exc:
        return Response(
            {"detail": str(exc), "retry_after_seconds": exc.retry_after_seconds},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    except ExtractionError as exc:
        return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
    return Response({"weather_condition": weather})


# ── Amend eligibility (last-5-period restriction) ──────────────────────
# Shift.choices values happen to sort "AM" < "PM" alphabetically too, but
# that's coincidental, not the reason AM comes first -- it's because the
# 0600H (AM) period is released before the same date's 1800H (PM) period.
# Relying on the alphabetical accident would silently break if the shift
# codes ever changed; this key is explicit instead. Not exported/used
# elsewhere yet, but same pattern as _current_batch_slot() above: a
# small, independently testable pure function.
_SHIFT_PERIOD_ORDER = {ManualBatch.Shift.AM: 0, ManualBatch.Shift.PM: 1}


def _period_sort_key(batch):
    """
    Chronological key for a batch's own (date, shift) PERIOD identity --
    deliberately NOT batch.finalized_at. A batch can be finalized out of
    period order (e.g. OPS catches up on a missed period via Batch
    History days later), but amend_eligible() below must still rank "the
    5 most recent periods" by the period each batch actually represents,
    not by when the Finalize button happened to be pressed.
    """
    return (batch.date, _SHIFT_PERIOD_ORDER[batch.shift])


def amend_eligible(batch):
    """
    Whether `batch` can currently be Amended. Fully lazy/derived, same
    spirit as _current_batch_slot(): no scheduled job, no explicit
    "lock" mutation anywhere. The moment a 6th post-cutoff batch
    finalizes and pushes an older one out of the window, that older
    batch's eligibility simply evaluates to False the next time this
    runs -- nothing has to notice or act on the transition.

    1. Only a FINALIZED batch can ever be eligible.
    2. A batch finalized before AMEND_RESTRICTION_CUTOFF is grandfathered
       -- always eligible, and does not occupy one of the 5 window slots
       for anyone else (see the exclusion in the query below).
    3. Otherwise, eligible only if `batch` is among the 5 most-recently-
       finalized batches whose finalized_at falls at/after the cutoff,
       ranked by _period_sort_key (period identity), not finalized_at.
    """
    if batch.status != ManualBatch.Status.FINALIZED or batch.finalized_at is None:
        return False

    cutoff = settings.AMEND_RESTRICTION_CUTOFF
    if batch.finalized_at < cutoff:
        return True

    # Small by construction (this office finalizes at most ~2 batches/day
    # -- see BatchAmendment's own docstring), so ranking in Python rather
    # than via a database-side CASE/WHEN on shift is simpler and just as
    # cheap in practice.
    post_cutoff = ManualBatch.objects.filter(
        status=ManualBatch.Status.FINALIZED,
        finalized_at__gte=cutoff,
    )
    most_recent_five = sorted(post_cutoff, key=_period_sort_key, reverse=True)[:5]
    return batch.id in {b.id for b in most_recent_five}


# ── POST /api/batches/<id>/amend/ ───────────────────────────────────────
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def amend_batch(request, pk):
    """
    Records an amendment and — via ManualBatch.is_locked comparing
    amended_at against finalized_at (and, now, amend_eligible() — see
    that function and models.ManualBatch.is_locked) — reopens entries/
    finalize-panel fields for editing, without ever moving status away
    from FINALIZED. finalize_batch (below) re-locks it the normal way
    once OPS is done editing and re-finalizes.

    ManualBatch.amended_at/amended_by/amendment_reason remain a
    denormalized "latest amendment" cache (overwritten every time, same
    as before) — PDF generation and is_locked's timestamp comparison
    both still read these directly. BatchAmendment is the append-only
    log alongside it: every successful amend now ALSO creates one row
    there, from this same `now` value, so the cache and the log can
    never drift apart. Only the eligibility check below can reject an
    amend; re-amending an already-open (still-eligible) batch is
    unaffected and just updates the reason, same as before.
    """
    batch = get_object_or_404(ManualBatch, pk=pk)
    if batch.status != ManualBatch.Status.FINALIZED:
        return Response(
            {"detail": "Only a finalized batch can be amended."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if not amend_eligible(batch):
        return Response(
            {
                "detail": (
                    "This period is outside the 5 most recent SitRep "
                    "periods and can no longer be amended."
                )
            },
            status=status.HTTP_403_FORBIDDEN,
        )

    body = AmendBatchRequestSerializer(data=request.data)
    body.is_valid(raise_exception=True)

    now = timezone.now()
    batch.amended_at = now
    batch.amended_by = request.user
    batch.amendment_reason = body.validated_data["reason"]
    batch.save()

    BatchAmendment.objects.create(
        batch=batch,
        amended_by=request.user,
        amended_at=now,
        amendment_reason=body.validated_data["reason"],
    )

    return Response(ManualBatchSerializer(batch).data)


# ── POST /api/batches/<id>/finalize/ ───────────────────────────────────
@api_view(["POST"])
@permission_classes([IsAuthenticated])
def finalize_batch(request, pk):
    """
    Freezes synopsis/weather/actions (from this request) and the current
    signatories (from SitRepSignatoryConfig) onto the batch itself —
    never regenerates a PDF here. Editing the settings page afterward
    must never retroactively change what an already-finalized batch's
    PDF says; freezing at this exact moment is what guarantees that.

    is_locked (not a raw status check) is what gates this — the same
    call handles both a fresh DRAFT batch's first finalize and an
    amended batch's re-finalize, since bumping finalized_at to now()
    here is exactly what makes is_locked true again afterward (see the
    property's own docstring). amended_at/amendment_reason are left
    untouched either way — the PDF's amendment note is a permanent
    record, not cleared by re-finalizing.
    """
    batch = get_object_or_404(ManualBatch, pk=pk)
    if batch.is_locked:
        return Response(
            {"detail": "This batch is already finalized."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not batch.entries.exists():
        return Response(
            {"detail": "Cannot finalize a batch with no entries."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    body = FinalizeBatchRequestSerializer(data=request.data)
    body.is_valid(raise_exception=True)

    signatories = SitRepSignatoryConfig.get_config()

    batch.synopsis = body.validated_data["synopsis"]
    batch.weather_conditions = body.validated_data["weather_conditions"]
    batch.actions_taken = body.validated_data["actions_taken"]
    batch.prepared_by = signatories.prepared_by
    batch.noted_by = signatories.noted_by
    batch.noted_by_title = signatories.noted_by_title
    batch.approved_by = signatories.approved_by
    batch.approved_by_title = signatories.approved_by_title
    batch.status = ManualBatch.Status.FINALIZED
    batch.finalized_at = timezone.now()
    batch.finalized_by = request.user
    batch.save()

    return Response(ManualBatchSerializer(batch).data)


# ── GET /api/batches/<id>/download/ ────────────────────────────────────
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def download_batch(request, pk):
    """
    Always regenerates the PDF fresh from the batch's frozen fields + its
    saved entries — no stored file, nothing that needs to survive on
    disk between requests. Content is byte-for-byte reproducible from
    the DB alone as long as the batch stays FINALIZED.
    """
    batch = get_object_or_404(ManualBatch, pk=pk)
    if batch.status != ManualBatch.Status.FINALIZED:
        return Response(
            {"detail": "This batch has not been finalized yet — no PDF available."},
            status=status.HTTP_404_NOT_FOUND,
        )
    filename, pdf_file = generate_batch_pdf(batch)
    return FileResponse(
        pdf_file,
        as_attachment=True,
        filename=filename,
        content_type="application/pdf",
    )
