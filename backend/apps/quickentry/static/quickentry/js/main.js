"use strict";

/* ════════════════════════════════════════════════════════════════════
 * Quick SitRep — main page logic.
 * Plain vanilla JS, no build step, no framework. Talks to the DRF
 * endpoints built in Step 4. See docs/quick-report-entry-spec.md
 * Section 7 for the intended flow.
 * ════════════════════════════════════════════════════════════════════ */

// ── Field schemas (mirror serializers.py exactly) ──────────────────────
const INCIDENT_TYPES = {
  road_crashes: {
    title: "Road Crashes",
    singular: "Road Crash",
    fields: [
      { key: "datetime", label: "Date / Time", type: "datetime" },
      { key: "location", label: "Location", type: "text" },
      { key: "barangay", label: "Barangay", type: "text" },
      { key: "cause", label: "Cause", type: "text" },
      { key: "vehicles_involved", label: "Vehicles Involved", type: "text" },
      { key: "actions_taken", label: "Actions Taken", type: "textarea" },
      { key: "responding_team", label: "Responding Team", type: "text" },
      { key: "driver", label: "Driver", type: "text" },
      { key: "ert_members", label: "ERT Members", type: "text" },
    ],
    victims: {
      fields: [
        { key: "age", label: "Age", type: "number", nullable: true },
        { key: "sex", label: "Sex", type: "text" },
        { key: "address", label: "Address", type: "text" },
        { key: "injuries", label: "Injuries", type: "textarea" },
        { key: "injury_classification", label: "Classification", type: "select", options: ["MINOR", "MAJOR", "FATALITY"] },
      ],
      blank: () => ({ age: null, sex: "", address: "", injuries: "", injury_classification: "MINOR" }),
    },
    blank: () => ({
      datetime: "", location: "", barangay: "", cause: "", vehicles_involved: "",
      actions_taken: "", responding_team: "", driver: "", ert_members: "", victims: [],
    }),
  },
  medical_assistance: {
    title: "Medical Assistance",
    singular: "Medical Assistance",
    fields: [
      { key: "datetime", label: "Date / Time", type: "datetime" },
      { key: "location", label: "Location", type: "text" },
      { key: "barangay", label: "Barangay", type: "text" },
      { key: "patient_age", label: "Patient Age", type: "number", nullable: true },
      { key: "patient_sex", label: "Patient Sex", type: "text" },
      { key: "patient_address", label: "Patient Address", type: "text" },
      { key: "nature_of_illness", label: "Nature of Illness", type: "text" },
      { key: "chief_complaint", label: "Chief Complaint", type: "textarea" },
      { key: "blood_pressure", label: "Blood Pressure", type: "text" },
      { key: "pulse_rate", label: "Pulse Rate", type: "text" },
      { key: "spo2", label: "SpO2", type: "text" },
      { key: "temperature", label: "Temperature", type: "text" },
      { key: "actions_taken", label: "Actions Taken", type: "textarea" },
      { key: "responding_team", label: "Responding Team", type: "text" },
      { key: "driver", label: "Driver", type: "text" },
      { key: "ert_members", label: "ERT Members", type: "text" },
    ],
    blank: () => ({
      datetime: "", location: "", barangay: "", patient_age: null, patient_sex: "",
      patient_address: "", nature_of_illness: "", chief_complaint: "", blood_pressure: "",
      pulse_rate: "", spo2: "", temperature: "", actions_taken: "", responding_team: "",
      driver: "", ert_members: "",
    }),
  },
  fire_incidents: {
    title: "Fire Incidents",
    singular: "Fire Incident",
    fields: [
      { key: "datetime", label: "Date / Time", type: "datetime" },
      { key: "location", label: "Location", type: "text" },
      { key: "barangay", label: "Barangay", type: "text" },
      { key: "cause", label: "Cause", type: "text" },
      { key: "response_time_minutes", label: "Response Time (min)", type: "number" },
      { key: "distance_km", label: "Distance (km)", type: "number", float: true, step: "0.01" },
      { key: "structure_type", label: "Structure Type", type: "text" },
      { key: "families_affected", label: "Families Affected", type: "number" },
      { key: "individuals_affected", label: "Individuals Affected", type: "number" },
      { key: "structures_burned", label: "Structures Burned", type: "number" },
      { key: "fire_area_sqm", label: "Fire Area (sqm)", type: "number", float: true, step: "0.01" },
      { key: "casualties", label: "Casualties", type: "number" },
      { key: "injured", label: "Injured", type: "number" },
      { key: "fatalities", label: "Fatalities", type: "number" },
      { key: "responding_team", label: "Responding Team", type: "text" },
      { key: "actions_taken", label: "Actions Taken", type: "textarea" },
    ],
    blank: () => ({
      datetime: "", location: "", barangay: "", cause: "",
      response_time_minutes: 0, distance_km: 0, structure_type: "", families_affected: 0,
      individuals_affected: 0, structures_burned: 0, fire_area_sqm: 0, casualties: 0,
      injured: 0, fatalities: 0, responding_team: "", actions_taken: "",
    }),
  },
  water_incidents: {
    title: "Water Incidents",
    singular: "Water Incident",
    fields: [
      { key: "datetime", label: "Date / Time", type: "datetime" },
      { key: "location", label: "Location", type: "text" },
      { key: "barangay", label: "Barangay", type: "text" },
      { key: "incident_type", label: "Incident Type", type: "text" },
      { key: "description", label: "Description", type: "textarea" },
      { key: "families_affected", label: "Families Affected", type: "number" },
      { key: "individuals_affected", label: "Individuals Affected", type: "number" },
      { key: "casualties", label: "Casualties", type: "number" },
      { key: "injured", label: "Injured", type: "number" },
      { key: "fatalities", label: "Fatalities", type: "number" },
      { key: "responding_team", label: "Responding Team", type: "text" },
      { key: "actions_taken", label: "Actions Taken", type: "textarea" },
    ],
    blank: () => ({
      datetime: "", location: "", barangay: "", incident_type: "", description: "",
      families_affected: 0, individuals_affected: 0, casualties: 0, injured: 0,
      fatalities: 0, responding_team: "", actions_taken: "",
    }),
  },
  trauma_emergencies: {
    title: "Trauma Emergencies",
    singular: "Trauma Emergency",
    fields: [
      { key: "datetime", label: "Date / Time", type: "datetime" },
      { key: "location", label: "Location", type: "text" },
      { key: "barangay", label: "Barangay", type: "text" },
      { key: "call_type", label: "Call Type", type: "text" },
      { key: "patient_age", label: "Patient Age", type: "number", nullable: true },
      { key: "patient_sex", label: "Patient Sex", type: "text" },
      { key: "patient_address", label: "Patient Address", type: "text" },
      { key: "chief_complaint", label: "Chief Complaint", type: "textarea" },
      { key: "actions_taken", label: "Actions Taken", type: "textarea" },
      { key: "responding_team", label: "Responding Team", type: "text" },
      { key: "driver", label: "Driver", type: "text" },
      { key: "ert_members", label: "ERT Members", type: "text" },
    ],
    blank: () => ({
      datetime: "", location: "", barangay: "", call_type: "", patient_age: null,
      patient_sex: "", patient_address: "", chief_complaint: "", actions_taken: "",
      responding_team: "", driver: "", ert_members: "",
    }),
  },
};

const LIFELINES_FIELDS = [
  { key: "power_supply", label: "Power Supply", type: "select", options: ["OPERATIONAL", "INTERRUPTED", "UNDER_REPAIR"] },
  { key: "power_supply_notes", label: "Power Notes", type: "textarea" },
  { key: "water_supply", label: "Water Supply", type: "select", options: ["OPERATIONAL", "INTERRUPTED", "UNDER_REPAIR"] },
  { key: "water_supply_notes", label: "Water Notes", type: "textarea" },
  { key: "communication", label: "Communication", type: "select", options: ["OPERATIONAL", "INTERRUPTED", "UNDER_REPAIR"] },
  { key: "communication_notes", label: "Communication Notes", type: "textarea" },
  { key: "road_condition", label: "Road Condition", type: "select", options: ["PASSABLE", "IMPASSABLE", "FLOODED"] },
  { key: "road_notes", label: "Road Notes", type: "textarea" },
  { key: "sea_travel", label: "Sea Travel", type: "select", options: ["NORMAL", "SUSPENDED", "MODIFIED"] },
  { key: "sea_notes", label: "Sea Notes", type: "textarea" },
  { key: "class_suspension", label: "Class Suspension", type: "checkbox" },
  { key: "class_suspension_notes", label: "Class Suspension Notes", type: "textarea" },
];

