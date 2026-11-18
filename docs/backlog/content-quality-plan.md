# Content quality plan

Status: planned, not started. Tracked as `follow-ups.md` F-10. Written 2026-11-18 after the live test in [evidence/2026-11-18-live-content-review](../evidence/2026-11-18-live-content-review/report.md).

## The problem

The pipeline works (write, check, fix, statuses, retries, logging). The content is not accurate enough for an app whose point is teaching niche subjects.

- **How many lessons had errors:** 12 of 19 real lessons had at least one wrong statement as first written. After checking and fixing, about 6 still had an error a reader could spot, and those are only the ones we could spot.
- **The checker:** it made 16 complaints. 10 were real, 1 real one was thrown out over a copying slip, and 3 were wrong, so their fixes damaged true statements.
- **The fixes:** of 13, 4 left the lesson wrong or added a new made-up claim.
- **Quizzes:** they are built from the lesson, so these errors become wrong answer keys.

**Root cause.** The writer and the checker are the same kind of model, drawing on the same memory. Where the writer does not know a niche fact, the checker usually does not either, so making the checker stricter or smarter only moves the guesswork around. Accuracy has to come from outside the model's memory.

## The six steps

1. **Ground content in real sources (the main lever).** Before writing a roadmap or a lesson, fetch real material on the subject and topic, for example from the Wikipedia API (free, no key). Give it to the writer with the rule "state only what these sources support". The checker then compares the lesson against the same sources ("is this sentence supported?") instead of against its memory. Unsupported is a clearer and fairer test than "clearly wrong". Links then come from real pages, which also closes F-1 (dead and invented links). Open question: what to do when a niche topic has thin or no sources (write more generally, or say so).
2. **Fix the error hotspot in the writer prompt.** Most errors were in the "one concrete example" paragraph, where the model invents specific words, dates and etymologies to sound convincing. Ask for a well-known example, or a plain analogy when unsure, instead of a specific factual claim. Cheap and immediate.
3. **Ground the roadmap too.** One topic title ("Language Contact with Bantu and Atlantic Languages") was itself false, so no lesson under it could be right. Roadmap topics need the same source grounding as lessons.
4. **Let learners report mistakes.** Add a "report a mistake" action on a lesson. `AIGenerationLog` already has `human_verdict` and `human_note`, so a report can attach to the call that produced the lesson. No model will be perfect on niche topics; readers catch what it misses. Needs a small backend endpoint and a frontend control.
5. **Measure before and after.** Use the 19 topics from the 2026-11-18 run as a fixed test set (`lessons.json` has the originals and the per-complaint judgements are in the report). Re-run them after each change and count wrong statements, unsupported statements and dead links. A judgement needs a person or a source check, not another model's memory.
6. **Small checker fixes.**
   - Treat both Unicode forms of accented letters as the same when matching quotes (topic 81 stored decomposed accents).
   - Forgive tiny copying slips in quotes, for example by fuzzy matching against the closest sentence (topic 72 lost a real catch over "16 century" against "16th century").
   - Make the checker's correction a sentence ready to drop into the lesson, not a comment, and let it suggest deleting a sentence instead (topic 11 got the comment pasted in).

   Any prompt wording change bumps that prompt's version.

## Suggested order

Steps 2 and 6 first (hours). Then a grounded-content pass for steps 1 and 3, plus step 4. Run step 5 after each. Related backlog: F-1 (links), F-2 (quiz answer keys), F-9 (re-review `UNREVIEWED`).
