"""What Wicky asks the AI about learning content: prompt text, schemas, versions.

Builders are pure: they take plain values and never touch the database or the AI.
Bump a prompt's version whenever its wording changes; the version is recorded on
every AIGenerationLog row. Quiz prompts live in services.py (out of this spec's scope).
"""
from dataclasses import dataclass

TOPIC_PROMPT_VERSION = "topic-v1"
ROADMAP_PROMPT_VERSION = "roadmap-v1"
REVIEW_PROMPT_VERSION = "review-v1"
REPAIR_PROMPT_VERSION = "topic_repair-v1"


@dataclass(frozen=True)
class Prompt:
    """Everything one AI call needs from this module."""

    text: str
    system_instruction: str
    schema: dict
    version: str


# --- Roadmap -----------------------------------------------------------------

ROADMAP_SYSTEM_INSTRUCTION = (
    "You are an expert curriculum designer. You create structured, pedagogically sound "
    "learning roadmaps that progress from foundational concepts through intermediate skills "
    "to advanced mastery. Each topic should be scoped to a single sitting and ordered "
    "so every topic builds on what came before it. Avoid generic placeholder names — "
    "topics should be concrete and specific to the subject."
)

ROADMAP_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "level": {"type": "integer", "minimum": 1, "maximum": 3},
                    "description": {"type": "string"},
                },
                "required": ["title", "level", "description"],
            },
            "minItems": 20,
            "maxItems": 25,
        },
    },
    "required": ["topics"],
}


def build_roadmap_prompt(subject_name: str) -> Prompt:
    text = (
        f"Design a 20-25 topic learning roadmap for the subject '{subject_name}'.\n\n"
        f"Structure it across 3 levels of depth:\n"
        f"  Level 1 (7-8 topics): Core foundations every beginner needs. "
        f"Start with the basics and build toward intermediate ground.\n"
        f"  Level 2 (7-8 topics): Deeper dives and practical application. "
        f"Assume the learner has completed Level 1.\n"
        f"  Level 3 (6-9 topics): Advanced concepts, edge cases, and current developments. "
        f"Push the learner toward mastery.\n\n"
        f"For each topic provide a title (concrete, 3-7 words), a level (1/2/3), "
        f"and a one-sentence description of what the learner will understand after completing it.\n\n"
        f"IMPORTANT: The full 20-25 topic sequence must be logically ordered — each topic "
        f"should build on concepts introduced in earlier topics."
    )
    return Prompt(text, ROADMAP_SYSTEM_INSTRUCTION, ROADMAP_SCHEMA, ROADMAP_PROMPT_VERSION)


# --- Topic content -----------------------------------------------------------

TOPIC_CONTENT_SYSTEM_INSTRUCTION = (
    "You are an educational content writer creating concise, engaging summaries for a "
    "self-paced learning platform. Prioritize clarity and practical understanding over "
    "academic formalism. Write plain prose that a reading app shows as paragraphs. "
    "Every resource link should point to a real, publicly accessible resource that deepens "
    "understanding of the specific topic — not generic reference sites."
)

TOPIC_CONTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "resource_links": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": ["article", "video", "wikipedia"],
                    },
                },
                "required": ["title", "url", "type"],
            },
            "minItems": 2,
            "maxItems": 5,
        },
    },
    "required": ["summary", "resource_links"],
}

LEVEL_READERS = {
    1: "a newcomer. Assume nothing, use plain words and define every term.",
    2: (
        "someone who has finished level 1 of this subject. Build on its basics "
        "and focus on application."
    ),
    3: (
        "someone who has finished levels 1 and 2 of this subject. They can handle "
        "nuance, edge cases and current practice."
    ),
}


FORMAT_RULES = (
    "Format: plain text only. Separate paragraphs with one blank line. No headings, "
    "no bullet points, no lists, no markdown symbols (such as #, * or _). Do not "
    "number the paragraphs."
)


def _brief_lines(
    level: int, goal: str, previous_title: str | None, next_title: str | None
) -> list[str]:
    """The topic's brief, shared by writer, reviewer and repairer so "on topic" means one thing."""
    lines = [f"Level {level} of 3. The reader is {LEVEL_READERS[level]}"]
    if goal.strip():
        lines.append(f"Goal of this topic: {goal.strip()}")
    if previous_title:
        lines.append(
            f"The previous topic was '{previous_title}'. Do not re-teach it in depth; "
            f"a brief reference is enough."
        )
    if next_title:
        lines.append(
            f"The next topic is '{next_title}'. Do not cover it; it has its own summary."
        )
    return lines


