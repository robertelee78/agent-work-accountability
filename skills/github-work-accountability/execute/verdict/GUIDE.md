---
name: execute-verdict
description: Open when a reviewer independent of the implementer accepts a candidate (Acceptance → Release ready) or rejects it. Teaches verdict evidence, the independence rule, the integration requirement at Release ready, and what a rejection does to the attempt count.
---

# Record a verdict

A session working this leaf MUST also follow the [execute domain rules](../GUIDE.md).

## Accept (Acceptance → Release ready)

1. The reviewer MUST post the verdict on the story's issue, naming the candidate and the criteria checked:

   ```text
   <!-- work-accountability:event key=OWNER/REPO:DOC:ITEM event=verdict actor=reviewer-1 time=2026-10-19T13:00:00Z -->
   Accepted https://github.com/OWNER/REPO/commit/FULL_SHA against every criterion.
   ```

2. The session MUST draft, set `work_phase` to `Release ready`, and add:

   ```json
   "verdict": {"ref": "https://github.com/OWNER/REPO/issues/N#issuecomment-ID",
               "work_key": "OWNER/REPO:DOC:ITEM", "requirement": "SOURCE_COMMIT:REQUIREMENT_ID",
               "author": "reviewer-1", "implementer": "impl-1",
               "candidate": "https://github.com/OWNER/REPO/commit/FULL_SHA"}
   ```

   - `author` and `implementer` MUST differ and MUST name the real reviewer and implementer.
   - `candidate` MUST equal the candidate evidence's `ref`.
   - A story delivered by `release` or `merge` MUST also carry `integration` evidence from this phase on
     ([integration](../integration/GUIDE.md)).

3. The session MUST dry run, read the plan, then apply. Expected plan:

   ```text
   planned_mutations: ["close #3 as completed (accepted)", "label #3 awaiting-release",
                       "set #3 Work phase=Release ready", "bind issue #3 to Project fields"]
   ```

   The issue closes because accepted work counts as done on its release milestone; release-delivered stories
   keep the `awaiting-release` label until the release ships.

## Reject (send the candidate back)

The session MUST draft, set `work_phase` back to `Ready` (or `Executing` when a new attempt has already
started), remove the rejected `attempt` and `candidate` from the story's evidence, and apply. The rejected
candidate stays in issue history, and the attempt counts as ended (`Ended attempts: N`). At three ended
attempts a person MUST decide before another starts ([attempt](../attempt/GUIDE.md#ended-attempts)).

A story's issue is closed exactly while the story is Release ready or Done. When a story falls back below
Release ready (a rejection after acceptance, or a regression), awa reopens its issue and comments once why;
a session MUST NOT reopen it by hand. An issue a person closed as not planned or duplicate is left closed and
reported in the receipt.

## What the results mean

| Exit | stderr | Means | Required action |
|---|---|---|---|
| 0 | — | `"verified": true`; the issue is closed as completed. | The session MUST report the receipt; the story waits for delivery. |
| 2 | `reconciliation failed: items[N].evidence.verdict is not independent` | `author` equals `implementer`. | A different reviewer MUST give the verdict; the session MUST NOT change the names to pass the check. |
| 2 | `reconciliation failed: items[N]: Release ready for a release-delivered story requires evidence: integration (the commit that landed on the default branch, bound to the accepted candidate)` | The change has not been recorded as landed. | The session MUST land the change, then add `integration`. |

Independence is checked as a string comparison, because agents usually post as the same GitHub user. The
verdict's `ref` MUST therefore point to review content that names the reviewer and what was checked.

## Dropping down a level

This leaf writes through two steps: the marker comment on the issue, then the phase change applied by the
reconcile loop. If a command here fails, a session MAY inspect those layers but MUST NOT write around a
refusal.

- The marker comment is posted with `gh api repos/OWNER/REPO/issues/N/comments -X POST --input FILE`
  (body in a file, never interpolated on the command line).
- The phase change is a reconcile; its API layer and failures are in
  [board/reconcile](../../board/reconcile/GUIDE.md#dropping-down-a-level).
