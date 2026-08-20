"""
Quick SitRep — AI extraction.

extract_incident_data(municipality_name, raw_text) -> dict

Single-provider MVP (Groq only, called directly via `requests` — no groq
SDK dependency for one provider). See docs/quick-report-entry-spec.md
Section 5. Fallback to other providers can be added later the same way
the main system's apps/ops/ai.py does it, if Groq reliability becomes an
issue for this tool too.
"""

import json
import logging
import re
import threading
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

GROQ_CHAT_URL = "https://api.groq.com/openai/v1/chat/completions"
# NOTE: llama-3.3-70b-versatile (used by the main system's apps/ops/ai.py)
# has been retired from Groq's catalog as of this writing — confirmed via
# GET /openai/v1/models, which 404s for that model id. Using a current
# model instead; re-check Groq's model list if this ever starts 404ing.
GROQ_MODEL = "openai/gpt-oss-120b"

# Groq's free tier has a per-minute token budget (hit directly during
# Step 3 testing — two calls back-to-back with this system prompt was
# enough to trip it). A 429 from that is NOT retried in-request anymore
# (see RateLimitedError below) — these two constants now cover only a
# genuine network blip (a dropped connection, a timeout), which usually
# resolves in well under a second, so a short bounded retry is safe and
# doesn't risk blocking the request for anything close to real rate-
# limit durations.
MAX_NETWORK_ATTEMPTS = 2
NETWORK_RETRY_BACKOFF_SECONDS = 1

# Hard cap on completion tokens per call — also doubles as the proactive
# rate-limit safety margin below (it's the single largest, most variable
# cost component of a call; the system prompt itself is fixed and
# comparatively small — confirmed at ~2804 prompt tokens via Groq's own
# `usage` field on a real call, not a chars/4 guess, which undercounts
# for this JSON-schema-heavy prompt).
MAX_TOKENS = 4096


class ExtractionError(Exception):
    """Raised when the Groq call fails or its response isn't valid JSON."""


class RateLimitedError(ExtractionError):
    """
    Raised instead of sleeping when a call is rate-limited — either
    proactively (a cooldown recorded from an earlier response is still
    active) or reactively (Groq itself returns 429 on this call). Never
    retried in-request either way; carries retry_after_seconds so the
    caller (views.py) can turn this into a fast response telling the
    client exactly how long to wait, rather than the request blocking on
    a server-side sleep.

    This matters beyond a nicety: Render's free tier runs
    WEB_CONCURRENCY=1, so any in-request sleep — even the old capped 90s
    one — makes the ENTIRE app unresponsive to every user for that
    duration, not just whoever triggered it. A real incident during
    testing showed this could run to ~56 minutes when Retry-After itself
    was unusually large, since the old exponential-backoff retry loop
    had no ceiling on that path at all. The frontend already has the UI
    for this (createCooldownController(), /api/ai-status/) — it does
    the actual waiting/retrying now, not the server.
    """

    def __init__(self, retry_after_seconds):
        self.retry_after_seconds = max(0.0, retry_after_seconds)
        super().__init__(
            f"The AI service is rate-limited; retry in about "
            f"{self.retry_after_seconds:.0f}s."
        )


