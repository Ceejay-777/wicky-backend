# Evidence

Results of real AI tests, kept as data for tuning prompts, models and the content pipeline. Each file is a snapshot from its date: the code and file names it mentions may have changed since. Add new test runs as `YYYY-MM-DD-<name>`.

| Date | Test | What it found |
|---|---|---|
| 2026-10-13 | [Gemini latency](2026-10-13-gemini-latency.md) | Gemini text generation took 30-270 s (hidden thinking tokens, 503 retries); why text moved to Groq |
| 2026-10-14 | [Groq model test](2026-10-14-groq-model-test.md) | Chains, efforts and token caps per action; the old reviewer's false rejections; 56 generated links checked (24 dead, 13 ok, 19 unverifiable) |
| 2026-11-18 | [Live content review](2026-11-18-live-content-review/report.md) | 19 real lessons through write, check and fix (7 Passed, 12 Fixed); the checker's accuracy per complaint; off-topic, Rejected and Retry branches; what the API shows |

## Raw data for 2026-11-18

- `lessons.json`: per topic, the lesson as first written (`original`) and as published (`final`), the stored verdict, the links and every AI call with model, outcome, latency and tokens. Topic 4 is as it stood before the off-topic tests.
- `off-topic-test.json`: the off-topic test on topic 4 (Kubernetes lesson planted into a WWII topic), with each call's parsed reply.
- `ai-call-log.json`: every `AIGenerationLog` row in the dev database as of 2026-11-18 (60 rows: the full prompts sent, raw replies, parsed data, verdicts). Includes the one 2026-11-17 `topic-v1` sample from pass 2. Exported because the log deletes rows after `AI_LOG_RETENTION_DAYS` (90 days).
