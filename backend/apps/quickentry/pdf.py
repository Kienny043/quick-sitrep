"""
Quick SitRep — PDF generation (Step 6).

Compiles a ManualBatch's saved entries into a combined SitRep PDF using
WeasyPrint (same library the main system uses per CLAUDE.md's documented
stack). See docs/quick-report-entry-spec.md Section 7 (steps 5-6).

Section III's summary is computed here from the saved DB records —
never asked of the AI (spec Section 2's scope boundary: summary counts
are "computed from saved incident records, not extracted").
"""

from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from weasyprint import HTML

from .logo_base64 import QUEZON_SEAL_BASE64
from .models import RoadCrashVictim


def _shift_label(shift):
    return "0600H" if shift == "AM" else "1800H"


def _entries_with_incidents_prefetched(batch):
    # municipality first (groups each muni's entries together for
    # build_municipality_sections below), processed_at/id as a stable
    # tiebreaker so a municipality's combined incident list has a
    # deterministic, chronological order across however many separate
    # entries it has — ManualEntry's own default ordering already does
    # this, but an explicit .order_by() here overrides that rather than
    # extending it, so it's spelled out in full.
    return (
        batch.entries.select_related()
        .prefetch_related(
            "road_crashes__victims",
            "medical_cases",
            "fire_incidents",
            "water_incidents",
            "trauma_incidents",
        )
        .order_by("municipality", "processed_at", "id")
    )


def compute_summary(batch):
    """
    Section III. Road crashes have no casualties/injured/fatalities
    counters of their own (see apps/ops/models.py) — that information
    lives per-victim on RoadCrashVictim.injury_classification instead,
    so it's tallied separately from FireIncident/WaterIncident's direct
    counters, then rolled up into combined totals.
    """
    entries = _entries_with_incidents_prefetched(batch)

    road_crash_total = medical_total = fire_total = water_total = trauma_total = 0
    victims_minor = victims_major = victims_fatality = 0
    fire_casualties = fire_injured = fire_fatalities = 0
    water_casualties = water_injured = water_fatalities = 0

    Classification = RoadCrashVictim.InjuryClassification

    for entry in entries:
        road_crashes = list(entry.road_crashes.all())
        road_crash_total += len(road_crashes)
        for rc in road_crashes:
            for v in rc.victims.all():
                if v.injury_classification == Classification.MINOR:
                    victims_minor += 1
                elif v.injury_classification == Classification.MAJOR:
                    victims_major += 1
                elif v.injury_classification == Classification.FATALITY:
                    victims_fatality += 1

        medical_total += len(entry.medical_cases.all())
        trauma_total += len(entry.trauma_incidents.all())

        for fi in entry.fire_incidents.all():
            fire_total += 1
            fire_casualties += fi.casualties
            fire_injured += fi.injured
            fire_fatalities += fi.fatalities

        for wi in entry.water_incidents.all():
            water_total += 1
            water_casualties += wi.casualties
            water_injured += wi.injured
            water_fatalities += wi.fatalities

    return {
        "road_crash_total": road_crash_total,
        "medical_total": medical_total,
        "fire_total": fire_total,
        "water_total": water_total,
        "trauma_total": trauma_total,
        "victims_minor": victims_minor,
        "victims_major": victims_major,
        "victims_fatality": victims_fatality,
        "fire_casualties": fire_casualties,
        "fire_injured": fire_injured,
        "fire_fatalities": fire_fatalities,
        "water_casualties": water_casualties,
        "water_injured": water_injured,
        "water_fatalities": water_fatalities,
        # Rollups spanning all incident types, as requested — road crash
        # fatalities come from injury_classification, fire/water
        # fatalities from their own counters; both feed one grand total.
        "total_fatalities": victims_fatality + fire_fatalities + water_fatalities,
        "total_injured": victims_minor + victims_major + fire_injured + water_injured,
        "total_casualties": fire_casualties + water_casualties,
    }


