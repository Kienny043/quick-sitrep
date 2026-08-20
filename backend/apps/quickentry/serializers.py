"""
Quick SitRep — serializers.

Style mirrors backend/apps/ops/serializers.py's incident serializers on
purpose (entry/submission via extra_kwargs required=False + injected in
.save(); nested victims via create()/update() overrides) so this stays a
field-mapping exercise, not a redesign, when it eventually merges into the
main system. See docs/quick-report-entry-spec.md Section 3 and 8.
"""

from rest_framework import serializers

from .models import (
    MUNICIPALITY_CHOICES,
    ManualBatch,
    ManualEntry,
    RoadCrash,
    RoadCrashVictim,
    MedicalAssistance,
    FireIncident,
    WaterIncident,
    TraumaEmergency,
    LifelinesStatus,
)


class NullToBlankMixin:
    """
    The AI extraction schema uses JSON null for "nothing here" on several
    text fields (e.g. "driver": "string or null"), but Django
    CharField/TextField with blank=True and no null=True only accepts
    omission or "" for that — passing None trips a confusing "This field
    may not be null." for a field that's genuinely optional at the model
    layer. Coerce None -> "" for the fields listed in
    null_to_blank_fields before validation runs.

    Deliberately NOT applied to fields that should stay strictly
    required (barangay, RoadCrash/FireIncident's cause) — those must
    keep raising a clear required-field error instead of silently
    becoming blank.
    """
    null_to_blank_fields = ()

    def to_internal_value(self, data):
        if self.null_to_blank_fields and isinstance(data, dict):
            data = dict(data)
            for field in self.null_to_blank_fields:
                if field in data and data[field] is None:
                    data[field] = ""
        return super().to_internal_value(data)


# ═══════════════════════════════════════════════════════════
# BATCH / ENTRY (read/summary use)
# ═══════════════════════════════════════════════════════════

class ManualBatchSerializer(serializers.ModelSerializer):
    # Computed from the model's is_locked property rather than left for
    # the frontend to re-derive from status/amended_at/finalized_at —
    # same "one implementation, not two that could drift" reasoning as
    # compute_summary()/get_incident_schema() elsewhere in this app.
    is_locked = serializers.BooleanField(read_only=True)

    class Meta:
        model = ManualBatch
        fields = [
            "id", "date", "shift", "status", "is_locked",
            "finalized_at", "finalized_by",
            "amended_at", "amended_by", "amendment_reason",
            # Included so an amended (reopened) batch's finalize form can
            # be pre-filled from what was actually frozen last time,
            # rather than showing blank textareas over real saved text —
            # previously unneeded since a DRAFT batch never has these set.
            "synopsis", "weather_conditions", "actions_taken",
        ]
        read_only_fields = fields


class ManualEntrySummarySerializer(serializers.ModelSerializer):
    municipality_name = serializers.CharField(
        source="get_municipality_display", read_only=True
    )

    class Meta:
        model = ManualEntry
        fields = [
            "id", "municipality", "municipality_name", "status",
            "processed_by", "processed_at",
        ]
        read_only_fields = fields


# ═══════════════════════════════════════════════════════════
# TOP-LEVEL REQUEST ENVELOPES
# ═══════════════════════════════════════════════════════════

class ExtractRequestSerializer(serializers.Serializer):
    municipality = serializers.ChoiceField(choices=MUNICIPALITY_CHOICES)
    raw_text = serializers.CharField(allow_blank=False)


class EntrySaveRequestSerializer(serializers.Serializer):
    batch_id = serializers.IntegerField()
    municipality = serializers.ChoiceField(choices=MUNICIPALITY_CHOICES)
    raw_text = serializers.CharField(allow_blank=False)
    edited_json = serializers.JSONField()
    # Present when editing a specific already-saved entry (the re-open
    # flow — GET /api/entries/<id>/, edit, save back to that same row).
    # Omitted/null for a fresh paste, which always creates a brand new
    # ManualEntry now that a municipality can have more than one per
    # batch — see views.save_entry.
    entry_id = serializers.IntegerField(required=False, allow_null=True)
    # Not in the spec's literal request body, but needed to actually honor
    # ManualEntry.ai_output's contract ("raw AI extraction result, kept
    # as-is even after human edits, for audit comparison") — see the
    # note in views.py. Optional: falls back to edited_json if the
    # frontend doesn't send it, so the literal spec shape still works.
    ai_output = serializers.JSONField(required=False)


