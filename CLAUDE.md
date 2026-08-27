# Quick SitRep — Project Context for Claude Code

Standalone bridge tool for the Provincial Disaster Risk Reduction and
Management Office (PDRRMO) of Quezon Province, Philippines. OPS pastes a
municipality's Messenger-style incident report, an AI extracts it into
structured incident records, OPS reviews/edits the extraction, saves it,
optionally drafts a synopsis/weather summary with AI, finalizes a batch
of municipality entries together, and downloads a combined SitRep PDF —
all without waiting on the main PDRRMO-IMS system's own SitRep AI
pipeline (a known, still-unresolved issue there: Groq fails on large
submitted-report batches) to be fixed first.

**This is now the client's live, actively-used tool** — deployed on
Render, real dated batches in production since 2026-08-22 (see
Deployment/Production Operations below). Treat production data as real;
it is not a sandbox.

Built as its **own separate Django project** on purpose, not a module
inside the main system, so OPS could start using something immediately.
Its incident model field names mirror `apps/ops/models.py` in the main
repo field-for-field where practical (one deliberate, client-directed
divergence: `FireIncident` — see Key Models), so migrating this tool's
data into the main system later is meant to be a field-mapping exercise,
not a redesign — but that migration is **not urgent and has not been
started**.

Lives at `../quick-sitrep/` (sibling of the main `PDRRMO_v3/` project),
own git repository, own `.env`, own history. Spec doc:
`docs/quick-report-entry-spec.md` (increasingly historical — several
features below shipped after the spec was written; code + this file are
the current source of truth, not the spec).

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python, Django 6.0.4, Django REST Framework 3.17 |
| Auth | Django session auth + CSRF (single shared login, no role system — see Production Operations for why that matters now that it's live) |
| Database | **Production: Postgres on Neon.** Dev: SQLite. Same `DATABASE_URL`-or-SQLite-fallback logic in `settings.py` either way. |
| Frontend | Django templates + vanilla JS, no build step, no npm/package.json |
| PDF Generation | WeasyPrint |
| Static files | WhiteNoise (`CompressedManifestStaticFilesStorage`) |
| AI | Groq only (`openai/gpt-oss-120b`, called directly via `requests`, no SDK), with an optional second-account fallback key |
| Deployment | Render (free tier, `WEB_CONCURRENCY=1`) + Neon Postgres |

There is no React/Vite frontend here and no JWT — deliberately simpler
than the main system, matching this tool's single-office-tool scope.

## Project Structure

```
quick-sitrep/
├── docs/quick-report-entry-spec.md
└── backend/
    ├── manage.py
    ├── requirements.txt
    ├── build.sh                # Render build command: pip install, collectstatic, migrate
    ├── .env / .env.example
    ├── db.sqlite3               # dev only
    ├── config/
    │   ├── settings.py          # single file, not split base/dev/prod
    │   ├── urls.py
    │   ├── wsgi.py / asgi.py
    └── apps/quickentry/
        ├── models.py            # ManualBatch, ManualEntry, incident child models, SitRepSignatoryConfig
        ├── serializers.py       # incident serializers + get_incident_schema()
        ├── views.py             # pages + API views
        ├── urls.py              # /api/... (included under config/urls.py's "api/")
        ├── ai.py                 # Groq extraction/synopsis/weather + fail-fast rate-limit + fallback key
        ├── pdf.py                 # compute_summary() + generate_batch_pdf()
        ├── logo_base64.py        # embedded PDRRMO logo / Quezon seal for the PDF
        ├── admin.py
        ├── migrations/           # 0001–0008, see git log for what each added
        ├── templates/
        │   ├── quickentry/{base,main,settings,history,sitrep_pdf}.html
        │   └── registration/login.html
        └── static/quickentry/{css/main.css, js/main.js, img/logo.png}
```

No `render.yaml` / `Procfile` in this repo — Render's start command and
all environment variables (`DATABASE_URL`, `GROQ_API_KEY`,
`DJANGO_SECRET_KEY`, etc.) are configured directly in Render's
dashboard, not committed here. `build.sh` is the only deploy-time
automation that lives in-repo.

## Key Models (`apps/quickentry/models.py`)

**`ManualBatch`** — one 12-hour reporting window.
`date`, `shift` (`AM`=6:00AM/0600H, `PM`=6:00PM/1800H),
`status` (`DRAFT`/`FINALIZED`), `finalized_at`, `finalized_by`.
Frozen-at-finalize fields (see "PDF generation" below): `synopsis`,
`weather_conditions`, `actions_taken`, `prepared_by`, `noted_by`,
`noted_by_title`, `approved_by`, `approved_by_title`.
`unique_together = (date, shift)`.

Amendment fields — `amended_at`, `amended_by`, `amendment_reason`. Only
the **latest** amendment is tracked, not a full append-only log (a
deliberate scope call — a log wasn't "barely more effort" so it was
skipped). The `is_locked` property is the single source of truth for
"can this batch be edited right now" — comparing `amended_at >
finalized_at` (timestamps), not a second open/closed flag or clearing
`amended_at` on re-finalize. This means: amending a `FINALIZED` batch
reopens it for editing without touching `status`, and re-finalizing
naturally re-locks it (finalize bumps `finalized_at` past `amended_at`)
while `amended_at`/`amendment_reason` stay set forever as a permanent
audit marker — the PDF keeps showing "this report was amended" even
after it's re-locked, same accountability spirit as `raw_text`/
`ai_output` being frozen elsewhere.

**`ManualEntry`** — one municipality's submission within a batch.
`batch` FK, `municipality` (choice field, all 41 Quezon municipalities),
`raw_text` (pasted text, immutable after first save — the accountability
record), `ai_output` (frozen raw extraction, kept as-is even after human
edits, for audit comparison), `unmapped_notes` (separate column, not just
folded into `ai_output`, so an OPS edit to it survives re-opening the
entry), `weather_condition` (own column, same "survives re-open" reason
as `unmapped_notes` — see AI Extraction), `status`
(`PENDING`/`PROCESSED`/`MANUALLY_EDITED`/`FAILED`).

**No `unique_together` on `(batch, municipality)` anymore.** A
municipality can submit several separate reports within one batch window
— confirmed in real usage (e.g. Lucban pastes one report per incident
rather than one combined report) — so each paste becomes its own
`ManualEntry` instead of overwriting the last. This is why `save_entry`
needs an explicit `entry_id` to disambiguate "new entry" from "edit this
specific one" (see API Endpoints), and why PDF/summary/history code all
group by municipality rather than assuming one entry each.

**Incident child models** (FK to `ManualEntry`, mirroring
`apps/ops/models.py` field-for-field except where noted):
- `RoadCrash` + `RoadCrashVictim` (victims are a separate child model,
  `age`/`sex`/`address`/`injuries`/`injury_classification` —
  `MINOR`/`MAJOR`/`FATALITY`)
- `MedicalAssistance`
- `FireIncident` — **`ipo`/`dtr`/`ted`/`tas` (BFP's four-timestamp
  fields) were dropped per explicit client direction**, replaced with a
  plain `cause` field, matching the same 4W1H convention every other
  incident type here already uses. This is a deliberate, client-directed
  divergence from `apps/ops/models.py` in the main repo — re-check with
  the client (not just the main repo) before ever reverting it.
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
  Groq call is safe to attempt, across *both* configured keys (whichever
  becomes free soonest) — drives the frontend's countdown
- `GET /api/current-batch/` — today's current-shift batch (no param), or
  a specific batch via `?batch_id=` (used by Batch History's "Open"
  links); returns the batch, all 41 municipalities' entry status
  (**a list of entries per municipality now, plus `entry_count`** — not
  a single entry, since a municipality can have several), and
  `compute_summary()`'s counts
- `POST /api/entries/extract/` — preview-only AI extraction, never
  touches the DB
- `GET /api/entries/<id>/` — re-open a saved entry for editing
- `POST /api/entries/save/` — validates every incident list against its
  real serializer, then writes the entry + child records in one
  transaction (replace, not diff). `entry_id` in the body picks the
  re-open-and-edit path; omitted, it always creates a brand new entry
  (see `ManualEntry`'s multi-entry note above). 400s if `batch.is_locked`.
- `POST /api/batches/<id>/generate-synopsis/` — AI-drafts a synopsis
  paragraph from the batch's saved entries ("Generate Draft" button).
  Draft-only: never writes to `batch.synopsis` itself, works on a
  `DRAFT` batch (OPS uses this *while* filling out the finalize panel,
  before locking). Deterministic short-circuits (no Groq call) for an
  empty batch or an all-zero-incident batch — see AI Extraction.
- `POST /api/batches/<id>/generate-weather/` — same shape, drafts a
  province-wide weather paragraph from each saved entry's own
  `weather_condition`, deduped, skipping entries that left it blank.
- `POST /api/batches/<id>/amend/` — reopens an already-`FINALIZED` batch
  for editing (sets `amended_at`/`amended_by`/`amendment_reason`; see
  `ManualBatch.is_locked` above). 400s if the batch isn't `FINALIZED`.
- `POST /api/batches/<id>/finalize/` — freezes synopsis/weather/actions
  (from the request) and the current `SitRepSignatoryConfig` values onto
  the batch, sets it `FINALIZED`. Same call handles both a fresh
  `DRAFT`'s first finalize and an amended batch's re-finalize — gated on
  `is_locked`, not a raw status check.
- `GET /api/batches/<id>/download/` — always regenerates the PDF fresh
  (see below); 404s if the batch isn't `FINALIZED` yet

All AI-backed endpoints above (`extract`, `generate-synopsis`,
`generate-weather`) return **503 + `retry_after_seconds`** (never a
blocking wait) when rate-limited — see AI Extraction's fail-fast
section; this is the single most important behavior to preserve if
touching any of them.

Non-API pages (`config/urls.py`): `/` (main page), `/settings/`
(signatories), `/history/` (Batch History), `/accounts/...` (Django's
built-in auth views), `/admin/`.

## The Batch Cycle

A day splits into two 12-hour windows, bounded by **6:00 AM and
6:00 PM** — PM (daytime, 6AM–6PM) is dated today and released as the
"1800H" report; AM (overnight, 6PM–6AM) is dated whichever calendar day
its "0600H" report gets released on, so the date rolls forward at 6PM
and stays put across midnight. `current_batch`'s no-param path computes
this via `_current_batch_slot()` against `timezone.localtime(now())`,
independent of the main system's own period logic. `get_or_create` never
touches an existing batch's status, so a `FINALIZED` batch can't be
silently reopened by someone loading the page later in the same window.

**Fixed 2026-08-25 (client alpha-test bug #1):** this used to check
`hour < 12` (a leftover noon/midnight split, same shape as the main
system's own `_get_or_create_current_period()`), which left a
`FINALIZED` AM batch showing as "current" for a full 6 hours after
6:00 AM — blocking all new submissions for the whole office during that
gap, since it looked locked with nowhere to submit into. Not a
UTC-vs-local bug (the caller already converts via `timezone.localtime()`
first) — just the wrong boundary constant. See `_current_batch_slot()`
in `views.py` and its tests in `tests.py` for the corrected 6/18
boundary and the date-rollover logic for the overnight window.

**Batch History** (`/history/`) lists every batch ever created, most
recent first, regardless of whether its window has closed, with an
**"Amended" badge** on any batch whose `amended_at` is set. A `DRAFT`
row (or an amended, currently-unlocked `FINALIZED` row) links back into
the normal entry-editing view via `?batch_id=`; a locked `FINALIZED` row
links straight to `/api/batches/<id>/download/`. This is deliberate: OPS
must be able to finish, amend, or re-download a past batch that was
missed in its own window.

## PDF Generation (`apps/quickentry/pdf.py`)

`generate_batch_pdf(batch)` is always called fresh, on every download
request — there is **no stored PDF file**. `ManualBatch.generated_pdf`
existed briefly and was removed once the design settled on
regenerate-on-demand. The PDF is rebuilt purely from:
- the batch's own frozen fields (`synopsis`, `weather_conditions`,
  `actions_taken`, `prepared_by`, `noted_by`, `noted_by_title`,
  `approved_by`, `approved_by_title` — all set once, at
  `finalize_batch()`, never re-derived from the live
  `SitRepSignatoryConfig` afterward), and
- the batch's saved entries/child records, read straight from the DB —
  `build_municipality_sections()`/`build_lifelines_sections()` **group
  by municipality**, combining every entry that municipality has in the
  batch into one section (lifelines: the *latest* entry's snapshot wins,
  since statuses don't meaningfully "combine" the way incident lists do).
- an **amendment note** rendered when `batch.amended_at` is set
  ("This report was amended on … Reason: …") — a permanent marker, not
  cleared by a later re-finalize.

This means the same batch produces a byte-for-byte reproducible PDF no
matter how many times or how long after finalizing it's downloaded, and
editing `/settings/` later never retroactively changes an
already-finalized document. It also means the tool has **no dependency
on persistent disk** between requests — required on Render's free tier,
whose disk is ephemeral across deploys/restarts.

`compute_summary()` in the same file is the single source of truth for
all incident/casualty counts — used by both the PDF's Section III and
the `/api/current-batch/` summary cards on the main page, so there is
only one counting implementation, not two that could drift apart.

## AI Extraction (`apps/quickentry/ai.py`)

- Groq only (`openai/gpt-oss-120b`), called directly via `requests` — no
  Groq SDK. Note: `llama-3.3-70b-versatile` (the model the main system's
  `ai.py` currently hardcodes) is confirmed retired from Groq's catalog —
  404s on `GET /openai/v1/models`. Worth fixing there too when that gets
  picked back up.
- JSON mode (`response_format: {"type": "json_object"}`), `temperature`
  0.1, `max_tokens` **2500** (see `MAX_TOKENS`'s own comment in `ai.py`
  for the full derivation — reduced from 4096 on 2026-08-27), one large
  system prompt with label aliases, extraction rules, and few-shot
  examples (road crash x2, fire incident, water incident/weather).
  Separate, shorter prose prompts (not JSON mode) power
  `generate_synopsis()`/`generate_weather_summary()`.
  **The system prompt's own real measured baseline is ~3945-3990
  `prompt_tokens`** (Groq's own `usage` field on a real call, not a
  chars/4 guess — confirmed across 6 diverse real reports; barely moves
  with report length, since the fixed system prompt dominates it). This
  is notably higher than an earlier ~2804-token figure once documented
  here — that was stale, predating several real-bug-driven prompt
  additions made since (the `RESPONDING TEAM`/`RESPONDERS` alias fix, the
  broadened `unmapped_notes` rule, `weather_condition`'s anti-inference
  rule, the injury-classification group rule). Re-measure before trusting
  either number if the prompt changes again — see "Every Groq-side
  failure becomes ExtractionError" below for why this number matters
  beyond documentation accuracy.
- **`RESPONDING TEAM` vs. `RESPONDERS` are NOT aliases of each other** —
  a real report often states both (the team's name, then who was on it).
  The system prompt calls this out explicitly after a real extraction
  bug conflated them.
- **`unmapped_notes` rule is intentionally broad**: it must catch not
  just uncertain content, but content the model is fully confident about
  that simply has no matching schema field (e.g. a land-travel line when
  only `sea_travel` exists) — "no matching field" and "nothing to
  report" are different reasons to end up in `unmapped_notes`, and both
  must land there rather than being silently dropped.
- **`weather_condition`**: only an explicit stated observation, never
  inferred from incident context (a flood report does not imply "it was
  raining"). Captured per-entry, has its own model column (survives
  re-opening an entry for edits), and feeds `generate_weather_summary()`
  across the whole batch.
- **Schema-driven required-field validation**: `get_incident_schema()`
  in `serializers.py` introspects the real DRF serializers
  (`field.required`, excluding admin/injected fields like `id`/`entry`/
  `created_at`) and embeds the result into `main.html` via
  `json_script`. The frontend's blocking-validation UI reads this at
  page load. **This only governs required-ness, not field existence** —
  `main.js`'s `INCIDENT_TYPES.*.fields` lists (which fields even render
  per incident type) are still hardcoded and must be updated by hand
  when a model/serializer field is added or removed (see Known Gaps).
- **A required `<select>` (e.g. victim `injury_classification`) must
  never default a genuinely-unset AI value to whichever option is
  first in the list.** Fixed 2026-08-25 (client alpha-test bug #2,
  reported as "AI downgrades Major injuries to Minor"): the actual root
  cause was NOT the AI — a real report reproduced live confirmed the
  model correctly leaves `injury_classification` null when a report
  states one aggregate severity for several listed victims instead of
  per-victim (and says so in `unmapped_notes`). The bug was
  `normalizeIncidentItem()` in `main.js` silently coercing that null to
  `"MINOR"` — the *least* severe option — before the required-field
  check ever saw it, so a real Major/Fatality injury could reach a
  saved record looking like an actively-chosen "Minor". Fixed by
  leaving it `""` (so `isBlank()`/the blocking-validation UI correctly
  flags it) and giving `renderField()`'s `<select>` case a genuine blank
  placeholder option so an unset value never visually looks like the
  first real option was chosen. The system prompt also gained an
  explicit rule: when a report states one classification for a group of
  victims, apply it to all of them (reading a stated value, not
  guessing one) rather than leaving it null and creating manual work.

### Fail-fast rate limiting (the most load-bearing design decision in this file)

Render's free tier runs **`WEB_CONCURRENCY=1`** — exactly one worker
process handles every request for every user of the live app. Any
blocking `time.sleep()` inside a request handler doesn't just delay the
one caller — it makes the **entire app unresponsive to everyone** for
that duration. This was proven the hard way: a real incident during
testing had a request block for roughly **56 minutes** because the old
retry loop slept out an unusually large `Retry-After` with no ceiling.

The fix, and the rule to preserve going forward: **nothing in the
request-handling path may sleep to wait out a rate limit.** Instead:
- Every real Groq response is inspected for `x-ratelimit-remaining-tokens`
  / `x-ratelimit-reset-tokens` (Go-duration format — e.g. `"45.989s"`,
  `"1m26.4s"` — parsed by `_parse_go_duration`, confirmed against real
  responses, not assumed) and logged unconditionally via
  `apps.quickentry`'s logger (see `settings.py`'s `LOGGING` — without
  this handler, that data silently vanishes below WARNING, which is
  exactly what made the 56-minute incident hard to diagnose the first
  time).
- If remaining tokens drop below `MAX_TOKENS` (2500), the next call for
  that key is refused immediately with `RateLimitedError` — never
  attempted, never slept out.
- A live 429 also raises `RateLimitedError` immediately (after recording
  the new cooldown) — no retry-with-backoff for a 429, ever. The only
  retry that still exists (`MAX_NETWORK_ATTEMPTS = 2`, ~1s backoff) is
  for a genuine network blip (dropped connection/timeout), which
  resolves in well under a second and is a fundamentally different kind
  of failure than a rate limit.
- Every AI-backed view (`extract_entry`, `generate_synopsis_view`,
  `generate_weather_view`) catches `RateLimitedError` and returns
  **503 + `retry_after_seconds`** — a fast response, not a hang.
- The frontend absorbs that 503 into the existing cooldown UI
  (`createCooldownController`, `main.js`): shows a live countdown,
  re-checks `/api/ai-status/` when it elapses, and **auto-retries the
  original request exactly once** (`allowAutoRetry` flag) — a second
  503 in a row just shows the new countdown normally rather than
  chaining further automatic retries. `GET /api/ai-status/` itself is
  cheap (reads in-memory state only, never calls Groq) so the frontend
  can disable AI-triggering buttons *before* OPS clicks into a call that
  would just get refused.

### Every Groq-side failure becomes ExtractionError — never a raw HTTPError

Fixed 2026-08-25 (client alpha-test bug #4, reported as "500 error on
long/decorated input"). `_attempt_groq_call()` used to call
`resp.raise_for_status()` unguarded for anything that wasn't a 429 —
that raises a bare `requests.exceptions.HTTPError`, which none of the
views catch (`extract_entry`/`generate_synopsis_view`/
`generate_weather_view` only catch `RateLimitedError`/`ExtractionError`),
so it reached Django uncaught as an opaque 500 with no usable message.
Reproduced live against Groq while diagnosing this — now `_attempt_groq_call`
raises `ExtractionError` (a clean 502, `detail` intact) for **any**
non-2xx/non-429 response, whatever the status code.

That real reproduction also explained the client's actual root cause,
which turned out to be neither pure length nor pure Unicode but both at
once: a report pasted from a "fancy text" generator (bold-via-Unicode,
e.g. YayText) pushed a request's estimated cost over Groq's per-minute
token (TPM) budget — confirmed via the real response body: `"Request
too large ... TPM: Limit 8000, Requested 8073 ... code:
rate_limit_exceeded"`. Groq reports **this specific case as 413, not
429** — `_is_groq_rate_limit_413()` recognizes it via that body code
(never by status code alone, so a genuinely different 413 still becomes
a hard `ExtractionError`) and routes it through the same
`RateLimitedError`/cooldown path as an ordinary 429, since it carries a
real `Retry-After` header and recovers the same way once the per-minute
window rolls over.

The actual fix for the trigger itself: `_normalize_decorative_unicode()`
runs NFKC (Unicode compatibility normalization) on `raw_text` before it
reaches Groq, in `extract_incident_data` only — never touching the
saved `ManualEntry.raw_text` accountability record. This folds
"fancy text" letter styling (Mathematical Alphanumeric Symbols) back to
plain ASCII automatically — confirmed against a real saved report
(`"𝙈𝘿𝙍𝙍𝙈𝙊..."` → `"MDRRMO..."`) — which is exactly the client's own
manual workaround (stripping the styling), now automatic. Genuine
content (emoji, accented characters, other scripts) has no such
compatibility mapping and passes through unchanged. This also directly
addresses the length side of the symptom: decorative Unicode code
points often cost several tokens each under the model's tokenizer
versus one for the plain letter they represent, so normalizing reduces
token cost too, not just visual styling.

### `MAX_TOKENS` reduced 4096 → 2500 (2026-08-27) — a different root cause than bug #4

Found via a real report with **zero decorative Unicode** (a genuinely
plain, moderate-length Lucban MDRRMO report) that 413'd identically on
every retry — confirmed via real data that this is a *deterministic*
per-request overage, not contention: the 413 arrived with
`x-ratelimit-remaining-tokens: '8000'` (the full budget, freshly reset,
nothing else competing for it) and still rejected the request. Bug #4's
fix (NFKC normalization) doesn't touch this case at all, since there's
no decorative Unicode to fold.

Derived the exact relationship Groq's TPM pre-check uses from two real
413 bodies: **`Requested = prompt_tokens + max_tokens`**, exactly (not
an estimate — confirmed to the token: `4442 + 4096 = 8538` and
`3953 + 4096 = 8049`, both exact matches against Groq's own reported
`Requested` figure). Combined with the system prompt's real ~3945-3990
`prompt_tokens` baseline (see AI Extraction above), the *old*
`MAX_TOKENS = 4096` meant fixed overhead alone (~3945 + 4096 ≈ 8041) was
already at or past the 8000 limit before counting a single character of
user input — this was never really "some reports are too long," it was
the app's own fixed cost nearly saturating the budget by itself.

Fix: measured real `completion_tokens` (Groq's own `usage` field, not
estimated) across 6 diverse real reports — single/multi-incident, a
fatality, weather-only, the long Lucban one — ranging 583-1873, max
1873. `MAX_TOKENS` is now **2500**, ~33% above that observed max. See
`MAX_TOKENS`'s own comment in `ai.py` for the full numbers — **don't
raise it back up without re-measuring real `completion_tokens` first**;
per the `Requested` formula above, every token added here directly
shrinks how much real input headroom reports have under the 8000 ceiling.

### Fallback key (`GROQ_API_KEY_FALLBACK`, optional)

Groq's free tier can impose an **account-level** cooldown (observed once
at ~54 minutes) on top of the ordinary per-minute token budget, after
enough sustained real usage in one day. A single key has no way to route
around that — it needs a genuinely **separate Groq account**, not just
another key on the same account, since the cooldown is account-scoped.

`_post_with_retry()` tries `primary` then `fallback` (if configured), in
order; a key already known to be cooling down is skipped without even
being called. Cooldown tracking (`_rate_limit_state`) is keyed by
`"primary"`/`"fallback"` rather than global, since the two accounts are
independent. If both are unavailable, the raised `RateLimitedError`
carries the **shorter** of the two remaining waits, so a retry has the
best chance of succeeding. A non-rate-limit failure on one key (e.g. a
misconfigured/invalid fallback key returning 401) is caught rather than
left to crash the request — confirmed necessary by testing with a
real-but-then-invalid fallback key, which without this would turn
"primary is rate-limited, backup didn't help either" into a raw 500
instead of the same clean 503 OPS already knows how to wait out.
Entirely optional — leave `GROQ_API_KEY_FALLBACK` blank and behavior is
identical to having no fallback system at all.

## Deployment

**Live now** at `https://quick-sitrep.onrender.com`, on Render's free
web-service tier, backed by a **Neon Postgres** database. This is no
longer aspirational — production `ManualBatch` rows exist with real
dates and `FINALIZED` status (verified directly against the production
DB), meaning the client is actively using this day-to-day.

- **`WEB_CONCURRENCY=1`** (Render free tier) is the reason the entire
  fail-fast rate-limit design above exists — see that section for the
  full "why."
- Render's disk is **ephemeral** across deploys/restarts — this is why
  PDF generation was designed to always regenerate on demand from DB
  fields rather than depend on a stored file (see PDF Generation above).
- `build.sh` (`pip install`, `collectstatic`, `migrate`) is Render's
  build command, committed in-repo. The **start command and every
  environment variable** (`DATABASE_URL` pointing at Neon, `GROQ_API_KEY`,
  `GROQ_API_KEY_FALLBACK`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`,
  `DJANGO_DEBUG=False`) are set directly in Render's dashboard, **not**
  committed anywhere in this repo (no `render.yaml`/`Procfile`) — check
  Render's dashboard, not this file, if you need the exact current
  values.
- WhiteNoise (`CompressedManifestStaticFilesStorage`) serves static
  files directly from the Django process — no separate CDN/static host.
- `settings.py`'s `DATABASE_URL`-or-SQLite-fallback logic is what makes
  local dev (no `DATABASE_URL` set → SQLite) and production (Render
  injects `DATABASE_URL` → Postgres) work from the same settings file
  with zero branching by environment name.

## Production Operations

This app has **no per-user data scoping** — every logged-in account sees
the exact same shared batches/entries (by design, per the spec's Auth
note: single shared login, no role system). That means:
- **Never leave test/dummy data in the production database.** Anything
  saved there is immediately visible to the client, not sandboxed per
  user.
- Prefer local SQLite (or a scratch batch you delete before finishing)
  for any exploratory/test work. If production must be touched for a
  real reason, be deliberate and minimal.

**Running a one-off command against the live database**, when asked to:
1. Read `PRODUCTION_DATABASE_URL` directly from `backend/.env` (a
   **local-only convention**, not referenced anywhere in application
   code — distinct from `DATABASE_URL`, which is what `settings.py`
   actually reads, and which Render sets itself in its own dashboard for
   the deployed app).
2. Scope it to `DATABASE_URL` for **that one subprocess only** — e.g.
   `DATABASE_URL="$DB_URL" venv/Scripts/python.exe manage.py shell -c "..."`
   in the same shell command. Never `export` it into the persistent
   shell session, never write it to a file, never print/echo the value
   itself anywhere (including in a chat reply, even if asked to confirm
   what it is) — only confirm *which database* (production vs. local)
   an action targets.
3. State clearly, before running anything that **modifies** data, which
   database it targets and what the command will do. A read-only listing
   doesn't strictly require this but doing it anyway is good practice.
4. Destructive/bulk-write commands against production may be blocked
   outright by the Claude Code auto-mode safety classifier regardless of
   user confirmation in chat — if that happens, stop and let the user
   choose how to proceed (adjust permissions, switch modes, run it
   themselves, or split the request into smaller pieces) rather than
   working around the denial.

**The client's account** — username `Quezon_PDRRMO_EOC` — was created
directly in the production database via `User.objects.create_superuser()`
in a `manage.py shell` one-liner (the interactive `createsuperuser`
prompt doesn't work over a non-interactive/headless command), following
the pattern above. A leftover `ops_test` superuser account from earlier
development testing is **still present in production as of this
writing** — flagged in Known Gaps below; not removed without explicit
instruction, since deleting user accounts wasn't itself the ask.

## Known Gaps / Not Yet Done

- **No unsaved-changes warning** — navigating away from an in-progress
  edit with unsaved changes doesn't prompt; nothing in `main.js` hooks
  `beforeunload`.
- **`INCIDENT_TYPES.*.fields` in `main.js` is hardcoded, not
  schema-driven** — `get_incident_schema()` only tells the frontend
  which fields are *required*, not which fields *exist* per incident
  type. Adding/removing a model field means updating both the
  serializer and this JS list by hand; they can silently drift.
- **`parseRoughDatetime()` only recognizes specific date/time formats**
  — as of 2026-08-25 (client alpha-test bugs #3/#5) that's: pipe
  (`"February 11, 2026 | 0900H"`), comma (`"February 19, 2026, 0600H"`),
  slash (`"Aug. 25, 2026/1016H"`), military-time-first
  (`"2306H August 24, 2026"`), and 12-hour-time-first
  (`"12:00am Aug 25 2026"`), plus abbreviated months with or without a
  trailing period (`"Aug"`/`"Aug."`). Anything else — notably free-form
  prose like `"August 25 around 1:15am"` (seen in a real Polillo report)
  — still fails to parse and leaves the date/time picker blocked rather
  than guessing. This is deliberate, not an oversight: the AI's raw
  `datetime` string is never auto-committed to a `DateTimeField`. OPS
  must always see a real date/time picker and confirm or correct the
  value before save, per the spec's "never silently guess" rule. When
  adding a new format, extend `parseRoughDatetime` additively (add
  another `splitDateTime_*` helper) rather than replacing the existing
  patterns — reports in the wild mix formats, sometimes within the same
  batch.
  **What alpha-test bug #3 ("rigid input-pattern validation rejects
  valid data") actually was**, diagnosed against the real Calauag/
  Pagbilao production entries rather than assumed: there is no separate
  hard validator anywhere in this codebase. It was two unrelated things
  wearing the same symptom: (a) the datetime-format gap above — the AI
  read the raw string correctly (shown via the "AI read: ..." hint) but
  the picker couldn't parse it, now fixed for those two reports' exact
  formats; and (b) the schema-driven required-field UI correctly
  blocking on `cause`/`responding_team` when the SOURCE report simply
  never stated one (Calauag's report has no cause line at all; Pagbilao
  names individual responders but no team name) — working as designed,
  not a bug, though worth a future UX pass making that distinction
  clearer to OPS than a plain red asterisk (e.g. "not stated in the
  source report").
- **Leftover `ops_test` superuser account in the production database**
  — see Production Operations. Cleanup candidate, not yet removed.
- **Migration path into the main system (spec Section 8) has not been
  started.** Intentional — not urgent; this tool exists precisely so OPS
  doesn't have to wait on that.
- Main system's other known issues (dead Groq model in its own `ai.py`,
  fragile PAGASA scraper, etc.) are out of scope here and untouched.

## Testing Approach (No Browser Tool Available)

This tool was built and verified in an environment with no browser
automation available, using techniques worth continuing rather than
reinventing:

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
   confirming `x-ratelimit-reset-tokens`'s Go-duration string format,
   that Groq's free tier really does trip its per-minute budget after
   just two back-to-back calls with this system prompt, and that a
   genuinely separate account's key is unaffected by the primary
   account's cooldown. Docs and reality drift; confirm against a live
   call before coding to an assumption.
4. When diagnosing something that looks like a hang or an unexplained
   delay, **check real server logs for the actual blocking call before
   assuming which function is at fault** — the ~56-minute incident above
   was root-caused this way to an uncapped `time.sleep()` in a retry
   loop, not the Go-duration parser that seemed like the more likely
   suspect at first glance.

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
`REST_FRAMEWORK` comment). **Do this against local SQLite, never
production** — see Production Operations above.

`.env` keys (dev): `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`,
`DJANGO_ALLOWED_HOSTS`, `GROQ_API_KEY` (extraction won't work without
it — `ExtractionError` is raised immediately if unset). `DATABASE_URL`
left blank locally (falls back to SQLite). `GROQ_API_KEY_FALLBACK` is
optional (see AI Extraction). `GEMINI_API_KEY` / `OPENAI_API_KEY` are
read into settings but currently unused (no multi-provider fallback
chain implemented — only the two-Groq-key fallback described above).
`PRODUCTION_DATABASE_URL` is a separate, locally-added convenience key
for one-off production commands (see Production Operations) — it is
**not** read by `settings.py` and has no effect on local dev.

## Notes for Claude Code

- This is a small, single-office internal tool that is now **in real
  production use** — match its existing simplicity (plain Django
  templates, vanilla JS, no build tooling, no role system) rather than
  introducing new architectural layers, but treat correctness and the
  fail-fast rate-limit discipline with production-grade care.
- Field names in the incident models must keep mirroring
  `apps/ops/models.py` in the main `PDRRMO_v3` repo — check there before
  changing or adding a field here, since drift defeats the point of the
  future migration path. `FireIncident` is the one confirmed, deliberate
  exception (see Key Models) — don't treat it as precedent for other
  fields without the same explicit client sign-off.
- **Never add a blocking sleep to any request-handling code path.**
  `WEB_CONCURRENCY=1` on Render means it stalls the entire app for every
  user, not just one — see AI Extraction's fail-fast section for the
  incident that established this rule.
- Any command against the production database follows the
  Production Operations protocol above — scoped `DATABASE_URL`, never
  saved or echoed, confirm target DB before a write.
- Ask before large refactors, same as before.
