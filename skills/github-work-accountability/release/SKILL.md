---
name: release
description: Domain for release milestones. Open to create a release milestone or record its agreed due date, to move a story to another release, to see who is in a release, to ship a published GitHub Release's stories to Done, or to find which past release shipped already-landed work.
---

# Release: milestones, delivery and Done

Each release has one **release milestone** in the repository, titled exactly as its tag (`v0.2.10`).
Leaves MUST say "release milestone" and MUST NOT say just "milestone", so it is not confused with ADR
milestones or acceptance gates. The milestone records which release a story is aimed at; a published, non-draft,
non-pre-release GitHub Release is the proof of delivery; Work phase stays the only lifecycle clock
([model](../model/SKILL.md#delivery-kinds)).

## Route

A session MUST open the leaf that the second column names for the task in the first column.

| The task is… | Open |
|---|---|
| Create a release milestone, record its due date, see who is in it, or move a story to another release | [plan](plan/SKILL.md) |
| A GitHub Release was published: move its stories to Done and close the milestone | [close](close/SKILL.md) |
| Find which past release shipped stories that never had a release milestone | [backfill](backfill/SKILL.md) |

## Rules for the whole domain

- A session MUST NOT close release milestones, or open or close managed issues, by hand. awa does both.
- A session MUST NOT move a story between release milestones in GitHub's UI: awa treats that as drift and
  stops. Moves MUST go through the manifest with a `milestone_change_reason` ([plan](plan/SKILL.md)).
- A release is not a planning document, so a session MUST NOT create a Project for one.
- A release milestone MUST carry a due date only when the operator stated one.

## Commands

| Command | Writes | Receipt `action` |
|---|---|---|
| `awa release plan TAG [--description TEXT] [--due YYYY-MM-DD --due-source TEXT] [--update]` | yes | `plan` |
| `awa release status TAG` | no | `status` |
| `awa release attribute ISSUE` | no | `attribute` |
| `awa release close TAG [--move-open-to TAG]` | yes | `close` |
| `awa release backfill [--accept PROPOSALS.json]` | only with `--accept` | `backfill` |

All of them take `--repo OWNER/REPO` (default: the current checkout's `origin`) and `--user LOGIN`, and print
a JSON receipt with
`"schema": "github-work-accountability/release-receipt-v1"`. Refusals go to stderr with exit 2; a write-budget
stop exits 75 ([access](../access/SKILL.md#budgets)).