def build_topic_prompt(
    *,
    subject_name: str,
    title: str,
    level: int,
    goal: str = "",
    previous_title: str | None = None,
    next_title: str | None = None,
) -> Prompt:
    lines = [
        f"Write an educational summary of the topic '{title}' in the subject '{subject_name}'.",
        "",
        *_brief_lines(level, goal, previous_title, next_title),
    ]
    goal = goal.strip()
    scope = (
        "Cover everything the goal promises and stay inside this topic's boundaries."
        if goal
        else "Cover the core of this topic and stay inside its boundaries."
    )
    lines += [
        f"Write for the reader described by the level. {scope}",
        "",
        "Write exactly four paragraphs, in this order:",
        "1. What it is, in one or two sentences, then a little more.",
        "2. The key terms and ideas, each explained in a sentence.",
        "3. One small concrete example or analogy.",
        "4. Why it matters in this subject, and how it connects to what comes next.",
        "",
        FORMAT_RULES,
        "Length: 300-500 words in total.",
        "",
        "Also provide 2-5 carefully chosen external resource links. Prefer Wikipedia for "
        "foundational overviews, reputable articles for depth, and YouTube for visual "
        "explanations. Each link must point to a real, accessible resource that is "
        "directly relevant to this specific topic.",
    ]
    return Prompt(
        "\n".join(lines),
        TOPIC_CONTENT_SYSTEM_INSTRUCTION,
        TOPIC_CONTENT_SCHEMA,
        TOPIC_PROMPT_VERSION,
    )


# --- Review ------------------------------------------------------------------

REVIEW_SYSTEM_INSTRUCTION = (
    "You check summaries on a learning platform for two things only: whether the summary "
    "is about its topic, and whether it states something clearly false. You are not an "
    "editor. Most summaries have no issues, and an empty issue list is the normal answer."
)

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "on_topic": {"type": "boolean"},
        "reason": {"type": "string"},
        "issues": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string"},
                    "problem": {"type": "string"},
                    "correction": {"type": "string"},
                },
                "required": ["quote", "problem", "correction"],
            },
        },
    },
    "required": ["on_topic", "reason", "issues"],
}


def build_review_prompt(
    *,
    subject_name: str,
    title: str,
    level: int,
    summary: str,
    goal: str = "",
    previous_title: str | None = None,
    next_title: str | None = None,
) -> Prompt:
    lines = [
        f"Check this summary of the topic '{title}' in the subject '{subject_name}'.",
        "",
        "The writer was given this brief:",
        *_brief_lines(level, goal, previous_title, next_title),
        "",
        "Summary:",
        summary,
        "",
        "Answer two questions.",
        "1. on_topic: is the summary about this topic, within its level and boundaries? "
        "Give a one-sentence reason.",
        "2. issues: list only statements that are clearly factually wrong. For each, copy "
        "the exact sentence from the summary, word for word, as the quote; say what is "
        "wrong as the problem; and give a corrected sentence as the correction.",
        "",
        "Do not report style, tone, reading level, omissions, missing depth, ordering or "
        "preferences. Those are not issues. If nothing is clearly wrong, return an empty "
        "issues list.",
    ]
    return Prompt(
        "\n".join(lines), REVIEW_SYSTEM_INSTRUCTION, REVIEW_SCHEMA, REVIEW_PROMPT_VERSION
    )


# --- Repair ------------------------------------------------------------------

REPAIR_SYSTEM_INSTRUCTION = (
    "You correct factual errors in educational summaries. You change only what each "
    "listed issue requires and keep everything else word for word."
)

REPAIR_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


def build_repair_prompt(
    *,
    subject_name: str,
    title: str,
    level: int,
    summary: str,
    issues: list[dict],
    goal: str = "",
    previous_title: str | None = None,
    next_title: str | None = None,
) -> Prompt:
    listed = [
        f"- Sentence: {i['quote']}\n  Problem: {i['problem']}\n  Correction: {i['correction']}"
        for i in issues
    ]
    lines = [
        f"Correct this summary of the topic '{title}' in the subject '{subject_name}'.",
        "",
        "The summary was written to this brief:",
        *_brief_lines(level, goal, previous_title, next_title),
        "",
        "Summary:",
        summary,
        "",
        "A reviewer found these factual errors:",
        *listed,
        "",
        "Fix each error. Change only what the errors require and keep everything else "
        "word for word where possible. Keep the same paragraphs.",
        FORMAT_RULES,
        "Return the whole corrected summary.",
    ]
    return Prompt(
        "\n".join(lines), REPAIR_SYSTEM_INSTRUCTION, REPAIR_SCHEMA, REPAIR_PROMPT_VERSION
    )
