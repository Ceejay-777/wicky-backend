from django.db import models


class AIGenerationLog(models.Model):
    """One row per chain step attempt of an AI call.

    Rows are written on their own database connection (``config.utils.db_routers``),
    so a failed call's row survives the caller's transaction rolling back.
    """

    class Outcome(models.TextChoices):
        SUCCESS = "success"
        HTTP_ERROR = "http_error"
        TRUNCATED = "truncated"
        MALFORMED_JSON = "malformed_json"
        SHAPE_INVALID = "shape_invalid"
        TIMEOUT = "timeout"
        OTHER = "other"

    class HumanVerdict(models.TextChoices):
        AGREE = "agree"
        DISAGREE = "disagree"
        UNSURE = "unsure"

    action = models.CharField(max_length=32)
    prompt_version = models.CharField(max_length=64)
    provider = models.CharField(max_length=32)
    model = models.CharField(max_length=100)
    step_position = models.PositiveSmallIntegerField()
    served = models.BooleanField(default=False)
    outcome = models.CharField(max_length=20, choices=Outcome.choices)
    error_message = models.TextField(blank=True)
    request_messages = models.JSONField()
    raw_response = models.TextField(blank=True)
    parsed_data = models.JSONField(null=True, blank=True)
    latency_ms = models.PositiveIntegerField()
    usage = models.JSONField(null=True, blank=True)
    context = models.JSONField(default=dict, blank=True)
    # Plain integer, not a FK: apps.ai must not depend on apps.learning.
    topic_id = models.PositiveBigIntegerField(null=True, blank=True, db_index=True)
    outcome_annotation = models.JSONField(null=True, blank=True)
    human_verdict = models.CharField(
        max_length=10, choices=HumanVerdict.choices, null=True, blank=True
    )
    human_note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["-created_at"], name="ailog_created_idx"),
            models.Index(fields=["action", "-created_at"], name="ailog_action_idx"),
            models.Index(fields=["model", "-created_at"], name="ailog_model_idx"),
            models.Index(fields=["outcome", "-created_at"], name="ailog_outcome_idx"),
            models.Index(fields=["prompt_version", "-created_at"], name="ailog_version_idx"),
        ]

    def __str__(self):
        return f"{self.action} {self.model} {self.outcome}"
