"""
Quick SitRep — API endpoints. See docs/quick-report-entry-spec.md
Section 6. PDF compilation (Step 6) isn't built yet — finalize/download
are stubbed accordingly.
"""

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

from .ai import extract_incident_data, seconds_until_available, ExtractionError
from .models import (
    MUNICIPALITY_CHOICES,
    ManualBatch,
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
    batches = ManualBatch.objects.annotate(entry_count=Count("entries")).order_by(
        "-date", "-shift"
    )
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
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def current_batch(request):
    """
    No ?batch_id= — simple wall-clock check, independent of the main
    system's period logic (and of the reopen-bug found there): noon
    splits the day into the AM batch (overnight 6PM–6AM, compiled as the
    "0600H" report) and the PM batch (daytime 6AM–6PM, compiled as the
    "1800H" report). get_or_create never touches an existing batch's
    status, so a FINALIZED batch can never get silently reopened by
    someone loading this page later in the same window.

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
        shift = ManualBatch.Shift.AM if now.hour < 12 else ManualBatch.Shift.PM

        batch, _created = ManualBatch.objects.get_or_create(
            date=now.date(),
            shift=shift,
            defaults={"status": ManualBatch.Status.DRAFT},
        )

    existing = {e.municipality: e for e in batch.entries.all()}
    municipalities = [
        {
            "id": code,
            "name": name,
            "has_entry": code in existing,
            "entry_id": existing[code].id if code in existing else None,
            "entry_status": existing[code].status if code in existing else None,
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
    if batch.status == ManualBatch.Status.FINALIZED:
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

    # raw_text is the accountability record — never silently overwritten
    # once a municipality's entry exists for this batch.
    existing_entry = ManualEntry.objects.filter(
        batch=batch, municipality=data["municipality"]
    ).first()
    if existing_entry and existing_entry.raw_text != data["raw_text"]:
        return Response(
            {
                "detail": (
                    "raw_text cannot be changed after the first save for this "
                    "municipality/batch — it's the accountability record. "
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

    with transaction.atomic():
        entry, _created = ManualEntry.objects.update_or_create(
            batch=batch,
            municipality=data["municipality"],
            defaults={
                "raw_text": data["raw_text"],
                "ai_output": ai_output,
                "unmapped_notes": edited.get("unmapped_notes") or "",
                "status": entry_status,
                "processed_by": request.user,
                "processed_at": timezone.now(),
            },
        )

        # edited_json is the full current state for this municipality's
        # entry — replace rather than diff.
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
    """
    batch = get_object_or_404(ManualBatch, pk=pk)
    if batch.status == ManualBatch.Status.FINALIZED:
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
