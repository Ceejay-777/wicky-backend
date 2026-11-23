from datetime import timedelta

import sentry_sdk
from celery import shared_task
from django.utils import timezone

from apps.ai.exceptions import ProviderError

# Topic content runs as three stages, one task each. Tasks hold no decisions:
# they call apps/learning/pipeline.py and, on their last retry, its give-up path.
_STAGE_TASK = dict(
    bind=True,
    autoretry_for=(ProviderError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)


def _last_retry(task) -> bool:
    return task.request.retries >= task.max_retries


@shared_task(name="learning.generate_content_for_topic", **_STAGE_TASK)
def generate_content_for_topic(self, topic_id: int) -> None:
    from .exceptions import GenerationError
    from .pipeline import generate_topic_content, mark_generation_failed

    # The caller already claimed the topic (request_topic_generation).
    try:
        generate_topic_content(topic_id)
    except GenerationError:
        sentry_sdk.capture_message(
            f"Topic {topic_id}: content validation failed after retries",
            level="error",
        )
        mark_generation_failed(topic_id)
    except ProviderError:
        # Re-raised so autoretry_for can retry; only the last attempt surfaces FAILED.
        if _last_retry(self):
            mark_generation_failed(topic_id)
        raise


@shared_task(name="learning.review_content_for_topic", **_STAGE_TASK)
def review_content_for_topic(self, topic_id: int) -> None:
    from .pipeline import give_up_review, review_topic_content

    try:
        review_topic_content(topic_id)
    except ProviderError:
        # An outage is not a verdict (D6): on the last retry, publish UNREVIEWED.
        if _last_retry(self):
            give_up_review(topic_id)
        raise


@shared_task(name="learning.repair_content_for_topic", **_STAGE_TASK)
def repair_content_for_topic(self, topic_id: int) -> None:
    from .pipeline import give_up_review, repair_topic_content

    try:
        repair_topic_content(topic_id)
    except ProviderError:
        # Review is not re-run: on the last retry, publish the original as UNREVIEWED.
        if _last_retry(self):
            give_up_review(topic_id)
        raise


@shared_task(
    bind=True,
    name="learning.generate_roadmap_for_subject",
    autoretry_for=(ProviderError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=3,
)
def generate_roadmap_for_subject(self, subject_id: int) -> None:
    """Generate a subject's roadmap in the background.

    The subject is already GENERATING (claimed by ``request_roadmap_generation``
    before dispatch). Building is idempotent, so a redelivered task is harmless.
    """
    from .events import publish_roadmap_status
    from .exceptions import GenerationError
    from .models import Subject
    from .services import build_roadmap, generate_initial_batch, set_roadmap_status

    try:
        subject = build_roadmap(subject_id)
    except GenerationError:
        sentry_sdk.capture_message(
            f"Subject {subject_id}: roadmap validation failed after retries",
            level="error",
        )
        set_roadmap_status(subject_id, Subject.RoadmapStatus.FAILED)
        publish_roadmap_status(Subject.objects.get(id=subject_id))
        return
    except ProviderError:
        # Re-raised so autoretry_for can retry; only on the last attempt do we
        # also surface FAILED, because no further retry will update the state.
        if self.request.retries >= self.max_retries:
            set_roadmap_status(subject_id, Subject.RoadmapStatus.FAILED)
            publish_roadmap_status(Subject.objects.get(id=subject_id))
        raise

    set_roadmap_status(subject_id, Subject.RoadmapStatus.READY)
    publish_roadmap_status(subject)
    generate_initial_batch(subject)


@shared_task(
    bind=True,
    name="learning.generate_quiz_for_attempt",
    autoretry_for=(ProviderError,),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    max_retries=2,  # each retry reruns the AI chain
)
def generate_quiz_for_attempt(self, attempt_id: int, prior_missed_questions=None) -> None:
    """Fill in a GENERATING quiz attempt claimed by ``request_quiz_generation``."""
    from .events import publish_quiz_status
    from .exceptions import GenerationError
    from .services import build_quiz, mark_quiz_failed

    try:
        built = build_quiz(attempt_id, prior_missed_questions)
    except GenerationError:
        sentry_sdk.capture_message(
            f"Quiz attempt {attempt_id}: validation failed after retries",
            level="error",
        )
        mark_quiz_failed(attempt_id)
        return
    except ProviderError:
        # Re-raised so autoretry_for can retry; only on the last attempt do we
        # also surface FAILED, because no further retry will update the state.
        if self.request.retries >= self.max_retries:
            mark_quiz_failed(attempt_id)
        raise

    if built:
        publish_quiz_status(attempt_id)


@shared_task(name="learning.dispatch_notifications")
def dispatch_notifications():
    from .services import get_due_notifications

    due = get_due_notifications()
    for usp in due:
        send_notification.delay(usp.id)


@shared_task(
    bind=True, name="learning.send_notification", max_retries=2, default_retry_delay=300
)
def send_notification(self, user_subject_progress_id):
    import logging
    from datetime import timedelta

    from django.utils import timezone

    from .models import Topic, UserSubjectProgress
    from .services import advance_due_time

    logger = logging.getLogger(__name__)

    try:
        usp = UserSubjectProgress.objects.select_related("subject").get(
            id=user_subject_progress_id,
        )
    except UserSubjectProgress.DoesNotExist:
        logger.warning(
            "UserSubjectProgress %s not found, skipping", user_subject_progress_id
        )
        return

    original_due = usp.next_due_at

    next_topic = (
        Topic.objects.filter(
            subject=usp.subject,
            content_status__in=(
                Topic.ContentStatus.NOT_GENERATED,
                Topic.ContentStatus.FAILED,
            ),
        )
        .exclude(review_status=Topic.ReviewStatus.REJECTED)  # restarts only via the retry endpoint
        .order_by("order")
        .first()
    )

    if next_topic is not None:
        if self.request.retries == 0:
            from .services import request_topic_generation

            if next_topic.content_status == Topic.ContentStatus.FAILED:
                Topic.objects.filter(id=next_topic.id).update(
                    content_status=Topic.ContentStatus.NOT_GENERATED,
                )
            request_topic_generation(next_topic.id)

        next_topic.refresh_from_db()
        content_not_ready = next_topic.content_status not in (
            Topic.ContentStatus.READY,
            Topic.ContentStatus.FAILED,
        )

        if content_not_ready and self.request.retries < self.max_retries:
            raise self.retry()

    if original_due is not None:
        advance_due_time(usp, original_due)
    else:
        usp.refresh_from_db()
        usp.next_due_at = timezone.now() + timedelta(
            hours=usp.notification_frequency_hours
        )
        usp.save(update_fields=["next_due_at"])

    logger.info(
        "Notification sent for user %s on subject %s",
        usp.user_id,
        usp.subject_id,
    )


@shared_task(name="learning.cleanup_stuck_generations")
def cleanup_stuck_generations() -> None:
    from .events import publish_roadmap_status, publish_topic_status
    from .models import Subject

    cutoff = timezone.now() - timedelta(hours=1)

    # A roadmap stuck in GENERATING (worker died, task lost) is surfaced as
    # FAILED so the user can retry, rather than waiting on it forever.
    stuck_subjects = list(
        Subject.objects.filter(
            roadmap_status=Subject.RoadmapStatus.GENERATING,
            roadmap_started_at__lt=cutoff,
        )
    )
    for subject in stuck_subjects:
        Subject.objects.filter(id=subject.id).update(
            roadmap_status=Subject.RoadmapStatus.FAILED
        )
        publish_roadmap_status(subject)

    from .pipeline import sweep_stuck_topics

    reset_topic_ids = sweep_stuck_topics(cutoff)

    # A quiz the learner is waiting on is not worth an hour: surface FAILED so the page offers a retry.
    from .models import QuizAttempt
    from .services import QUIZ_STALE_AFTER, mark_quiz_failed

    stuck_quiz_ids = list(
        QuizAttempt.objects.filter(
            status=QuizAttempt.Status.GENERATING,
            generation_started_at__lt=timezone.now() - QUIZ_STALE_AFTER,
        ).values_list("id", flat=True)
    )
    for attempt_id in stuck_quiz_ids:
        mark_quiz_failed(attempt_id)

    # Tell open pages the topic changed so they refetch (opening it restarts generation).
    for topic_id in reset_topic_ids:
        publish_topic_status(topic_id)


@shared_task(name="learning.auto_select_subjects")
def auto_select_subjects() -> None:
    from django.contrib.auth import get_user_model
    from django.db.models import Q

    from .models import Subject, UserSubjectProgress
    from .services import _generate_subject_suggestions, add_subject_to_user

    cutoff = timezone.now() - timedelta(hours=24)
    User = get_user_model()

    opted_in_users = (
        User.objects.filter(
            preferences__auto_select_subjects_enabled=True,
        )
        .filter(
            Q(last_login__lt=cutoff)
            | Q(last_login__isnull=True, date_joined__lt=cutoff),
        )
        .exclude(
            subject_progress__status=UserSubjectProgress.Status.ACTIVE,
        )
        .distinct()
    )

    for user in opted_in_users:
        suggestions = _generate_subject_suggestions(user)
        if not suggestions:
            continue
        top = Subject.objects.get(id=suggestions[0]["id"])
        try:
            add_subject_to_user(user, top)
        except ValueError:
            continue
        UserSubjectProgress.objects.filter(
            user=user,
            needs_subject_selection=True,
        ).update(needs_subject_selection=False, selection_pending_since=None)