SYSTEM_PROMPT = """You are extracting structured incident data from a Philippine LGU disaster
response report (Quezon Province PDRRMO). Reports typically follow a
semi-structured WHAT/WHEN/WHERE convention, but field labels vary between
reports even for the same meaning. Your job: normalize this into the JSON
schema below. The municipality is provided separately — never include or
guess it.

LABEL ALIASES (same meaning, different wording seen in real reports):
- Victims: "PERSONS INVOLVED", "PATIENT DETAILS", "VICTIM", "PATIENT",
  "RIDER"/"BACKRIDER" (lettered A/B lists are multiple victims)
- Responding team NAME (e.g. "TEAM ALPHA", "TEAM ZULU") → responding_team:
  "RESPONDING TEAM", "RESPONDING UNIT"
- Individual responder NAMES (e.g. "R. Racoma, T. Dayo, R. Villa") →
  ert_members: "RESPONDERS", "ERT"
- These are NOT aliases of each other, even though "RESPONDERS" and
  "RESPONDING TEAM/UNIT" sound similar — a report very often states BOTH
  as two separate pieces of information (the team's name, then who was
  on it), e.g. "RESPONDING TEAM: TEAM ZULU | DRIVER: S. Calma |
  RESPONDERS: R. Racoma, T. Dayo, R. Villa" has a team name AND a
  personnel list, not one repeated twice. Map each label to its own
  field; never let populating one cause the other to be dropped or left
  in unmapped_notes.
- Illness reason: "CAUSE OF ILLNESS", "NATURE OF ILLNESS", "CHIEF COMPLAINT"
- Vitals (BP/PR/SpO2/Temp) are often embedded inline inside ACTIONS TAKEN
  text in parentheses, not as separate labeled lines — pull them out into
  their own fields when you see them there.

RULES:
- Empty arrays are valid and common — "no incidents" is a normal result.
- Never guess a value you're not confident about. Prefer null/omission and
  put the uncertain fragment into unmapped_notes instead.
- "No matching schema field" is NOT the same as "nothing to report."
  These are two separate reasons content can fail to land in a
  structured field, and BOTH must still put the content in
  unmapped_notes:
    (a) you're uncertain what a fragment means, or
    (b) you're fully confident what it means, but there is genuinely no
        field for it (e.g. a line about land-travel schedules when
        there's a sea_travel field but no land-travel field — don't
        force it into sea_travel just because it's the closest-sounding
        field, but don't silently drop it either).
  Only skip a line producing NOTHING at all — not even an unmapped_notes
  mention — when it is genuinely uninformative boilerplate that carries
  no situational content: contact numbers, hotline lines, signature
  blocks, letterhead/office titles. When you're unsure whether something
  clears that bar, note it — a silently dropped real detail is a worse
  failure than an over-cautious note.
- Never compute summary counts or totals — that's handled downstream.
- If a per-person or per-item qualitative detail (symptom, condition,
  notable circumstance) can't be preserved because the target field only
  holds an aggregate count or a bare classification, note the specific
  detail in unmapped_notes even though the count/classification itself was
  confident. Never let a real detail exist only inside a coarse number.
- weather_condition is ONLY for an explicit weather observation line
  (e.g. "WEATHER CONDITION: Sunny skies") — never infer it from incident
  context. A flooding or water rescue report does NOT imply "it must
  have been raining"; that's a guess about the cause of an incident, not
  a report of what was actually observed and stated. Leave it "" if the
  report never states a weather observation.

JSON SCHEMA:
{
  "weather_condition": "string (this entry's own stated weather observation, or empty string if none given)",
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
      "cause": "string",
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

EXAMPLES:

---
INPUT:
WHAT: Road Crash (Collision)
WHEN: February 10, 2026 | 1400H
WHERE: Brgy. Malabo, Sample Town, Quezon
VEHICLE INVOLVED: 1 Motorcycle
PATIENT DETAILS: Male, 40 years old, resident of Brgy. Malabo
INJURIES: Lacerated wound on left knee; abrasion on left arm
ACTIONS TAKEN: Ensured scene safety, assessed patient and provided first aid, and transported to Sample District Hospital
RESPONDING UNIT: Team Bravo | AMBULANCE: Rescue 102 | DRIVER: J. Cruz | ERT: M. Santos, L. Reyes

OUTPUT:
{
  "weather_condition": "",
  "lifelines_status": {},
  "road_crashes": [{
    "datetime": "February 10, 2026 | 1400H",
    "location": "Brgy. Malabo, Sample Town, Quezon", "barangay": "Malabo",
    "cause": null, "vehicles_involved": "1 Motorcycle",
    "actions_taken": "Ensured scene safety, assessed patient and provided first aid, and transported to Sample District Hospital",
    "responding_team": "Team Bravo", "driver": "J. Cruz", "ert_members": "M. Santos, L. Reyes",
    "victims": [{ "age": 40, "sex": "Male", "address": "Brgy. Malabo",
      "injuries": "Lacerated wound on left knee; abrasion on left arm",
      "injury_classification": "MINOR" }]
  }],
  "medical_assistance": [], "fire_incidents": [], "water_incidents": [], "trauma_emergencies": [],
  "unmapped_notes": "Ambulance unit noted as 'Rescue 102' — no matching field in schema."
}
---
INPUT:
WHAT: Road Crash
WHEN: February 11, 2026 | 0900H
WHERE: Brgy. Riverside, Sample City (near the public market)
CAUSE OF INCIDENT: Brake failure
VEHICLES INVOLVED: 1 tricycle and 1 jeepney
PERSONS INVOLVED:
A. Male, 60 years old — Suspected fracture of left leg
B. Female, 15 years old — Minor abrasions on both knees
RESPONDERS: Sample City DRRMO Team Alpha, Brgy. Officials of Riverside, PNP Sample City
ACTIONS TAKEN: Coordinated with BFP and PNP upon receiving the report; ensured scene safety; provided pre-hospital medical treatment; monitored vital signs; transported Victim A to Sample General Hospital and endorsed Victim B to on-scene medics.

OUTPUT:
{
  "weather_condition": "",
  "lifelines_status": {},
  "road_crashes": [{
    "datetime": "February 11, 2026 | 0900H",
    "location": "Brgy. Riverside, Sample City (near the public market)", "barangay": "Riverside",
    "cause": "Brake failure", "vehicles_involved": "1 tricycle and 1 jeepney",
    "actions_taken": "Coordinated with BFP and PNP upon receiving the report; ensured scene safety; provided pre-hospital medical treatment; monitored vital signs; transported Victim A to Sample General Hospital and endorsed Victim B to on-scene medics.",
    "responding_team": "Sample City DRRMO Team Alpha, Brgy. Officials of Riverside, PNP Sample City",
    "driver": null, "ert_members": null,
    "victims": [
      { "age": 60, "sex": "Male", "address": null, "injuries": "Suspected fracture of left leg", "injury_classification": "MAJOR" },
      { "age": 15, "sex": "Female", "address": null, "injuries": "Minor abrasions on both knees", "injury_classification": "MINOR" }
    ]
  }],
  "medical_assistance": [], "fire_incidents": [], "water_incidents": [], "trauma_emergencies": [],
  "unmapped_notes": ""
}
---
INPUT:
WHAT: Residential Fire
WHEN: 15 February 2026 | 1000H
WHERE: Sample Homes, Brgy. Malinis, Sample City
CAUSE OF FIRE: Electrical Short Circuit
RESPONSE TIME: 4 minutes
DISTANCE: 2.0 km
TYPE OF STRUCTURE: Single-Storey Residential
NO. OF FAMILIES AFFECTED: 1
NO. OF INDIVIDUALS AFFECTED: 3
RESPONDING TEAM: Sample City BFP
ACTIONS TAKEN: Fire suppressed, area secured, no casualties reported

OUTPUT:
{
  "weather_condition": "",
  "lifelines_status": {},
  "road_crashes": [], "medical_assistance": [],
  "fire_incidents": [{
    "datetime": "15 February 2026 | 1000H", "location": "Sample Homes, Brgy. Malinis, Sample City", "barangay": "Malinis",
    "cause": "Electrical Short Circuit",
    "response_time_minutes": 4, "distance_km": 2.0, "structure_type": "Single-Storey Residential",
    "families_affected": 1, "individuals_affected": 3, "structures_burned": 0, "fire_area_sqm": 0,
    "casualties": 0, "injured": 0, "fatalities": 0, "responding_team": "Sample City BFP",
    "actions_taken": "Fire suppressed, area secured, no casualties reported"
  }],
  "water_incidents": [], "trauma_emergencies": [],
  "unmapped_notes": ""
}
---
INPUT:
WEATHER CONDITION: Partly cloudy, no rain at time of incident
WHAT: Water Rescue
WHEN: February 8, 2026 | 1015H
WHERE: Brgy. Look, Sample Town (fishpond area)
DESCRIPTION: Two fishermen capsized when their boat overturned near the fishpond
PERSONS INVOLVED:
A. Male, 45 years old — Rescued, mild hypothermia, stable
B. Male, 50 years old — Rescued, no visible injuries
RESPONDERS: Sample Town DRRMO Water Rescue Team, Barangay Look tanod
ACTIONS TAKEN: Deployed rescue boat and flotation devices, retrieved both individuals, provided first aid, monitored for delayed symptoms, released to family

OUTPUT:
{
  "weather_condition": "Partly cloudy, no rain at time of incident",
  "lifelines_status": {},
  "road_crashes": [], "medical_assistance": [], "fire_incidents": [],
  "water_incidents": [{
    "datetime": "February 8, 2026 | 1015H",
    "location": "Brgy. Look, Sample Town (fishpond area)", "barangay": "Look",
    "incident_type": "Water Rescue",
    "description": "Two fishermen capsized when their boat overturned near the fishpond",
    "families_affected": 0, "individuals_affected": 2,
    "casualties": 0, "injured": 1, "fatalities": 0,
    "responding_team": "Sample Town DRRMO Water Rescue Team, Barangay Look tanod",
    "actions_taken": "Deployed rescue boat and flotation devices, retrieved both individuals, provided first aid, monitored for delayed symptoms, released to family"
  }],
  "trauma_emergencies": [],
  "unmapped_notes": "Victim A (Male, 45) had mild hypothermia; Victim B (Male, 50) had no visible injuries — water_incidents has no per-victim breakdown field, so these individual details are preserved here rather than lost inside the aggregate injured count."
}
---

Return ONLY the JSON object. No markdown code fences, no commentary before or after."""


