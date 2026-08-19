from django.contrib import admin

from .models import (
    ManualBatch,
    ManualEntry,
    RoadCrash,
    RoadCrashVictim,
    MedicalAssistance,
    FireIncident,
    WaterIncident,
    TraumaEmergency,
    LifelinesStatus,
    SitRepSignatoryConfig,
)


@admin.register(ManualBatch)
class ManualBatchAdmin(admin.ModelAdmin):
    list_display = ("date", "shift", "status", "finalized_at", "finalized_by")
    list_filter = ("status", "shift")
    ordering = ("-date", "-shift")


class RoadCrashVictimInline(admin.TabularInline):
    model = RoadCrashVictim
    extra = 0


@admin.register(ManualEntry)
class ManualEntryAdmin(admin.ModelAdmin):
    list_display = ("municipality", "batch", "status", "processed_by", "processed_at")
    list_filter = ("status", "municipality", "batch")
    search_fields = ("raw_text",)


@admin.register(RoadCrash)
class RoadCrashAdmin(admin.ModelAdmin):
    list_display = ("entry", "datetime", "location", "barangay", "responding_team")
    list_filter = ("entry__batch",)
    inlines = [RoadCrashVictimInline]


@admin.register(MedicalAssistance)
class MedicalAssistanceAdmin(admin.ModelAdmin):
    list_display = ("entry", "datetime", "location", "barangay", "nature_of_illness")
    list_filter = ("entry__batch",)


@admin.register(FireIncident)
class FireIncidentAdmin(admin.ModelAdmin):
    list_display = ("entry", "datetime", "location", "barangay", "structure_type", "fatalities")
    list_filter = ("entry__batch",)


@admin.register(WaterIncident)
class WaterIncidentAdmin(admin.ModelAdmin):
    list_display = ("entry", "datetime", "location", "barangay", "incident_type")
    list_filter = ("entry__batch",)


@admin.register(TraumaEmergency)
class TraumaEmergencyAdmin(admin.ModelAdmin):
    list_display = ("entry", "datetime", "location", "barangay", "call_type")
    list_filter = ("entry__batch",)


@admin.register(LifelinesStatus)
class LifelinesStatusAdmin(admin.ModelAdmin):
    list_display = (
        "entry", "power_supply", "water_supply", "communication",
        "road_condition", "sea_travel", "class_suspension",
    )


@admin.register(SitRepSignatoryConfig)
class SitRepSignatoryConfigAdmin(admin.ModelAdmin):
    list_display = ("prepared_by", "noted_by", "approved_by")

    def has_add_permission(self, request):
        # Singleton — same guard as the model's own save(); the /settings/
        # page is the intended editing path, this is just for visibility.
        return not SitRepSignatoryConfig.objects.exists()
