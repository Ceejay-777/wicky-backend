from datetime import timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.ai.exceptions import ProviderError
from apps.ai.tests.helpers import ai_result
from apps.learning.models import Subject, Topic, UserSubjectProgress
from apps.learning.tasks import (
    cleanup_stuck_generations,
    generate_content_for_topic,
    review_content_for_topic,
)

User = get_user_model()
SEND = "apps.learning.events.send_event"
AI = "apps.learning.pipeline.generate_content"
ON_TOPIC = ai_result({"on_topic": True, "reason": "ok", "issues": []})
OFF_TOPIC = ai_result({"on_topic": False, "reason": "about something else", "issues": []})


@pytest.fixture
def enrolled():
    return User.objects.create_user(email="in@b.com", password="p")


@pytest.fixture
def topic(enrolled):
    subject = Subject.objects.create(name="Sourdough")
    UserSubjectProgress.objects.create(user=enrolled, subject=subject)
    User.objects.create_user(email="out@b.com", password="p")  # not enrolled
    return Topic.objects.create(
        subject=subject, title="T", level=1, order=1,
        content_status=Topic.ContentStatus.GENERATING,
    )


@pytest.fixture
def in_review(topic):
    """Generated and waiting for review."""
    Topic.objects.filter(id=topic.id).update(summary="content")
    return topic


@pytest.mark.django_db
class TestTopicStatusEvent:
    @patch(SEND)
    @patch(AI, return_value=ON_TOPIC)
    def test_ready_is_published_to_enrolled_users_only(self, _ai, send, in_review, enrolled):
        review_content_for_topic(in_review.id)

        send.assert_called_once_with(
            f"user-{enrolled.id}",
            "topic.status",
            {
                "subject_id": in_review.subject_id,
                "topic_id": in_review.id,
                "content_status": "READY",
                "review_status": "PASSED",
            },
        )

    @patch(SEND)
    @patch(AI, return_value=OFF_TOPIC)
    def test_rejected_is_published_when_off_topic_twice(self, _ai, send, in_review):
        Topic.objects.filter(id=in_review.id).update(regeneration_count=1)

        review_content_for_topic(in_review.id)

        payload = send.call_args.args[2]
        assert (payload["content_status"], payload["review_status"]) == ("FAILED", "REJECTED")

    @patch(SEND)
    @patch("apps.learning.tasks.generate_content_for_topic.delay")
    @patch(AI, return_value=OFF_TOPIC)
    def test_regeneration_is_not_published(self, _ai, _gen, send, in_review):
        review_content_for_topic(in_review.id)  # first off-topic: regenerates, not final

        send.assert_not_called()

    @patch(SEND, side_effect=RuntimeError("redis down"))
    @patch(AI, return_value=ON_TOPIC)
    def test_publish_failure_does_not_fail_the_task(self, _ai, _send, in_review):
        review_content_for_topic(in_review.id)

        in_review.refresh_from_db()
        assert in_review.content_status == Topic.ContentStatus.READY


@pytest.mark.django_db
class TestProviderDownOnLastRetry:
    def _run(self, topic, retries):
        task = generate_content_for_topic
        task.push_request(retries=retries)
        try:
            with patch(AI, side_effect=ProviderError("down", provider="groq")):
                with pytest.raises(ProviderError):
                    task(topic.id)
        finally:
            task.pop_request()

    @patch(SEND)
    def test_last_retry_marks_failed_and_publishes(self, send, topic):
        self._run(topic, retries=generate_content_for_topic.max_retries)
        topic.refresh_from_db()
        assert (topic.content_status, topic.review_status) == ("FAILED", "PENDING")
        assert send.call_args.args[2]["content_status"] == "FAILED"

    @patch(SEND)
    def test_earlier_retry_leaves_topic_generating(self, send, topic):
        self._run(topic, retries=0)
        topic.refresh_from_db()
        assert topic.content_status == "GENERATING"
        send.assert_not_called()


@pytest.mark.django_db
class TestCleanupPublishes:
    @patch(SEND)
    def test_reset_topics_are_published(self, send, topic):
        Topic.objects.filter(id=topic.id).update(
            generation_started_at=timezone.now() - timedelta(hours=2),
        )
        cleanup_stuck_generations()
        assert send.call_args.args[2]["content_status"] == "NOT_GENERATED"

    @patch(SEND)
    def test_nothing_published_when_nothing_stuck(self, send, topic):
        Topic.objects.filter(id=topic.id).update(content_status="READY")
        cleanup_stuck_generations()
        send.assert_not_called()