# Short prose paragraph, not JSON — this is a DRAFT for OPS to edit before
# it goes into an official report, not a final statement. Deliberately
# lower than MAX_TOKENS; a synopsis is a short paragraph, not a
# schema-shaped extraction.
SYNOPSIS_MAX_TOKENS = 600

SYNOPSIS_SYSTEM_PROMPT = """You are drafting a short narrative synopsis paragraph for a Philippine LGU
disaster response situational report (Quezon Province PDRRMO). You will be
given already-computed incident counts and per-municipality incident
detail for one reporting period — never recompute those counts yourself,
only use the ones given to you.

This is a DRAFT for a human OPS officer to review and edit before it goes
into an official report — write plainly and concisely, not floridly.

RULES:
- Summarize only what is actually present in the data you were given:
  counts, municipalities involved, incident types, and any notable
  pattern (e.g. multiple incidents concentrated in one municipality or
  barangay, a cluster of the same incident type).
- Never invent or guess a detail, number, or cause that isn't present in
  the input. If the input has few or no notable incidents, say so plainly
  and briefly — do not pad or fabricate content to sound more substantial
  than the data supports.
- Do not include recommendations, next steps, or projected outlook — this
  is a factual overview of what was reported, nothing else.
- Plain prose, one short paragraph (two only if there's genuinely enough
  distinct material to warrant it). No headers, no bullet points, no
  markdown formatting.
- If there are no entries, or every count is zero, state plainly that no
  significant incidents were reported for the period — don't stretch that
  into a longer paragraph than the fact warrants.

Return ONLY the synopsis text. No preamble, no commentary, no quotation
marks around it."""


