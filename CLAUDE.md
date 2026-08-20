# Quick SitRep — Project Context for Claude Code

Standalone bridge tool for the Provincial Disaster Risk Reduction and
Management Office (PDRRMO) of Quezon Province, Philippines. OPS pastes a
municipality's Messenger-style incident report, an AI extracts it into
structured incident records, OPS reviews/edits the extraction, saves it,
finalizes a batch of municipality entries together, and downloads a
combined SitRep PDF — all without waiting on the main PDRRMO-IMS system's
own SitRep AI pipeline (a known, still-unresolved issue there: Groq
fails on large submitted-report batches) to be fixed first.

Built as its **own separate Django project** on purpose, not a module
inside the main system, so OPS could start using something immediately.
Its incident model field names mirror `apps/ops/models.py` in the main
repo field-for-field (only the parent FK differs: `ManualEntry` here vs.
`LGUSubmission` there), so migrating this tool's data into the main
system later is meant to be a field-mapping exercise, not a redesign —
but that migration is **not urgent and has not been started**.

Lives at `../quick-sitrep/` (sibling of the main `PDRRMO_v3/` project),
own git repository, own `.env`, own history. Spec doc:
`docs/quick-report-entry-spec.md`.

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, Django 6.0.4, Django REST Framework 3.17 |
| Auth | Django session auth + CSRF (single shared OPS login, no role system) |
| Database | SQLite (dev only — see Deployment) |
| Frontend | Django templates + vanilla JS, no build step, no npm/package.json |
| PDF Generation | WeasyPrint |
| AI | Groq only (`openai/gpt-oss-120b`, called directly via `requests`, no SDK) |

There is no React/Vite frontend here and no JWT — deliberately simpler
than the main system, matching this tool's single-office-tool scope.

## Project Structure

```
quick-sitrep/
├── docs/quick-report-entry-spec.md
└── backend/
    ├── manage.py
    ├── requirements.txt
    ├── .env / .env.example
    ├── db.sqlite3
    ├── config/
    │   ├── settings.py         # single file, not split base/dev/prod
    │   ├── urls.py
    │   ├── wsgi.py / asgi.py
    └── apps/quickentry/
        ├── models.py            # ManualBatch, ManualEntry, incident child models, SitRepSignatoryConfig
        ├── serializers.py       # incident serializers + get_incident_schema()
        ├── views.py             # pages + API views
        ├── urls.py              # /api/... (included under config/urls.py's "api/")
        ├── ai.py                 # Groq extraction + adaptive cooldown
        ├── pdf.py                 # compute_summary() + generate_batch_pdf()
        ├── logo_base64.py        # embedded PDRRMO logo / Quezon seal for the PDF
        ├── admin.py
        ├── migrations/
        ├── templates/
        │   ├── quickentry/{base,main,settings,history,sitrep_pdf}.html
        │   └── registration/login.html
        └── static/quickentry/{css/main.css, js/main.js, img/logo.png}
```

## Key Models (`apps/quickentry/models.py`)

**`ManualBatch`** — one 12-hour reporting window.
`date`, `shift` (`AM`=6:00AM/0600H, `PM`=6:00PM/1800H),
`status` (`DRAFT`/`FINALIZED`), `finalized_at`, `finalized_by`.
Frozen-at-finalize fields (see "PDF generation" below): `synopsis`,
`weather_conditions`, `actions_taken`, `prepared_by`, `noted_by`,
`noted_by_title`, `approved_by`, `approved_by_title`.
`unique_together = (date, shift)`.

**`ManualEntry`** — one municipality's submission within a batch.
`batch` FK, `municipality` (choice field, all 41 Quezon municipalities),
`raw_text` (pasted text, immutable after first save — the accountability
record), `ai_output` (frozen raw extraction, kept as-is even after human
edits, for audit comparison), `unmapped_notes` (separate column, not just
folded into `ai_output`, so an OPS edit to it survives re-opening the
entry), `status` (`PENDING`/`PROCESSED`/`MANUALLY_EDITED`/`FAILED`).
`unique_together = (batch, municipality)`.

