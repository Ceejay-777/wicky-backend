from apps.ai.tests.helpers import ai_result
import pytest
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.learning.models import (
    Subject, Topic, TopicProgress, QuizAttempt, UserSubjectProgress,
)
from apps.ai.exceptions import ProviderError
from apps.learning.services import (
    request_quiz_generation, build_quiz, QUIZ_STALE_AFTER,
    submit_quiz, mark_resource_links_viewed,
    can_take_advanced_quiz, _next_attempt_number, _validate_quiz_response,
    _quiz_available_at, get_topic_progress,
)
from apps.learning.exceptions import GenerationError

User = get_user_model()


def generate_quiz(user, topic, quiz_type, prior_missed_questions=None):
    """Request a quiz. Tasks run eagerly in tests, so the attempt is READY (or FAILED) on return."""
    return request_quiz_generation(user, topic, quiz_type, prior_missed_questions)[0]


@pytest.fixture
def user():
    return User.objects.create_user(email="a@b.com", password="p")


@pytest.fixture
def subject():
    return Subject.objects.create(name="World War II")


@pytest.fixture
def topic(subject):
    return Topic.objects.create(
        subject=subject, title="Causes of WW2", level=1, order=1,
        summary="The Treaty of Versailles, rise of fascism, and failure of appeasement led to WW2.",
        resource_links=[
            {"title": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Causes_of_WW2", "type": "wikipedia"},
            {"title": "Article", "url": "https://example.com/article", "type": "article"},
        ],
    )


def _make_quiz_data(questions_count=5, total_points=100):
    return {
        "questions": [
            {
                "question": f"Test question {i}?",
                "options": [f"Option A for {i}", f"Option B for {i}", f"Option C for {i}", f"Option D for {i}"],
                "correct_index": 0,
                "explanation": f"This is why answer A is correct for question {i}.",
            }
            for i in range(questions_count)
        ],
        "total_points": total_points,
    }


@pytest.mark.django_db
class TestGenerateQuiz:

    @patch("apps.learning.services.generate_content")
    def test_normal_quiz_creates_attempt(self, mock_gen, user, topic):
        mock_gen.return_value = ai_result(_make_quiz_data(5, 100))
        attempt = generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        assert attempt.quiz_type == QuizAttempt.QuizType.NORMAL
        assert attempt.attempt_number == 1
        assert len(attempt.questions) == 5
        assert attempt.total_points == 100
        assert attempt.user == user
        assert attempt.topic == topic

    @patch("apps.learning.services.generate_content")
    def test_advanced_quiz_creates_attempt(self, mock_gen, user, topic):
        mock_gen.return_value = ai_result(_make_quiz_data(10, 200))
        TopicProgress.objects.create(
            user=user, topic=topic,
            status=TopicProgress.Status.PASSED,
            resource_links_viewed_at=timezone.now(),
        )
        attempt = generate_quiz(user, topic, QuizAttempt.QuizType.ADVANCED)
        assert attempt.quiz_type == QuizAttempt.QuizType.ADVANCED
        assert len(attempt.questions) == 10
        assert attempt.total_points == 200

    @patch("apps.learning.services.generate_content")
    def test_advanced_quiz_blocked_without_pass(self, mock_gen, user, topic):
        with pytest.raises(ValueError, match="Advanced quiz not available"):
            generate_quiz(user, topic, QuizAttempt.QuizType.ADVANCED)

    @patch("apps.learning.services.generate_content")
    def test_advanced_quiz_blocked_without_resource_links(self, mock_gen, user, topic):
        TopicProgress.objects.create(
            user=user, topic=topic, status=TopicProgress.Status.PASSED,
        )
        with pytest.raises(ValueError, match="Advanced quiz not available"):
            generate_quiz(user, topic, QuizAttempt.QuizType.ADVANCED)

    @patch("apps.learning.services.generate_content")
    def test_advanced_quiz_blocked_after_advanced_passed(self, mock_gen, user, topic):
        TopicProgress.objects.create(
            user=user, topic=topic,
            status=TopicProgress.Status.ADVANCED_PASSED,
            resource_links_viewed_at=timezone.now(),
        )
        with pytest.raises(ValueError, match="Advanced quiz not available"):
            generate_quiz(user, topic, QuizAttempt.QuizType.ADVANCED)

    @patch("apps.learning.services.generate_content")
    def test_normal_quiz_blocked_after_pass(self, mock_gen, user, topic):
        TopicProgress.objects.create(
            user=user, topic=topic, status=TopicProgress.Status.PASSED,
        )
        with pytest.raises(ValueError, match="Normal quiz already passed"):
            generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)

    @patch("apps.learning.services.generate_content")
    def test_attempt_number_increments(self, mock_gen, user, topic):
        mock_gen.return_value = ai_result(_make_quiz_data(5, 100))
        a1 = generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        assert a1.attempt_number == 1
        submit_quiz(a1.id, [1] * 5)
        QuizAttempt.objects.filter(id=a1.id).update(
            created_at=timezone.now() - timedelta(hours=2),
        )
        a2 = generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        assert a2.attempt_number == 2

    @patch("apps.learning.services.generate_content")
    def test_attempt_number_independent_per_type(self, mock_gen, user, topic):
        mock_gen.return_value = ai_result(_make_quiz_data(5, 100))
        a1 = generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        assert a1.attempt_number == 1
        QuizAttempt.objects.filter(id=a1.id).update(
            created_at=timezone.now() - timedelta(hours=2),
        )
        tp = TopicProgress.objects.get(user=user, topic=topic)
        tp.status = TopicProgress.Status.PASSED
        tp.resource_links_viewed_at = timezone.now()
        tp.save(update_fields=["status", "resource_links_viewed_at"])
        mock_gen.return_value = ai_result(_make_quiz_data(10, 200))
        a2 = generate_quiz(user, topic, QuizAttempt.QuizType.ADVANCED)
        assert a2.attempt_number == 1

    @patch("apps.learning.services.generate_content")
    def test_increments_normal_quiz_attempts_on_submit(self, mock_gen, user, topic):
        mock_gen.return_value = ai_result(_make_quiz_data(5, 100))
        a1 = generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        tp = TopicProgress.objects.get(user=user, topic=topic)
        assert tp.normal_quiz_attempts == 0
        submit_quiz(a1.id, [0, 0, 0, 0, 0])
        tp.refresh_from_db()
        assert tp.normal_quiz_attempts == 1

    @patch("apps.learning.services.generate_content")
    def test_increments_advanced_quiz_attempts_on_submit(self, mock_gen, user, topic):
        TopicProgress.objects.create(
            user=user, topic=topic,
            status=TopicProgress.Status.PASSED,
            resource_links_viewed_at=timezone.now(),
        )
        mock_gen.return_value = ai_result(_make_quiz_data(10, 200))
        a1 = generate_quiz(user, topic, QuizAttempt.QuizType.ADVANCED)
        tp = TopicProgress.objects.get(user=user, topic=topic)
        assert tp.advanced_quiz_attempts == 0
        submit_quiz(a1.id, [0] * 10)
        tp.refresh_from_db()
        assert tp.advanced_quiz_attempts == 1

    @patch("apps.learning.services.generate_content")
    def test_retake_cooldown(self, mock_gen, user, topic):
        mock_gen.return_value = ai_result(_make_quiz_data(5, 100))
        a1 = generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        submit_quiz(a1.id, [1] * 5)
        with pytest.raises(ValueError, match="Please wait 60 minutes"):
            generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)

    @patch("apps.learning.services.generate_content")
    def test_unsubmitted_attempt_does_not_start_cooldown(self, mock_gen, user, topic):
        mock_gen.return_value = ai_result(_make_quiz_data(5, 100))
        generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        assert _quiz_available_at(user, topic, QuizAttempt.QuizType.NORMAL) is None

    @patch("apps.learning.services.generate_content")
    def test_retake_cooldown_expires(self, mock_gen, user, topic):
        mock_gen.return_value = ai_result(_make_quiz_data(5, 100))
        a1 = generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        submit_quiz(a1.id, [1] * 5)
        QuizAttempt.objects.filter(user=user, topic=topic).update(
            created_at=timezone.now() - timedelta(hours=2),
        )
        a2 = generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        assert a2.attempt_number == 2

    @patch("apps.learning.services.generate_content")
    def test_prior_missed_included_in_prompt(self, mock_gen, user, topic):
        mock_gen.return_value = ai_result(_make_quiz_data(5, 100))
        with patch("apps.learning.services.generate_content") as gen:
            gen.return_value = ai_result(_make_quiz_data(5, 100))
            generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL,
                          prior_missed_questions=["What caused WW2?"])
            call_kwargs = gen.call_args[1]
            assert "What caused WW2?" in call_kwargs["prompt"]

    @patch("apps.learning.services.generate_content")
    def test_malformed_retry_then_succeeds(self, mock_gen, user, topic):
        mock_gen.side_effect = [
            ai_result({"questions": [], "total_points": 50}),
            ai_result(_make_quiz_data(5, 100)),
        ]
        attempt = generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        assert len(attempt.questions) == 5
        assert mock_gen.call_count == 2

    @patch("apps.learning.services.generate_content")
    def test_malformed_exhausted_marks_failed(self, mock_gen, user, topic):
        mock_gen.return_value = ai_result({"questions": [], "total_points": 50})
        attempt = generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
        assert attempt.status == QuizAttempt.Status.FAILED
        assert mock_gen.call_count == 2