def build_municipality_sections(batch):
    """
    Section II — only municipalities that reported at least one incident.
    A municipality can have several separate entries in one batch (one
    paste per incident is how some LGUs actually submit — Lucban in
    particular), so this combines every entry's incidents into that
    municipality's ONE section rather than emitting a section per entry;
    the reader shouldn't need to know or care how many separate pastes
    it took to build up a municipality's incident list.
    """
    sections_by_muni = {}
    for entry in _entries_with_incidents_prefetched(batch):
        road_crashes = list(entry.road_crashes.all())
        medical = list(entry.medical_cases.all())
        fire = list(entry.fire_incidents.all())
        water = list(entry.water_incidents.all())
        trauma = list(entry.trauma_incidents.all())

        if not (road_crashes or medical or fire or water or trauma):
            continue

        section = sections_by_muni.setdefault(
            entry.municipality,
            {
                "municipality_name": entry.get_municipality_display(),
                "road_crashes": [],
                "medical_assistance": [],
                "fire_incidents": [],
                "water_incidents": [],
                "trauma_emergencies": [],
            },
        )
        section["road_crashes"].extend(road_crashes)
        section["medical_assistance"].extend(medical)
        section["fire_incidents"].extend(fire)
        section["water_incidents"].extend(water)
        section["trauma_emergencies"].extend(trauma)

    return list(sections_by_muni.values())


def build_lifelines_sections(batch):
    """
    Section IV — only municipalities that actually reported lifelines.
    Unlike incidents, lifelines is a status snapshot, not a list of
    events, so when a municipality has more than one entry with
    lifelines data (each ManualEntry can carry its own), there's no
    sensible way to "combine" two different power/water/road statuses —
    the most recently processed entry's lifelines wins rather than
    showing several conflicting blocks for the same municipality.
    Iterating in (municipality, processed_at, id) order and always
    overwriting the dict's value for that key is what makes "last write
    wins" land on the latest entry, while insertion order (first time a
    municipality is seen) keeps the section list itself alphabetical.
    """
    latest_by_muni = {}
    for entry in batch.entries.select_related("lifelines").order_by(
        "municipality", "processed_at", "id"
    ):
        lifelines = getattr(entry, "lifelines", None)
        if lifelines is None:
            continue
        latest_by_muni[entry.municipality] = {
            "municipality_name": entry.get_municipality_display(),
            "lifelines": lifelines,
        }
    return list(latest_by_muni.values())


def generate_batch_pdf(batch):
    """
    Renders the template purely from the batch's own frozen fields
    (synopsis/weather_conditions/actions_taken/signatories — set once at
    finalize time, see views.finalize_batch) plus its saved entries.
    Deliberately takes no other arguments: this must be reproducible on
    demand from the DB alone, byte-for-byte, however many times it's
    called — no stored file, no live config lookup. Returns
    (filename, ContentFile); caller decides what to do with it.
    """
    context = {
        "batch": batch,
        "shift_label": _shift_label(batch.shift),
        "synopsis": batch.synopsis,
        "weather_conditions": batch.weather_conditions,
        "actions_taken": batch.actions_taken,
        "municipality_sections": build_municipality_sections(batch),
        "lifelines_sections": build_lifelines_sections(batch),
        "summary": compute_summary(batch),
        "prepared_by": batch.prepared_by,
        "noted_by": batch.noted_by,
        "noted_by_title": batch.noted_by_title,
        "approved_by": batch.approved_by,
        "approved_by_title": batch.approved_by_title,
        "seal_base64": QUEZON_SEAL_BASE64,
    }
    html_string = render_to_string("quickentry/sitrep_pdf.html", context)
    pdf_bytes = HTML(string=html_string).write_pdf()

    filename = f"QuickSitRep_{batch.date}_{batch.shift}.pdf"
    return filename, ContentFile(pdf_bytes)
