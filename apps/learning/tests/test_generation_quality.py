"""Pass 2 of the AI content review spec: prompts, topic goals, normalisation, pipeline."""
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.ai.exceptions import ProviderError
from apps.ai.models import AIGenerationLog
from apps.ai.tests.helpers import ai_result
from apps.ai.tests.test_services import fake_groq, reply
from apps.learning.exceptions import GenerationError
from apps.learning.models import Subject, Topic, UserSubjectProgress
from apps.learning.pipeline import (
    SUMMARY_MAX_WORDS,
    SUMMARY_MIN_WORDS,
    generate_topic_content,
    normalise_summary,
    validate_topic_content,
)
from apps.learning.prompts import (
    ROADMAP_PROMPT_VERSION,
    TOPIC_PROMPT_VERSION,
    build_roadmap_prompt,
    build_topic_prompt,
)
from apps.learning.services import (
    append_roadmap_topics,
    generate_roadmap,
    insert_roadmap_topics,
    update_roadmap_topic,
)
from apps.learning.tasks import generate_content_for_topic

User = get_user_model()
LINKS = [
    {"title": "A", "url": "https://a.com", "type": "article"},
    {"title": "B", "url": "https://b.com", "type": "video"},
]


def words(n):
    return " ".join(["word"] * n)


def content(n=200):
    return {"summary": words(n), "resource_links": LINKS}


# --- Prompt builders (pure) --------------------------------------------------


class TestTopicPrompt:
    def test_includes_every_input_and_the_shape(self):
        text = build_topic_prompt(
            subject_name="Astronomy",
            title="Red Giants",
            level=2,
            goal="How a star swells as it runs out of hydrogen.",
            previous_title="Main Sequence Stars",
            next_title="White Dwarfs",
        ).text
        for part in (
            "Astronomy",
            "Red Giants",
            "Level 2 of 3",
            "finished level 1",
            "focus on application",
            "How a star swells as it runs out of hydrogen.",
            "'Main Sequence Stars'",
            "Do not re-teach it in depth",
            "'White Dwarfs'",
            "Do not cover it",
            "four paragraphs",
            "What it is",
            "key terms and ideas",
            "concrete example or analogy",
            "Why it matters in this subject",
            "plain text only",
            "No headings",
            "300-500 words",
            "resource links",
        ):
            assert part in text, part

    def test_level_one_reader_is_a_newcomer(self):
        text = build_topic_prompt(subject_name="S", title="T", level=1).text
        assert "a newcomer" in text and "define every term" in text

    def test_first_topic_without_goal_has_no_empty_labels(self):
        text = build_topic_prompt(
            subject_name="Astronomy", title="What Is a Star", level=1, goal="   ",
        ).text
        assert "Goal" not in text
        assert "goal" not in text
        assert "previous topic" not in text.lower()
        assert "next topic" not in text.lower()
        assert "''" not in text
        assert "None" not in text

    def test_last_topic_has_previous_and_no_next(self):
        text = build_topic_prompt(
            subject_name="S", title="T", level=3, previous_title="Before",
        ).text
        assert "'Before'" in text
        assert "next topic" not in text.lower()

    def test_versions_are_set_and_distinct(self):
        topic = build_topic_prompt(subject_name="S", title="T", level=1)
        roadmap = build_roadmap_prompt("S")
        assert topic.version == TOPIC_PROMPT_VERSION and topic.version
        assert roadmap.version == ROADMAP_PROMPT_VERSION and roadmap.version
        assert topic.version != roadmap.version


# --- Roadmap descriptions ----------------------------------------------------