# Same reasoning as SYNOPSIS_MAX_TOKENS — a short prose summary, not a
# schema-shaped extraction.
WEATHER_SUMMARY_MAX_TOKENS = 300

WEATHER_SUMMARY_SYSTEM_PROMPT = """You are drafting a short synthesized weather summary for a Philippine LGU
disaster response situational report (Quezon Province PDRRMO), covering
one reporting period across Quezon Province. You will be given each
municipality's own reported weather observation for this period —
combine them into one short province-wide weather statement.

This is a DRAFT for a human OPS officer to review and edit before it
goes into an official report — write plainly and concisely, not
floridly.

RULES:
- Summarize only what is actually stated in the observations you were
  given. Never invent a condition, a municipality, or a detail (rainfall
  amount, wind speed, a weather system name) that isn't present in the
  input.
- If municipalities report different conditions, say so plainly (e.g.
  "mostly sunny across the province, with isolated rainshowers reported
  in X") rather than picking one and ignoring the rest, and rather than
  vaguely averaging them into something no municipality actually
  reported.
- If every municipality reports essentially the same condition, one
  plain sentence covering all of them is enough — don't pad it out
  restating the same thing per municipality.
- Do not include forecasts, warnings, or recommendations — this is a
  factual restatement of what was reported, nothing else.
- Plain prose, one short sentence or two. No headers, no bullet points,
  no markdown formatting.

Return ONLY the weather summary text. No preamble, no commentary, no
quotation marks around it."""