**Incident child models** (FK to `ManualEntry`, mirroring
`apps/ops/models.py` field-for-field):
- `RoadCrash` + `RoadCrashVictim` (victims are a separate child model,
  `age`/`sex`/`address`/`injuries`/`injury_classification` —
  `MINOR`/`MAJOR`/`FATALITY`)
- `MedicalAssistance`
- `FireIncident` — `ipo`/`dtr`/`ted`/`tas` are required `TimeField`s, no
  default, matching the main system exactly
- `WaterIncident`
- `TraumaEmergency`
- `LifelinesStatus` (`OneToOneField` to `ManualEntry`) — power/water/
  communication/road/sea status + notes, `class_suspension` boolean

**`SitRepSignatoryConfig`** — singleton (`get_config()` classmethod,
`save()` guards against a 2nd row), same pattern as the main system's
`SitRepConfig`. `prepared_by`, `noted_by`, `noted_by_title`,
`approved_by`, `approved_by_title`. Edited via the `/settings/` page —
this is the single source of truth; there are no hardcoded signatory
constants anywhere else in the codebase.

## API Endpoints (`apps/quickentry/urls.py`, mounted at `/api/`)

- `GET /api/ai-status/` — cooldown seconds remaining before the next
  Groq call is safe to attempt (drives the frontend's countdown)
- `GET /api/current-batch/` — today's current-shift batch (no param), or
  a specific batch via `?batch_id=` (used by Batch History's "Open"
  links); returns the batch, all 41 municipalities' entry status, and
  `compute_summary()`'s counts
- `POST /api/entries/extract/` — preview-only AI extraction, never
  touches the DB
- `GET /api/entries/<id>/` — re-open a saved entry for editing
- `POST /api/entries/save/` — validates every incident list against its
  real serializer, then writes the entry + child records in one
  transaction (replace, not diff)
- `POST /api/batches/<id>/finalize/` — freezes synopsis/weather/actions
  (from the request) and the current `SitRepSignatoryConfig` values onto
  the batch, sets it `FINALIZED`
- `GET /api/batches/<id>/download/` — always regenerates the PDF fresh
  (see below); 404s if the batch isn't `FINALIZED` yet

Non-API pages (`config/urls.py`): `/` (main page), `/settings/`
(signatories), `/history/` (Batch History), `/accounts/...` (Django's
built-in auth views), `/admin/`.

## The Batch Cycle

A day splits into two 12-hour windows: AM (overnight 6:00 AM–6:00 PM
compiled as the "0600H" report) and PM (daytime 6:00 AM–6:00 PM compiled
as "1800H") — `current_batch`'s no-param path uses a simple wall-clock
check (noon) against `timezone.localtime(now())`, independent of the
main system's own period logic. `get_or_create` never touches an
existing batch's status, so a `FINALIZED` batch can't be silently
reopened by someone loading the page later in the same window.

**Batch History** (`/history/`) lists every batch ever created, most
recent first, regardless of whether its window has closed. A `DRAFT` row
links back into the normal entry-editing view for that batch via
`?batch_id=` even if it's no longer "current"; a `FINALIZED` row links
straight to `/api/batches/<id>/download/`. This is deliberate: OPS must
be able to finish or re-download a past batch that was missed in its own
window.

## PDF Generation (`apps/quickentry/pdf.py`)

`generate_batch_pdf(batch)` is always called fresh, on every download
request — there is **no stored PDF file**. `ManualBatch.generated_pdf`
existed briefly and was removed (migration
`0004_remove_manualbatch_generated_pdf_and_more`) once the design settled
on regenerate-on-demand. The PDF is rebuilt purely from:
- the batch's own frozen fields (`synopsis`, `weather_conditions`,
  `actions_taken`, `prepared_by`, `noted_by`, `noted_by_title`,
  `approved_by`, `approved_by_title` — all set once, at
  `finalize_batch()`, never re-derived from the live
  `SitRepSignatoryConfig` afterward), and