def roadmap_reply(descriptions):
    return ai_result({
        "topics": [
            {"title": f"Topic {i}", "level": 1 + i // 8, **d}
            for i, d in enumerate(descriptions)
        ],
    })


@pytest.mark.django_db
class TestRoadmapDescriptions:
    @patch("apps.learning.services.generate_content")
    def test_descriptions_are_stored_trimmed(self, gen):
        gen.return_value = roadmap_reply([{"description": f"  Goal {i}.  "} for i in range(20)])
        topics = generate_roadmap(Subject.objects.create(name="S"))
        assert [t.description for t in topics] == [f"Goal {i}." for i in range(20)]
        assert Topic.objects.get(order=1).description == "Goal 0."

    @patch("apps.learning.services.generate_content")
    def test_missing_or_blank_description_stores_blank(self, gen):
        descriptions = [{"description": "Goal."}] * 17 + [{}, {"description": "  "}, {"description": None}]
        gen.return_value = roadmap_reply(descriptions)
        topics = generate_roadmap(Subject.objects.create(name="S"))
        assert len(topics) == 20
        assert [t.description for t in topics[-3:]] == ["", "", ""]
        assert gen.call_count == 1

    def test_append_and_insert_accept_optional_description(self):
        subject = Subject.objects.create(name="S")
        Topic.objects.create(subject=subject, title="First", level=1, order=1)
        appended = append_roadmap_topics(subject, [
            {"title": "With", "level": 1, "description": "Goal A."},
            {"title": "Without", "level": 1},
        ])
        inserted = insert_roadmap_topics(subject, 1, [{"title": "New", "level": 1, "description": "Goal B."}])
        assert [t.description for t in appended] == ["Goal A.", ""]
        assert Topic.objects.get(id=inserted[0].id).description == "Goal B."

    def test_update_accepts_description(self):
        subject = Subject.objects.create(name="S")
        topic = Topic.objects.create(subject=subject, title="T", level=1, order=1)
        assert update_roadmap_topic(topic.id, description="Goal.").description == "Goal."


@pytest.mark.django_db
class TestDescriptionInResponses:
    @pytest.fixture
    def setup(self):
        user = User.objects.create_user(email="d@e.com", password="p")
        subject = Subject.objects.create(name="S")
        topic = Topic.objects.create(
            subject=subject, title="T", level=1, order=1, description="Goal.",
            content_status=Topic.ContentStatus.READY,
        )
        UserSubjectProgress.objects.create(user=user, subject=subject)
        client = APIClient()
        client.cookies["access_token"] = str(RefreshToken.for_user(user).access_token)
        return client, subject, topic

    def test_topic_detail(self, setup):
        client, _, topic = setup
        data = client.get(f"/api/learning/topics/{topic.id}").json()["data"]
        assert data["description"] == "Goal."

    def test_subject_roadmap(self, setup):
        client, subject, _ = setup
        data = client.get(f"/api/learning/subjects/{subject.id}").json()["data"]
        assert data["topics"][0]["description"] == "Goal."

    def test_preview(self, setup):
        client, subject, _ = setup
        data = client.get(f"/api/learning/explore/{subject.id}").json()["data"]
        assert data["topics"][0]["description"] == "Goal."


# --- Normalisation and validation -------------------------------------------


class TestNormalise:
    def test_strips_markdown_and_keeps_text(self):
        raw = "## Heading\n\n- first **bold** point\n* second *italic* point\n+ third __strong__ one"
        assert normalise_summary(raw) == (
            "Heading\n\nfirst bold point\nsecond italic point\nthird strong one"
        )

    def test_leaves_ordinary_symbols_alone(self):
        text = "2 * 3 * 4 = 24, and snake_case_name stays."
        assert normalise_summary(text) == text

    def test_collapses_blank_lines_and_trims(self):
        raw = "  \n\nOne.  \n\n\n\n   Two.\n \n\t\nThree.\n\n  "
        assert normalise_summary(raw) == "One.\n\nTwo.\n\nThree."

    def test_smart_quotes_become_straight(self):
        assert normalise_summary("“Hi,” it’s ‘fine’") == "\"Hi,\" it's 'fine'"


class TestValidate:
    @pytest.mark.parametrize("n, ok", [
        (SUMMARY_MIN_WORDS - 1, False),
        (SUMMARY_MIN_WORDS, True),
        (SUMMARY_MAX_WORDS, True),
        (SUMMARY_MAX_WORDS + 1, False),
    ])
    def test_word_bounds(self, n, ok):
        assert (SUMMARY_MIN_WORDS, SUMMARY_MAX_WORDS) == (120, 900)
        if ok:
            assert validate_topic_content(content(n))["summary"] == words(n)
        else:
            with pytest.raises(GenerationError):
                validate_topic_content(content(n))

    def test_three_paragraphs_are_accepted(self):
        summary = "\n\n".join([words(60)] * 3)
        assert validate_topic_content({"summary": summary, "resource_links": LINKS})["summary"] == summary

    def test_summary_that_is_only_markdown_is_empty(self):
        with pytest.raises(GenerationError, match="Empty"):
            validate_topic_content({"summary": "##\n\n- \n", "resource_links": LINKS})

    def test_saved_summary_is_normalised(self):
        raw = "**" + words(150) + "**\n\n\n\nMore “text”."
        assert validate_topic_content({"summary": raw, "resource_links": LINKS})["summary"] == (
            words(150) + "\n\nMore \"text\"."
        )


# --- Pipeline ----------------------------------------------------------------


@pytest.fixture
def roadmap():
    subject = Subject.objects.create(name="Astronomy")
    titles = ["What Is a Star", "Main Sequence Stars", "Red Giants", "White Dwarfs"]
    topics = [
        Topic.objects.create(
            subject=subject, title=t, level=1 + i // 2, order=i + 1,
            description=f"Goal for {t}.",
        )
        for i, t in enumerate(titles)
    ]
    return subject, topics


@pytest.mark.django_db(databases=["default", "ai_log"])
class TestPipeline:
    @pytest.fixture(autouse=True)
    def claimed(self, roadmap):
        Topic.objects.filter(subject=roadmap[0]).update(content_status=Topic.ContentStatus.GENERATING)
        with patch("apps.learning.tasks.review_content_for_topic.delay"):
            yield

    def test_messages_carry_level_goal_and_neighbours(self, roadmap):
        _, topics = roadmap
        red_giants = topics[2]
        patcher, client = fake_groq(reply(content()))
        with patcher:
            generate_topic_content(red_giants.id)
        prompt = client.payloads[0]["messages"][-1]["content"]
        assert "Level 2 of 3" in prompt
        assert "Goal for Red Giants." in prompt
        assert "'Main Sequence Stars'" in prompt
        assert "'White Dwarfs'" in prompt
        red_giants.refresh_from_db()
        assert red_giants.summary == words(200)

    def test_first_topic_gets_no_previous(self, roadmap):
        _, topics = roadmap
        patcher, client = fake_groq(reply(content()))
        with patcher:
            generate_topic_content(topics[0].id)
        prompt = client.payloads[0]["messages"][-1]["content"]
        assert "previous topic" not in prompt.lower()
        assert "'Main Sequence Stars'" in prompt

    def test_log_row_has_topic_version_and_ids(self, roadmap):
        subject, topics = roadmap
        patcher, _ = fake_groq(reply(content()))
        with patcher:
            generate_topic_content(topics[1].id)
        row = AIGenerationLog.objects.get()
        assert row.action == "topic"
        assert row.prompt_version == TOPIC_PROMPT_VERSION
        assert row.topic_id == topics[1].id
        assert row.context == {"topic_id": topics[1].id, "subject_id": subject.id}


@pytest.mark.django_db
class TestGenerationTask:
    """The task's handling of GenerationError and ProviderError is unchanged."""

    @pytest.fixture
    def topic(self, roadmap):
        topic = roadmap[1][0]
        Topic.objects.filter(id=topic.id).update(content_status=Topic.ContentStatus.GENERATING)
        return topic

    def _run(self, topic, retries):
        generate_content_for_topic.push_request(retries=retries)
        try:
            generate_content_for_topic(topic.id)
        finally:
            generate_content_for_topic.pop_request()

    @patch("apps.learning.tasks.review_content_for_topic.delay")
    @patch("apps.learning.pipeline.generate_content")
    def test_generation_error_marks_failed_without_review(self, gen, review, topic):
        gen.return_value = ai_result(content(SUMMARY_MIN_WORDS - 1))
        self._run(topic, retries=0)
        topic.refresh_from_db()
        assert topic.content_status == Topic.ContentStatus.FAILED
        assert gen.call_count == 2
        review.assert_not_called()

    @patch("apps.learning.tasks.review_content_for_topic.delay")
    @patch("apps.learning.pipeline.generate_content", side_effect=ProviderError("down", provider="groq"))
    def test_provider_error_before_last_retry_leaves_generating(self, _gen, review, topic):
        with pytest.raises(ProviderError):
            self._run(topic, retries=0)
        topic.refresh_from_db()
        assert topic.content_status == Topic.ContentStatus.GENERATING
        review.assert_not_called()

    @patch("apps.learning.tasks.review_content_for_topic.delay")
    @patch("apps.learning.pipeline.generate_content", side_effect=ProviderError("down", provider="groq"))
    def test_provider_error_on_last_retry_marks_failed(self, _gen, review, topic):
        with pytest.raises(ProviderError):
            self._run(topic, retries=generate_content_for_topic.max_retries)
        topic.refresh_from_db()
        assert topic.content_status == Topic.ContentStatus.FAILED
        review.assert_not_called()

    @patch("apps.learning.tasks.review_content_for_topic.delay")
    @patch("apps.learning.pipeline.generate_content")
    def test_success_saves_and_queues_review(self, gen, review, topic):
        gen.return_value = ai_result(content())
        self._run(topic, retries=0)
        topic.refresh_from_db()
        assert topic.summary == words(200)
        review.assert_called_once_with(topic.id)
