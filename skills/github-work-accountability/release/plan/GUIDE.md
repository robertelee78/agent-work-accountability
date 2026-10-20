---
name: release-plan
description: Open to create a release milestone, record a due date the operator agreed, see which stories a release holds, or move a story to another release. Teaches awa release plan, awa release status, delivery in the manifest, and milestone_change_reason.
---

# Plan a release

## Create the release milestone

The session MUST create a release milestone with awa, titled exactly as the tag:

```sh
awa release plan v1.1.0 --description "Second release" --repo OWNER/REPO
```

Observed receipt:

```json
{"action": "plan", "applied_mutations": ["create release milestone v1.1.0"],
 "milestone": {"description": "Second release", "due_on": null, "number": 2, "state": "open"},
 "schema": "github-work-accountability/release-receipt-v1", "tag": "v1.1.0", "verified": true}
```

## Record an agreed due date

A session MUST set a due date only when the operator stated one, and MUST record who agreed it and when:

```sh
awa release plan v1.1.0 --due 2026-11-01 --due-source "agreed with OPERATOR on 2026-10-19" --update --repo OWNER/REPO
```

Observed receipt:

```json
{"action": "plan", "applied_mutations": ["update release milestone v1.1.0"],
 "milestone": {"description": "Due date 2026-11-01 agreed: agreed with OPERATOR on 2026-10-19",
               "due_on": "2026-11-01T00:00:00Z", "number": 2, "state": "open"},
 "schema": "github-work-accountability/release-receipt-v1", "tag": "v1.1.0", "verified": true}
```

`--due-source` is appended to the description. `--update` is REQUIRED to change an existing milestone.

## Put a story in a release, or move it

A story joins a release through its `delivery` in the manifest ([board/reconcile](../../board/reconcile/GUIDE.md)):

- `{"kind": "release", "release": "TAG"}` puts it in that release milestone;
- `{"kind": "release", "release": "next"}` leaves it without one until `awa release close` of the first full
  release that contains its landing commit.

To move a story that awa already placed, the session MUST draft, change `delivery.release` (and
`milestone`, when the draft shows one) and add a one-line `"milestone_change_reason"` to the same item, then
dry run and apply:

```json
{"number": 4, "delivery": {"kind": "release", "release": "v1.1.0"}, "milestone": "v1.1.0",
 "milestone_change_reason": "beta slipped from the placeholder release into v1.1.0"}
```

```text
planned_mutations: ["set #4 release milestone v1.1.0", "bind issue #4 to Project fields",
                    "update Project status text (ON_TRACK)"]
```

awa posts the reason on the issue once:

```text
Release target moved from v9.9.9 to v1.1.0: beta slipped from the placeholder release into v1.1.0

<!-- work-accountability:release-move #4 v9.9.9->v1.1.0 -->
```

A move without a reason is refused before anything is written:

```text
reconciliation failed: release milestones (nothing was written): #4 moves from release v9.9.9 to 'v1.1.0';
a release move needs milestone_change_reason
```

## See who is in a release

The session SHOULD check a release's membership before closing it:

```sh
awa release status v1.0.0 --repo OWNER/REPO
```

Observed receipt:

```json
{"action": "status", "issues": {"closed": 1, "open": 0},
 "members": [{"document_root": 1, "joins_at_close": false, "number": 3, "state": "closed", "title": "Alpha story"}],
 "milestone_state": "open", "notes": [], "problems": [],
 "schema": "github-work-accountability/release-receipt-v1", "tag": "v1.0.0"}
```

## What the results mean

| Exit | Output | Means | Required action |
|---|---|---|---|
| 0 | `"verified": true`, `applied_mutations` | The milestone was created or updated and read back. | The session MUST report the receipt. |
| 0 | `applied_mutations: []` | The milestone already matched. | No action is REQUIRED. |
| 2 | `reconciliation failed: release milestone v1.1.0 already exists with a different description or due date; pass --update to change it` | A plan without `--update` would change an existing milestone. | The session MUST add `--update` only if the change is intended. |
| 2 | `reconciliation failed: --due must be a date such as 2026-10-15` | The date is not a valid `YYYY-MM-DD`. | The session MUST pass the date the operator stated, in that form. |
| 2 | `reconciliation failed: --due-source records where a --due date came from; pass --due too` | A source without a date. | The session MUST pass both, or neither. |
| 2 | ``reconciliation failed: there is no release milestone v7.7.7; create it with `awa release plan v7.7.7` `` | `status` or `close` named a tag with no release milestone. | The session MUST check the tag, and MAY create the milestone. |
| 0 (status) | `members[].joins_at_close: true` | A `next` story whose landing commit is in this release; it joins at close. | No action is REQUIRED. |
| 0 (status) | `problems` non-empty, e.g. `#N is in milestone TAG but is not a managed work item; remove it or manage it` | The release cannot close cleanly. | The session MUST resolve each problem before `awa release close`. |
| 0 (status) | `notes` saying GitHub's counter is stale | GitHub's cached milestone counter disagrees with the milestone's contents. | The session MUST trust `issues` and the milestone's issue list; it MUST NOT "fix" the counter by hand. |

## Dropping down a level

```sh
gh api "repos/OWNER/REPO/milestones?state=all&per_page=100" --jq '.[] | {number, title, state, due_on, open_issues, closed_issues}'
gh api "repos/OWNER/REPO/issues?state=all&milestone=NUMBER&per_page=100" --jq '.[] | select(.pull_request == null) | {number, state}'
```

awa creates and updates milestones with `POST`/`PATCH repos/OWNER/REPO/milestones[/NUMBER]`. A session MUST
NOT use these to change a milestone awa manages.

GitHub facts awa relies on:

- A milestone belongs to one repository, and an issue has at most one. Milestones are created, edited and
  closed only through REST; GraphQL can read them and set them on issues but has no milestone mutations.
- Without push access, GitHub silently drops milestone changes on issues while reporting success, so awa
  reads every assignment back.
- New sub-issues can inherit their parent's milestone; awa sets every story's milestone explicitly instead.
- A Project's built-in Milestone field can group a table but cannot be written on the Project.
- GitHub has no native link between a Release and a milestone; awa enforces "milestone title equals tag".
- Closed milestones are never deleted.
- In a manifest, omitting a story's `milestone` key leaves the milestone as awa last recorded it.
