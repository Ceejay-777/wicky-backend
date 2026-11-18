"""The topic content pipeline: generate, review, interpret, decide, repair, save.

Each stage reads its input from the database, persists its result, then queues the
next stage. A stage that finds the topic outside the state it expects does nothing,
so a redelivered or retried task never repeats an AI call whose result is saved.

Stage states (content_status stays GENERATING until a terminal state):
- generate: summary empty (the claim clears it)
- review:   summary saved, no verdict
- repair:   verdict saved, on topic, with kept issues

Celery tasks only load ids, call in here, and on their last retry call the stage's
give-up function.
"""
import logging
import re
from dataclasses import asdict, dataclass

from django.db.models import F

from apps.ai.calllog import annotate_outcome
from apps.ai.services import generate_content

from .exceptions import GenerationError
from .models import Topic
from .prompts import build_repair_prompt, build_review_prompt, build_topic_prompt

logger = logging.getLogger(__name__)

# Wide guard rails against broken output, not the 300-500 word target.
SUMMARY_MIN_WORDS = 120
SUMMARY_MAX_WORDS = 900

_VALID_LINK_TYPES = {"article", "video", "wikipedia"}

_HEADING_RE = re.compile(r"^[ \t]*#{1,6}[ \t]*", re.MULTILINE)
_BULLET_RE = re.compile(r"^[ \t]*[-*+•][ \t]+", re.MULTILINE)
_BOLD_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1")
_ITALIC_RE = re.compile(r"(?<![\w*])([*_])(?=\S)(.+?)(?<=\S)\1(?![\w*])")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n[ \t]*\n")
_SMART_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"'})
_WS_RE = re.compile(r"\s+")


def normalise_summary(text: str) -> str:
    """Fix cosmetic problems in code so they never fail a generation."""
    text = text.replace("\r\n", "\n").translate(_SMART_QUOTES)
    text = _HEADING_RE.sub("", text)
    text = _BULLET_RE.sub("", text)
    text = _BOLD_RE.sub(r"\2", text)
    text = _ITALIC_RE.sub(r"\2", text)
    paragraphs = (
        "\n".join(line.strip() for line in p.strip().split("\n"))
        for p in _PARAGRAPH_SPLIT_RE.split(text)
    )
    return "\n\n".join(p for p in paragraphs if p)


def validate_topic_content(data: dict) -> dict:
    """Normalise the summary, then reject only what cannot be fixed."""
    if not isinstance(data, dict):
        raise GenerationError("Response is not a dict", phase="topic_content_validation")
    summary = data.get("summary")
    if not isinstance(summary, str):
        raise GenerationError("Missing 'summary'", phase="topic_content_validation")
    summary = normalise_summary(summary)
    if not summary:
        raise GenerationError("Empty 'summary'", phase="topic_content_validation")
    words = len(summary.split())
    if not SUMMARY_MIN_WORDS <= words <= SUMMARY_MAX_WORDS:
        raise GenerationError(
            f"Summary has {words} words, outside {SUMMARY_MIN_WORDS}-{SUMMARY_MAX_WORDS}",
            phase="topic_content_validation",
        )
    links = data.get("resource_links")
    if not isinstance(links, list) or len(links) < 2 or len(links) > 5:
        raise GenerationError(
            f"Expected 2-5 resource_links, got {len(links) if isinstance(links, list) else 'invalid'}",
            phase="topic_content_validation",
        )
    for i, link in enumerate(links):
        if not isinstance(link, dict):
            raise GenerationError(
                f"Resource link {i} is not a dict", phase="topic_content_validation"
            )
        if not isinstance(link.get("title"), str) or not link["title"].strip():
            raise GenerationError(
                f"Resource link {i} missing valid title",
                phase="topic_content_validation",
            )
        if not isinstance(link.get("url"), str) or not link["url"].strip():
            raise GenerationError(
                f"Resource link {i} missing valid url", phase="topic_content_validation"
            )
        if link.get("type") not in _VALID_LINK_TYPES:
            raise GenerationError(
                f"Resource link {i} invalid type '{link.get('type')}'",
                phase="topic_content_validation",
            )
    return {"summary": summary, "resource_links": links}


# --- Review interpretation (code, not AI) -------------------------------------


@dataclass(frozen=True)
class ReviewIssue:
    quote: str
    problem: str
    correction: str


