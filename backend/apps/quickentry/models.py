"""
Quick SitRep — standalone bridge tool models.

ManualBatch / ManualEntry are specific to this tool. The incident child
models (RoadCrash, RoadCrashVictim, MedicalAssistance, FireIncident,
WaterIncident, TraumaEmergency, LifelinesStatus) mirror
backend/apps/ops/models.py field-for-field on purpose, so a future import
into the main PDRRMO-IMS system is a field-mapping exercise rather than a
redesign. Only the parent FK differs: ManualEntry here, LGUSubmission
there. See docs/quick-report-entry-spec.md Section 3.
"""

from django.conf import settings
from django.db import models

AUTH_USER = settings.AUTH_USER_MODEL

# Same 41 Quezon Province municipalities as frontend/src/utils/cities.js
# (main repo) — id/name pairs copied over verbatim, not reinvented.
MUNICIPALITY_CHOICES = [
    ("agdangan", "Agdangan"),
    ("alabat", "Alabat"),
    ("atimonan", "Atimonan"),
    ("buenavista", "Buenavista"),
    ("burdeos", "Burdeos"),
    ("calauag", "Calauag"),
    ("candelaria", "Candelaria"),
    ("catanauan", "Catanauan"),
    ("dolores", "Dolores"),
    ("general_luna", "General Luna"),
    ("general_nakar", "General Nakar"),
    ("guinayangan", "Guinayangan"),
    ("gumaca", "Gumaca"),
    ("infanta", "Infanta"),
    ("jomalig", "Jomalig"),
    ("lopez", "Lopez"),
    ("lucban", "Lucban"),
    ("lucena", "Lucena City"),
    ("macalelon", "Macalelon"),
    ("mauban", "Mauban"),
    ("mulanay", "Mulanay"),
    ("padre_burgos", "Padre Burgos"),
    ("pagbilao", "Pagbilao"),
    ("panukulan", "Panukulan"),
    ("patnanungan", "Patnanungan"),
    ("perez", "Perez"),
    ("pitogo", "Pitogo"),
    ("plaridel", "Plaridel"),
    ("polillo", "Polillo"),
    ("quezon", "Quezon"),
    ("real", "Real"),
    ("sampaloc", "Sampaloc"),
    ("san_andres", "San Andres"),
    ("san_antonio", "San Antonio"),
    ("san_francisco", "San Francisco"),
    ("san_narciso", "San Narciso"),
    ("sariaya", "Sariaya"),
    ("tagkawayan", "Tagkawayan"),
    ("tayabas", "Tayabas City"),
    ("tiaong", "Tiaong"),
    ("unisan", "Unisan"),
]


# ═══════════════════════════════════════════════════════════
# BATCH / ENTRY
# ═══════════════════════════════════════════════════════════

