import ast
from datetime import timedelta
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.db import connections
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.ai.models import AIGenerationLog
from apps.ai.tasks import delete_old_generation_logs

LOG_DB = pytest.mark.django_db(databases=["default", "ai_log"])
AI_DIR = Path(__file__).resolve().parents[1]


def make_row(**overrides):
    fields = {
        "action": "topic",
        "prompt_version": "topic-v0",
        "provider": "groq",
        "model": "openai/gpt-oss-120b",
        "step_position": 0,
        "served": True,
        "outcome": "success",
        "request_messages": [{"role": "user", "content": "hello"}],
        "raw_response": '{"answer": "42"}',
        "parsed_data": {"answer": "42"},
        "latency_ms": 12,
        "context": {"topic_id": 5},
        "topic_id": 5,
    }
    return AIGenerationLog.objects.create(**{**fields, **overrides})


@LOG_DB
class TestRetention:
    @override_settings(AI_LOG_RETENTION_DAYS=90)
    def test_deletes_old_rows_in_batches_and_keeps_new_ones(self):
        old = [make_row() for _ in range(5)]
        new = [make_row() for _ in range(2)]
        AIGenerationLog.objects.filter(id__in=[r.id for r in old]).update(
            created_at=timezone.now() - timedelta(days=91)
        )
        AIGenerationLog.objects.filter(id=new[0].id).update(
            created_at=timezone.now() - timedelta(days=89)
        )

        deleted = delete_old_generation_logs(batch_size=2)

        assert deleted == 5
        assert set(AIGenerationLog.objects.values_list("id", flat=True)) == {r.id for r in new}


class TestArchitecture:
    def test_apps_ai_never_imports_apps_learning(self):
        offenders = []
        for path in AI_DIR.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module or ""]
                else:
                    continue
                if any(n == "apps.learning" or n.startswith("apps.learning.") for n in names):
                    offenders.append(f"{path.relative_to(AI_DIR)}:{node.lineno}")
        assert offenders == []


@LOG_DB
class TestAdmin:
    LARGE_COLUMNS = ("request_messages", "raw_response", "parsed_data", "context", "usage", "error_message")

    @pytest.fixture
    def admin_client(self, client):
        user = get_user_model().objects.create_superuser(email="admin@test.com", password="p")
        client.force_login(user)
        return client

    def test_changelist_loads_without_large_columns(self, admin_client):
        make_row()
        with CaptureQueriesContext(connections["ai_log"]) as queries:
            resp = admin_client.get(reverse("admin:ai_aigenerationlog_changelist"))
        assert resp.status_code == 200
        selects = [q["sql"] for q in queries if '"ai_aigenerationlog"."action"' in q["sql"]]
        assert selects
        for sql in selects:
            for column in self.LARGE_COLUMNS:
                assert f'"ai_aigenerationlog"."{column}"' not in sql

    def test_changelist_search_by_topic_id(self, admin_client):
        make_row(topic_id=5)
        make_row(topic_id=6, context={"topic_id": 6})
        resp = admin_client.get(reverse("admin:ai_aigenerationlog_changelist"), {"q": "6"})
        assert resp.status_code == 200
        assert resp.context["cl"].result_count == 1

    def test_change_page_loads_and_only_verdict_is_editable(self, admin_client):
        row = make_row()
        resp = admin_client.get(reverse("admin:ai_aigenerationlog_change", args=[row.id]))
        assert resp.status_code == 200
        assert set(resp.context["adminform"].form.fields) == {"human_verdict", "human_note"}

    def test_saving_a_human_verdict(self, admin_client):
        row = make_row()
        resp = admin_client.post(
            reverse("admin:ai_aigenerationlog_change", args=[row.id]),
            {"human_verdict": "disagree", "human_note": "wrong date"},
        )
        assert resp.status_code == 302
        row.refresh_from_db()
        assert (row.human_verdict, row.human_note) == ("disagree", "wrong date")
