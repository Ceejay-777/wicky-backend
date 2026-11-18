# Live test of the new lesson checker, 18 November 2026

Real AI (Groq), the Neon dev database, pass 3 code from `feat/ai-content-review`. Raw data is next to this file (see `docs/evidence/README.md`); the follow-up plan is `docs/backlog/content-quality-plan.md`. Celery ran every job inline inside one script, so nothing was queued and no worker or scheduler took part.

## The run at a glance

| | |
|---|---|
| Lessons run end to end | 19 (the ones reset yesterday), across 4 subjects, all 3 levels |
| Result | 7 Passed, 12 Fixed, 0 Unreviewed, 0 Rejected, 0 failed |
| Extra branch tests | off topic once (rewritten, then Fixed), off topic twice (Rejected), Retry on the Rejected topic (new round, Fixed) |
| AI calls | 59 attempts, 58 succeeded, 1 fell over to the backup model |
| Tokens | about 143,000 in total |
| Time per lesson | 15-35 seconds (write ~4-10 s, check ~2.5-4.5 s, fix ~3.5-6 s) |
| Lesson shape | all 4 paragraphs, 252-400 words, 2-4 links, no markdown left |

## Each step, per lesson

Every step ran on gpt-oss-120b except topic 78's check (see below).

| Topic | Level | Write | Check said | Fix | Final |
|---|---|---|---|---|---|
| 4 Allied Initial Response (WWII) | 1 | 328 words | on topic, no issues | none | Passed |
| 11 Code Breaking and Intel | 2 | 367 words | 1 issue | done | Fixed |
| 16 Soviet Invasion of Manchuria | 2 | 310 words | 1 issue | done | Fixed |
| 18 Occupation and Reform | 3 | 381 words | 1 issue | done | Fixed |
| 67 Proto-Niger-Congo Roots of Yoruba | 1 | 273 words | 1 issue | done | Fixed |
| 69 Influence of Neighboring Languages | 1 | 364 words | 1 issue | done | Fixed |
| 71 Basic Yoruba Morphosyntax | 1 | 380 words | no issues | none | Passed |
| 72 Lexical Borrowing (Arabic, Portuguese) | 2 | 305 words | 1 issue, **thrown out** (quote did not match) | none | Passed |
| 74 Classical Yoruba Literature | 2 | 397 words | no issues | none | Passed |
| 75 Dialectal Variation | 2 | 319 words | no issues | none | Passed |
| 77 Proto-Yoruba Sound Changes | 2 | 252 words | 1 issue | done | Fixed |
| 78 Phonotactics and Morphology | 2 | 342 words | no issues (backup model) | none | Passed |
| 79 Contact with Bantu and Atlantic | 2 | 323 words | 2 issues | done | Fixed |
| 81 Computational Phonology | 3 | 300 words | 1 issue | done | Fixed |
| 83 Historical Linguistic Methodology | 3 | 326 words | 1 issue | done | Fixed |
| 84 Current Debates on Classification | 3 | 400 words | 2 issues | done | Fixed |
| 85 Revitalization and Digital Preservation | 3 | 381 words | no issues | none | Passed |
| 102 Advanced Itertools (Python) | 3 | 395 words | 1 issue | done | Fixed |
| 161 Autoscaling with HPA and VPA (Kubernetes) | 2 | 334 words | 2 issues | done | Fixed |

## Were the checker's complaints right?

My own judgement, not a second AI. I am confident on the history, Python and Kubernetes items and less certain on Yoruba linguistics.