- the batch's saved entries/child records, read straight from the DB.

This means the same batch produces a byte-for-byte reproducible PDF no
matter how many times or how long after finalizing it's downloaded, and
editing `/settings/` later never retroactively changes an
already-finalized document. It also means the tool has **no dependency
on persistent disk** between requests — relevant because the intended
deployment target (Render free tier, see below) doesn't have one.

`compute_summary()` in the same file is the single source of truth for
all incident/casualty counts — used by both the PDF's Section III and
the `/api/current-batch/` summary cards on the main page, so there is
only one counting implementation, not two that could drift apart.

## AI Extraction (`apps/quickentry/ai.py`)

- Groq only (`openai/gpt-oss-120b`), called directly via `requests` — no
  Groq SDK, no fallback chain (unlike the main system's Groq → Gemini →
  OpenAI chain in `apps/ops/ai.py`). Note: `llama-3.3-70b-versatile`
  (the model the main system's `ai.py` currently hardcodes) is confirmed
  retired from Groq's catalog — 404s on `GET /openai/v1/models`. Worth
  fixing there too when that gets picked back up.
- JSON mode (`response_format: {"type": "json_object"}`), `temperature`
  0.1, `max_tokens` 4096, one large system prompt with label aliases,
  extraction rules, and few-shot examples (road crash x2, fire incident).
- **Schema-driven required-field validation**: `get_incident_schema()`
  in `serializers.py` introspects the real DRF serializers
  (`field.required`, excluding admin/injected fields like `id`/`entry`/
  `created_at`) and embeds the result into `main.html` via
  `json_script`. The frontend's blocking-validation UI reads this at
  page load — there is no separate hardcoded copy of "which fields are
  required per incident type" to drift out of sync.
- **Adaptive rate-limit cooldown**: every real Groq response is inspected
  for `x-ratelimit-remaining-tokens` / `x-ratelimit-reset-tokens`
  (Go-duration format — e.g. `"45.989s"`, `"1m26.4s"` — parsed by
  `_parse_go_duration`, confirmed against real responses, not assumed).
  If remaining tokens drop below `MAX_TOKENS` (4096), the *next* call
  proactively waits out the reported reset window (capped at 90s) before
  even trying, instead of firing and hitting a 429. `GET
  /api/ai-status/` exposes `seconds_until_available()` so the frontend
  can show a live countdown and disable "Process with AI" before the
  user clicks into a call that would just block. On top of this,
  `_post_with_retry` still retries on an actual 429 (honoring
  `Retry-After` when present, a plain integer of seconds; falling back
  to exponential backoff otherwise) up to `MAX_ATTEMPTS = 3`. Any
  non-429 failure is treated as non-transient and raised immediately.

## Known Gaps / Not Yet Done

- **No unsaved-changes warning** — navigating away from an in-progress
  edit with unsaved changes doesn't prompt; nothing in `main.js` hooks
  `beforeunload`.
- **`parseRoughDatetime()` only handles two date formats** (pipe-
  separated — `"February 11, 2026 | 0900H"` — and comma-separated —
  `"February 19, 2026, 0600H"`). Anything else fails to parse and leaves
  the date/time picker blocked rather than guessing — this is
  deliberate, not an oversight: the AI's raw `datetime` string is never
  auto-committed to a `DateTimeField`. OPS must always see a real
  date/time picker and confirm or correct the value before save, per the
  spec's "never silently guess" rule. When adding a new format, extend
  `parseRoughDatetime` additively (add another `splitDateTime_*` helper)
  rather than replacing the existing patterns — reports in the wild mix
  formats, sometimes within the same batch.
- **Migration path into the main system (spec Section 8) has not been
  started.** Intentional — not urgent; this tool exists precisely so OPS
  doesn't have to wait on that.
- Main system's other known issues (dead Groq model in its own `ai.py`,
  fragile PAGASA scraper, etc.) are out of scope here and untouched.

