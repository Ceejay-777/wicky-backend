# Pass 2: Generation quality

Goal: make the first summary good. Give the writer the context it lacks, give the summary a fixed plain-paragraph shape, and move all topic and roadmap prompt text into one versioned home. Review behaviour does not change in this pass.

Read `README.md` sections 3 and 5 and the pass 1 entry in `PROGRESS.md` first.

## 2.1 Keep the topic goal

- `Topic` gets a `description` field: the one-sentence "what the learner will understand after this topic" that the roadmap step already asks for. Short text, required for new topics, blank allowed at the database level.
- Roadmap validation keeps `description` (trimmed) instead of dropping it, and stores it on each created topic.
- A topic with a missing or blank description from the AI does not fail the roadmap. Store it blank. The writer and reviewer prompts must read well when it is blank.
- Topics added through roadmap editing (append, insert, update) also accept and store a description. Where the API takes topic data, a description is optional. Expose `description` read-only wherever a topic's title is already returned (topic detail, subject roadmap, preview), and document it in the serializers' help text.
- No backfill (`README.md` D12).

## 2.2 One prompts module for learning

- Create one module in `apps/learning` that owns the prompt text, system instructions, schemas and prompt versions for **roadmap** and **topic content**. Pass 3 adds the reviewer and repair prompts here.
- Move the roadmap and topic prompt text, system instructions and schemas out of `apps/learning/services.py` into it. `services.py` and the pipeline only call builders that take domain values (subject, topic, neighbours) and return messages plus a version.
- Quiz prompts stay where they are (`README.md` section 4).
- Builders are pure: no database access, no AI calls. They take plain values, so they are trivial to test.
- Each prompt has a version label (for example `topic-v1`, `roadmap-v1`). Bump it whenever the wording changes. Passing the version to the AI call (pass 1) records it on every log row.

## 2.3 What the writer is told

The topic writer prompt includes:
- subject name and topic title;
- the topic goal sentence, when present;
- the level (1, 2 or 3) and what it means:
  - 1: a newcomer; assume nothing; plain words; define every term.
  - 2: has finished level 1 of this subject; can build on its basics; focus on application.
  - 3: has finished levels 1 and 2; can handle nuance, edge cases and current practice.
- the previous topic title and the next topic title in roadmap order, when they exist, with the instruction: do not re-teach the previous topic in depth, and do not cover the next one;
- the fixed shape: four plain paragraphs, in order:
  1. what it is, in one or two sentences, then a little more;
  2. the key terms and ideas, each explained in a sentence;
  3. one small concrete example or analogy;
  4. why it matters in this subject, and how it connects to what comes next;
- the format rule: plain text only, paragraphs separated by one blank line, no headings, no bullet points, no markdown symbols, no lists;
- length: 300-500 words in total;
- the resource links instruction, unchanged in meaning (links are out of scope).

Audience fit and completeness, removed from review in pass 3, are written here as instructions: match the level's reader, cover what the goal sentence promises, and stay inside the topic's boundaries.

Neighbours are read by the pipeline (one query) and passed to the builder as plain titles. The builder never queries.

## 2.4 Normalise, then validate

Before validation, the pipeline normalises the summary in code:
- strip markdown markers that slipped through: heading hashes, bullet markers at line start, bold and italic markers;
- collapse runs of blank lines to one blank line; trim each paragraph and the whole text;
- normalise smart quotes to straight quotes.

Validation then rejects only:
- an empty summary;
- fewer than 120 or more than 900 words. These are deliberately wide guard rails against broken output, not the 300-500 target. Put both bounds in one place as named constants.

Link validation is unchanged.

Paragraph count is **not** validated. Four is the target, not a gate.

## 2.5 Pipeline home (generation half)

- Create the topic content pipeline module described in `README.md` section 5. In this pass it owns generation: gather inputs (topic, subject, neighbours), build messages, call the AI with version and context (topic id, subject id), normalise, validate, save.
- `generate_content_for_topic` in `apps/learning/tasks.py` becomes thin: it calls the pipeline and handles `GenerationError` and `ProviderError` exactly as today.
- Review stays as pass 1 left it. Pass 3 moves it into this module.

## 2.6 Docs

- `docs/reference/ai-providers.md`: a short "Topic prompts" section: where prompts live, the prompt-version rule, and the four-paragraph plain-text shape and why (the frontend renders plain paragraphs).
- `AGENTS.md`: under Generation flows, one line saying where learning prompts live and that any wording change bumps the version.

## 2.7 Tests (all required)

Prompt builders (pure, no database)
- The topic prompt includes subject, title, level meaning, goal, previous and next titles, the four-part shape and the plain-text rule.
- With a blank goal and no neighbours (first topic), the prompt has no empty labels or dangling text such as "Previous topic: ''".
- Last topic in the roadmap: has a previous title and no next.
- Each builder returns a non-empty version; topic and roadmap versions differ.

Roadmap
- A roadmap response with descriptions stores them on topics, trimmed.
- A missing or blank description does not fail the roadmap and stores blank.
- Roadmap edit endpoints accept an optional description and store it.
- Topic detail, subject roadmap and preview responses include `description`.

Normalisation and validation
- Markdown headings, bullets and bold markers are stripped; text is kept.
- Multiple blank lines become one; smart quotes become straight.
- A 119-word summary is rejected; 120 accepted; 900 accepted; 901 rejected.
- A three-paragraph summary inside the word bounds is accepted.

Pipeline
- Generation passes the topic's level, goal and neighbours into the messages (assert on the captured request).
- The logged row for a topic generation has the topic prompt version and topic id in context.
- `generate_content_for_topic` behaviour on `GenerationError` and on `ProviderError` (including last retry) is unchanged.

## 2.8 Done when

- [ ] Description stored, exposed and documented
- [ ] Prompts module owns roadmap and topic prompts with versions; no prompt text left in `services.py` for these
- [ ] Pipeline module owns generation; task is thin
- [ ] All listed tests exist and pass; full suite passes
- [ ] Docs updated; `PROGRESS.md` entry written
- [ ] Committed on the branch, not pushed
