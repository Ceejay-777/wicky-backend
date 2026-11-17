"""The topic content pipeline: gather inputs, prompt, generate, normalise, validate, save.

Celery tasks only call into this module. Review still lives in services.py until pass 3.
"""
import re

from apps.ai.services import generate_content

from .exceptions import GenerationError
from .models import Topic
from .prompts import build_topic_prompt

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


def _neighbour_titles(topic: Topic) -> tuple[str | None, str | None]:
    # Roadmap orders are contiguous (create, append and insert all renumber).
    titles = dict(
        Topic.objects.filter(
            subject_id=topic.subject_id, order__in=(topic.order - 1, topic.order + 1)
        ).values_list("order", "title")
    )
    return titles.get(topic.order - 1), titles.get(topic.order + 1)


def generate_topic_content(topic_id: int) -> None:
    """Write and save a topic's summary and links. ProviderError propagates."""
    topic = Topic.objects.select_related("subject").get(id=topic_id)
    previous_title, next_title = _neighbour_titles(topic)
    prompt = build_topic_prompt(
        subject_name=topic.subject.name,
        title=topic.title,
        level=topic.level,
        goal=topic.description,
        previous_title=previous_title,
        next_title=next_title,
    )
    max_attempts = 2
    for attempt in range(max_attempts):
        response = generate_content(
            prompt.text,
            prompt.system_instruction,
            prompt.schema,
            action="topic",
            prompt_version=prompt.version,
            context={"topic_id": topic.id, "subject_id": topic.subject_id},
        )
        try:
            validated = validate_topic_content(response.data)
            break
        except GenerationError:
            if attempt >= max_attempts - 1:
                raise
    Topic.objects.filter(id=topic_id).update(
        summary=validated["summary"],
        resource_links=validated["resource_links"],
    )