## Testing Approach (No Browser Tool Available)

This tool was built and verified in an environment with no browser
automation available, using three techniques worth continuing rather
than reinventing:

1. **Session + CSRF Python scripts against the real running dev server**
   — `session.get()` the login page, POST credentials with the scraped
   `csrftoken` cookie, then send subsequent requests with
   `X-CSRFToken` + a matching `Referer` header. Exercises the actual
   Django session/CSRF stack rather than bypassing it.
2. **The real, shipped `main.js` executed under Node** — loaded via
   `new Function(...)` against hand-built `document`/`fetch` stubs, so
   assertions run against the exact production code path, not a
   reimplementation of its logic. Several bugs turned up this way were
   in the *test harness* itself (stubs not faithfully matching real
   browser/`requests` behavior) rather than the app — always check which
   side a failure is on before "fixing" app code.
   One concrete case: mocking `requests.Response.headers` as a plain
   Python `dict` in a rate-limit test silently hid a would-be
   case-sensitivity bug, because a plain dict *is* case-sensitive while
   the real `requests.Response.headers` (a `CaseInsensitiveDict`) is
   not — Groq sends a lowercase `retry-after` header and `ai.py` reads
   `"Retry-After"`, which only works against the real case-insensitive
   type. Use `requests.structures.CaseInsensitiveDict` for any header
   mock, never a plain dict.
3. **`pypdf`** to extract text/images back out of generated PDFs for
   assertions, and **real Groq API calls** (not mocked) whenever the
   exact response shape or header names/formats mattered — e.g.
   confirming `x-ratelimit-reset-tokens`'s Go-duration string format and
   that Groq's free tier really does trip its per-minute budget after
   just two back-to-back calls with this system prompt. Docs and reality
   drift; confirm against a live call before coding to an assumption.

## Running the Project

```bash
cd backend
python -m venv venv && source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # fill in DJANGO_SECRET_KEY and GROQ_API_KEY at minimum
python manage.py migrate
python manage.py createsuperuser   # or any manage.py shell User.objects.create_user(...)
python manage.py runserver
```

No frontend build step — templates and `static/quickentry/js/main.js`
are served directly by Django (`runserver` + `django.contrib.staticfiles`
in dev). Visit `http://127.0.0.1:8000/`, log in with whatever user you
created — there's no seeded test-user fixture or seed command in this
repo; any Django auth user works, since auth here is deliberately just
"logged in or not," with no role system (see `settings.py`'s
`REST_FRAMEWORK` comment).

`.env` required keys: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`,
`DJANGO_ALLOWED_HOSTS`, `GROQ_API_KEY` (extraction won't work without
it — `ExtractionError` is raised immediately if unset). `GEMINI_API_KEY`
/ `OPENAI_API_KEY` are read into settings but currently unused (no
fallback chain implemented yet, see AI Extraction above).

## Deployment (Planned, Not Yet Done)

Target: Render (free tier) + an external managed Postgres (Neon or
Supabase — free tier has no persistent disk of its own for SQLite, and
Render's free tier disk is ephemeral across deploys/restarts). This is
exactly why PDF generation was redesigned to always regenerate on demand
from DB fields rather than rely on a stored file (see "PDF Generation"
above) — the app needs to survive with zero durable local disk. As of
this writing the app has not actually been deployed anywhere; it only
runs against local SQLite in dev.

## Notes for Claude Code

- This is a small, single-office internal tool — match its existing
  simplicity (plain Django templates, vanilla JS, no build tooling, no
  role system) rather than introducing new architectural layers.
- Field names in the incident models must keep mirroring
  `apps/ops/models.py` in the main `PDRRMO_v3` repo — check there before
  changing or adding a field here, since drift defeats the point of the
  future migration path.
- Ask before large refactors, same as the main project.