class ManualBatch(models.Model):
    class Shift(models.TextChoices):
        AM = "AM", "6:00 AM (0600H)"
        PM = "PM", "6:00 PM (1800H)"

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        FINALIZED = "FINALIZED", "Finalized"

    date = models.DateField()
    shift = models.CharField(max_length=2, choices=Shift.choices)
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    finalized_at = models.DateTimeField(null=True, blank=True)
    finalized_by = models.ForeignKey(
        AUTH_USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="finalized_batches",
    )

    # Frozen at finalize time (finalize_batch view) — never regenerated
    # from the live SitRepSignatoryConfig or re-derived afterward, so
    # editing the settings page later never retroactively changes an
    # already-finalized document. No stored PDF file anymore either
    # (generated_pdf removed) — the PDF is always rebuilt on demand from
    # these fields + the batch's saved entries (see pdf.generate_batch_pdf
    # and views.download_batch), so there's nothing that needs a
    # persistent disk to survive between requests.
    synopsis = models.TextField(blank=True, default="")
    weather_conditions = models.TextField(blank=True, default="")
    actions_taken = models.TextField(blank=True, default="")
    prepared_by = models.CharField(max_length=255, blank=True, default="")
    noted_by = models.CharField(max_length=255, blank=True, default="")
    noted_by_title = models.CharField(max_length=255, blank=True, default="")
    approved_by = models.CharField(max_length=255, blank=True, default="")
    approved_by_title = models.CharField(max_length=255, blank=True, default="")

    # Amendment — only the LATEST amendment is tracked, not a full history
    # (deliberately not a separate append-only log model; see the Amend
    # flow's design note). Once set, these stay set permanently — even
    # after a re-finalize re-locks the batch (see is_locked below), the
    # PDF keeps showing "this report was amended" as a permanent audit
    # marker, same spirit as raw_text/ai_output being frozen elsewhere in
    # this app for accountability.
    amended_at = models.DateTimeField(null=True, blank=True)
    amended_by = models.ForeignKey(
        AUTH_USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="amended_batches",
    )
    amendment_reason = models.TextField(null=True, blank=True)

    class Meta:
        unique_together = ("date", "shift")
        ordering = ["-date", "-shift"]
        verbose_name = "Manual Batch"
        verbose_name_plural = "Manual Batches"

    def __str__(self):
        return f"Batch {self.date} {self.shift} [{self.status}]"

    @property
    def is_locked(self):
        """
        The single source of truth for "can this batch's entries/finalize
        fields be edited right now" — used by save_entry and
        finalize_batch instead of a blanket status == FINALIZED check.

        A FINALIZED batch is locked UNLESS it was amended more recently
        than it was last finalized — comparing timestamps rather than
        clearing amended_at on re-finalize means: (a) amending reopens
        editing without needing a second "is this currently open" flag,
        and (b) re-finalizing naturally re-locks it again (finalized_at
        moves forward past amended_at) while still leaving the permanent
        amended_at/amendment_reason record in place for the PDF note.
        """
        if self.status != self.Status.FINALIZED:
            return False
        if self.amended_at and self.amended_at > self.finalized_at:
            return False
        return True