// Populated from window.QUICKSITREP.schema at init() — see applySchema().
// Not hardcoded per incident type: derived from each serializer's own
// required fields (serializers.get_incident_schema), so this can never
// drift out of sync with what /save/ actually validates against.
function applySchema(schema) {
  if (!schema) return;
  for (const [key, typeSchema] of Object.entries(INCIDENT_TYPES)) {
    const fieldSchema = schema[key] || {};
    typeSchema.fields.forEach((f) => {
      f.required = !!fieldSchema[f.key];
    });
    if (typeSchema.victims) {
      const victimSchema = schema._victim || {};
      typeSchema.victims.fields.forEach((f) => {
        f.required = !!victimSchema[f.key];
      });
    }
  }
  const lifelinesSchema = schema.lifelines_status || {};
  LIFELINES_FIELDS.forEach((f) => {
    f.required = !!lifelinesSchema[f.key];
  });
}

function isBlank(value, field) {
  if (field.type === "number") return value === null || value === undefined;
  if (field.type === "checkbox") return false; // booleans are never "blank"
  return value === null || value === undefined || value === "";
}

function blankLifelines() {
  return {
    power_supply: "OPERATIONAL", power_supply_notes: "",
    water_supply: "OPERATIONAL", water_supply_notes: "",
    communication: "OPERATIONAL", communication_notes: "",
    road_condition: "PASSABLE", road_notes: "",
    sea_travel: "NORMAL", sea_notes: "",
    class_suspension: false, class_suspension_notes: "",
  };
}

// ── App state ───────────────────────────────────────────────────────
const state = {
  batch: null,
  municipalities: [],
  summary: null,           // compute_summary() output — same source PDF Section III uses
  selected: null,
  rawText: "",
  extraction: null,        // editable, UI-normalized copy
  originalExtraction: null, // untouched AI response, for ai_output's audit-trail contract
  reopenedEntryId: null,   // set when the form was populated from a saved entry, not a fresh extract
  forceNewPaste: false,    // true after "Add Another Report" — show the paste box even though this municipality already has entries
  finalizeForm: { synopsis: "", weather_conditions: "", actions_taken: "" },
};

// Backed by the server's is_locked (ManualBatch.is_locked — status ==
// FINALIZED and not amended more recently than the last finalize), not
// a raw status check — an amended FINALIZED batch is open for editing
// again even though its status is still "FINALIZED". See models.py.
function isBatchLocked() {
  return !!(state.batch && state.batch.is_locked);
}

// finalize/download URLs need a batch id DRF's url reverse can't give us
// client-side without a router — main.html injects them with a sentinel
// pk that we swap for the real id, so the path itself still comes from
// urls.py (via {% url %}) rather than being duplicated here.
const API = {
  finalize: (id) => window.QUICKSITREP.endpoints.finalizeTemplate.replace("999999", id),
  download: (id) => window.QUICKSITREP.endpoints.downloadTemplate.replace("999999", id),
  entryDetail: (id) => window.QUICKSITREP.endpoints.entryDetailTemplate.replace("999999", id),
  generateSynopsis: (id) => window.QUICKSITREP.endpoints.generateSynopsisTemplate.replace("999999", id),
  generateWeather: (id) => window.QUICKSITREP.endpoints.generateWeatherTemplate.replace("999999", id),
  amend: (id) => window.QUICKSITREP.endpoints.amendTemplate.replace("999999", id),
};

// ── Small helpers ───────────────────────────────────────────────────
function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function getCookie(name) {
  const match = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
  return match ? decodeURIComponent(match[1]) : null;
}

async function apiFetch(url, options = {}) {
  const opts = { credentials: "same-origin", ...options };
  opts.headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS", "TRACE"].includes(method)) {
    opts.headers["X-CSRFToken"] = getCookie("csrftoken");
  }
  const resp = await fetch(url, opts);
  let data = null;
  try {
    data = await resp.json();
  } catch (e) {
    /* empty body, e.g. some error responses */
  }
  if (!resp.ok) {
    const err = new Error((data && data.detail) || `Request failed (${resp.status})`);
    err.status = resp.status;
    err.data = data;
    throw err;
  }
  return data;
}

// ── Best-effort raw-string date/time parsing ───────────────────────
// The AI is told to return "raw as reported" strings (spec Section 4),
// e.g. "February 11, 2026 | 0900H" or "15 February 2026 | 1000H" — never
// something a DateTimeField/TimeField will accept directly (confirmed in
// Step 4 testing). We pre-populate real pickers on a best-effort basis;
// OPS always sees + can correct the result. A failed parse leaves the
// picker empty rather than guessing.
const MONTHS = {
  january: 1, february: 2, march: 3, april: 4, may: 5, june: 6, july: 7,
  august: 8, september: 9, october: 10, november: 11, december: 12,
  // Common abbreviations seen in real reports (e.g. Calauag's "Aug",
  // Infanta's "Aug." — alpha-test bugs #3/#5, client feedback
  // 2026-08-25). The optional trailing "." is stripped by the date-part
  // regexes below before this lookup runs, so only the bare abbreviation
  // needs listing here.
  jan: 1, feb: 2, mar: 3, apr: 4, jun: 6, jul: 7, aug: 8,
  sep: 9, sept: 9, oct: 10, nov: 11, dec: 12,
};

// Each of these tries to split "<date stuff><separator><time token>" one
// specific way (a few try the reverse order — time first). Add more here
// as new report formats show up — none of them replace each other,
// parseRoughDatetime tries them all in order and only gives up (leaves
// the picker blocked) if none match.
function splitDateTime_Pipe(trimmed) {
  // "February 11, 2026 | 0900H"
  const m = trimmed.match(/^(.*?)\|\s*(\d{3,4})H?\s*$/i);
  return m ? { datePart: m[1].trim(), timeToken: m[2] } : null;
}

function splitDateTime_Comma(trimmed) {
  // "February 19, 2026, 0600H" — greedy backtracking naturally finds the
  // LAST 4-digit year (not the trailing time token) as the split point,
  // since only that position lets the rest of the pattern match at all.
  const m = trimmed.match(/^(.*\d{4}),\s*(\d{3,4})H?\s*$/i);
  return m ? { datePart: m[1].trim(), timeToken: m[2] } : null;
}

function splitDateTime_Slash(trimmed) {
  // "Aug. 25, 2026/1016H" (Infanta — alpha-test bug #5). Same greedy-
  // backtracking trick as splitDateTime_Comma, just on "/" instead.
  const m = trimmed.match(/^(.*?)\/\s*(\d{3,4})H?\s*$/i);
  return m ? { datePart: m[1].trim(), timeToken: m[2] } : null;
}

function splitDateTime_MilitaryTimeFirst(trimmed) {
  // "2306H August 24, 2026" (Pagbilao — alpha-test bug #3): the AI read
  // the raw string correctly (shown in the "AI read:" hint), but every
  // splitter above assumes the date comes first — this is the same
  // military-time token, just leading instead of trailing.
  const m = trimmed.match(/^(\d{3,4})H\s+(.+)$/i);
  return m ? { datePart: m[2].trim(), timeToken: m[1] } : null;
}

function _hhmmFrom12Hour(hourStr, minuteStr, ampm) {
  let hh = parseInt(hourStr, 10);
  const mm = parseInt(minuteStr, 10);
  if (hh < 1 || hh > 12 || mm > 59) return null;
  const isPM = /pm/i.test(ampm);
  if (hh === 12) hh = isPM ? 12 : 0; // 12:00am -> 00:xx, 12:00pm -> 12:xx
  else if (isPM) hh += 12;
  return `${String(hh).padStart(2, "0")}${String(mm).padStart(2, "0")}`;
}

function splitDateTime_12HourTimeFirst(trimmed) {
  // "12:00am Aug 25 2026" (Calauag — alpha-test bug #3): time first
  // again, but 12-hour clock with am/pm instead of military time.
  const m = trimmed.match(/^(\d{1,2}):(\d{2})\s*([ap]m)\s+(.+)$/i);
  if (!m) return null;
  const timeToken = _hhmmFrom12Hour(m[1], m[2], m[3]);
  return timeToken ? { datePart: m[4].trim(), timeToken } : null;
}

