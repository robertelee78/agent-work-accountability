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
<!-- work-accountability:event key=WORK_KEY event=attempt-started|attempt-released|candidate|verdict|delivery|decision|design-approval actor=WHO time=RFC3339 -->
```

The session MUST post it with `awa comment`, which writes the marker, posts the text from a file, reads the
comment back from GitHub, and prints its URL only when the stored body is the one that was sent:

```sh
awa comment N --event verdict --actor reviewer-1 --body verdict.md [--repo OWNER/REPO] [--time RFC3339] [--json]
```

- `N` MUST be a managed issue; the command refuses any other. The marker's `key` is read from the issue;
  there is no option to type it.
- `--body` MUST name a file holding the comment text (`-` reads standard input). The session MUST NOT put a
  marker in the file and MUST NOT interpolate the text into a shell command.
- `--actor` MUST be one word naming the real agent or person (`impl-1`, `github:claude`).
- A rerun with the same issue, event, actor and text posts nothing and prints the existing comment's URL, so
  the session MUST rerun the same command after a lost response rather than post by hand.

The session MUST use the printed URL (`https://github.com/OWNER/REPO/issues/N#issuecomment-ID`) as the
evidence `ref`. An `issue_comment` reference MUST point to the exact story it advances.

| Exit | Output | Means | Required action |
|---|---|---|---|
| 0 | The comment URL on stdout; `awa: posted … read back and verified` on stderr | Posted (or already present) and read back intact. | The session MUST use the URL as the evidence `ref`. |
| 2 | `reconciliation failed: --body FILE holds only '@…', which looks like an unexpanded file placeholder …` | The file holds a path, not the text. Nothing was posted. | The session MUST pass the path to `--body` and keep the text in the file. |
| 2 | `reconciliation failed: #N is not managed by awa …` | The issue carries no work-accountability key. | The session MUST find the story's own issue ([plan/issues](../plan/issues/GUIDE.md)). |
| 2 | `reconciliation failed: posted comment URL on #N, but …` | GitHub stored something other than what was sent. | The session MUST report it and MUST NOT use the URL as evidence. |
| 75 | `temporary failure: …` | A retryable GitHub failure. | The session MUST rerun the same command; a comment that did land is found and nothing is posted twice. |

Reconcile reads every `issue_comment` reference back before it writes. On each story a manifest moves, the
referenced comment MUST exist, MUST sit on that story's issue, MUST NOT be the literal text of an `@FILE`
placeholder, and MUST carry a marker naming that story's key and the event the evidence records; otherwise
reconcile refuses with `evidence (nothing was written): #N evidence.verdict names URL, but …`, and the
session MUST repost the evidence with `awa comment` and use the new URL
([board/reconcile](../board/reconcile/GUIDE.md)). A story whose phase the manifest does not change is not
re-read.

## Dropping down a level

`awa comment` sends `{"body": …}` as the request body and then fetches the comment by id:

```sh
jq -n --rawfile b comment.md '{body: $b}' > comment.json
gh api repos/OWNER/REPO/issues/N/comments -X POST --input comment.json --jq .html_url
gh api repos/OWNER/REPO/issues/comments/ID --jq .body     # read it back before using the URL
```

A session that posts this way MUST read the comment back and confirm its first line is the marker before
using the URL. It MUST NOT pass the body as `-f body=@FILE`: `-f` is gh's raw string field, so it sends the
text `@FILE` itself, GitHub accepts that as a comment, and the URL looks right. Only `-F body=@FILE` or
`--input FILE` reads a file.