class ManualEntry(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PROCESSED = "PROCESSED", "Processed"
        MANUALLY_EDITED = "MANUALLY_EDITED", "Manually Edited"
        FAILED = "FAILED", "Failed"

    batch = models.ForeignKey(
        ManualBatch, on_delete=models.CASCADE, related_name="entries"
    )
    municipality = models.CharField(max_length=20, choices=MUNICIPALITY_CHOICES)
    raw_text = models.TextField(
        help_text="Pasted Messenger text, verbatim — never edited after save."
    )
    ai_output = models.JSONField(
        null=True,
        blank=True,
        help_text="Raw AI extraction result, kept as-is even after human edits.",
    )
    # Unlike the incident types, unmapped_notes has no dedicated child
    # model — without its own column it would only survive inside the
    # frozen ai_output blob, so an OPS edit to it would silently vanish
    # the next time the entry is re-opened for editing (Step 8).
    unmapped_notes = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    processed_by = models.ForeignKey(
        AUTH_USER,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="processed_entries",
    )
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        # No unique_together on (batch, municipality) — a municipality
        # can submit several separate reports within one batch window
        # (confirmed in real PDRRMO usage, e.g. Lucban pasting one report
        # per incident rather than one combined report), so each paste
        # becomes its own ManualEntry rather than overwriting the last.
        ordering = ["municipality", "processed_at", "id"]
        verbose_name_plural = "Manual Entries"

    def __str__(self):
        return f"{self.get_municipality_display()} — {self.batch}"


# ═══════════════════════════════════════════════════════════
# INCIDENT CHILD MODELS
# Field lists mirror backend/apps/ops/models.py exactly (verified against
# source, not memory). FK target is ManualEntry instead of LGUSubmission.
# ═══════════════════════════════════════════════════════════

class RoadCrash(models.Model):
    entry = models.ForeignKey(
        ManualEntry, on_delete=models.CASCADE, related_name="road_crashes"
    )
    datetime = models.DateTimeField()
    location = models.CharField(max_length=255)
    barangay = models.CharField(max_length=100)
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    cause = models.CharField(max_length=255)
    vehicles_involved = models.CharField(max_length=255)
    actions_taken = models.TextField()
    responding_team = models.CharField(max_length=100)
    driver = models.CharField(max_length=100, blank=True)
    ert_members = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Road Crash @ {self.location} ({self.datetime:%Y-%m-%d %H:%M})"


class RoadCrashVictim(models.Model):
    class InjuryClassification(models.TextChoices):
        MINOR = "MINOR", "Minor"
        MAJOR = "MAJOR", "Major"
        FATALITY = "FATALITY", "Fatality"

    road_crash = models.ForeignKey(
        RoadCrash, on_delete=models.CASCADE, related_name="victims"
    )
    age = models.PositiveIntegerField(null=True, blank=True)
    sex = models.CharField(max_length=10, blank=True)
    address = models.CharField(max_length=255, blank=True)
    injuries = models.TextField()
    injury_classification = models.CharField(
        max_length=10, choices=InjuryClassification.choices
    )

    def __str__(self):
        return f"{self.sex}, {self.age} - {self.injury_classification}"


class MedicalAssistance(models.Model):
    entry = models.ForeignKey(
        ManualEntry, on_delete=models.CASCADE, related_name="medical_cases"
    )
    datetime = models.DateTimeField()
    location = models.CharField(max_length=255)
    barangay = models.CharField(max_length=100)
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    patient_age = models.PositiveIntegerField(null=True, blank=True)
    patient_sex = models.CharField(max_length=10, blank=True)
    patient_address = models.CharField(max_length=255, blank=True)
    nature_of_illness = models.CharField(max_length=255)
    chief_complaint = models.TextField(blank=True)
    blood_pressure = models.CharField(max_length=20, blank=True)
    pulse_rate = models.CharField(max_length=20, blank=True)
    spo2 = models.CharField(max_length=20, blank=True)
    temperature = models.CharField(max_length=20, blank=True)
    actions_taken = models.TextField()
    responding_team = models.CharField(max_length=100)
    driver = models.CharField(max_length=100, blank=True)
    ert_members = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Medical Assistance Cases"

    def __str__(self):
        return f"Medical @ {self.location} ({self.datetime:%Y-%m-%d %H:%M})"


class FireIncident(models.Model):
    entry = models.ForeignKey(
        ManualEntry, on_delete=models.CASCADE, related_name="fire_incidents"
    )
    datetime = models.DateTimeField()
    location = models.CharField(max_length=255)
    barangay = models.CharField(max_length=100)
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    # ipo/dtr/ted/tas (BFP's four-timestamp incident timeline) were
    # dropped per client direction in favor of the same 4W1H convention
    # every other incident type already uses — this is a deliberate,
    # client-directed divergence from apps/ops/models.py in the main
    # repo (see CLAUDE.md's note on field mirroring), not an oversight;
    # re-check with the client, not just the main repo, before reverting.
    cause = models.CharField(max_length=255)
    response_time_minutes = models.PositiveIntegerField(default=0)
    distance_km = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    structure_type = models.CharField(max_length=100)
    families_affected = models.PositiveIntegerField(default=0)
    individuals_affected = models.PositiveIntegerField(default=0)
    structures_burned = models.PositiveIntegerField(default=0)
    fire_area_sqm = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    casualties = models.PositiveIntegerField(default=0)
    injured = models.PositiveIntegerField(default=0)
    fatalities = models.PositiveIntegerField(default=0)
    responding_team = models.CharField(max_length=100)
    actions_taken = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Fire @ {self.location} ({self.datetime:%Y-%m-%d %H:%M})"


class WaterIncident(models.Model):
    entry = models.ForeignKey(
        ManualEntry, on_delete=models.CASCADE, related_name="water_incidents"
    )
    datetime = models.DateTimeField()
    location = models.CharField(max_length=255)
    barangay = models.CharField(max_length=100)
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    incident_type = models.CharField(
        max_length=100, help_text="e.g. Flood, Storm Surge, Water Rescue"
    )
    description = models.TextField(blank=True)
    families_affected = models.PositiveIntegerField(default=0)
    individuals_affected = models.PositiveIntegerField(default=0)
    casualties = models.PositiveIntegerField(default=0)
    injured = models.PositiveIntegerField(default=0)
    fatalities = models.PositiveIntegerField(default=0)
    responding_team = models.CharField(max_length=100)
    actions_taken = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Water Incident @ {self.location} ({self.datetime:%Y-%m-%d %H:%M})"


class TraumaEmergency(models.Model):
    entry = models.ForeignKey(
        ManualEntry, on_delete=models.CASCADE, related_name="trauma_incidents"
    )
    datetime = models.DateTimeField()
    location = models.CharField(max_length=255)
    barangay = models.CharField(max_length=100)
    latitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    longitude = models.DecimalField(
        max_digits=9, decimal_places=6, null=True, blank=True
    )
    call_type = models.CharField(
        max_length=100, help_text="e.g. Vehicular Accident, Fall, Gunshot"
    )
    patient_age = models.PositiveIntegerField(null=True, blank=True)
    patient_sex = models.CharField(max_length=10, blank=True)
    patient_address = models.CharField(max_length=255, blank=True)
    chief_complaint = models.TextField(blank=True)
    actions_taken = models.TextField()
    responding_team = models.CharField(max_length=100)
    driver = models.CharField(max_length=100, blank=True)
    ert_members = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Trauma Emergencies"

    def __str__(self):
        return f"Trauma @ {self.location} ({self.datetime:%Y-%m-%d %H:%M})"


class LifelinesStatus(models.Model):
    class UtilityStatus(models.TextChoices):
        OPERATIONAL = "OPERATIONAL", "Operational"
        INTERRUPTED = "INTERRUPTED", "Interrupted"
        UNDER_REPAIR = "UNDER_REPAIR", "Under Repair"

    class RoadStatus(models.TextChoices):
        PASSABLE = "PASSABLE", "All Roads Passable"
        IMPASSABLE = "IMPASSABLE", "Some Roads Impassable"
        FLOODED = "FLOODED", "Flooded"

    class SeaStatus(models.TextChoices):
        NORMAL = "NORMAL", "Normal Schedule"
        SUSPENDED = "SUSPENDED", "Suspended"
        MODIFIED = "MODIFIED", "Modified Schedule"

    entry = models.OneToOneField(
        ManualEntry, on_delete=models.CASCADE, related_name="lifelines"
    )
    power_supply = models.CharField(max_length=20, choices=UtilityStatus.choices)
    power_notes = models.TextField(blank=True)
    water_supply = models.CharField(max_length=20, choices=UtilityStatus.choices)
    water_notes = models.TextField(blank=True)
    communication = models.CharField(max_length=20, choices=UtilityStatus.choices)
    communication_notes = models.TextField(blank=True)
    road_condition = models.CharField(max_length=20, choices=RoadStatus.choices)
    road_notes = models.TextField(blank=True)
    sea_travel = models.CharField(max_length=20, choices=SeaStatus.choices)
    sea_notes = models.TextField(blank=True)
    class_suspension = models.BooleanField(default=False)
    class_suspension_notes = models.TextField(blank=True)

    class Meta:
        verbose_name_plural = "Lifelines Statuses"

    def __str__(self):
        return f"Lifelines - {self.entry}"


# ═══════════════════════════════════════════════════════════
# SITREP SIGNATORY CONFIG
# Same singleton pattern as the main system's SitRepConfig
# (backend/apps/ops/models.py) — same fields, same seeded defaults.
# ═══════════════════════════════════════════════════════════

class SitRepSignatoryConfig(models.Model):
    prepared_by = models.CharField(max_length=255, default="Team Charlie")
    noted_by = models.CharField(max_length=255, default="NIEL A. PARCO")
    noted_by_title = models.CharField(
        max_length=255, default="Operations and Warning Division Head"
    )
    approved_by = models.CharField(
        max_length=255, default="DR. MELCHOR P. AVENILLA, JR."
    )
    approved_by_title = models.CharField(max_length=255, default="PGDH - PDRRMC")

    class Meta:
        verbose_name = "SitRep Signatory Configuration"
        verbose_name_plural = "SitRep Signatory Configuration"

    def save(self, *args, **kwargs):
        # Ensure only one instance exists
        if not self.pk and SitRepSignatoryConfig.objects.exists():
            raise ValueError("Only one SitRepSignatoryConfig instance is allowed.")
        super().save(*args, **kwargs)

    @classmethod
    def get_config(cls):
        config = cls.objects.first()
        if config is None:
            config = cls.objects.create()
        return config

    def __str__(self):
        return "SitRep Signatory Configuration"