function parseRoughDatetime(raw) {
  if (!raw || typeof raw !== "string") return null;
  const trimmed = raw.trim();

  // Already ISO-ish (e.g. re-editing a saved entry) — use directly.
  const iso = trimmed.match(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})/);
  if (iso) return iso[1];

  const split =
    splitDateTime_Pipe(trimmed) ||
    splitDateTime_Comma(trimmed) ||
    splitDateTime_Slash(trimmed) ||
    splitDateTime_MilitaryTimeFirst(trimmed) ||
    splitDateTime_12HourTimeFirst(trimmed);
  // No recognized time portion at all -> nothing to safely default to.
  // (Pre-existing bug, not introduced by the comma-format addition: this
  // used to fall through and silently default to 00:00, which is exactly
  // the kind of guess the picker is supposed to never make.)
  if (!split) return null;
  const datePart = split.datePart;
  const timeToken = split.timeToken.padStart(4, "0");

  // \.?  after the month allows an abbreviation with a trailing period
  // ("Aug.", "Sept.") alongside the full name — both look up the same in
  // MONTHS since the period itself is consumed here, never passed on.
  let year, month, day;
  let m = datePart.match(/^([A-Za-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})$/);
  if (m) {
    month = MONTHS[m[1].toLowerCase()];
    day = parseInt(m[2], 10);
    year = parseInt(m[3], 10);
  } else {
    m = datePart.match(/^(\d{1,2})\s+([A-Za-z]+)\.?\s+(\d{4})$/);
    if (m) {
      day = parseInt(m[1], 10);
      month = MONTHS[m[2].toLowerCase()];
      year = parseInt(m[3], 10);
    }
  }
  if (!year || !month || !day) return null;

  // timeToken is guaranteed non-null here (returned early above otherwise).
  const hh = parseInt(timeToken.slice(0, 2), 10);
  const mm = parseInt(timeToken.slice(2), 10);
  if (hh > 23 || mm > 59) return null;

  const pad = (n) => String(n).padStart(2, "0");
  return `${year}-${pad(month)}-${pad(day)}T${pad(hh)}:${pad(mm)}`;
}

// ── Normalize a fresh AI extraction into editable UI state ─────────
function normalizeExtraction(raw) {
  const clone = JSON.parse(JSON.stringify(raw || {}));
  clone.lifelines_status = clone.lifelines_status && Object.keys(clone.lifelines_status).length
    ? clone.lifelines_status : {};
  clone.unmapped_notes = clone.unmapped_notes || "";
  clone.weather_condition = clone.weather_condition || "";

  for (const key of Object.keys(INCIDENT_TYPES)) {
    clone[key] = (clone[key] || []).map((item) => normalizeIncidentItem(key, item));
  }
  return clone;
}

function normalizeIncidentItem(sectionKey, rawItem) {
  const item = { ...rawItem };

  item._raw_datetime = item.datetime || "";
  item.datetime = parseRoughDatetime(item.datetime) || "";

  for (const f of INCIDENT_TYPES[sectionKey].fields) {
    if (f.type === "number") {
      if (item[f.key] === null || item[f.key] === undefined) {
        item[f.key] = f.nullable ? null : 0;
      }
    } else if (f.type !== "datetime" && f.type !== "time") {
      if (item[f.key] === null || item[f.key] === undefined) item[f.key] = "";
    }
  }

  if (sectionKey === "road_crashes") {
    item.victims = (rawItem.victims || []).map((v) => {
      const victim = { ...v };
      if (victim.age === undefined) victim.age = null;
      if (victim.sex == null) victim.sex = "";
      if (victim.address == null) victim.address = "";
      if (victim.injuries == null) victim.injuries = "";
      // Do NOT default a missing classification to "MINOR" -- alpha-test
      // bug #2 (client feedback, 2026-08-25): a report can state an
      // overall/aggregate severity ("Classification of Injury: Major")
      // without breaking it down per victim, which the AI correctly
      // leaves null rather than guess (see the unmapped_notes rule in
      // ai.py's SYSTEM_PROMPT). Silently coercing that null to the LEAST
      // severe option meant a real Major/Fatality injury could reach a
      // saved record looking like an active "Minor" classification the
      // AI supposedly chose, when nothing was actually chosen. Leaving
      // it "" instead makes isBlank()/renderField() correctly flag it as
      // a required field OPS must consciously fill in -- same
      // never-silently-guess principle as parseRoughDatetime().
      if (victim.injury_classification == null) victim.injury_classification = "";
      return victim;
    });
  }

  return item;
}

// ── Rendering ────────────────────────────────────────────────────
function fieldDataAttrs(section, idx, victimIndex, field) {
  let attrs = `data-field="${field.key}" data-section="${section}"`;
  if (idx !== null && idx !== undefined) attrs += ` data-index="${idx}"`;
  if (victimIndex !== null && victimIndex !== undefined) attrs += ` data-victim-index="${victimIndex}"`;
  return attrs;
}

function renderFieldHint(field, parentItem) {
  if (field.type !== "datetime" && field.type !== "time") return "";
  if (!parentItem) return "";
  // Re-opened entries: the value is already a real stored date/time, not
  // an AI guess — showing "AI read: <same value>" would just be a
  // confusing, redundant echo, so skip the hint entirely here.
  if (state.reopenedEntryId) return "";
  const raw = parentItem["_raw_" + field.key];
  if (!raw) return "";
  return `<span class="field-hint">AI read: "${escapeHtml(raw)}"</span>`;
}

function renderField(section, idx, victimIndex, field, value, parentItem) {
  const attrs = fieldDataAttrs(section, idx, victimIndex, field);
  const missing = field.required && isBlank(value, field);
  let inputHtml;

  switch (field.type) {
    case "textarea":
      inputHtml = `<textarea ${attrs} rows="2">${escapeHtml(value || "")}</textarea>`;
      break;
    case "select": {
      // A blank/unrecognized value (e.g. injury_classification left null
      // by the AI -- see normalizeIncidentItem) must render as visibly
      // unselected, not silently fall back to whichever option happens
      // to be first in the list -- that's exactly how alpha-test bug #2
      // slipped through (an unset classification LOOKED like "Minor" was
      // already chosen). Every real option in field.options is a genuine
      // saved value already (lifelines fields always arrive pre-filled
      // via blankLifelines()), so this placeholder only ever shows up
      // for a true no-value case.
      const matchesOption = field.options.includes(value);
      const placeholder = matchesOption
        ? ""
        : `<option value="" selected disabled hidden>-- Select --</option>`;
      inputHtml = `<select ${attrs}>${placeholder}${field.options.map((o) =>
        `<option value="${o}" ${o === value ? "selected" : ""}>${o}</option>`).join("")}</select>`;
      break;
    }
    case "checkbox":
      inputHtml = `<input type="checkbox" ${attrs} ${value ? "checked" : ""}>`;
      break;
    case "number":
      inputHtml = `<input type="number" ${attrs} value="${value === null || value === undefined ? "" : value}" ` +
        `data-nullable="${field.nullable ? "true" : "false"}" data-float="${field.float ? "true" : "false"}" ` +
        `${field.step ? `step="${field.step}"` : ""}>`;
      break;
    case "datetime":
      inputHtml = `<input type="datetime-local" ${attrs} value="${value || ""}">`;
      break;
    case "time":
      inputHtml = `<input type="time" ${attrs} value="${value || ""}">`;
      break;
    default:
      inputHtml = `<input type="text" ${attrs} value="${escapeHtml(value || "")}">`;
  }

  return `
    <label class="field ${missing ? "field-missing" : ""}">
      <span class="field-label">${field.label}${field.required ? " *" : ""}</span>
      ${inputHtml}
      ${renderFieldHint(field, parentItem)}
    </label>
  `;
}

function renderVictims(sectionKey, idx, item) {
  const schema = INCIDENT_TYPES[sectionKey].victims;
  const victims = item.victims || [];
  const rows = victims.map((v, vIdx) => `
    <div class="victim-row">
      <div class="field-grid">
        ${schema.fields.map((f) => renderField(sectionKey, idx, vIdx, f, v[f.key], v)).join("")}
      </div>
      <button type="button" class="btn btn-danger btn-small" data-action="remove-victim"
        data-section="${sectionKey}" data-index="${idx}" data-victim-index="${vIdx}">Remove Victim</button>
    </div>
  `).join("");

  return `
    <div class="victims-block">
      <div class="section-header">
        <h4>Victims <span class="count-badge">${victims.length}</span></h4>
        <button type="button" class="btn btn-small" data-action="add-victim"
          data-section="${sectionKey}" data-index="${idx}">+ Add Victim</button>
      </div>
      ${rows || `<p class="empty-hint">None listed.</p>`}
    </div>
  `;
}

function renderIncidentCard(sectionKey, schema, item, idx) {
  const fieldsHtml = schema.fields.map((f) => renderField(sectionKey, idx, null, f, item[f.key], item)).join("");
  const victimsHtml = schema.victims ? renderVictims(sectionKey, idx, item) : "";
  const titleBits = [item.location, item.barangay].filter(Boolean).join(", ");
  return `
    <div class="card">
      <div class="card-header">
        <span class="card-title">#${idx + 1}${titleBits ? " — " + escapeHtml(titleBits) : ""}</span>
        <button type="button" class="btn btn-danger btn-small" data-action="remove-item"
          data-section="${sectionKey}" data-index="${idx}">Remove</button>
      </div>
      <div class="field-grid">${fieldsHtml}</div>
      ${victimsHtml}
    </div>
  `;
}

