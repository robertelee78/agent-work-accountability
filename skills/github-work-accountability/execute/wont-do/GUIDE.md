---
name: execute-wont-do
description: Open when the decider rules a story out, or takes that decision back. Teaches the decision comment, decision evidence, and what awa does to the issue and its release milestone.
---

# Rule a story out (any phase → Won't do)

A session working this leaf MUST also follow the [execute domain rules](../GUIDE.md).

Won't do is the only way to end a story without delivering it. A session MUST NOT park ruled-out work in
Backlog instead, and MUST NOT record Won't do unless the decider made that decision.

## Steps

1. The session MUST record the decision as a comment on the story's own issue, with the text in a file
   ([marker comments](../GUIDE.md#marker-comments)):

   ```sh
   awa comment N --event decision --actor DECIDER --body decision.md    # prints the comment URL
   ```

   The posted comment reads:

   ```text
   <!-- work-accountability:event key=OWNER/REPO:DOC:ITEM event=decision actor=DECIDER time=2026-10-19T15:00:00Z -->
   Won't do: superseded by the beta story.
   ```

2. The session MUST draft, set `"work_phase": "Won't do"`, and add:

   ```json
   "decision": {"ref": "https://github.com/OWNER/REPO/issues/N#issuecomment-ID",
                "work_key": "OWNER/REPO:DOC:ITEM", "requirement": "SOURCE_COMMIT:REQUIREMENT_ID",
                "author": "DECIDER", "reason": "superseded by the beta story"}
   ```

   `author` MUST name who decided; `reason` MUST be one line.

3. The session MUST dry run, read the plan, then apply. Observed plan:

   ```text
   planned_mutations: ["close #5 as not planned (won't do)", "update section progress in the By section headers",
     "set #1 Progress=1/2 Done · 1 won't do · 1 blocked", "set #5 Work phase=Won't do",
     "bind issue #5 to Project fields", "update Project status text (OFF_TRACK)"]
   ```

## What happens

- The issue is closed as *not planned*, so it never counts as accepted or delivered.
- Its release milestone is removed, and the reason is posted once.
- The managed block records `Won't do: REASON (decided by @AUTHOR: REF)`.
- Epic progress counts it apart; `awa release close` never counts it as delivered.
- A Won't do decision ends any open attempt and is not counted as a failed attempt.

## Taking the decision back

Only the decider MAY take a Won't do back. The session MUST then draft, set the phase the story's evidence
supports, remove `decision`, and apply. awa reopens the issue with a one-line comment and removes the
`Won't do:` line.

## Dropping down a level

This leaf writes through two steps: the marker comment on the issue, then the phase change applied by the
reconcile loop. If a command here fails, a session MAY inspect those layers but MUST NOT write around a
refusal.

- The marker comment is posted with `awa comment` ([execute](../GUIDE.md#dropping-down-a-level)); beneath
  it, `gh api repos/OWNER/REPO/issues/N/comments -X POST --input FILE` with the body in a file. A session
  that posts at this level MUST read the comment back before using its URL, MUST NOT interpolate the body
  on the command line, and MUST NOT pass it as `-f body=@FILE`.
- The phase change is a reconcile; its API layer and failures are in
  [board/reconcile](../../board/reconcile/GUIDE.md#dropping-down-a-level).