@pytest.mark.django_db
class TestSubmitQuiz:

    VALID_ANSWERS = [0, 0, 0, 0, 0]

    def _create_attempt(self, user, topic, quiz_type="NORMAL", total_points=100):
        return QuizAttempt.objects.create(
            user=user, topic=topic, quiz_type=quiz_type,
            attempt_number=1,
            questions=_make_quiz_data(5, total_points)["questions"],
            total_points=total_points,
        )

    def test_passed_updates_score_and_status(self, user, topic):
        attempt = self._create_attempt(user, topic)
        result = submit_quiz(attempt.id, self.VALID_ANSWERS)
        assert result.passed is True
        assert result.score > 0
        assert result.answers == self.VALID_ANSWERS

    def test_failed_sets_passed_false(self, user, topic):
        attempt = self._create_attempt(user, topic)
        wrong = [1, 2, 3, 0, 1]
        result = submit_quiz(attempt.id, wrong)
        assert result.passed is False
        assert result.score == 0

    def test_passed_earns_points_on_topic_progress(self, user, topic):
        attempt = self._create_attempt(user, topic)
        result = submit_quiz(attempt.id, self.VALID_ANSWERS)
        tp = TopicProgress.objects.get(user=user, topic=topic)
        assert tp.points == result.score

    def test_passed_earns_points_on_subject_progress(self, user, topic, subject):
        usp = UserSubjectProgress.objects.create(user=user, subject=subject)
        attempt = self._create_attempt(user, topic)
        result = submit_quiz(attempt.id, self.VALID_ANSWERS)
        usp.refresh_from_db()
        assert usp.points == result.score

    def test_failed_earns_no_points(self, user, topic):
        attempt = self._create_attempt(user, topic)
        wrong = [1, 2, 3, 0, 1]
        submit_quiz(attempt.id, wrong)
        tp = TopicProgress.objects.filter(user=user, topic=topic).first()
        assert tp is None or tp.points == 0

    def test_passed_normal_updates_status_to_passed(self, user, topic):
        attempt = self._create_attempt(user, topic)
        submit_quiz(attempt.id, self.VALID_ANSWERS)
        tp = TopicProgress.objects.get(user=user, topic=topic)
        assert tp.status == TopicProgress.Status.PASSED
        assert tp.completed_at is not None

    def test_passed_advanced_updates_status(self, user, topic):
        TopicProgress.objects.create(
            user=user, topic=topic, status=TopicProgress.Status.PASSED,
        )
        attempt = self._create_attempt(user, topic, "ADVANCED", 200)
        submit_quiz(attempt.id, self.VALID_ANSWERS)
        tp = TopicProgress.objects.get(user=user, topic=topic)
        assert tp.status == TopicProgress.Status.ADVANCED_PASSED

    def test_wrong_answer_count_raises(self, user, topic):
        attempt = self._create_attempt(user, topic)
        with pytest.raises(ValueError, match="Expected 5 answers"):
            submit_quiz(attempt.id, [0, 1, 2])

    def test_duplicate_submission_raises(self, user, topic):
        attempt = self._create_attempt(user, topic)
        submit_quiz(attempt.id, self.VALID_ANSWERS)
        with pytest.raises(ValueError, match="already submitted"):
            submit_quiz(attempt.id, self.VALID_ANSWERS)

    def test_normal_quiz_passed_does_not_allow_retake(self, user, topic):
        attempt = self._create_attempt(user, topic)
        submit_quiz(attempt.id, self.VALID_ANSWERS)
        with pytest.raises(ValueError, match="Normal quiz already passed"):
            with patch("apps.learning.services.generate_content") as mock_gen:
                mock_gen.return_value = ai_result(_make_quiz_data(5, 100))
                generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)

    def test_advanced_quiz_passed_does_not_allow_retake(self, user, topic):
        TopicProgress.objects.create(
            user=user, topic=topic, status=TopicProgress.Status.PASSED,
            resource_links_viewed_at=timezone.now(),
        )
        attempt = self._create_attempt(user, topic, "ADVANCED", 200)
        submit_quiz(attempt.id, self.VALID_ANSWERS)
        assert not can_take_advanced_quiz(user, topic)