function renderIncidentSection(sectionKey) {
  const schema = INCIDENT_TYPES[sectionKey];
  const items = state.extraction[sectionKey] || [];
  const cards = items.map((item, idx) => renderIncidentCard(sectionKey, schema, item, idx)).join("");
  const singular = schema.singular;
  return `
    <section class="incident-section">
      <div class="section-header">
        <h3>${schema.title} <span class="count-badge">${items.length}</span></h3>
        <button type="button" class="btn btn-small" data-action="add-item" data-section="${sectionKey}">+ Add ${singular}</button>
      </div>
      ${items.length ? cards : `<p class="empty-hint">None reported.</p>`}
    </section>
  `;
}

function renderLifelines() {
  const active = state.extraction.lifelines_status && Object.keys(state.extraction.lifelines_status).length > 0;
  if (!active) {
    return `
      <section class="incident-section">
        <div class="section-header">
          <h3>Lifelines Status</h3>
          <button type="button" class="btn btn-small" data-action="enable-lifelines">+ Report Lifelines</button>
        </div>
        <p class="empty-hint">Not reported for this municipality.</p>
      </section>
    `;
  }
  const fieldsHtml = LIFELINES_FIELDS.map((f) =>
    renderField("lifelines_status", null, null, f, state.extraction.lifelines_status[f.key])).join("");
  return `
    <section class="incident-section">
      <div class="section-header">
        <h3>Lifelines Status</h3>
        <button type="button" class="btn btn-danger btn-small" data-action="disable-lifelines">Remove</button>
      </div>
      <div class="field-grid">${fieldsHtml}</div>
    </section>
  `;
}

function renderUnmappedNotes() {
  const notes = state.extraction.unmapped_notes || "";
  return `
    <section class="unmapped-box ${notes ? "unmapped-box--active" : ""}">
      <h3>⚠ Unmapped / Needs Review</h3>
      <textarea data-section="unmapped_notes" data-field="unmapped_notes" rows="3"
        placeholder="Nothing flagged by the AI.">${escapeHtml(notes)}</textarea>
    </section>
  `;
}

// weather_condition is a sibling to unmapped_notes (a plain column on
// ManualEntry, not part of any incident type or lifelines_status) — this
// entry's own stated weather observation, if the report had one. Feeds
// ai.generate_weather_summary() across the batch from the finalize panel.
function renderWeatherCondition() {
  const value = state.extraction.weather_condition || "";
  return `
    <label class="field weather-field">
      <span class="field-label">Weather Condition (this report)</span>
      <input type="text" data-section="weather_condition" data-field="weather_condition"
        value="${escapeHtml(value)}" placeholder="e.g. Sunny skies — leave blank if not stated in the report">
    </label>
  `;
}

function renderEntryList(muni) {
  const items = muni.entries.map((e) => {
    const when = e.processed_at ? new Date(e.processed_at).toLocaleString() : "(unsaved)";
    return `
      <button type="button" class="entry-list-item" data-entry-id="${e.id}">
        <span class="entry-list-meta">${escapeHtml(when)} <span class="entry-list-status">${escapeHtml(e.status)}</span></span>
        <span class="entry-list-preview">${escapeHtml(e.preview)}</span>
      </button>
    `;
  }).join("");

  return `
    <p class="entry-list-hint">
      ${muni.entry_count} report${muni.entry_count === 1 ? "" : "s"} saved for this municipality.
      Click one to open it, or add another below.
    </p>
    <div class="entry-list">${items}</div>
    <div class="actions">
      <button type="button" class="btn btn-primary" data-action="add-report">Add Another Report</button>
    </div>
  `;
}

function renderEntryPanel() {
  const panel = document.getElementById("entry-panel");

  if (isBatchLocked()) {
    panel.innerHTML = `
      <div class="alert alert-warning">
        <strong>This batch has been finalized.</strong> No further entries can be added or
        edited — see the Finalize panel below for the download link, or to amend it.
      </div>
    `;
    return;
  }

  if (!state.selected) {
    panel.innerHTML = `<p class="entry-placeholder">Select a municipality to begin.</p>`;
    return;
  }

  const muni = state.municipalities.find((m) => m.id === state.selected);
  let html = `<h2>${escapeHtml(muni.name)}</h2>`;

  if (!state.extraction && muni.entry_count > 0 && !state.forceNewPaste) {
    // A municipality can have several separate entries per batch (one
    // paste per incident, e.g. Lucban's actual submission pattern) — show
    // what's already there first rather than jumping straight into either
    // a blank paste box or an arbitrary one of them.
    html += renderEntryList(muni);
  } else if (!state.extraction) {
    html += `
      <div class="paste-box">
        <label>Paste the raw report text
          <textarea id="raw-text-input" rows="10" placeholder="Paste the Messenger report here…">${escapeHtml(state.rawText)}</textarea>
        </label>
        <div id="ai-cooldown-notice"></div>
        <div class="actions">
          <button type="button" class="btn btn-primary" data-action="process">Process with AI</button>
          ${muni.entry_count > 0 ? `<button type="button" class="btn" data-action="back-to-list">Back to List</button>` : ""}
        </div>
        <div id="process-error"></div>
      </div>
    `;
  } else {
    const reopened = !!state.reopenedEntryId;
    html += `
      <div class="raw-text-readonly">
        <details ${reopened ? "open" : ""}>
          <summary>${reopened ? "Original pasted text (read-only — cannot be changed once saved)" : "Original pasted text"}</summary>
          <pre>${escapeHtml(state.rawText)}</pre>
        </details>
      </div>
      ${renderWeatherCondition()}
      ${renderUnmappedNotes()}
      ${Object.keys(INCIDENT_TYPES).map(renderIncidentSection).join("")}
      ${renderLifelines()}
      <div id="validation-warnings"></div>
      <div class="save-actions">
        <button type="button" id="save-entry-btn" class="btn btn-primary" data-action="save">${reopened ? "Save Changes" : "Save Entry"}</button>
        ${reopened
          ? `<button type="button" class="btn" data-action="cancel-reopen">Cancel</button>`
          : `<button type="button" class="btn" data-action="discard">Discard &amp; Re-paste</button>`}
        ${!reopened && muni.entry_count > 0 ? `<button type="button" class="btn" data-action="back-to-list">Back to List</button>` : ""}
      </div>
      <div id="save-message"></div>
    `;
  }

  panel.innerHTML = html;
  processCooldown.stop();
  if (state.extraction) {
    refreshValidation();
  } else {
    checkAiCooldown().then((secs) => {
      if (secs > 0) processCooldown.start(secs);
    });
  }
}

// Generalized across all 5 incident types + victims + lifelines — driven
// entirely by field.required, which applySchema() sets from the live
// serializers (see serializers.get_incident_schema). Nothing here is
// hardcoded per incident type; a field is a blocker if and only if the
// backend would also reject it as required.
function collectMissingRequiredFields() {
  const blockers = [];

  for (const [sectionKey, schema] of Object.entries(INCIDENT_TYPES)) {
    const items = state.extraction[sectionKey] || [];
    const singular = schema.singular;

    items.forEach((item, idx) => {
      const missingFields = schema.fields
        .filter((f) => f.required && isBlank(item[f.key], f))
        .map((f) => f.label);
      if (missingFields.length) {
        blockers.push(`${singular} #${idx + 1}: missing ${missingFields.join(", ")}`);
      }

      if (schema.victims) {
        (item.victims || []).forEach((v, vIdx) => {
          const missingVictimFields = schema.victims.fields
            .filter((f) => f.required && isBlank(v[f.key], f))
            .map((f) => f.label);
          if (missingVictimFields.length) {
            blockers.push(`${singular} #${idx + 1}, Victim ${vIdx + 1}: missing ${missingVictimFields.join(", ")}`);
          }
        });
      }
    });
  }

  const lifelines = state.extraction.lifelines_status;
  if (lifelines && Object.keys(lifelines).length > 0) {
    const missingLifelines = LIFELINES_FIELDS
      .filter((f) => f.required && isBlank(lifelines[f.key], f))
      .map((f) => f.label);
    if (missingLifelines.length) {
      blockers.push(`Lifelines Status: missing ${missingLifelines.join(", ")}`);
    }
  }

  return blockers;
}

function refreshValidation() {
  const blockers = collectMissingRequiredFields();

  const warnBox = document.getElementById("validation-warnings");
  const saveBtn = document.getElementById("save-entry-btn");
  if (warnBox) {
    warnBox.innerHTML = blockers.length
      ? `<div class="alert alert-warning"><strong>Cannot save yet:</strong><ul>${
          blockers.map((b) => `<li>${escapeHtml(b)}</li>`).join("")}</ul></div>`
      : "";
  }
  if (saveBtn) saveBtn.disabled = blockers.length > 0;
}