# ── Proactive rate-limit cooldown ───────────────────────────────────
# Reactive, not predictive: we never guess how expensive a report will be
# to process. Instead, every real Groq response tells us exactly how much
# token budget is left and exactly when it recovers (x-ratelimit-remaining-
# tokens / x-ratelimit-reset-tokens — confirmed against real responses,
# not assumed). If that looks thin, the *next* call is refused up front
# with RateLimitedError instead of firing and hoping — or, previously,
# instead of sleeping it out in-request. In-memory only — fine for a
# single Django process; Render's free tier runs exactly one
# (WEB_CONCURRENCY=1), which is precisely why nothing here may block it.
#
# TOKEN_COOLDOWN_THRESHOLD = MAX_TOKENS is empirically validated in this
# repo: a real call attempted with 3815 tokens remaining (< 4096) hit a
# 429; one attempted with 7917 remaining (> 4096) succeeded.
TOKEN_COOLDOWN_THRESHOLD = MAX_TOKENS

# Sanity ceiling on the STORED/reported cooldown — guards only against a
# garbled header producing a nonsensical duration (confirmed by testing:
# a malformed value can parse to e.g. 9999h). Not a "max time we'll
# block for" anymore, since nothing blocks on this value now; it's
# generous on purpose so a real, large Retry-After (observed once during
# testing at roughly 56 minutes) is still reported honestly rather than
# silently truncated into a number that would make OPS retry too early.
MAX_REPORTED_COOLDOWN_SECONDS = 3600

_rate_limit_lock = threading.Lock()
_rate_limit_state = {"available_at": 0.0}  # epoch seconds; 0.0 = no known cooldown

_GO_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)(h|ms|m|s)")


def _parse_go_duration(text):
    """
    Groq's x-ratelimit-reset-* headers use Go's time.Duration.String()
    format ("622ms", "45.989s", "1m26.4s" — confirmed against real
    responses). Returns seconds as a float, or None if it doesn't look
    like this format at all.
    """
    if not text:
        return None
    total = 0.0
    matched = False
    for value, unit in _GO_DURATION_RE.findall(text):
        matched = True
        value = float(value)
        if unit == "h":
            total += value * 3600
        elif unit == "m":
            total += value * 60
        elif unit == "ms":
            total += value / 1000
        else:  # "s"
            total += value
    return total if matched else None


def _parse_retry_after_seconds(response):
    """
    Groq's Retry-After header on a 429 (confirmed present on real 429s: a
    plain integer number of seconds). Returns None if absent/malformed so
    the caller can fall back to whatever seconds_until_available() has
    from x-ratelimit-reset-tokens instead.
    """
    retry_after = response.headers.get("Retry-After")
    if not retry_after:
        return None
    try:
        return float(retry_after)
    except ValueError:
        return None