| Topic | What it flagged | Right? | What the fix did |
|---|---|---|---|
| 11 | "Ultra Pacific" as a nickname for JN-25 intelligence | Right, the term is invented | Pasted the checker's comment in: the lesson now says the intelligence "was not collectively called 'Ultra Pacific'", naming a term that never existed |
| 16 | Stalin voided the neutrality pact "once Germany fell" | Right, it was renounced on 5 April 1945 | Correct; also changed "World War II" to "World war II", unasked |
| 18 | "dissolution of the zaibatsu" | Nitpick; "dissolution" is the standard term | Harmless rewording |
| 67 | Yoruba "ìyá" said to match the *-ma root | Probably right that the original is shaky | Added a new, unverified claim about its origin |
| 69 | "sùúrù" said to resemble Hausa "sura" | Right; it comes from Arabic "sabr" | Good fix |
| 72 | Portuguese source of "bàtà" given as "bàta" | Right; the word is "bota" | **Never applied**: the checker quoted "16 century" where the lesson says "16th century", so the code threw the complaint out |
| 77 | Yoruba for "head" given as "táá" | Right; it is "orí" | Added a new dubious example ("káká", "to strike") |
| 79 | Bantu speakers as Yoruba neighbours | Right (the roadmap's topic title itself is the problem) | Good fix |
| 79 | "sòkò" as Yoruba for "market", from Bantu | Right that it is wrong ("soko" is Swahili; Yoruba is "ọjà") | Still wrong: now says "sòkò" is a native Yoruba word |
| 81 | Yoruba "does not exhibit vowel harmony" | **Wrong**; Yoruba has well-documented vowel harmony | Removed a true statement |
| 83 | Odd vowel-length example with Igbo "bá" | Right that the example made no sense | Acceptable replacement |
| 84 | "loss of the labial-velar series" | Right that it is wrong (Yoruba has kp and gb) | Still wrong after the fix |
| 84 | vowel length and tone "rare elsewhere" | Right | Good fix |
| 102 | functools.partial "cannot" fix accumulate's start value | **Wrong**; `partial(accumulate, initial=0)` works | Harmless (switched to `initial=`) |
| 161 | VPA adjusts "requests and limits" | **Wrong**; VPA scales limits with requests by default | Now slightly inaccurate ("optionally") |
| 161 | "max surge" as an autoscaling policy | Right; it is a Deployment rollout setting | Good fix |

Tally of the 16 complaints:
- **10 real errors**, plus 1 more (72) that was real but thrown out.
- **3 wrong complaints** (81, 102, 161 first).
- **1 nitpick** (18).
- **1 uncertain** (67).

Fixes:
- **7 clean.**
- **4 left the lesson wrong or added new unverified claims:** 67, 77, 79 second, 84 first.
- **2 small side effects:** 11 (pasted comment) and 16 (changed capitalisation).

Nothing re-checks a fix, by design (decision D5).

## Branch tests

| Test | What happened | Expected? |
|---|---|---|
| Kubernetes lesson planted into a WWII topic | Check: off topic ("discusses Kubernetes autoscaling and not the Allied Initial Response"). Rewritten once (counter 1). New lesson checked: on topic, 2 issues. Fixed. | Yes |
| Same plant, rewrite already used | Check: off topic. Topic Failed / Rejected after 1 call, no rewrite | Yes |
| Retry button on the Rejected topic | HTTP 200. Counter back to 0. New round: write, check, fix. Ended Ready / Fixed | Yes |
| Checker's main model errors (topic 78) | gpt-oss-120b connection reset after 9 s. qwen3.8-27b answered in 3 s. Both attempts logged | Yes |

## What the app gets back

| Status | Lesson text and links in the topic page | Title, level, statuses |
|---|---|---|
| Pending (being written or checked) | empty | shown |
| Passed | shown (2,253 characters, 3 links) | shown |
| Fixed | shown | shown |
| Rejected | empty | shown, `FAILED` / `REJECTED` |

The subject roadmap row carries the new review status. Each finished lesson sent one `topic.status` event per enrolled user (16 in the WWII subject) with the right final status. Opening topics started no extra generation, because their next two topics were already written.

Not seen live: Unreviewed (needs a checker or fixer outage after all retries) and an unusable fix. The automated tests cover both.

## Problems found during the test

1. **Your Celery worker is running old code.** It was started on 17 November at 1:49 PM, before pass 3. My first attempt queued the 19 jobs and it took them; every job crashed at once on the removed database column. No AI calls were made and no credits were spent. Restart it before using the app; until then any lesson the app queues will fail the same way.
2. **The quote check is too strict for small copying slips.** It lost a real catch on topic 72 ("16 century" against "16th century").
3. **Accented text can be stored in two forms.** Topic 81's lesson stores its accents in the decomposed form. The quote check does not even these out, so a correctly copied quote could still be thrown out for Yoruba lessons. A one-line fix (Unicode normalisation) would close it.
4. **Corrections are written as comments, and fixes can add new claims.** See topics 11, 67, 77, 79 and 84. The prompts could tell the checker to write the correction as a replacement sentence ready to drop in, and allow it to suggest deleting the sentence instead.
5. **The checker is unreliable on niche subjects.** It flagged 3 true statements as wrong. Both writer and checker are weakest on Yoruba linguistics.
6. **Not tested live:** goal sentences. All 19 topics come from older roadmaps with no goal. Unreviewed, an unusable fix and SSE delivery to a real browser were not tested live either.
7. **The dev database dropped a connection once** during the long script. That was my test setup; a real worker opens a fresh connection per job. If it happened inside a real job, though, the topic would stay "being written" until the hourly clean-up, because a database error is not retried.

## Data changed in the dev database

- The 19 topics now hold their new lessons.
- Topic 4 holds the lesson from the Retry round.
- Topic 71 was briefly switched to Pending for the visibility check, then put back.
- No other topics were touched.