// ── Summary cards ────────────────────────────────────────────────
// Batch-scoped counts only — no trends/sparklines/animated count-up/dark
// mode, unlike the main project's OpsDashboard this is styled after:
// this tool has no "previous period" to compare against, so those don't
// fit. Reads state.summary directly (compute_summary()'s own field
// names from pdf.py) — never recomputed client-side, same source of
// truth as the PDF's Section III.
const SUMMARY_CARD_ICONS = {
  road: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="1" y="3" width="15" height="13" rx="2"/><path d="M16 8h4l3 3v5h-7V8z"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/></svg>',
  medical: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20.84 4.61a5.5 5.5 0 0 0-7.78 0L12 5.67l-1.06-1.06a5.5 5.5 0 0 0-7.78 7.78l1.06 1.06L12 21.23l7.78-7.78 1.06-1.06a5.5 5.5 0 0 0 0-7.78z"/></svg>',
  fire: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8.5 14.5A2.5 2.5 0 0 0 11 12c0-1.38-.5-2-1-3-1.072-2.143-.224-4.054 2-6 .5 2.5 2 4.9 4 6.5 2 1.6 3 3.5 3 5.5a7 7 0 1 1-14 0c0-1.153.433-2.294 1-3a2.5 2.5 0 0 0 2.5 2.5z"/></svg>',
  water: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2s-6 7-6 12a6 6 0 0 0 12 0c0-5-6-12-6-12z"/></svg>',
  trauma: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="4"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg>',
};

const SUMMARY_CARDS_CONFIG = [
  { key: "road", label: "Road Crashes", summaryField: "road_crash_total" },
  { key: "medical", label: "Medical Cases", summaryField: "medical_total" },
  { key: "fire", label: "Fire Incidents", summaryField: "fire_total" },
  { key: "water", label: "Water Incidents", summaryField: "water_total" },
  { key: "trauma", label: "Trauma Emergencies", summaryField: "trauma_total" },
];

function renderSummaryCards() {
  const container = document.getElementById("summary-cards");
  if (!container) return;
  if (!state.summary) {
    container.innerHTML = "";
    return;
  }
  container.innerHTML = SUMMARY_CARDS_CONFIG.map((c) => `
    <div class="summary-card summary-card--${c.key}">
      <div class="icon-badge">${SUMMARY_CARD_ICONS[c.key]}</div>
      <div class="card-value">${state.summary[c.summaryField] ?? 0}</div>
      <div class="card-label">${c.label}</div>
    </div>
  `).join("");
}

// ── Checklist ────────────────────────────────────────────────────
function renderChecklist() {
  const container = document.getElementById("checklist");
  const locked = isBatchLocked();
  container.innerHTML = state.municipalities.map((m) => `
    <button type="button" class="checklist-item ${m.entry_count > 0 ? "done" : ""} ${state.selected === m.id ? "selected" : ""} ${locked ? "locked" : ""}"
      data-muni="${m.id}" ${locked ? "disabled title=\"Batch is finalized — read-only\"" : ""}>
      <span class="check-dot">${m.entry_count > 0 ? "✓" : ""}</span>
      <span class="check-name">${escapeHtml(m.name)}</span>
      ${m.entry_count > 0 ? `<span class="check-status">${m.entry_count} report${m.entry_count === 1 ? "" : "s"}</span>` : ""}
    </button>
  `).join("");

  const progress = document.getElementById("checklist-progress");
  const done = state.municipalities.filter((m) => m.entry_count > 0).length;
  progress.textContent = `${done} / ${state.municipalities.length} reporting`;
}

// Batch History's "Open" link for a DRAFT batch navigates here with
// ?batch_id=<id> instead of letting the page default to today's
// current-shift batch — see loadCurrentBatch().
function getRequestedBatchId() {
  const params = new URLSearchParams(window.location.search);
  const id = params.get("batch_id");
  return id ? parseInt(id, 10) : null;
}

function updateBatchStatusText() {
  const el = document.getElementById("batch-status");
  const b = state.batch;
  const done = state.municipalities.filter((m) => m.entry_count > 0).length;
  const base = `${b.date} · ${b.shift} · ${b.status} · ${done}/${state.municipalities.length} reporting`;

  if (getRequestedBatchId()) {
    el.innerHTML = `${base} — <a href="/">viewing from History, back to today</a>`;
  } else {
    el.textContent = base;
  }

  const finalizeProgress = document.getElementById("finalize-progress");
  if (finalizeProgress) finalizeProgress.textContent = `${done} / ${state.municipalities.length}`;
}

// Selecting a municipality never auto-loads any specific entry anymore —
// a municipality can have several, so there's no single "the" entry to
// jump into. It either shows the paste box (zero entries) or the entry
// list (one or more) — see renderEntryPanel/renderEntryList. Loading a
// specific saved entry only happens from openEntryForEdit, when OPS
// actually clicks one in that list.
function selectMunicipality(code) {
  if (isBatchLocked()) return;
  state.selected = code;
  state.rawText = "";
  state.extraction = null;
  state.originalExtraction = null;
  state.reopenedEntryId = null;
  state.forceNewPaste = false;
  renderChecklist();
  renderEntryPanel();
}

async function openEntryForEdit(entryId) {
  document.getElementById("entry-panel").innerHTML = `<p class="entry-placeholder">Loading saved entry…</p>`;
  try {
    const data = await apiFetch(API.entryDetail(entryId));
    state.rawText = data.raw_text;
    state.originalExtraction = data.ai_output; // frozen audit-trail copy — passed straight through on next save
    state.reopenedEntryId = data.id;
    // Only the schema-shaped fields go into state.extraction — entry
    // detail's extra metadata (id, batch_id, status, ai_output, ...)
    // stays out of it so it never leaks into the next save's edited_json.
    state.extraction = normalizeExtraction({
      road_crashes: data.road_crashes,
      medical_assistance: data.medical_assistance,
      fire_incidents: data.fire_incidents,
      water_incidents: data.water_incidents,
      trauma_emergencies: data.trauma_emergencies,
      lifelines_status: data.lifelines_status,
      unmapped_notes: data.unmapped_notes,
      weather_condition: data.weather_condition,
    });
  } catch (err) {
    document.getElementById("entry-panel").innerHTML =
      `<div class="alert alert-error">Failed to load saved entry: ${escapeHtml(err.message)}</div>`;
    return;
  }
  renderEntryPanel();
}

function handleAddAnotherReport() {
  state.rawText = "";
  state.extraction = null;
  state.originalExtraction = null;
  state.reopenedEntryId = null;
  state.forceNewPaste = true;
  renderEntryPanel();
}

// ── Finalize & Generate PDF ──────────────────────────────────────
function renderFinalizePanel() {
  const panel = document.getElementById("finalize-panel");
  const b = state.batch;
  const done = state.municipalities.filter((m) => m.entry_count > 0).length;
  const total = state.municipalities.length;

  synopsisCooldown.stop();
  weatherCooldown.stop();

  if (b.status === "FINALIZED" && b.is_locked) {
    const when = b.finalized_at ? new Date(b.finalized_at).toLocaleString() : "";
    panel.innerHTML = `
      <h2>Finalize &amp; Generate PDF</h2>
      <div class="alert alert-success">
        This batch (${escapeHtml(b.date)} · ${escapeHtml(b.shift)}) was finalized${when ? " on " + escapeHtml(when) : ""}.
        No further entries can be added or edited for this batch.
      </div>
      <div class="actions">
        <a class="btn btn-primary" href="${API.download(b.id)}" target="_blank" rel="noopener">Download PDF</a>
        <button type="button" id="amend-btn" class="btn btn-warning">Amend This Batch</button>
      </div>
      <div id="amend-form-container"></div>
    `;
    return;
  }

  // Not locked: either a normal DRAFT batch, or a FINALIZED batch that
  // was amended more recently than it was last finalized (see
  // ManualBatch.is_locked) — same editable form either way, with a
  // banner added on top for the amended case.
  const amendedBanner = (b.status === "FINALIZED" && b.amended_at)
    ? `<div class="alert alert-warning">
        This finalized batch was amended on ${escapeHtml(new Date(b.amended_at).toLocaleString())}
        — you are editing the current version.
        <a href="${API.download(b.id)}" target="_blank" rel="noopener">Download the current PDF</a>
      </div>`
    : "";

  panel.innerHTML = `
    <h2>Finalize &amp; Generate PDF</h2>
    ${amendedBanner}
    <p class="finalize-progress"><span id="finalize-progress">${done} / ${total}</span> municipalities reporting</p>
    <label class="finalize-field">
      <span class="finalize-field-header">
        <span>Synopsis</span>
        <button type="button" id="generate-synopsis-btn" class="btn btn-small" data-action="generate-synopsis">Generate Draft</button>
      </span>
      <textarea data-finalize-field="synopsis" rows="3" placeholder="Province-wide narrative summary of this period's incidents…">${escapeHtml(state.finalizeForm.synopsis)}</textarea>
    </label>
    <div id="synopsis-cooldown-notice"></div>
    <div id="synopsis-error"></div>
    <label class="finalize-field">
      <span class="finalize-field-header">
        <span>Weather Conditions</span>
        <button type="button" id="generate-weather-btn" class="btn btn-small" data-action="generate-weather">Generate Draft</button>
      </span>
      <textarea data-finalize-field="weather_conditions" rows="2" placeholder="e.g. Partly cloudy, isolated rain showers, no tropical cyclone within PAR…">${escapeHtml(state.finalizeForm.weather_conditions)}</textarea>
    </label>
    <div id="weather-cooldown-notice"></div>
    <div id="weather-error"></div>
    <label class="finalize-field">Actions Taken (EOC-level)
      <textarea data-finalize-field="actions_taken" rows="3" placeholder="Province-level coordination and actions…">${escapeHtml(state.finalizeForm.actions_taken)}</textarea>
    </label>
    <div class="actions">
      <button type="button" id="finalize-btn" class="btn btn-primary">Finalize &amp; Generate PDF</button>
    </div>
    <div id="finalize-message"></div>
  `;

  checkAiCooldown().then((secs) => {
    if (secs > 0) synopsisCooldown.start(secs);
  });
  checkAiCooldown().then((secs) => {
    if (secs > 0) weatherCooldown.start(secs);
  });
}

