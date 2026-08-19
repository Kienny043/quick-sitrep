# Quick SitRep — Standalone Bridge Tool — Feature Spec (v2)

**Status:** Ready for implementation — standalone-first approach
**Purpose:** A small, independently-deployable tool so OPS can start
generating combined SitRep PDFs from pasted Messenger reports *now*, while
the main PDRRMO-IMS capstone system is still being finished. Built to be
portable into the main system later without redoing the extraction/schema
design work.

---

## 0. Why Standalone (context for whoever picks this up later)

Building this directly into the main system would tie its usability to the
main system's deploy-readiness — which currently isn't there yet (known
issues: PAGASA scraper, Groq large-payload SitRep failures, a just-found
hardcoded-secrets regression, analytics revamp in progress). The client
needs something usable immediately. So: standalone tool first, ship it,
then port/integrate into the main system once both are stable.

To keep porting cheap later, this tool's incident model **field names
mirror the real PDRRMO-IMS models exactly** (confirmed via a Claude Code
audit of `apps/ops/models.py` — see Section 3). Migrating later should be a
field-mapping exercise, not a redesign.

---

## 1. Project Structure

Own project root, sibling to the main system's `backend/`/`frontend/`, same
git repo:

```
PDRRMO-IMS/                  (main repo root)
├── backend/                 (main capstone — untouched by this work)
├── frontend/                (main capstone — untouched by this work)
├── quick-sitrep/             (standalone bridge tool — own project root)
│   ├── backend/
│   │   ├── manage.py
│   │   ├── config/
│   │   │   ├── settings.py   (own .env — never hardcode keys; see the
│   │   │   │                  security note from the main-repo audit,
│   │   │   │                  same discipline applies here)
│   │   │   └── urls.py
│   │   ├── apps/quickentry/
│   │   │   ├── models.py
│   │   │   ├── ai.py          (own copy of the fallback-chain logic)
│   │   │   ├── serializers.py
│   │   │   ├── views.py
│   │   │   ├── pdf.py         (PDF generation — WeasyPrint, same approach
│   │   │   │                  as the main system)
│   │   │   └── templates/quickentry/sitrep_template.html
│   │   └── requirements.txt
│   └── frontend/             (lightweight — plain Django templates + a
│                               little vanilla JS is enough for one internal
│                               OPS-only page; a full Vite/React app is
│                               optional polish, not required for MVP)
└── docs/
    └── quick-report-entry-spec.md   (this file)
```

Deployed as its own process/port, independent of the main system's deploy
status. Can move servers, get killed, or be rebuilt without touching the
main capstone project.

---

## 2. Scope Boundaries (unchanged from the sample SitRep analysis)

From the sample doc (`February 15, 2026 - 1800H SITUATIONAL REPORT`):

**Municipality-sourced (this tool's job):**
- Incidents (Road Crash, Medical, Fire, Water, Trauma)
- Lifelines Status

**NOT this tool's job — filled in separately by OPS on the compiled PDF, or
left as a manual field on the batch:**
- Synopsis/Weather narrative (province-wide, EOC-written)
- Summary counts (computed from saved incident records, not extracted)
- EOC-level Actions Taken
- Signatories (can hardcode from the same values as `SitRepConfig`, or make
  it a simple settings row — see Section 4)

---

## 3. Models

### `ManualBatch`
Represents one 6AM–6PM / 6PM–6AM cycle's compiled report.
- `date` — DateField
- `shift` — CharField, choices `AM` (0600H) / `PM` (1800H)
- `status` — DRAFT / FINALIZED
- `generated_pdf` — FileField, null/blank
- `finalized_at` — DateTimeField, null/blank
- `finalized_by` — FK to a simple staff/user model (see auth note below)
- Meta: `unique_together = ("date", "shift")`

### `ManualEntry`
One municipality's slot within a batch.
- `batch` — FK `ManualBatch`, CASCADE
- `municipality` — CharField or FK (see note below) — the 41 Quezon
  municipalities; reuse the same list as `frontend/src/utils/cities.js`
- `raw_text` — TextField — the pasted Messenger text, stored verbatim,
  **never edited after save** (this is the accountability record)
- `ai_output` — JSONField — the raw AI extraction result, kept as-is even
  after human edits, for audit comparison
- `status` — PENDING / PROCESSED / MANUALLY_EDITED / FAILED
- `processed_by` — FK to staff/user
- `processed_at` — DateTimeField
- Meta: `unique_together = ("batch", "municipality")`

### Incident child models
Mirror the real system's fields exactly (confirmed field-by-field via the
main-repo audit), FK'd to `ManualEntry` instead of `LGUSubmission`:

- `RoadCrash` (+ `RoadCrashVictim`) — same fields as
  `apps/ops/models.py:170` / `:195`
- `MedicalAssistance` — same fields as `:216`
- `FireIncident` — same fields as `:248` — **note the four required
  TimeFields (`ipo`/`dtr`/`ted`/`tas`) with no default**; the preview UI
  must block finalizing a fire incident missing any of these
- `WaterIncident` — same fields as `:283`
- `TraumaEmergency` — same fields as `:313`
- `LifelinesStatus` — OneToOne on `ManualEntry`, same fields as `:343`

### Auth
Doesn't need the full role system — this is one internal page for OPS
staff. Simplest: Django's built-in User model + `is_staff`, or even a
single shared login for the office if that's acceptable for now. Not worth
building out SUPER_ADMIN/ADMIN_TRAINING/OPS/LGU roles for a bridge tool.

---

## 4. JSON Extraction Schema

**Identical to the schema validated against the main system's real models**
— reused as-is, since the field names now match both the standalone models
above and the eventual migration target:

```json
{
  "lifelines_status": {
    "power_supply": "OPERATIONAL | INTERRUPTED | UNDER_REPAIR",
    "power_supply_notes": "string",
    "water_supply": "OPERATIONAL | INTERRUPTED | UNDER_REPAIR",
    "water_supply_notes": "string",
    "communication": "OPERATIONAL | INTERRUPTED | UNDER_REPAIR",
    "communication_notes": "string",
    "road_condition": "PASSABLE | IMPASSABLE | FLOODED",
    "road_notes": "string",
    "sea_travel": "NORMAL | SUSPENDED | MODIFIED",
    "sea_notes": "string",
    "class_suspension": "boolean",
    "class_suspension_notes": "string"
  },
  "road_crashes": [
    {
      "datetime": "string (raw as reported)",
      "location": "string", "barangay": "string or null",
      "cause": "string", "vehicles_involved": "string",
      "actions_taken": "string", "responding_team": "string",
      "driver": "string or null", "ert_members": "string or null",
      "victims": [
        { "age": "number or null", "sex": "string or null", "address": "string or null",
          "injuries": "string", "injury_classification": "MINOR | MAJOR | FATALITY" }
      ]
    }
  ],
  "medical_assistance": [
    {
      "datetime": "string", "location": "string", "barangay": "string or null",
      "patient_age": "number or null", "patient_sex": "string or null", "patient_address": "string or null",
      "nature_of_illness": "string", "chief_complaint": "string or null",
      "blood_pressure": "string or null", "pulse_rate": "string or null",
      "spo2": "string or null", "temperature": "string or null",
      "actions_taken": "string", "responding_team": "string",
      "driver": "string or null", "ert_members": "string or null"
    }
  ],
  "fire_incidents": [
    {
      "datetime": "string", "location": "string", "barangay": "string or null",
      "ipo": "string or null (REQUIRED before save)", "dtr": "string or null (REQUIRED before save)",
      "ted": "string or null (REQUIRED before save)", "tas": "string or null (REQUIRED before save)",
      "response_time_minutes": "number or 0", "distance_km": "number or 0",
      "structure_type": "string", "families_affected": "number or 0",
      "individuals_affected": "number or 0", "structures_burned": "number or 0",
      "fire_area_sqm": "number or 0", "casualties": "number or 0",
      "injured": "number or 0", "fatalities": "number or 0",
      "responding_team": "string", "actions_taken": "string"
    }
  ],
  "water_incidents": [
    {
      "datetime": "string", "location": "string", "barangay": "string or null",
      "incident_type": "string (free text)", "description": "string or null",
      "families_affected": "number or 0", "individuals_affected": "number or 0",
      "casualties": "number or 0", "injured": "number or 0", "fatalities": "number or 0",
      "responding_team": "string", "actions_taken": "string"
    }
  ],
  "trauma_emergencies": [
    {
      "datetime": "string", "location": "string", "barangay": "string or null",
      "call_type": "string (free text)", "patient_age": "number or null",
      "patient_sex": "string or null", "patient_address": "string or null",
      "chief_complaint": "string or null", "actions_taken": "string",
      "responding_team": "string", "driver": "string or null", "ert_members": "string or null"
    }
  ],
  "unmapped_notes": "Anything found but not confidently placed above — surfaced to OPS, never silently dropped."
}
```