@dataclass(frozen=True)
class ReviewVerdict:
    on_topic: bool
    reason: str
    issues: tuple[ReviewIssue, ...]
    dropped: int

    def to_json(self) -> dict:
        return {**asdict(self), "issues": [asdict(i) for i in self.issues]}

    @classmethod
    def from_json(cls, data: dict) -> "ReviewVerdict":
        return cls(
            on_topic=data["on_topic"],
            reason=data["reason"],
            issues=tuple(ReviewIssue(**i) for i in data["issues"]),
            dropped=data["dropped"],
        )


def _match_form(text: str) -> str:
    """Case, whitespace and quote characters never decide whether a quote matches."""
    return _WS_RE.sub(" ", text.translate(_SMART_QUOTES)).strip().lower()


def interpret_review(data: dict, summary: str) -> ReviewVerdict:
    """Keep only issues whose quote really is in the summary (D4: no quote, no rejection)."""
    haystack = _match_form(summary)
    kept: list[ReviewIssue] = []
    seen: set[str] = set()
    dropped = 0
    for raw in data["issues"]:
        issue = ReviewIssue(
            *(str(raw.get(k) or "").strip() for k in ("quote", "problem", "correction"))
        )
        key = _match_form(issue.quote)
        if not (key and issue.problem and issue.correction) or key not in haystack:
            dropped += 1
        elif key not in seen:
            seen.add(key)
            kept.append(issue)
    return ReviewVerdict(
        on_topic=bool(data["on_topic"]),
        reason=str(data["reason"]).strip(),
        issues=tuple(kept),
        dropped=dropped,
    )


# --- Stages -------------------------------------------------------------------


def _neighbour_titles(topic: Topic) -> tuple[str | None, str | None]:
    # Roadmap orders are contiguous (create, append and insert all renumber).
    titles = dict(
        Topic.objects.filter(
            subject_id=topic.subject_id, order__in=(topic.order - 1, topic.order + 1)
        ).values_list("order", "title")
    )
    return titles.get(topic.order - 1), titles.get(topic.order + 1)


def _brief(topic: Topic) -> dict:
    """The same topic context for writer, reviewer and repairer."""
    previous_title, next_title = _neighbour_titles(topic)
    return {
        "subject_name": topic.subject.name,
        "title": topic.title,
        "level": topic.level,
        "goal": topic.description,
        "previous_title": previous_title,
        "next_title": next_title,
    }


def _call(topic: Topic, prompt, action: str):
    return generate_content(
        prompt.text,
        prompt.system_instruction,
        prompt.schema,
        action=action,
        prompt_version=prompt.version,
        context={"topic_id": topic.id, "subject_id": topic.subject_id},
    )


def _in_stage(topic_id: int, **state) -> Topic | None:
    return (
        Topic.objects.select_related("subject")
        .filter(id=topic_id, content_status=Topic.ContentStatus.GENERATING, **state)
        .first()
    )


def _finish(topic_id: int, content_status: str, review_status: str, **fields) -> None:
    """Terminal transition: only out of GENERATING, and announced over SSE."""
    from .events import publish_topic_status

    if Topic.objects.filter(
        id=topic_id, content_status=Topic.ContentStatus.GENERATING
    ).update(content_status=content_status, review_status=review_status, **fields):
        publish_topic_status(topic_id)


def generate_topic_content(topic_id: int) -> None:
    """Write and save a topic's summary and links, then queue review. ProviderError propagates."""
    from .tasks import review_content_for_topic

    topic = _in_stage(topic_id, summary="")
    if topic is None:
        return
    prompt = build_topic_prompt(**_brief(topic))
    max_attempts = 2
    for attempt in range(max_attempts):
        response = _call(topic, prompt, "topic")
        try:
            validated = validate_topic_content(response.data)
            break
        except GenerationError:
            if attempt >= max_attempts - 1:
                raise
    # Save only into the round we started in; a newer round (or a saved summary) wins.
    saved = Topic.objects.filter(
        id=topic_id, content_status=Topic.ContentStatus.GENERATING, summary=""
    ).update(summary=validated["summary"], resource_links=validated["resource_links"])
    if saved:
        review_content_for_topic.delay(topic_id)


def mark_generation_failed(topic_id: int) -> None:
    """Generation could not finish. Review status stays PENDING, so the sweeper may requeue it."""
    _finish(topic_id, Topic.ContentStatus.FAILED, Topic.ReviewStatus.PENDING)