// ── Amend a finalized batch ────────────────────────────────────────
// A real text input for the reason, not window.prompt() — the reason is
// substantive content (it ends up permanently on the PDF), not a yes/no
// confirmation, so it gets the same inline-form treatment as every other
// editable field in this app rather than a browser dialog.
function renderAmendForm() {
  const container = document.getElementById("amend-form-container");
  if (!container) return;
  container.innerHTML = `
    <div class="amend-form">
      <label class="finalize-field">Amendment Reason
        <textarea id="amend-reason-input" rows="2" placeholder="Why is this finalized batch being amended?"></textarea>
      </label>
      <div class="actions">
        <button type="button" id="amend-submit-btn" class="btn btn-primary">Submit Amendment</button>
        <button type="button" id="amend-cancel-btn" class="btn">Cancel</button>
      </div>
      <div id="amend-error"></div>
    </div>
  `;
  document.getElementById("amend-reason-input").focus();
}

async function handleAmendSubmit() {
  const textarea = document.getElementById("amend-reason-input");
  const errBox = document.getElementById("amend-error");
  const reason = textarea.value.trim();
  errBox.innerHTML = "";

  if (!reason) {
    errBox.innerHTML = `<div class="alert alert-error">Enter a reason for this amendment.</div>`;
    return;
  }

  const submitBtn = document.getElementById("amend-submit-btn");
  submitBtn.disabled = true;
  submitBtn.textContent = "Submitting…";

  try {
    const result = await apiFetch(API.amend(state.batch.id), {
      method: "POST",
      body: JSON.stringify({ reason }),
    });
    state.batch = result;
    // The batch's synopsis/weather/actions are whatever was frozen at
    // its last finalize — seed the now-reopened form from that instead
    // of leaving it blank (see loadCurrentBatch's own seeding for why).
    state.finalizeForm = {
      synopsis: result.synopsis || "",
      weather_conditions: result.weather_conditions || "",
      actions_taken: result.actions_taken || "",
    };
    renderChecklist();
    renderEntryPanel();
    renderFinalizePanel();
    updateBatchStatusText();
  } catch (err) {
    errBox.innerHTML = `<div class="alert alert-error">${escapeHtml(err.message)}</div>`;
    submitBtn.disabled = false;
    submitBtn.textContent = "Submit Amendment";
  }
}

function handleFinalizeFieldChange(e) {
  const el = e.target;
  if (!el.dataset.finalizeField) return;
  state.finalizeForm[el.dataset.finalizeField] = el.value;
}

async function handleFinalize() {
  const msgBox = document.getElementById("finalize-message");
  msgBox.innerHTML = "";

  const confirmed = window.confirm(
    "Finalize this batch and generate the combined PDF?\n\n" +
    "This cannot be undone from this page — no further entries can be added or edited " +
    "for this batch once finalized."
  );
  if (!confirmed) return;

  const btn = document.getElementById("finalize-btn");
  btn.disabled = true;
  btn.textContent = "Generating…";

  try {
    const result = await apiFetch(API.finalize(state.batch.id), {
      method: "POST",
      body: JSON.stringify({
        synopsis: state.finalizeForm.synopsis,
        weather_conditions: state.finalizeForm.weather_conditions,
        actions_taken: state.finalizeForm.actions_taken,
      }),
    });
    state.batch = result;
    // The batch is now locked — drop any in-progress editable form so
    // nothing stale is left on screen.
    state.selected = null;
    state.extraction = null;
    state.originalExtraction = null;
    renderChecklist();
    renderEntryPanel();
    renderFinalizePanel();
    updateBatchStatusText();
  } catch (err) {
    msgBox.innerHTML = renderSaveError(err);
    btn.disabled = false;
    btn.textContent = "Finalize & Generate PDF";
  }
}

async function handleGenerateSynopsis() {
  const textarea = document.querySelector('[data-finalize-field="synopsis"]');
  if (!textarea) return;

  if (textarea.value.trim() !== "") {
    const confirmed = window.confirm("Replace your current synopsis with an AI-generated draft?");
    if (!confirmed) return;
  }

  await attemptGenerateSynopsis(textarea, /* allowAutoRetry */ true);
}

// Split out from handleGenerateSynopsis so a 503 (rate-limited — see
// ai.RateLimitedError) can trigger exactly one automatic retry once its
// cooldown elapses, without re-running the confirm() dialog a second
// time. allowAutoRetry=false on the retry call itself, so a second 503
// in a row just shows the new cooldown normally instead of chaining
// another automatic attempt.
async function attemptGenerateSynopsis(textarea, allowAutoRetry) {
  const errBox = document.getElementById("synopsis-error");
  const notice = document.getElementById("synopsis-cooldown-notice");
  if (errBox) errBox.innerHTML = "";
  if (notice) notice.innerHTML = "";

  const btn = document.getElementById("generate-synopsis-btn");
  btn.disabled = true;
  btn.textContent = "Generating…";

  let rateLimitedSeconds = null;
  try {
    const result = await apiFetch(API.generateSynopsis(state.batch.id), { method: "POST" });
    textarea.value = result.synopsis;
    state.finalizeForm.synopsis = result.synopsis;
  } catch (err) {
    if (err.status === 503 && err.data && typeof err.data.retry_after_seconds === "number") {
      rateLimitedSeconds = err.data.retry_after_seconds;
    } else if (errBox) {
      errBox.innerHTML = `<div class="alert alert-error">${escapeHtml(err.message)}</div>`;
    }
  }

  btn.textContent = "Generate Draft";

  if (rateLimitedSeconds !== null) {
    // Rate-limited, not a real failure — the server fails fast on
    // purpose now instead of blocking its one worker thread. Absorb
    // this into the existing cooldown UI rather than showing an error:
    // show the countdown, then retry automatically once it elapses, so
    // from OPS's perspective this reads as "took a bit" rather than
    // "failed".
    if (allowAutoRetry) {
      synopsisCooldown.start(rateLimitedSeconds, () => attemptGenerateSynopsis(textarea, false));
    } else {
      synopsisCooldown.start(rateLimitedSeconds);
    }
    return;
  }

  // Re-check rather than just re-enabling — the call just made may itself
  // have pushed the shared Groq rate limit into a cooldown state.
  const secs = await checkAiCooldown();
  if (secs > 0) {
    synopsisCooldown.start(secs);
  } else {
    btn.disabled = false;
  }
}

async function handleGenerateWeather() {
  const textarea = document.querySelector('[data-finalize-field="weather_conditions"]');
  if (!textarea) return;

  if (textarea.value.trim() !== "") {
    const confirmed = window.confirm("Replace your current weather conditions with an AI-generated draft?");
    if (!confirmed) return;
  }

  await attemptGenerateWeather(textarea, /* allowAutoRetry */ true);
}

// Same shape as attemptGenerateSynopsis — see its comment.
async function attemptGenerateWeather(textarea, allowAutoRetry) {
  const errBox = document.getElementById("weather-error");
  const notice = document.getElementById("weather-cooldown-notice");
  if (errBox) errBox.innerHTML = "";
  if (notice) notice.innerHTML = "";

  const btn = document.getElementById("generate-weather-btn");
  btn.disabled = true;
  btn.textContent = "Generating…";

  let rateLimitedSeconds = null;
  try {
    const result = await apiFetch(API.generateWeather(state.batch.id), { method: "POST" });
    textarea.value = result.weather_condition;
    state.finalizeForm.weather_conditions = result.weather_condition;
  } catch (err) {
    if (err.status === 503 && err.data && typeof err.data.retry_after_seconds === "number") {
      rateLimitedSeconds = err.data.retry_after_seconds;
    } else if (errBox) {
      errBox.innerHTML = `<div class="alert alert-error">${escapeHtml(err.message)}</div>`;
    }
  }

  btn.textContent = "Generate Draft";

  if (rateLimitedSeconds !== null) {
    if (allowAutoRetry) {
      weatherCooldown.start(rateLimitedSeconds, () => attemptGenerateWeather(textarea, false));
    } else {
      weatherCooldown.start(rateLimitedSeconds);
    }
    return;
  }

  const secs = await checkAiCooldown();
  if (secs > 0) {
    weatherCooldown.start(secs);
  } else {
    btn.disabled = false;
  }
}