def _open_attempt(user, topic, **kw):
    kw.setdefault("status", QuizAttempt.Status.GENERATING)
    kw.setdefault("generation_started_at", timezone.now())
    return QuizAttempt.objects.create(
        user=user, topic=topic, quiz_type="NORMAL", attempt_number=1,
        questions=[], **kw,
    )


@pytest.mark.django_db
class TestRequestQuizGeneration:

    @patch("apps.learning.tasks.generate_quiz_for_attempt.delay")
    def test_creates_generating_attempt_and_queues_once(self, mock_delay, user, topic):
        attempt, started = request_quiz_generation(
            user, topic, "normal", ["Missed one?"]
        )
        assert started is True
        assert attempt.status == QuizAttempt.Status.GENERATING
        assert attempt.questions == []
        mock_delay.assert_called_once_with(attempt.id, ["Missed one?"])

    @patch("apps.learning.tasks.generate_quiz_for_attempt.delay")
    def test_second_call_returns_same_attempt_without_queueing(self, mock_delay, user, topic):
        a1, _ = request_quiz_generation(user, topic, "NORMAL")
        a2, started = request_quiz_generation(user, topic, "NORMAL")
        assert a2.id == a1.id and started is False
        assert mock_delay.call_count == 1
        assert QuizAttempt.objects.count() == 1

    @patch("apps.learning.services.generate_content")
    def test_ready_attempt_is_resumed_with_no_ai_call(self, mock_gen, user, topic):
        ready = _open_attempt(user, topic, status=QuizAttempt.Status.READY)
        attempt, started = request_quiz_generation(user, topic, "NORMAL")
        assert attempt.id == ready.id and started is False
        mock_gen.assert_not_called()

    @patch("apps.learning.tasks.generate_quiz_for_attempt.delay")
    def test_failed_attempt_is_requeued_on_the_same_row(self, mock_delay, user, topic):
        failed = _open_attempt(user, topic, status=QuizAttempt.Status.FAILED)
        attempt, started = request_quiz_generation(user, topic, "NORMAL")
        assert attempt.id == failed.id and started is True
        assert attempt.status == QuizAttempt.Status.GENERATING
        assert attempt.attempt_number == 1
        mock_delay.assert_called_once()

    @patch("apps.learning.tasks.generate_quiz_for_attempt.delay")
    def test_stale_generating_attempt_is_reclaimed(self, mock_delay, user, topic):
        _open_attempt(
            user, topic,
            generation_started_at=timezone.now() - QUIZ_STALE_AFTER - timedelta(minutes=1),
        )
        request_quiz_generation(user, topic, "NORMAL")
        mock_delay.assert_called_once()

    @patch("apps.learning.tasks.generate_quiz_for_attempt.delay")
    def test_fresh_generating_attempt_is_left_alone(self, mock_delay, user, topic):
        _open_attempt(user, topic)
        request_quiz_generation(user, topic, "NORMAL")
        mock_delay.assert_not_called()

    @patch("apps.learning.tasks.generate_quiz_for_attempt.delay")
    def test_recheck_under_lock_returns_the_winner(self, mock_delay, user, topic):
        winner = _open_attempt(user, topic)
        # First look finds nothing; by the time the row lock is ours the winner exists.
        with patch("apps.learning.services.get_open_quiz_attempt", side_effect=[None, winner]):
            attempt, started = request_quiz_generation(user, topic, "NORMAL")
        assert attempt.id == winner.id and started is False
        mock_delay.assert_not_called()
        assert QuizAttempt.objects.count() == 1

    @patch("apps.learning.tasks.generate_quiz_for_attempt.delay")
    def test_broker_down_marks_failed(self, mock_delay, user, topic):
        from kombu.exceptions import OperationalError

        mock_delay.side_effect = OperationalError("broker down")
        attempt, _ = request_quiz_generation(user, topic, "NORMAL")
        assert attempt.status == QuizAttempt.Status.FAILED

    @patch("apps.learning.tasks.generate_quiz_for_attempt.delay")
    def test_eligibility_errors_create_nothing(self, mock_delay, user, topic):
        with pytest.raises(ValueError, match="Advanced quiz not available"):
            request_quiz_generation(user, topic, "ADVANCED")
        assert QuizAttempt.objects.count() == 0
        mock_delay.assert_not_called()