Rules for the AI: municipality is never its job (OPS already picked it);
empty arrays are valid and common; don't compute summary counts (downstream
logic does that); prefer null/omission over guessing, route uncertain
fragments to `unmapped_notes`.

---

## 5. AI Integration (`quick-sitrep/backend/apps/quickentry/ai.py`)

Own copy of a provider-fallback function (can start with just one provider
— e.g. Groq or OpenAI directly — if the full fallback chain is more than
this MVP needs; add fallback later if reliability becomes an issue).

```
extract_incident_data(municipality_name, raw_text) -> dict
```

- Dedicated system prompt: the JSON schema above + the label-alias table
  (WHAT/WHEN/WHERE convention, "PERSONS INVOLVED"/"PATIENT DETAILS"/
  "VICTIM" all mean victims, etc.) + 2–3 few-shot examples built from the
  sample SitRep's style (genericized, no real PII)
- Parse the AI's JSON response; on parse failure, surface a clear error
- Validate against real serializers before saving — especially the
  FireIncident four-required-timefields case (Section 3)

---

## 6. API Endpoints

- `GET /api/current-batch/` — returns/creates today's batch for the current
  6AM/6PM shift (simple wall-clock check, no dependency on the main
  system's period logic), plus which municipalities already have an entry
- `POST /api/entries/extract/` — body: `{ municipality, raw_text }` → calls
  `extract_incident_data()`, returns structured JSON for preview (not
  saved yet)
- `POST /api/entries/save/` — body: `{ batch_id, municipality, raw_text,
  edited_json }` → validates, creates/updates the `ManualEntry` + child
  incident records
- `POST /api/batches/<id>/finalize/` — compiles all entries in the batch
  into the combined PDF template, saves `generated_pdf`, marks FINALIZED
- `GET /api/batches/<id>/download/` — serves the generated PDF

---

## 7. UI Flow (single OPS-facing page)

1. Page loads today's current batch (auto-detected AM/PM shift) and shows
   a checklist of all 41 municipalities — which have an entry, which don't
2. Click a municipality → paste box + "Process with AI"
3. AI result shown in an editable form, grouped by incident type;
   `unmapped_notes` shown prominently; FireIncident missing required time
   fields is flagged and blocks save until filled
4. "Save Entry" → stores it, checklist updates
5. Once enough municipalities are in (not all 41 need to report — matches
   real-world pattern), "Generate Combined PDF" compiles everything into
   one document using the same template structure as the sample SitRep
   (grouped by municipality, Section II style entries, Section IV
   lifelines) — reuse WeasyPrint the same way the main system does
6. Download the PDF

---

## 8. Migration Path (future, once main system is stable)

Because field names already match, integrating later means:
- Writing a one-off script that reads `ManualBatch`/`ManualEntry` +
  children and creates corresponding `ReportPeriod`/`LGUSubmission` +
  incident rows in the main system, tagged `source=MANUAL_ENTRY`
  (matches the `source`/`entered_by`/`raw_source_text` fields already
  planned for `LGUSubmission` in the original integrated-approach spec —
  keep that design on file for when this day comes)
- Not urgent — do this once the main system's own submission flow is
  reliable enough that the bridge tool can be retired

---

## 9. Suggested Build Order

1. Scaffold `quick-sitrep/backend/` — new Django project, own `.env`,
   own `requirements.txt` (can literally start from a copy of the main
   backend's `requirements.txt` and trim)
2. Models (Section 3) + migrations
3. `extract_incident_data()` in `ai.py` (Section 5) — test with fragments
   from the sample SitRep doc as manual input before any UI exists
4. API endpoints (Section 6)
5. Frontend page (Section 7) — start with plain Django templates/vanilla
   JS for speed; upgrade later if worth it
6. PDF template/generation (reuse the main system's WeasyPrint approach)
7. Deploy — own process/port, test with real OPS usage
