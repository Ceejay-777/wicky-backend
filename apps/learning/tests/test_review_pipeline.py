"""Pass 3 of the AI content review spec: narrow review, quote check, repair, statuses."""
from datetime import timedelta
from unittest.mock import patch

import pytest
from celery.exceptions import Retry
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.ai.exceptions import ProviderError
from apps.ai.models import AIGenerationLog
from apps.ai.tests.test_services import fake_groq, reply
from apps.learning.models import Subject, Topic, UserSubjectProgress
from apps.learning.pipeline import interpret_review
from apps.learning.prompts import (
    REPAIR_PROMPT_VERSION,
    REVIEW_PROMPT_VERSION,
    TOPIC_PROMPT_VERSION,
    build_review_prompt,
)
from apps.learning.services import request_topic_generation
from apps.learning.tasks import (
    cleanup_stuck_generations,
    generate_content_for_topic,
    repair_content_for_topic,
    review_content_for_topic,
)

User = get_user_model()
LOG_DB = pytest.mark.django_db(databases=["default", "ai_log"])
SEND = "apps.learning.events.send_event"

WRONG = "Water boils at 50 degrees Celsius at sea level."
FIXED_SENTENCE = "Water boils at 100 degrees Celsius at sea level."
FILLER = " ".join(["word"] * 150)
SUMMARY = f"{WRONG} {FILLER}"
LINKS = [
    {"title": "A", "url": "https://a.example/boiling", "type": "article"},
    {"title": "B", "url": "https://b.example/steam", "type": "video"},
]


def review(on_topic=True, issues=()):
    return reply({"on_topic": on_topic, "reason": "because", "issues": list(issues)})


def issue(quote=WRONG, problem="Wrong temperature.", correction=FIXED_SENTENCE):
    return {"quote": quote, "problem": problem, "correction": correction}


def generated(summary=SUMMARY):
    return reply({"summary": summary, "resource_links": LINKS})


def repaired(summary=f"{FIXED_SENTENCE} {FILLER}"):
    return reply({"summary": summary})


def actions():
    return list(AIGenerationLog.objects.order_by("id").values_list("action", flat=True))


@pytest.fixture
def subject():
    return Subject.objects.create(name="Physics")


@pytest.fixture
def topic(subject):
    """A topic whose summary is saved and waiting for review."""
    return Topic.objects.create(
        subject=subject, title="Boiling", level=1, order=1, description="What boiling is.",
        content_status=Topic.ContentStatus.GENERATING, summary=SUMMARY, resource_links=LINKS,
    )


def refreshed(topic):
    topic.refresh_from_db()
    return topic


# --- Interpretation (pure) ---------------------------------------------------


class TestInterpretReview:
    def _verdict(self, *issues, summary=SUMMARY):
        return interpret_review({"on_topic": True, "reason": "r", "issues": list(issues)}, summary)

    def test_verbatim_quote_is_kept(self):
        v = self._verdict(issue())
        assert [i.quote for i in v.issues] == [WRONG] and v.dropped == 0

    def test_case_whitespace_and_quote_characters_do_not_matter(self):
        summary = "He said “it's hot”  at noon.\n" + FILLER
        v = self._verdict(issue(quote='he SAID "it’s hot"\nat noon.'), summary=summary)
        assert len(v.issues) == 1 and v.dropped == 0

    def test_quote_not_in_summary_is_dropped_and_counted(self):
        v = self._verdict(issue(quote="The moon is made of cheese."))
        assert v.issues == () and v.dropped == 1

    @pytest.mark.parametrize("field", ["quote", "problem", "correction"])
    def test_empty_field_is_dropped(self, field):
        v = self._verdict(issue(**{field: "  "}))
        assert v.issues == () and v.dropped == 1

    def test_duplicate_quotes_are_kept_once(self):
        v = self._verdict(issue(), issue(quote=WRONG.upper()))
        assert len(v.issues) == 1


# --- Reviewer prompt ---------------------------------------------------------


class TestReviewPrompt:
    def _text(self):
        return build_review_prompt(
            subject_name="Astronomy", title="Red Giants", level=2,
            goal="How a star swells as it runs out of hydrogen.",
            previous_title="Main Sequence Stars", next_title="White Dwarfs",
            summary="Some summary.",
        ).text

    def test_contains_goal_level_and_neighbours_but_no_links(self):
        text = self._text()
        for part in ("Level 2 of 3", "How a star swells", "'Main Sequence Stars'", "'White Dwarfs'"):
            assert part in text
        assert "http" not in text

    def test_states_that_omissions_and_style_are_not_issues(self):
        text = self._text()
        assert "Do not report style" in text and "omissions" in text
        assert "empty issues list" in text

    @LOG_DB
    def test_review_call_does_not_send_resource_links(self, topic):
        patcher, client = fake_groq(review())
        with patcher:
            review_content_for_topic(topic.id)
        sent = str(client.payloads[0]["messages"])
        assert "a.example" not in sent and "b.example" not in sent


# --- Decisions ---------------------------------------------------------------