@pytest.mark.django_db
class TestGenerateQuizTask:

    @patch("apps.learning.events.send_event")
    @patch("apps.learning.services.generate_content")
    def test_success_marks_ready_and_notifies_only_the_owner(self, mock_gen, mock_send, user, topic):
        mock_gen.return_value = ai_result(_make_quiz_data(5, 100))
        attempt = generate_quiz(user, topic, "NORMAL")
        assert attempt.status == QuizAttempt.Status.READY
        mock_send.assert_called_once()
        channel, event, payload = mock_send.call_args[0]
        assert channel == f"user-{user.id}" and event == "quiz.status"
        assert payload == {
            "subject_id": topic.subject_id, "topic_id": topic.id,
            "attempt_id": attempt.id, "quiz_type": "NORMAL", "status": "READY",
        }

    @patch("apps.learning.services.generate_content")
    def test_redelivered_task_makes_no_ai_call(self, mock_gen, user, topic):
        ready = _open_attempt(user, topic, status=QuizAttempt.Status.READY)
        assert build_quiz(ready.id) is False
        mock_gen.assert_not_called()

    @patch("apps.learning.services.generate_content")
    def test_provider_error_marks_failed_only_on_last_retry(self, mock_gen, user, topic):
        from apps.learning.tasks import generate_quiz_for_attempt

        mock_gen.side_effect = ProviderError("down", provider="groq")
        attempt = _open_attempt(user, topic)
        with pytest.raises(ProviderError):
            generate_quiz_for_attempt(attempt.id)
        attempt.refresh_from_db()
        assert attempt.status == QuizAttempt.Status.GENERATING

        generate_quiz_for_attempt.push_request(retries=generate_quiz_for_attempt.max_retries)
        try:
            with pytest.raises(ProviderError):
                generate_quiz_for_attempt(attempt.id)
        finally:
            generate_quiz_for_attempt.pop_request()
        attempt.refresh_from_db()
        assert attempt.status == QuizAttempt.Status.FAILED

    def test_submit_requires_ready(self, user, topic):
        attempt = _open_attempt(user, topic)
        with pytest.raises(ValueError, match="not ready"):
            submit_quiz(attempt.id, [0] * 5)

    def test_cleanup_fails_stale_generating_attempts(self, user, topic):
        from apps.learning.tasks import cleanup_stuck_generations

        stale = _open_attempt(
            user, topic,
            generation_started_at=timezone.now() - QUIZ_STALE_AFTER - timedelta(minutes=1),
        )
        cleanup_stuck_generations()
        stale.refresh_from_db()
        assert stale.status == QuizAttempt.Status.FAILED


