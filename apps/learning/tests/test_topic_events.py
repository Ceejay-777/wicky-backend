from unittest.mock import patch

import pytest
from celery.exceptions import Retry
from django.contrib.auth import get_user_model

from datetime import timedelta

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
REVIEW = "apps.ai.services.review_content"
PASSED = ai_result({"overall_passed": True})
FAILED = ai_result({"overall_passed": False})


@pytest.fixture
def enrolled():
    return User.objects.create_user(email="in@b.com", password="p")


@pytest.fixture
def topic(enrolled):
    subject = Subject.objects.create(name="Sourdough")
    UserSubjectProgress.objects.create(user=enrolled, subject=subject)
    User.objects.create_user(email="out@b.com", password="p")  # not enrolled
    return Topic.objects.create(
        subject=subject, title="T", level=1, order=1, summary="content"
    )


@pytest.mark.django_db
class TestTopicStatusEvent:
    @patch(SEND)
    @patch(REVIEW, return_value=PASSED)
    def test_ready_is_published_to_enrolled_users_only(self, _rev, send, topic, enrolled):
        review_content_for_topic(topic.id)

        send.assert_called_once_with(
            f"user-{enrolled.id}",
            "topic.status",
            {
                "subject_id": topic.subject_id,
                "topic_id": topic.id,
                "content_status": "READY",
                "review_status": "PASSED",
            },
        )

    @patch(SEND)
    @patch("apps.learning.tasks.generate_content_for_topic.delay")
    @patch(REVIEW, return_value=FAILED)
    def test_failed_is_published_when_reviews_exhausted(self, _rev, _gen, send, topic):
        Topic.objects.filter(id=topic.id).update(review_attempts=1)

        review_content_for_topic(topic.id)

        assert send.call_args.args[2]["content_status"] == "FAILED"

    @patch(SEND)
    @patch("apps.learning.tasks.generate_content_for_topic.delay")
    @patch(REVIEW, return_value=FAILED)
    def test_regeneration_is_not_published(self, _rev, _gen, send, topic):
        review_content_for_topic(topic.id)  # first miss: regenerates, not final

        send.assert_not_called()

    @patch(SEND, side_effect=RuntimeError("redis down"))
    @patch(REVIEW, return_value=PASSED)
    def test_publish_failure_does_not_fail_the_task(self, _rev, _send, topic):
        review_content_for_topic(topic.id)

        topic.refresh_from_db()
        assert topic.content_status == Topic.ContentStatus.READY


@pytest.mark.django_db
class TestProviderDownOnLastRetry:
    def _run(self, topic, retries):
        task = generate_content_for_topic
        task.push_request(retries=retries)
        try:
            with patch("apps.learning.services.generate_content",
                       side_effect=ProviderError("down", provider="groq")):
                with pytest.raises(ProviderError):
                    task(topic.id)
        finally:
            task.pop_request()

    @patch(SEND)
    def test_last_retry_marks_failed_and_publishes(self, send, topic):
        Topic.objects.filter(id=topic.id).update(content_status="GENERATING")
        self._run(topic, retries=generate_content_for_topic.max_retries)
        topic.refresh_from_db()
        assert topic.content_status == "FAILED"
        assert send.call_args.args[2]["content_status"] == "FAILED"

    @patch(SEND)
    def test_earlier_retry_leaves_topic_generating(self, send, topic):
        Topic.objects.filter(id=topic.id).update(content_status="GENERATING")
        self._run(topic, retries=0)
        topic.refresh_from_db()
        assert topic.content_status == "GENERATING"
        send.assert_not_called()


@pytest.mark.django_db
class TestReviewOutage:
    """A review outage is retried by Celery and never fails the topic."""

    @pytest.fixture
    def generating(self, topic):
        Topic.objects.filter(id=topic.id).update(content_status="GENERATING")
        return topic

    @patch(SEND)
    @patch(REVIEW, side_effect=ProviderError("down", provider="groq"))
    def test_outage_triggers_a_celery_retry(self, _rev, send, generating):
        task = review_content_for_topic
        task.push_request(retries=0)
        try:
            with patch.object(task, "retry", side_effect=Retry()) as retry:
                with pytest.raises(Retry):
                    task(generating.id)
        finally:
            task.pop_request()
        assert isinstance(retry.call_args.kwargs["exc"], ProviderError)
        generating.refresh_from_db()
        assert generating.content_status == "GENERATING"
        send.assert_not_called()

    @patch(SEND)
    @patch("apps.learning.tasks.generate_content_for_topic.delay")
    @patch(REVIEW, side_effect=ProviderError("down", provider="groq"))
    def test_outage_at_retry_exhaustion_publishes_instead_of_failing(
        self, _rev, gen_delay, send, generating
    ):
        task = review_content_for_topic
        task.push_request(retries=task.max_retries)
        try:
            with pytest.raises(ProviderError):
                task(generating.id)
        finally:
            task.pop_request()
        generating.refresh_from_db()
        assert generating.content_status == "READY"
        assert generating.review_status == "PENDING"
        assert generating.review_attempts == 0
        gen_delay.assert_not_called()
        assert send.call_args.args[2]["content_status"] == "READY"


@pytest.mark.django_db
class TestCleanupPublishes:
    @patch(SEND)
    def test_reset_topics_are_published(self, send, topic):
        Topic.objects.filter(id=topic.id).update(
            content_status="GENERATING",
            generation_started_at=timezone.now() - timedelta(hours=2),
        )
        cleanup_stuck_generations()
        assert send.call_args.args[2]["content_status"] == "NOT_GENERATED"

    @patch(SEND)
    def test_nothing_published_when_nothing_stuck(self, send, topic):
        cleanup_stuck_generations()
        send.assert_not_called()