@LOG_DB
class TestDecisions:
    def test_on_topic_no_issues_passes(self, topic):
        patcher, _ = fake_groq(review())
        with patcher:
            review_content_for_topic(topic.id)
        t = refreshed(topic)
        assert (t.content_status, t.review_status) == ("READY", "PASSED")
        assert actions() == ["review"]

    def test_only_made_up_quotes_passes_without_repair(self, topic):
        patcher, _ = fake_groq(review(issues=[issue(quote="Ice is hot.")]))
        with patcher:
            review_content_for_topic(topic.id)
        t = refreshed(topic)
        assert t.review_status == "PASSED"
        assert t.review_verdict["dropped"] == 1
        assert actions() == ["review"]

    def test_real_issue_is_repaired_without_re_review(self, topic):
        patcher, _ = fake_groq(review(issues=[issue()]), repaired())
        with patcher:
            review_content_for_topic(topic.id)
        t = refreshed(topic)
        assert (t.content_status, t.review_status) == ("READY", "FIXED")
        assert t.summary == f"{FIXED_SENTENCE} {FILLER}"
        assert actions() == ["review", "topic_repair"]

    def test_invalid_repair_keeps_the_original(self, topic):
        patcher, _ = fake_groq(review(issues=[issue()]), repaired("Too short."))
        with patcher:
            review_content_for_topic(topic.id)
        t = refreshed(topic)
        assert (t.content_status, t.review_status) == ("READY", "UNREVIEWED")
        assert t.summary == SUMMARY
        assert AIGenerationLog.objects.get(action="topic_repair").outcome_annotation["repair"] == "invalid"

    def test_off_topic_once_regenerates_then_passes(self, topic):
        patcher, _ = fake_groq(review(on_topic=False), generated(), review())
        with patcher:
            review_content_for_topic(topic.id)
        t = refreshed(topic)
        assert (t.content_status, t.review_status) == ("READY", "PASSED")
        assert t.regeneration_count == 1
        assert actions() == ["review", "topic", "review"]

    def test_off_topic_twice_is_rejected(self, topic):
        patcher, _ = fake_groq(review(on_topic=False), generated(), review(on_topic=False))
        with patcher:
            review_content_for_topic(topic.id)
        t = refreshed(topic)
        assert (t.content_status, t.review_status) == ("FAILED", "REJECTED")
        assert actions() == ["review", "topic", "review"]  # no third generation

    def test_off_topic_with_issues_regenerates_not_repairs(self, topic):
        patcher, _ = fake_groq(review(on_topic=False, issues=[issue()]), generated(), review())
        with patcher:
            review_content_for_topic(topic.id)
        assert "topic_repair" not in actions()
        assert actions() == ["review", "topic", "review"]


# --- Outages and retries -----------------------------------------------------


def run(task, topic_id, retries):
    task.push_request(retries=retries)
    try:
        task(topic_id)
    finally:
        task.pop_request()


OUTAGE = [reply({}, status=503)] * 3


@LOG_DB
class TestOutages:
    def test_review_outage_retries_then_publishes_unreviewed(self, topic):
        patcher, _ = fake_groq(*OUTAGE)
        with patcher, patch.object(review_content_for_topic, "retry", side_effect=Retry()):
            with pytest.raises(Retry):
                run(review_content_for_topic, topic.id, retries=0)
        assert refreshed(topic).content_status == "GENERATING"

        patcher, _ = fake_groq(*OUTAGE)
        with patcher, pytest.raises(ProviderError):
            run(review_content_for_topic, topic.id, retries=review_content_for_topic.max_retries)
        t = refreshed(topic)
        assert (t.content_status, t.review_status) == ("READY", "UNREVIEWED")
        assert t.summary == SUMMARY

    def test_repair_outage_retries_then_publishes_without_re_review(self, topic):
        patcher, _ = fake_groq(review(issues=[issue()]))
        with patcher, patch("apps.learning.tasks.repair_content_for_topic.delay") as queued:
            review_content_for_topic(topic.id)
        queued.assert_called_once_with(topic.id)

        patcher, _ = fake_groq(*OUTAGE)
        with patcher, patch.object(repair_content_for_topic, "retry", side_effect=Retry()):
            with pytest.raises(Retry):
                run(repair_content_for_topic, topic.id, retries=0)
        patcher, _ = fake_groq(*OUTAGE)
        with patcher, pytest.raises(ProviderError):
            run(repair_content_for_topic, topic.id, retries=repair_content_for_topic.max_retries)

        t = refreshed(topic)
        assert (t.content_status, t.review_status) == ("READY", "UNREVIEWED")
        assert t.summary == SUMMARY
        assert actions().count("review") == 1

    def test_redelivered_review_on_ready_topic_makes_no_ai_call(self, topic):
        Topic.objects.filter(id=topic.id).update(content_status="READY", review_status="PASSED")
        patcher, client = fake_groq()
        with patcher:
            review_content_for_topic(topic.id)
        assert client.payloads == []

    def test_redelivered_generation_after_save_makes_no_ai_call(self, topic):
        patcher, client = fake_groq()
        with patcher:
            generate_content_for_topic(topic.id)
        assert client.payloads == []

    def test_generation_outage_fails_pending_and_sweeper_requeues(self, topic):
        Topic.objects.filter(id=topic.id).update(summary="")
        patcher, _ = fake_groq(*OUTAGE)
        with patcher, pytest.raises(ProviderError):
            run(generate_content_for_topic, topic.id, retries=generate_content_for_topic.max_retries)
        t = refreshed(topic)
        assert (t.content_status, t.review_status) == ("FAILED", "PENDING")

        Topic.objects.filter(id=topic.id).update(
            generation_started_at=timezone.now() - timedelta(hours=2)
        )
        cleanup_stuck_generations()
        assert refreshed(topic).content_status == "NOT_GENERATED"

    def test_sweeper_never_requeues_rejected(self, topic):
        Topic.objects.filter(id=topic.id).update(
            content_status="FAILED", review_status="REJECTED",
            generation_started_at=timezone.now() - timedelta(hours=2),
        )
        cleanup_stuck_generations()
        assert refreshed(topic).content_status == "FAILED"

    @patch(SEND)
    def test_topic_stuck_in_review_is_published_unreviewed(self, send, topic, api):
        Topic.objects.filter(id=topic.id).update(
            generation_started_at=timezone.now() - timedelta(hours=2)
        )
        cleanup_stuck_generations()
        t = refreshed(topic)
        assert (t.content_status, t.review_status) == ("READY", "UNREVIEWED")
        assert t.summary == SUMMARY
        assert send.call_args.args[2]["review_status"] == "UNREVIEWED"