@pytest.mark.django_db
class TestSubmitTimestamps:

    @patch("apps.learning.services.generate_content")
    def test_submit_sets_submitted_at_and_advanced_completed_at(self, mock_gen, user, topic):
        UserSubjectProgress.objects.create(user=user, subject=topic.subject)
        TopicProgress.objects.create(
            user=user, topic=topic, status=TopicProgress.Status.PASSED,
            resource_links_viewed_at=timezone.now(),
        )
        mock_gen.return_value = ai_result(_make_quiz_data(10, 200))
        a = generate_quiz(user, topic, QuizAttempt.QuizType.ADVANCED)
        assert a.submitted_at is None
        a = submit_quiz(a.id, [0] * 10)
        assert a.submitted_at is not None
        progress = get_topic_progress(user, topic)
        assert progress["advanced_completed_at"] is not None


@pytest.mark.django_db
class TestValidateQuizResponse:

    def test_valid_normal_passes(self):
        data = _make_quiz_data(5, 100)
        result = _validate_quiz_response(data, QuizAttempt.QuizType.NORMAL)
        assert result == data

    def test_valid_advanced_passes(self):
        data = _make_quiz_data(10, 200)
        result = _validate_quiz_response(data, QuizAttempt.QuizType.ADVANCED)
        assert result == data

    def test_wrong_question_count_normal(self):
        data = _make_quiz_data(2, 100)
        with pytest.raises(GenerationError, match="4-6"):
            _validate_quiz_response(data, QuizAttempt.QuizType.NORMAL)

    def test_wrong_question_count_advanced(self):
        data = _make_quiz_data(5, 200)
        with pytest.raises(GenerationError, match="9-12"):
            _validate_quiz_response(data, QuizAttempt.QuizType.ADVANCED)

    def test_wrong_points_normal(self):
        data = _make_quiz_data(5, 50)
        with pytest.raises(GenerationError, match="90-110"):
            _validate_quiz_response(data, QuizAttempt.QuizType.NORMAL)

    def test_wrong_points_advanced(self):
        data = _make_quiz_data(10, 100)
        with pytest.raises(GenerationError, match="180-220"):
            _validate_quiz_response(data, QuizAttempt.QuizType.ADVANCED)

    def test_missing_question_text(self):
        data = _make_quiz_data(5, 100)
        data["questions"][0]["question"] = ""
        with pytest.raises(GenerationError, match="missing text"):
            _validate_quiz_response(data, QuizAttempt.QuizType.NORMAL)

    def test_invalid_correct_index(self):
        data = _make_quiz_data(5, 100)
        data["questions"][0]["correct_index"] = 5
        with pytest.raises(GenerationError, match="correct_index"):
            _validate_quiz_response(data, QuizAttempt.QuizType.NORMAL)

    def test_missing_explanation(self):
        data = _make_quiz_data(5, 100)
        data["questions"][0]["explanation"] = ""
        with pytest.raises(GenerationError, match="missing explanation"):
            _validate_quiz_response(data, QuizAttempt.QuizType.NORMAL)

    def test_strips_letter_prefix_from_options(self):
        data = _make_quiz_data(4, 100)
        data["questions"][0]["options"] = ["a) The Treaty of Versailles", "B. Rise of fascism", "c) Failure of appeasement", "d) Invasion of Poland"]
        data["questions"][1]["options"] = ["A) foo", "b) bar", "C. baz", "d qux"]
        result = _validate_quiz_response(data, QuizAttempt.QuizType.NORMAL)
        assert result["questions"][0]["options"] == [
            "The Treaty of Versailles", "Rise of fascism", "Failure of appeasement", "Invasion of Poland",
        ]
        assert result["questions"][1]["options"] == ["foo", "bar", "baz", "qux"]

    def test_options_without_prefix_unchanged(self):
        data = _make_quiz_data(4, 100)
        data["questions"][0]["options"] = ["The Treaty of Versailles", "Rise of fascism", "Failure of appeasement", "Invasion of Poland"]
        original = list(data["questions"][0]["options"])
        result = _validate_quiz_response(data, QuizAttempt.QuizType.NORMAL)
        assert result["questions"][0]["options"] == original

    def test_options_empty_after_strip_raises(self):
        data = _make_quiz_data(4, 100)
        data["questions"][0]["options"] = ["a)", "b)", "c)", "d)"]
        with pytest.raises(GenerationError, match="empty after stripping prefix"):
            _validate_quiz_response(data, QuizAttempt.QuizType.NORMAL)

    def test_prompt_contains_no_letter_label_instruction(self, user, topic):
        with patch("apps.learning.services.generate_content") as gen:
            gen.return_value = ai_result(_make_quiz_data(5, 100))
            generate_quiz(user, topic, QuizAttempt.QuizType.NORMAL)
            prompt = gen.call_args[1]["prompt"]
            assert "labeled a/b/c/d" not in prompt
            assert "no 'a)'" in prompt or "no a)" in prompt.lower()