// ── Value reading for inputs ─────────────────────────────────────
function readInputValue(el) {
  if (el.type === "checkbox") return el.checked;
  if (el.type === "number") {
    if (el.value === "") return el.dataset.nullable === "true" ? null : 0;
    return el.dataset.float === "true" ? parseFloat(el.value) : parseInt(el.value, 10);
  }
  return el.value;
}

function handleFieldChange(e) {
  const el = e.target;
  if (!el.dataset.field) return;
  const section = el.dataset.section;
  const field = el.dataset.field;
  const value = readInputValue(el);

  if (section === "unmapped_notes") {
    state.extraction.unmapped_notes = value;
  } else if (section === "weather_condition") {
    state.extraction.weather_condition = value;
  } else if (section === "lifelines_status") {
    state.extraction.lifelines_status[field] = value;
  } else {
    const idx = parseInt(el.dataset.index, 10);
    if (el.dataset.victimIndex !== undefined) {
      const vIdx = parseInt(el.dataset.victimIndex, 10);
      state.extraction[section][idx].victims[vIdx][field] = value;
    } else {
      state.extraction[section][idx][field] = value;
    }
  }
  refreshValidation();
}

// ── Structural edits (add/remove cards & victims) ────────────────
function handleAddItem(section) {
  state.extraction[section] = state.extraction[section] || [];
  state.extraction[section].push(INCIDENT_TYPES[section].blank());
  renderEntryPanel();
}
function handleRemoveItem(section, idx) {
  state.extraction[section].splice(idx, 1);
  renderEntryPanel();
}
function handleAddVictim(section, idx) {
  const item = state.extraction[section][idx];
  item.victims = item.victims || [];
  item.victims.push(INCIDENT_TYPES[section].victims.blank());
  renderEntryPanel();
}
function handleRemoveVictim(section, idx, vIdx) {
  state.extraction[section][idx].victims.splice(vIdx, 1);
  renderEntryPanel();
}

// ── Proactive AI rate-limit cooldown ─────────────────────────────
// Mirrors ai.py's own reactive cooldown: rather than letting OPS click
// into a call that the backend already knows would just wait (or 429),
// check /api/ai-status/ before showing an AI-triggering button as
// clickable, and count down visibly if it isn't yet. "Process with AI",
// synopsis's "Generate Draft", and weather's "Generate Draft" all hit
// the same Groq account/rate limit, but they live in separate,
// independently-rendered spots on the page — each of the three gets its
// own controller instance (own timer, own button/notice) from this one
// factory rather than three copy-pasted countdown implementations.
function formatCountdown(seconds) {
  const s = Math.max(0, Math.ceil(seconds));
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return `${m}:${String(rem).padStart(2, "0")}`;
}

async function checkAiCooldown() {
  try {
    const data = await apiFetch(window.QUICKSITREP.endpoints.aiStatus);
    return data.cooldown_seconds || 0;
  } catch (err) {
    return 0; // fail open — a status-check hiccup shouldn't block OPS
  }
}

// Real incident (client alpha-test, local testing 2026-08-27): sustained
// account-level Groq contention meant /api/ai-status/ kept reporting a
// nonzero cooldown indefinitely. The old start() recursed on every
// recheck with no cap, so it just kept re-arming the same countdown
// forever, polling ai-status at whatever (often near-zero) interval the
// server happened to report, with no backoff growth and no path that
// ever gave up and told the user something was actually wrong. Confirmed
// via a controlled reproduction: 9 ai-status polls in 20 real seconds,
// zero progress, button stuck disabled forever.
const MAX_COOLDOWN_RECHECKS = 4; // up to 4 rechecks past the first (5 checkAiCooldown calls total) before giving up
const RECHECK_BACKOFF_BASE_SECONDS = 3;
const RECHECK_BACKOFF_MAX_SECONDS = 60;

function createCooldownController(getBtn, getNotice) {
  let timer = null;

  function stop() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
  }

  // onComplete: optional. Called instead of the default "just re-enable
  // the button" behavior once the countdown genuinely elapses (confirmed
  // by re-checking the server, not just the local clock). Used for the
  // 503-triggered countdowns (Process with AI, both Generate Draft
  // buttons) to automatically retry the request that got rate-limited,
  // exactly once — the proactive, pre-click countdown (started after
  // every render from /api/ai-status/) never passes this, since nothing
  // was actually attempted yet for it to retry. This "exactly once"
  // behavior is unaffected by recheckAttempt below — that's a separate
  // cap on the recheck-the-server-clock loop, not on how many times the
  // actual request itself gets retried (see attemptProcess's own
  // allowAutoRetry flag for that).
  //
  // recheckAttempt: internal only — external callers never pass this, so
  // it always starts at 0. Incremented on each "still not clear" recheck;
  // once it reaches MAX_COOLDOWN_RECHECKS, this gives up instead of
  // recursing again — see the hard-fail branch below.
  function start(initialSeconds, onComplete, recheckAttempt = 0) {
    stop();
    let remaining = initialSeconds;

    const tick = () => {
      const btn = getBtn();
      const notice = getNotice();
      if (!btn || !notice) {
        stop();
        return;
      }
      if (remaining <= 0) {
        stop();
        // Re-check with the server rather than just trusting the local
        // clock — it was seeded from a real value, but only once.
        checkAiCooldown().then((secs) => {
          if (secs <= 0) {
            if (onComplete) {
              onComplete();
            } else {
              const b = getBtn();
              const n = getNotice();
              if (b) b.disabled = false;
              if (n) n.innerHTML = "";
            }
            return;
          }
          if (recheckAttempt >= MAX_COOLDOWN_RECHECKS) {
            // Give up: a real, clear error instead of an endless silent
            // loop. Button re-enabled so OPS isn't stuck.
            const b = getBtn();
            const n = getNotice();
            if (b) b.disabled = false;
            if (n) {
              n.innerHTML = `<div class="alert alert-error">AI extraction is currently unavailable — please try again in a few minutes, or enter this report manually.</div>`;
            }
            return;
          }
          // Modestly growing backoff between rechecks, not a flat
          // interval tied purely to whatever (often near-zero) value the
          // server happens to report — avoids hammering /api/ai-status/
          // at a fast fixed cadence while the underlying condition isn't
          // actually clearing.
          const backoff = Math.min(
            RECHECK_BACKOFF_MAX_SECONDS,
            Math.max(secs, RECHECK_BACKOFF_BASE_SECONDS * Math.pow(2, recheckAttempt))
          );
          start(backoff, onComplete, recheckAttempt + 1);
        });
        return;
      }
      btn.disabled = true;
      notice.innerHTML = `<div class="alert alert-warning">AI cooling down — ready in ${formatCountdown(remaining)}</div>`;
      remaining -= 1;
    };

    tick();
    timer = setInterval(tick, 1000);
  }

  return { start, stop };
}

const processCooldown = createCooldownController(
  () => document.querySelector('[data-action="process"]'),
  () => document.getElementById("ai-cooldown-notice")
);
const synopsisCooldown = createCooldownController(
  () => document.getElementById("generate-synopsis-btn"),
  () => document.getElementById("synopsis-cooldown-notice")
);
const weatherCooldown = createCooldownController(
  () => document.getElementById("generate-weather-btn"),
  () => document.getElementById("weather-cooldown-notice")
);

// ── Process with AI ──────────────────────────────────────────────
async function handleProcess() {
  const textarea = document.getElementById("raw-text-input");
  const rawText = textarea.value.trim();
  const errBox = document.getElementById("process-error");
  errBox.innerHTML = "";

  if (!rawText) {
    errBox.innerHTML = `<div class="alert alert-error">Paste the report text first.</div>`;
    return;
  }

  await attemptProcess(rawText, /* allowAutoRetry */ true);
}