def _record_rate_limit(response):
    """
    Called after every real Groq response (success or 429). Logs the raw
    headers unconditionally first — this exact data was the one thing
    missing when a real ~56-minute stuck request needed diagnosing, so it
    is captured every time now, not just when something looks thin.
    """
    remaining = response.headers.get("x-ratelimit-remaining-tokens")
    reset = response.headers.get("x-ratelimit-reset-tokens")
    retry_after = response.headers.get("Retry-After")
    logger.info(
        "Groq response %s — x-ratelimit-remaining-tokens=%r x-ratelimit-reset-tokens=%r Retry-After=%r",
        response.status_code, remaining, reset, retry_after,
    )

    if remaining is None or reset is None:
        return
    try:
        remaining = int(remaining)
    except ValueError:
        return
    if remaining >= TOKEN_COOLDOWN_THRESHOLD:
        return

    reset_seconds = _parse_go_duration(reset)
    if reset_seconds is None:
        return
    reset_seconds = min(reset_seconds, MAX_REPORTED_COOLDOWN_SECONDS)

    with _rate_limit_lock:
        _rate_limit_state["available_at"] = time.time() + reset_seconds


def seconds_until_available():
    """
    Public — checked at the top of _post_with_retry() (raises
    RateLimitedError instead of calling out to Groq at all when this is
    positive) and by the /api/ai-status/ view, so the frontend can show a
    countdown and disable "Process with AI" *before* OPS clicks into a
    call that would just get refused.
    """
    with _rate_limit_lock:
        available_at = _rate_limit_state["available_at"]
    return max(0.0, available_at - time.time())


def _post_with_retry(payload: dict, api_key: str) -> requests.Response:
    """
    POST to Groq once (plus a short bounded retry for a genuine network
    blip — see MAX_NETWORK_ATTEMPTS). Never sleeps out a rate limit
    in-request:
    - If a cooldown recorded from an earlier response is still active,
      raises RateLimitedError immediately — Groq isn't even called.
    - If Groq itself returns 429 on this call, records the rate-limit
      state (same as any other response) and raises RateLimitedError
      immediately — no retry loop, no backoff sleep. A single worker
      process (Render free tier's WEB_CONCURRENCY=1) can't absorb
      blocking on that; the caller turns this into a fast response and
      the frontend's existing cooldown UI (createCooldownController(),
      /api/ai-status/) handles the actual wait and retry.
    Any other failure (a real 4xx/5xx besides 429, or a network error
    that didn't recover within the short retry) is not transient and
    raises immediately.
    """
    cooldown = seconds_until_available()
    if cooldown > 0:
        raise RateLimitedError(cooldown)

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    delay = NETWORK_RETRY_BACKOFF_SECONDS
    resp = None
    for attempt in range(1, MAX_NETWORK_ATTEMPTS + 1):
        try:
            resp = requests.post(GROQ_CHAT_URL, headers=headers, json=payload, timeout=60)
            break
        except requests.RequestException as exc:
            if attempt == MAX_NETWORK_ATTEMPTS:
                raise ExtractionError(f"Groq request failed: {exc}") from exc
            time.sleep(delay)
            delay *= 2

    _record_rate_limit(resp)

    if resp.status_code == 429:
        retry_after = _parse_retry_after_seconds(resp)
        raise RateLimitedError(
            retry_after if retry_after is not None else seconds_until_available()
        )

    resp.raise_for_status()
    return resp