def review_topic_content(topic_id: int) -> None:
    """Review the saved summary, persist the verdict and act on it. ProviderError propagates."""
    from .services import request_topic_generation
    from .tasks import repair_content_for_topic

    topic = _in_stage(topic_id, review_verdict__isnull=True)
    if topic is None or not topic.summary:
        return
    result = _call(topic, build_review_prompt(**_brief(topic), summary=topic.summary), "review")
    verdict = interpret_review(result.data, topic.summary)
    stored = verdict.to_json()
    annotate_outcome(result.log_id, stored)

    # Persist only into the round we reviewed: the sweeper may have published it meanwhile.
    this_round = Topic.objects.filter(
        id=topic_id, content_status=Topic.ContentStatus.GENERATING, review_verdict__isnull=True
    )

    if not verdict.on_topic:  # off topic wins over issues: never repaired
        if topic.regeneration_count == 0:
            if this_round.update(
                content_status=Topic.ContentStatus.NOT_GENERATED,
                review_verdict=stored,
                regeneration_count=F("regeneration_count") + 1,
                generation_started_at=None,
            ):
                request_topic_generation(topic_id)
        else:
            _finish(
                topic_id, Topic.ContentStatus.FAILED, Topic.ReviewStatus.REJECTED,
                review_verdict=stored,
            )
        return

    if not verdict.issues:
        _finish(
            topic_id, Topic.ContentStatus.READY, Topic.ReviewStatus.PASSED,
            review_verdict=stored,
        )
        return

    if this_round.update(review_verdict=stored):
        repair_content_for_topic.delay(topic_id)


def repair_topic_content(topic_id: int) -> None:
    """One repair of the verified issues, with no re-review (D5). ProviderError propagates."""
    topic = _in_stage(topic_id, review_verdict__isnull=False)
    if topic is None:
        return
    verdict = ReviewVerdict.from_json(topic.review_verdict)
    if not (verdict.on_topic and verdict.issues):
        return
    prompt = build_repair_prompt(
        **_brief(topic),
        summary=topic.summary,
        issues=[asdict(i) for i in verdict.issues],
    )
    result = _call(topic, prompt, "topic_repair")
    try:
        repaired = validate_topic_content(
            {"summary": result.data["summary"], "resource_links": topic.resource_links}
        )["summary"]
    except GenerationError as exc:
        logger.warning("Topic %s: repair unusable, keeping the original: %s", topic_id, exc)
        annotate_outcome(result.log_id, {"repair": "invalid", "reason": str(exc)})
        _finish(topic_id, Topic.ContentStatus.READY, Topic.ReviewStatus.UNREVIEWED)
        return
    annotate_outcome(result.log_id, {"repair": "repaired"})
    _finish(topic_id, Topic.ContentStatus.READY, Topic.ReviewStatus.FIXED, summary=repaired)


def give_up_review(topic_id: int) -> None:
    """Review or repair could not complete (outage, stuck): publish the original (D6)."""
    _finish(topic_id, Topic.ContentStatus.READY, Topic.ReviewStatus.UNREVIEWED)


def sweep_stuck_topics(cutoff) -> list[int]:
    """Recover topics whose pipeline stalled before ``cutoff``.

    Returns the ids reset to NOT_GENERATED, for the caller to announce.
    """
    stuck = Topic.objects.filter(
        content_status=Topic.ContentStatus.GENERATING, generation_started_at__lt=cutoff
    )
    # Stuck in review or repair: the summary is saved, so publish it unreviewed.
    for topic_id in stuck.exclude(summary="").values_list("id", flat=True):
        give_up_review(topic_id)
    # Stuck in generation (nothing saved), or generation failed (FAILED with PENDING):
    # let the next page open restart it. REJECTED is never requeued.
    reset_ids = list(stuck.filter(summary="").values_list("id", flat=True)) + list(
        Topic.objects.filter(
            content_status=Topic.ContentStatus.FAILED,
            review_status=Topic.ReviewStatus.PENDING,
            generation_started_at__lt=cutoff,
        ).values_list("id", flat=True)
    )
    Topic.objects.filter(id__in=reset_ids).update(
        content_status=Topic.ContentStatus.NOT_GENERATED, generation_started_at=None
    )
    return reset_ids