// Split out from handleProcess so a 503 (rate-limited — see
// ai.RateLimitedError; the server fails fast now instead of blocking its
// one worker thread on a Groq rate limit) can trigger exactly one
// automatic retry once its cooldown elapses. allowAutoRetry=false on the
// retry call itself, so a second 503 in a row just shows the new
// cooldown normally (button re-enables when it clears) instead of
// chaining another automatic attempt — OPS clicks again from there.
async function attemptProcess(rawText, allowAutoRetry) {
  const errBox = document.getElementById("process-error");
  const notice = document.getElementById("ai-cooldown-notice");
  errBox.innerHTML = "";
  if (notice) notice.innerHTML = "";

  const btn = document.querySelector('[data-action="process"]');
  btn.disabled = true;
  btn.textContent = "Processing with AI…";

  // A single request taking noticeably longer than a normal extraction
  // is the only signal we have that Groq itself is just slow right now
  // (the server no longer retries a 429 in-request — see
  // ai.RateLimitedError — so this is ordinary latency, not a hidden
  // retry loop).
  const busyTimer = setTimeout(() => {
    btn.textContent = "This is taking a while…";
  }, 3000);

  try {
    const result = await apiFetch(window.QUICKSITREP.endpoints.extract, {
      method: "POST",
      body: JSON.stringify({ municipality: state.selected, raw_text: rawText }),
    });
    state.rawText = rawText;
    state.originalExtraction = JSON.parse(JSON.stringify(result));
    state.extraction = normalizeExtraction(result);
    renderEntryPanel();
  } catch (err) {
    if (err.status === 503 && err.data && typeof err.data.retry_after_seconds === "number") {
      // Rate-limited, not a real failure — absorb it into the existing
      // cooldown UI instead of showing an error: show the countdown,
      // then retry automatically once it elapses, so from OPS's
      // perspective this reads as "took a bit" rather than "failed".
      btn.textContent = "Process with AI";
      if (allowAutoRetry) {
        processCooldown.start(err.data.retry_after_seconds, () => attemptProcess(rawText, false));
      } else {
        processCooldown.start(err.data.retry_after_seconds);
      }
    } else {
      errBox.innerHTML = `<div class="alert alert-error">${escapeHtml(err.message)}</div>`;
      btn.disabled = false;
      btn.textContent = "Process with AI";
    }
  } finally {
    clearTimeout(busyTimer);
  }
}

// ── Save ──────────────────────────────────────────────────────────
function stripUiKeys(item) {
  const out = {};
  for (const k of Object.keys(item)) {
    if (k.startsWith("_")) continue;
    out[k] = k === "victims" ? item.victims.map(stripUiKeys) : item[k];
  }
  return out;
}

function buildEditedJson() {
  const clone = JSON.parse(JSON.stringify(state.extraction));
  for (const key of Object.keys(INCIDENT_TYPES)) {
    clone[key] = (clone[key] || []).map(stripUiKeys);
  }
  if (!clone.lifelines_status || Object.keys(clone.lifelines_status).length === 0) {
    clone.lifelines_status = {};
  }
  return clone;
}

function renderSaveError(err) {
  const data = err.data || {};
  let html = `<div class="alert alert-error"><strong>${escapeHtml(data.detail || err.message)}</strong>`;
  if (data.errors) {
    html += "<ul>";
    for (const [section, val] of Object.entries(data.errors)) {
      html += `<li><strong>${escapeHtml(section)}:</strong> ${escapeHtml(JSON.stringify(val))}</li>`;
    }
    html += "</ul>";
  }
  html += "</div>";
  return html;
}

async function handleSave() {
  const msgBox = document.getElementById("save-message");
  const saveBtn = document.getElementById("save-entry-btn");
  msgBox.innerHTML = "";
  saveBtn.disabled = true;
  saveBtn.textContent = "Saving…";

  try {
    const result = await apiFetch(window.QUICKSITREP.endpoints.save, {
      method: "POST",
      body: JSON.stringify({
        batch_id: state.batch.id,
        municipality: state.selected,
        raw_text: state.rawText,
        ai_output: state.originalExtraction,
        edited_json: buildEditedJson(),
        entry_id: state.reopenedEntryId,
      }),
    });
    // A fresh (not-yet-reopened) save just created a brand new entry —
    // without recording its id here, clicking Save again on this same
    // still-open form would omit entry_id and create ANOTHER duplicate
    // entry rather than updating the one just saved (unlike before this
    // feature, one entry per municipality made every save idempotent by
    // construction; that's no longer true once multiple entries per
    // municipality are allowed).
    state.reopenedEntryId = result.entry.id;
    await loadCurrentBatch();
    renderChecklist();
    renderSummaryCards();
    updateBatchStatusText();
    renderEntryPanel();
    document.getElementById("save-message").innerHTML =
      `<div class="alert alert-success">Saved — entry status: ${result.entry.status}.</div>`;
  } catch (err) {
    msgBox.innerHTML = renderSaveError(err);
  } finally {
    saveBtn.textContent = "Save Entry";
    refreshValidation();
  }
}

// ── Wiring ───────────────────────────────────────────────────────
function initEventListeners() {
  document.getElementById("checklist").addEventListener("click", (e) => {
    const btn = e.target.closest("[data-muni]");
    if (btn) selectMunicipality(btn.dataset.muni);
  });

  const panel = document.getElementById("entry-panel");
  panel.addEventListener("input", handleFieldChange);
  panel.addEventListener("change", handleFieldChange);

  panel.addEventListener("click", (e) => {
    const entryItem = e.target.closest("[data-entry-id]");
    if (entryItem) return openEntryForEdit(parseInt(entryItem.dataset.entryId, 10));

    const btn = e.target.closest("[data-action]");
    if (!btn) return;
    const action = btn.dataset.action;

    if (action === "process") return handleProcess();
    if (action === "save") return handleSave();
    if (action === "add-report") return handleAddAnotherReport();
    if (action === "discard") {
      // Fresh (not-yet-saved) paste — stays in "add another" mode if
      // that's how OPS got here, so re-pasting doesn't dump them back
      // out to the list they explicitly chose to leave.
      state.extraction = null;
      state.originalExtraction = null;
      renderEntryPanel();
      return;
    }
    if (action === "cancel-reopen" || action === "back-to-list") {
      // Both land on the same place: this municipality's entry list (if
      // it has any — it does, since only that state has these actions
      // available) rather than deselecting the municipality entirely.
      state.extraction = null;
      state.originalExtraction = null;
      state.reopenedEntryId = null;
      state.forceNewPaste = false;
      renderEntryPanel();
      return;
    }
    if (action === "add-item") return handleAddItem(btn.dataset.section);
    if (action === "remove-item") return handleRemoveItem(btn.dataset.section, parseInt(btn.dataset.index, 10));
    if (action === "add-victim") return handleAddVictim(btn.dataset.section, parseInt(btn.dataset.index, 10));
    if (action === "remove-victim") {
      return handleRemoveVictim(btn.dataset.section, parseInt(btn.dataset.index, 10), parseInt(btn.dataset.victimIndex, 10));
    }
    if (action === "enable-lifelines") {
      state.extraction.lifelines_status = blankLifelines();
      renderEntryPanel();
      return;
    }
    if (action === "disable-lifelines") {
      state.extraction.lifelines_status = {};
      renderEntryPanel();
      return;
    }
  });

  const finalizePanel = document.getElementById("finalize-panel");
  finalizePanel.addEventListener("input", handleFinalizeFieldChange);
  finalizePanel.addEventListener("click", (e) => {
    if (e.target.closest("#finalize-btn")) return handleFinalize();
    if (e.target.closest("#generate-synopsis-btn")) return handleGenerateSynopsis();
    if (e.target.closest("#generate-weather-btn")) return handleGenerateWeather();
    if (e.target.closest("#amend-btn")) return renderAmendForm();
    if (e.target.closest("#amend-cancel-btn")) {
      document.getElementById("amend-form-container").innerHTML = "";
      return;
    }
    if (e.target.closest("#amend-submit-btn")) return handleAmendSubmit();
  });
}

async function loadCurrentBatch() {
  const batchId = getRequestedBatchId();
  const url = batchId
    ? `${window.QUICKSITREP.endpoints.currentBatch}?batch_id=${batchId}`
    : window.QUICKSITREP.endpoints.currentBatch;
  const data = await apiFetch(url);
  state.batch = data.batch;
  state.municipalities = data.municipalities;
  state.summary = data.summary;
  // Seeds the finalize form from whatever's actually saved on the batch
  // (blank for a fresh DRAFT batch, same as before; the batch's real
  // frozen text for an amended/reopened FINALIZED one) rather than
  // always starting blank.
  state.finalizeForm = {
    synopsis: data.batch.synopsis || "",
    weather_conditions: data.batch.weather_conditions || "",
    actions_taken: data.batch.actions_taken || "",
  };
}

async function init() {
  applySchema(window.QUICKSITREP.schema);
  initEventListeners();
  try {
    await loadCurrentBatch();
    renderChecklist();
    renderSummaryCards();
    updateBatchStatusText();
    renderEntryPanel();
    renderFinalizePanel();
  } catch (err) {
    document.getElementById("batch-status").textContent = "Failed to load batch — " + err.message;
  }
}

init();
