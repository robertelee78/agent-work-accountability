---
name: execute
description: Domain for story events during execution. Open to choose a story, record design approval, start or end an attempt, submit a candidate, record a verdict or integration, record a blocker, rule a story out, or list a story's evidence.
---

# Execute: one event, one story

Every event in this domain MUST be recorded with the same three steps:

1. When the event needs a durable reference, the session MUST post a **marker comment** on the story's own
   issue (below).
2. The session MUST **draft** the document's manifest and change **only this story's** phase and evidence.
3. The session MUST **dry run**, read `planned_mutations`, then **apply**, and MUST require
   `"verified": true` ([board/reconcile](../board/reconcile/GUIDE.md)).

## Route

A session MUST open the leaf that the second column names for the event in the first column.

| The event is… | Open |
|---|---|
| Choosing what to start | [pick](pick/GUIDE.md) |
| Design selected or approved: Backlog → Designing → Ready | [approve](approve/GUIDE.md) |
| An attempt starts (Ready → Executing), is released, or another attempt holds the story | [attempt](attempt/GUIDE.md) |
| A candidate is submitted (Executing → Acceptance) | [candidate](candidate/GUIDE.md) |
| An acceptance verdict, or a candidate sent back | [verdict](verdict/GUIDE.md) |
| The accepted change landed (the story MUST carry evidence from Release ready) | [integration](integration/GUIDE.md) |
| A blocker or dependency appears or clears | [blocker](blocker/GUIDE.md) |
| The decider rules the story out | [wont-do](wont-do/GUIDE.md) |
| Checking what evidence a story already has | [evidence](evidence/GUIDE.md) |
| The release shipped (Release ready → Done) | [release/close](../release/close/GUIDE.md) |

## Rules for every event

- A session MUST advance only the exact story the event supports. A branch or pull request that touches
  several stories MUST change a story's phase only when the work or result is explicitly bound to that
  story. PR open, PR merged, file overlap, CI green, agent exit and claim ownership MUST NOT be treated as
  completion verdicts.
- A session MUST NOT edit `observed`, MUST NOT hand-type record lines, and MUST NOT run awa writers in
  parallel.
- Agents usually share one GitHub identity, so the `actor`, `author` and `implementer` strings in evidence
  are what distinguish them. Each MUST name the real agent or person, on one line.

## Marker comments

Every evidence comment a session posts MUST carry a marker as its first line:

```html
<!-- work-accountability:event key=WORK_KEY event=attempt-started|candidate|verdict|delivery|decision actor=WHO time=RFC3339 -->
```

The session MUST post it as structured input and MUST NOT interpolate the text into a shell command:

```sh
jq -n --rawfile b comment.md '{body: $b}' > comment.json
gh api repos/OWNER/REPO/issues/N/comments -X POST --input comment.json --jq .html_url
```

The session MUST use the printed `html_url` (`https://github.com/OWNER/REPO/issues/N#issuecomment-ID`) as
the evidence `ref`. An `issue_comment` reference MUST point to the exact story it advances.