def extract_incident_data(municipality_name: str, raw_text: str) -> dict:
    """
    Extract structured incident data from one municipality's pasted report
    text. municipality_name is accepted for the caller's own logging/audit
    trail — it is deliberately never sent to the model as something to
    fill in or guess (the schema has no municipality field; see the system
    prompt).
    """
    api_key = getattr(settings, "GROQ_API_KEY", "")
    if not api_key:
        raise ExtractionError("GROQ_API_KEY is not configured.")

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": raw_text},
        ],
        "temperature": 0.1,
        "max_tokens": MAX_TOKENS,
        "response_format": {"type": "json_object"},
        "stream": False,
    }

    resp = _post_with_retry(payload, api_key)

    try:
        content = resp.json()["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError) as exc:
        raise ExtractionError(f"Unexpected Groq response shape: {exc}") from exc

    # Defensive: strip markdown fences in case the model adds them anyway.
    if content.startswith("```"):
        content = content.strip("`").strip()
        if content.lower().startswith("json"):
            content = content[4:].strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError as exc:
        raise ExtractionError(
            f"Groq response was not valid JSON: {exc}. Raw response: {content[:500]}"
        ) from exc


# Per-incident detail lines feed into the synopsis prompt as free-form
# context (not extracted data), so a hard length cap here is just about
# keeping the prompt bounded for batches with many/verbose entries — it
# doesn't need to be exact, just not unbounded.
_SYNOPSIS_DETAIL_MAX_CHARS = 300

# For each incident type, which field holds its distinguishing free-text
# label (fire/road have none — cause/structure_type are less central than
# the actions_taken narrative itself) and which holds its narrative detail.
_SYNOPSIS_INCIDENT_FIELDS = [
    ("road_crashes", "Road Crash", "cause", "actions_taken"),
    ("medical_assistance", "Medical Assistance", "nature_of_illness", "actions_taken"),
    ("fire_incidents", "Fire Incident", "structure_type", "actions_taken"),
    ("water_incidents", "Water Incident", "incident_type", "description"),
    ("trauma_emergencies", "Trauma Emergency", "call_type", "actions_taken"),
]


def _build_synopsis_prompt_input(batch, summary, sections) -> str:
    """
    Assembles the plain-text context handed to the model: the same
    compute_summary() counts the PDF's Section III and the dashboard cards
    use (never re-derived here — see pdf.py), plus each municipality's
    saved incidents and their actions_taken/description text. Only called
    when sections is non-empty — see generate_synopsis's own short-circuit
    for the all-zero case.
    """
    lines = [
        f"Reporting period: {batch.date} ({batch.get_shift_display()})",
        f"Municipalities with at least one saved entry: {batch.entries.count()}",
        "",
        "Counts (already computed — restate, never recompute or contradict these):",
        f"- Road crashes: {summary['road_crash_total']} "
        f"(injuries — minor: {summary['victims_minor']}, major: {summary['victims_major']}, "
        f"fatalities: {summary['victims_fatality']})",
        f"- Medical assistance cases: {summary['medical_total']}",
        f"- Fire incidents: {summary['fire_total']} "
        f"(casualties: {summary['fire_casualties']}, injured: {summary['fire_injured']}, "
        f"fatalities: {summary['fire_fatalities']})",
        f"- Water incidents: {summary['water_total']} "
        f"(casualties: {summary['water_casualties']}, injured: {summary['water_injured']}, "
        f"fatalities: {summary['water_fatalities']})",
        f"- Trauma emergencies: {summary['trauma_total']}",
        "",
        "Per-municipality incident detail:",
    ]

    for section in sections:
        lines.append(f"\n{section['municipality_name']}:")
        for key, label, type_field, detail_field in _SYNOPSIS_INCIDENT_FIELDS:
            for item in section[key]:
                type_note = getattr(item, type_field, None) or ""
                detail = (getattr(item, detail_field, None) or "").strip()
                detail = detail[:_SYNOPSIS_DETAIL_MAX_CHARS]
                suffix = f" ({type_note})" if type_note else ""
                lines.append(f"  - {label}{suffix}: {detail}")

    return "\n".join(lines)


# Returned directly, without a Groq call, when a batch has no incidents to
# summarize — this outcome is entirely mechanical (there is exactly one
# correct thing to say), and testing showed the model tends to phrase it
# as an awkward "no X, no Y, no Z" enumeration rather than the plain
# sentence it was asked for. Deterministic in, deterministic out.
_EMPTY_BATCH_SYNOPSIS = "No entries have been saved for this batch yet — nothing to summarize."
_NO_INCIDENTS_SYNOPSIS = "No significant incidents were reported for this period."


def generate_synopsis(batch) -> str:
    """
    Drafts a short narrative synopsis paragraph from a batch's already-
    saved entries — never touches the database, never called in JSON
    mode (the response is prose, not structured data). Reuses
    _post_with_retry/the module's cooldown state rather than a second
    rate-limit implementation, same Groq account either way. Local pdf.py
    import to keep pdf.py's own import graph (models/logo_base64 only)
    simple and one-directional — pdf.py has no reason to know ai.py exists.
    """
    from .pdf import build_municipality_sections, compute_summary

    if not batch.entries.exists():
        return _EMPTY_BATCH_SYNOPSIS

    summary = compute_summary(batch)
    sections = build_municipality_sections(batch)
    if not sections:
        return _NO_INCIDENTS_SYNOPSIS

    api_key = getattr(settings, "GROQ_API_KEY", "")
    if not api_key:
        raise ExtractionError("GROQ_API_KEY is not configured.")

    user_content = _build_synopsis_prompt_input(batch, summary, sections)

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": SYNOPSIS_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
        "max_tokens": SYNOPSIS_MAX_TOKENS,
        "stream": False,
    }

    resp = _post_with_retry(payload, api_key)

    try:
        content = resp.json()["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError) as exc:
        raise ExtractionError(f"Unexpected Groq response shape: {exc}") from exc

    # Defensive: the model was told not to quote-wrap the result, but strip
    # a wrapping pair if it does anyway — matches extract_incident_data's
    # own defensive fence-stripping in spirit.
    if len(content) >= 2 and content[0] == content[-1] == '"':
        content = content[1:-1].strip()

    return content


# Returned directly, without a Groq call, when no entry in the batch has
# a weather_condition set at all — same reasoning as
# _EMPTY_BATCH_SYNOPSIS/_NO_INCIDENTS_SYNOPSIS above: there is exactly
# one correct thing to say, so say it deterministically rather than
# spending a call and a cooldown slot asking the model to say it for us.
_NO_WEATHER_DATA = "No weather data available from municipality reports for this batch."


def generate_weather_summary(batch) -> str:
    """
    Same shape as generate_synopsis(): drafts a short synthesized
    province-wide weather paragraph from each entry's own
    weather_condition (skipping entries that left it blank), never
    touches the database, never called in JSON mode, reuses
    _post_with_retry/the module's cooldown state rather than a second
    rate-limit implementation.
    """
    conditions = []
    seen = set()
    # A municipality can have several entries per batch now — dedupe
    # exact repeats (OPS often pastes the same current-weather line into
    # every report within a shift) while still keeping genuinely
    # different observations for the same municipality (e.g. sunny in
    # the morning, rain by evening), which is real signal, not noise.
    for entry in batch.entries.exclude(weather_condition="").order_by(
        "municipality", "processed_at", "id"
    ):
        key = (entry.municipality, entry.weather_condition)
        if key in seen:
            continue
        seen.add(key)
        conditions.append(f"{entry.get_municipality_display()}: {entry.weather_condition}")

    if not conditions:
        return _NO_WEATHER_DATA

    api_key = getattr(settings, "GROQ_API_KEY", "")
    if not api_key:
        raise ExtractionError("GROQ_API_KEY is not configured.")

    user_content = "\n".join(conditions)

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": WEATHER_SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
        "max_tokens": WEATHER_SUMMARY_MAX_TOKENS,
        "stream": False,
    }

    resp = _post_with_retry(payload, api_key)

    try:
        content = resp.json()["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, ValueError) as exc:
        raise ExtractionError(f"Unexpected Groq response shape: {exc}") from exc

    if len(content) >= 2 and content[0] == content[-1] == '"':
        content = content[1:-1].strip()

    return content
