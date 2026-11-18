# Frontend handoff: AI content review

For `wicky-frontend`. The backend changes are on `feat/ai-content-review` (not merged yet). Nothing here has been changed in the frontend.

## 1. `ReviewStatus` values changed

`packages/types/src/common.ts`:

```ts
// old
export type ReviewStatus = "PENDING" | "PASSED" | "FAILED" | "FLAGGED";
// new
export type ReviewStatus = "PENDING" | "PASSED" | "FIXED" | "UNREVIEWED" | "REJECTED";
```

`FLAGGED` and the review value `FAILED` no longer exist. `content_status` is unchanged (`NOT_GENERATED`, `GENERATING`, `READY`, `FAILED`). The type is used in `topic.ts` (`Topic`, `TopicDetail`), `subject.ts` (`SubjectTopicItem`) and `events.ts` (the `topic.status` SSE payload); all three carry the new values.

| `review_status` | Meaning | `content_status` | Summary and links in `GET /topics/:id` |
|---|---|---|---|
| `PENDING` | Not reviewed yet (new, generating, or waiting for review) | `NOT_GENERATED`, `GENERATING` or `FAILED` | Empty (`""`, `[]`) |
| `PASSED` | On topic, no verified errors | `READY` | Shown |
| `FIXED` | Verified errors found and corrected | `READY` | Shown |
| `UNREVIEWED` | The check could not finish (outage); the original is published | `READY` | Shown |
| `REJECTED` | Off topic twice | `FAILED` | Empty |

The backend now hides the summary and links itself: until review finishes, `summary` is `""` and `resource_links` is `[]`, even while `content_status` is `GENERATING`. A topic goes `GENERATING` → `READY` or `FAILED` in one step, announced by `topic.status` as before. There is no visible "written but not yet checked" state.

## 2. Topic page: the `underReview` check

`apps/web/src/app/(app)/topics/[id]/page.tsx` line 95 tests `review_status === "FLAGGED" || review_status === "FAILED"`. Neither value can occur any more, so the banner never shows. Suggested change:

- `PASSED` and `FIXED`: show the reading normally, no banner. `FIXED` is a success; do not tell the learner something was wrong.
- `UNREVIEWED`: show the reading normally, with no banner. Owner decision (2026-11-18): whether a reading was checked is not the learner's business. No status needs the "still being reviewed" banner any more, so remove the `underReview` prop and its banner from the reading view.
- `REJECTED`: comes with `content_status === "FAILED"`, so it already lands in the `FAILED` branch (the "Not ready yet" screen with `RetryButton`). That screen's text, "Check your connection and try again", is wrong for this case. Suggested text when `review_status === "REJECTED"`: "We could not write a reading that fits this topic. You can try again." `POST /topics/:id/retry` restarts it, but a retry may give the same result, so keep the Back to roadmap link prominent.
- `FAILED` with `PENDING`: generation itself failed (outage or unusable output). The current text and retry button fit. The backend also requeues these hourly.

## 3. New `description` field (optional to show)

Topics now have `description`: a one-sentence goal from the roadmap ("what the learner will understand after this topic"). It can be `""` (topics created before this change, or when the AI omitted it). It is returned by:

- `GET /topics/:id` (`TopicDetail`)
- the subject roadmap's topic items (`SubjectTopicItem`)
- the subject preview's topic items

Possible uses: a subtitle under the topic title on the reading page, or a second line on roadmap and preview rows. Render nothing when it is empty. Add `description: string` to the types if you use it.

## 4. Summary format

The summary is now always plain paragraphs (asked for four), separated by a blank line, with markdown stripped by the backend. `toParagraphs` in `apps/web/src/lib/learning/reading.ts` already splits on newlines: nothing to change.
