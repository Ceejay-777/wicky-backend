import json

from django.contrib import admin
from django.utils.html import format_html, format_html_join

from .models import AIGenerationLog

_LIST_COLUMNS = (
    "created_at",
    "action",
    "prompt_version",
    "model",
    "outcome",
    "latency_ms",
    "topic_id",
    "human_verdict",
)


def _pre(text: str):
    return format_html('<pre style="white-space: pre-wrap; margin: 0">{}</pre>', text)


def _pretty_json(value):
    return _pre(json.dumps(value, indent=2, ensure_ascii=False, default=str))


@admin.register(AIGenerationLog)
class AIGenerationLogAdmin(admin.ModelAdmin):
    list_display = _LIST_COLUMNS
    list_filter = (
        "action",
        "model",
        "outcome",
        "prompt_version",
        "human_verdict",
        ("created_at", admin.DateFieldListFilter),
    )
    search_fields = ("=topic_id",)
    list_per_page = 50
    fieldsets = (
        ("Review", {"fields": ("human_verdict", "human_note")}),
        (
            "Call",
            {
                "fields": (
                    "created_at", "action", "prompt_version", "provider", "model",
                    "step_position", "served", "outcome", "error_message",
                    "latency_ms", "usage_display", "topic_id", "context_display",
                    "outcome_annotation_display",
                )
            },
        ),
        (
            "Request and response",
            {"fields": ("messages_display", "raw_response_display", "parsed_data_display")},
        ),
    )
    readonly_fields = (
        "created_at", "action", "prompt_version", "provider", "model",
        "step_position", "served", "outcome", "error_message", "latency_ms",
        "usage_display", "topic_id", "context_display", "outcome_annotation_display",
        "messages_display", "raw_response_display", "parsed_data_display",
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        match = request.resolver_match
        if match and match.url_name and match.url_name.endswith("_changelist"):
            # The list never shows the large text/JSON columns, so never load them.
            qs = qs.only("id", *_LIST_COLUMNS)
        return qs

    def has_add_permission(self, request):
        return False

    @admin.display(description="Request messages")
    def messages_display(self, obj):
        return format_html_join(
            "",
            "<p><strong>{}</strong></p>{}",
            ((m.get("role", ""), _pre(m.get("content", ""))) for m in obj.request_messages),
        )

    @admin.display(description="Raw response")
    def raw_response_display(self, obj):
        try:
            return _pretty_json(json.loads(obj.raw_response))
        except (ValueError, TypeError):
            return _pre(obj.raw_response)

    @admin.display(description="Parsed data")
    def parsed_data_display(self, obj):
        return _pretty_json(obj.parsed_data)

    @admin.display(description="Context")
    def context_display(self, obj):
        return _pretty_json(obj.context)

    @admin.display(description="Usage")
    def usage_display(self, obj):
        return _pretty_json(obj.usage)

    @admin.display(description="Outcome annotation")
    def outcome_annotation_display(self, obj):
        return _pretty_json(obj.outcome_annotation)