# --- State and API -----------------------------------------------------------


@pytest.fixture
def api(subject):
    user = User.objects.create_user(email="r@e.com", password="p")
    UserSubjectProgress.objects.create(user=user, subject=subject)
    client = APIClient()
    client.cookies["access_token"] = str(RefreshToken.for_user(user).access_token)
    return client, user


@LOG_DB
class TestStateAndApi:
    def test_retry_on_rejected_starts_a_new_cycle(self, topic, api):
        client, _ = api
        Topic.objects.filter(id=topic.id).update(
            content_status="FAILED", review_status="REJECTED", regeneration_count=1,
            review_verdict={"on_topic": False, "reason": "x", "issues": [], "dropped": 0},
        )
        patcher, _ = fake_groq(generated(), review())
        with patcher:
            resp = client.post(f"/api/learning/topics/{topic.id}/retry")
        assert resp.status_code == 200
        t = refreshed(topic)
        assert (t.content_status, t.review_status) == ("READY", "PASSED")
        assert t.regeneration_count == 0
        assert t.review_verdict["on_topic"] is True
        assert actions() == ["topic", "review"]

    def test_topic_subject_and_sse_carry_new_values(self, topic, api):
        client, user = api
        patcher, _ = fake_groq(review(issues=[issue()]), repaired())
        with patcher, patch(SEND) as send:
            review_content_for_topic(topic.id)
        assert send.call_args.args[2]["review_status"] == "FIXED"
        detail = client.get(f"/api/learning/topics/{topic.id}").json()["data"]
        assert detail["review_status"] == "FIXED"
        roadmap = client.get(f"/api/learning/subjects/{topic.subject_id}").json()["data"]
        assert roadmap["topics"][0]["review_status"] == "FIXED"

    @pytest.mark.parametrize(
        "content_status,review_status,visible",
        [
            ("GENERATING", "PENDING", False),
            ("FAILED", "REJECTED", False),
            ("READY", "PASSED", True),
            ("READY", "FIXED", True),
            ("READY", "UNREVIEWED", True),
        ],
    )
    def test_summary_shown_only_for_visible_statuses(
        self, topic, api, content_status, review_status, visible
    ):
        client, _ = api
        Topic.objects.filter(id=topic.id).update(
            content_status=content_status, review_status=review_status
        )
        data = client.get(f"/api/learning/topics/{topic.id}").json()["data"]
        assert (data["summary"] == SUMMARY) is visible
        assert (data["resource_links"] == LINKS) is visible


# --- Logging -----------------------------------------------------------------


@LOG_DB
def test_full_cycle_logs_each_stage_with_its_version(subject):
    topic = Topic.objects.create(subject=subject, title="Boiling", level=1, order=1)
    patcher, _ = fake_groq(generated(), review(issues=[issue()]), repaired())
    with patcher:
        request_topic_generation(topic.id)
    assert refreshed(topic).review_status == "FIXED"
    rows = {r.action: r for r in AIGenerationLog.objects.all()}
    assert set(rows) == {"topic", "review", "topic_repair"}
    assert rows["topic"].prompt_version == TOPIC_PROMPT_VERSION
    assert rows["review"].prompt_version == REVIEW_PROMPT_VERSION
    assert rows["topic_repair"].prompt_version == REPAIR_PROMPT_VERSION
    assert rows["review"].outcome_annotation == {
        "on_topic": True, "reason": "because", "issues": [issue()], "dropped": 0,
    }
    assert rows["topic_repair"].outcome_annotation == {"repair": "repaired"}