@pytest.mark.django_db
class TestCanTakeAdvancedQuiz:

    def test_no_topic_progress_returns_false(self, user, topic):
        assert can_take_advanced_quiz(user, topic) is False

    def test_not_passed_normal_returns_false(self, user, topic):
        TopicProgress.objects.create(user=user, topic=topic, status=TopicProgress.Status.READING)
        assert can_take_advanced_quiz(user, topic) is False

    def test_passed_but_no_resource_links_returns_false(self, user, topic):
        TopicProgress.objects.create(user=user, topic=topic, status=TopicProgress.Status.PASSED)
        assert can_take_advanced_quiz(user, topic) is False

    def test_passed_and_viewed_links_returns_true(self, user, topic):
        TopicProgress.objects.create(
            user=user, topic=topic,
            status=TopicProgress.Status.PASSED,
            resource_links_viewed_at=timezone.now(),
        )
        assert can_take_advanced_quiz(user, topic) is True

    def test_advanced_already_passed_returns_false(self, user, topic):
        TopicProgress.objects.create(
            user=user, topic=topic,
            status=TopicProgress.Status.ADVANCED_PASSED,
            resource_links_viewed_at=timezone.now(),
        )
        assert can_take_advanced_quiz(user, topic) is False


@pytest.mark.django_db
class TestMarkResourceLinksViewed:

    def test_creates_topic_progress_if_missing(self, user, topic):
        tp = mark_resource_links_viewed(user, topic)
        assert tp.resource_links_viewed_at is not None
        assert tp.user == user
        assert tp.topic == topic

    def test_updates_existing(self, user, topic):
        tp = TopicProgress.objects.create(user=user, topic=topic)
        assert tp.resource_links_viewed_at is None
        tp2 = mark_resource_links_viewed(user, topic)
        assert tp2.resource_links_viewed_at is not None


@pytest.mark.django_db
class TestNextAttemptNumber:

    def test_first_attempt(self, user, topic):
        assert _next_attempt_number(user, topic, QuizAttempt.QuizType.NORMAL) == 1

    def test_second_attempt(self, user, topic):
        QuizAttempt.objects.create(
            user=user, topic=topic, quiz_type=QuizAttempt.QuizType.NORMAL,
            attempt_number=1, questions=[],
        )
        assert _next_attempt_number(user, topic, QuizAttempt.QuizType.NORMAL) == 2

    def test_independent_per_type(self, user, topic):
        QuizAttempt.objects.create(
            user=user, topic=topic, quiz_type=QuizAttempt.QuizType.NORMAL,
            attempt_number=1, questions=[],
        )
        QuizAttempt.objects.create(
            user=user, topic=topic, quiz_type=QuizAttempt.QuizType.ADVANCED,
            attempt_number=1, questions=[],
        )
        assert _next_attempt_number(user, topic, QuizAttempt.QuizType.NORMAL) == 2
        assert _next_attempt_number(user, topic, QuizAttempt.QuizType.ADVANCED) == 2