class FinalizeBatchRequestSerializer(serializers.Serializer):
    synopsis = serializers.CharField(required=False, allow_blank=True, default="")
    weather_conditions = serializers.CharField(required=False, allow_blank=True, default="")
    actions_taken = serializers.CharField(required=False, allow_blank=True, default="")


class AmendBatchRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(allow_blank=False, trim_whitespace=True)


# ═══════════════════════════════════════════════════════════
# INCIDENT SERIALIZERS
# Field lists match the models exactly (Step 2), which in turn mirror
# apps/ops/models.py. `entry` is excluded from required input — the view
# injects it via serializer.save(entry=entry) after validation, same
# pattern as the main system's `submission` FK.
# ═══════════════════════════════════════════════════════════

class RoadCrashVictimSerializer(NullToBlankMixin, serializers.ModelSerializer):
    null_to_blank_fields = ("sex", "address")

    class Meta:
        model = RoadCrashVictim
        fields = ["id", "age", "sex", "address", "injuries", "injury_classification"]
        read_only_fields = ["id"]


class RoadCrashSerializer(NullToBlankMixin, serializers.ModelSerializer):
    null_to_blank_fields = ("driver", "ert_members")
    victims = RoadCrashVictimSerializer(many=True, required=False)

    class Meta:
        model = RoadCrash
        fields = [
            "id", "entry", "datetime", "location", "barangay",
            "latitude", "longitude", "cause", "vehicles_involved",
            "actions_taken", "responding_team", "driver", "ert_members",
            "created_at", "victims",
        ]
        read_only_fields = ["id", "created_at"]
        extra_kwargs = {"entry": {"required": False}}

    def _save_victims(self, road_crash, data):
        road_crash.victims.all().delete()
        for v in data:
            RoadCrashVictim.objects.create(road_crash=road_crash, **v)

    def create(self, validated_data):
        victims_data = validated_data.pop("victims", [])
        road_crash = RoadCrash.objects.create(**validated_data)
        self._save_victims(road_crash, victims_data)
        return road_crash

    def update(self, instance, validated_data):
        victims_data = validated_data.pop("victims", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        if victims_data is not None:
            self._save_victims(instance, victims_data)
        return instance


class MedicalAssistanceSerializer(NullToBlankMixin, serializers.ModelSerializer):
    null_to_blank_fields = (
        "patient_sex", "patient_address", "chief_complaint",
        "blood_pressure", "pulse_rate", "spo2", "temperature",
        "driver", "ert_members",
    )

    class Meta:
        model = MedicalAssistance
        fields = [
            "id", "entry", "datetime", "location", "barangay",
            "latitude", "longitude", "patient_age", "patient_sex",
            "patient_address", "nature_of_illness", "chief_complaint",
            "blood_pressure", "pulse_rate", "spo2", "temperature",
            "actions_taken", "responding_team", "driver", "ert_members",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]
        extra_kwargs = {"entry": {"required": False}}


class FireIncidentSerializer(serializers.ModelSerializer):
    class Meta:
        model = FireIncident
        fields = [
            "id", "entry", "datetime", "location", "barangay",
            "latitude", "longitude",
            # cause: no blank=True on the model, so DRF marks it
            # required=True automatically — same treatment as
            # RoadCrash.cause, a missing/null value here surfaces as a
            # normal per-field 400, not a DB error.
            "cause",
            "response_time_minutes", "distance_km", "structure_type",
            "families_affected", "individuals_affected", "structures_burned",
            "fire_area_sqm", "casualties", "injured", "fatalities",
            "responding_team", "actions_taken", "created_at",
        ]
        read_only_fields = ["id", "created_at"]
        extra_kwargs = {"entry": {"required": False}}


class WaterIncidentSerializer(NullToBlankMixin, serializers.ModelSerializer):
    null_to_blank_fields = ("description",)

    class Meta:
        model = WaterIncident
        fields = [
            "id", "entry", "datetime", "location", "barangay",
            "latitude", "longitude", "incident_type", "description",
            "families_affected", "individuals_affected", "casualties",
            "injured", "fatalities", "responding_team", "actions_taken",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]
        extra_kwargs = {"entry": {"required": False}}


class TraumaEmergencySerializer(NullToBlankMixin, serializers.ModelSerializer):
    null_to_blank_fields = (
        "patient_sex", "patient_address", "chief_complaint",
        "driver", "ert_members",
    )

    class Meta:
        model = TraumaEmergency
        fields = [
            "id", "entry", "datetime", "location", "barangay",
            "latitude", "longitude", "call_type", "patient_age",
            "patient_sex", "patient_address", "chief_complaint",
            "actions_taken", "responding_team", "driver", "ert_members",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]
        extra_kwargs = {"entry": {"required": False}}


class LifelinesStatusSerializer(NullToBlankMixin, serializers.ModelSerializer):
    # Schema marks these plain "string" (not nullable), but the model
    # itself doesn't guarantee that, so coercing defensively costs
    # nothing and matches the other incident serializers' behavior.
    null_to_blank_fields = (
        "power_supply_notes", "water_supply_notes", "communication_notes",
        "road_notes", "sea_notes", "class_suspension_notes",
    )

    # The AI extraction schema (ai.py SYSTEM_PROMPT / spec Section 4) uses
    # "power_supply_notes" / "water_supply_notes" as key names, but the
    # model (mirroring apps/ops/models.py exactly) uses "power_notes" /
    # "water_notes". Bridging that naming mismatch here via source=, so
    # the API accepts the schema's actual key names while still writing
    # to the correctly-named model fields.
    power_supply_notes = serializers.CharField(
        source="power_notes", required=False, allow_blank=True
    )
    water_supply_notes = serializers.CharField(
        source="water_notes", required=False, allow_blank=True
    )

    class Meta:
        model = LifelinesStatus
        fields = [
            "id", "entry",
            "power_supply", "power_supply_notes",
            "water_supply", "water_supply_notes",
            "communication", "communication_notes",
            "road_condition", "road_notes",
            "sea_travel", "sea_notes",
            "class_suspension", "class_suspension_notes",
        ]
        read_only_fields = ["id"]
        extra_kwargs = {"entry": {"required": False}}


# Serializer classes keyed by the JSON schema's list names — used by
# views.save_entry to validate each incident type generically.
INCIDENT_SERIALIZERS = {
    "road_crashes": RoadCrashSerializer,
    "medical_assistance": MedicalAssistanceSerializer,
    "fire_incidents": FireIncidentSerializer,
    "water_incidents": WaterIncidentSerializer,
    "trauma_emergencies": TraumaEmergencySerializer,
}

# Fields that exist on the serializer but aren't part of the AI
# extraction schema / editable form — never worth reporting as
# "required" to the frontend even though DRF may say required=False
# for them anyway (they're read-only or injected server-side).
_ADMIN_FIELDS = {"id", "entry", "created_at", "victims"}


def _required_map(serializer_class):
    """{field_name: bool} for one serializer's required, non-admin fields."""
    instance = serializer_class()
    return {
        name: bool(field.required)
        for name, field in instance.fields.items()
        if name not in _ADMIN_FIELDS and not field.read_only
    }


def get_incident_schema():
    """
    Required-field metadata for every incident type, victims, and
    lifelines — derived live from the same serializers /save/ validates
    against (see the introspection in the shell that confirmed this:
    datetime/cause/injuries etc. all come back required=True, driver/
    ert_members/notes fields required=False). Embedded into main.html so
    the frontend's blocking-validation UI (Step 9) never hardcodes a
    second, driftable copy of "which fields matter" per incident type.
    """
    schema = {key: _required_map(cls) for key, cls in INCIDENT_SERIALIZERS.items()}
    schema["_victim"] = _required_map(RoadCrashVictimSerializer)
    schema["lifelines_status"] = _required_map(LifelinesStatusSerializer)
    return schema
