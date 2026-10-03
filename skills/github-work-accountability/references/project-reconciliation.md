# Project reconciliation

Use the bundled reconciler after creating or updating source-bound GitHub issues. One manifest declares one planning document (or one independently managed workstream): its root epic, any nested section epics, and every story. The reconciler turns that tree into one repository-linked GitHub Project and proves that people see the right cards on it.

## The everyday loop

Write manifests outside the product repository unless the repository deliberately tracks operations configuration.

```sh
SKILL_DIR="/path/to/skills/github-work-accountability"
RECONCILE="python3 $SKILL_DIR/scripts/reconcile_project.py"

# 1. Read what GitHub shows now (read-only). Prints a complete manifest.
$RECONCILE --draft --repo OWNER/REPOSITORY --root ROOT_ISSUE --user LOGIN > /var/tmp/doc.json

# 2. Edit only what your event changes: a story's phase and its evidence, a Health, a Priority.

# 3. Preview the delta (read-only), then apply it.
$RECONCILE --manifest /var/tmp/doc.json --user LOGIN
$RECONCILE --manifest /var/tmp/doc.json --user LOGIN --apply
```

The dry run lists exactly the writes `--apply` would make, issue by issue; right after a verified apply it lists none. A dry run never reads back its own result, so its receipt always has `"verified": false`. Report completion only when an `--apply` receipt says `"verified": true`. If a run stops, rerun the same command: it re-reads GitHub and applies only what is missing.

`--draft` copies evidence from manifests this machine applied successfully before, newest first; pass `--base FILE` (repeatable, v3 or v4) to supply evidence from elsewhere. Evidence is never read from GitHub, so a story whose phase needs evidence the draft cannot find is listed on stderr and must be completed before `--apply`.

## Manifest `project-v4`

```json
{
  "schema": "github-work-accountability/project-v4",
  "repository": "OWNER/REPOSITORY",
  "scope": {
    "root_number": 100,
    "root_work_key": "OWNER/REPOSITORY:PRD-001",
    "source": {"kind": "prd", "path": "docs/prd/PRD-001-example.md"}
  },
  "project": {
    "owner": "OWNER",
    "title": "REPOSITORY — PRD-001 Example",
    "priority_options": ["High", "Medium", "Low"],
    "general_section_label": "PRD-001 (general)"
  },
  "supersedes": [],
  "acknowledged_changes": [],
  "observed": {
    "100": {},
    "101": {"Source freshness": "Current", "Rank": 1},
    "120": {"Work phase": "Backlog", "Health": "On track", "Source freshness": "Current", "Priority": "High", "Rank": 2}
  },
  "items": [
    {"number": 100, "work_key": "OWNER/REPOSITORY:PRD-001", "kind": "epic",
     "source_freshness": "Current", "priority": "High", "rank": 0},
    {"number": 101, "work_key": "OWNER/REPOSITORY:PRD-001:S3.1", "kind": "epic",
     "parent": "OWNER/REPOSITORY:PRD-001", "section_label": "§3.1 Direct mode",
     "source_freshness": "Current", "priority": "High", "rank": 1},
    {"number": 120, "work_key": "OWNER/REPOSITORY:PRD-001:S3.1:handshake", "kind": "story",
     "parent": "OWNER/REPOSITORY:PRD-001:S3.1", "work_phase": "Ready", "health": "On track",
     "source_freshness": "Current", "priority": "High", "rank": 2,
     "evidence": {"design_approval": {"ref": "docs/design.md@COMMIT", "work_key": "OWNER/REPOSITORY:PRD-001:S3.1:handshake", "requirement": "SOURCE_COMMIT:REQUIREMENT_ID"}}}
  ]
}
```

Rules the reconciler enforces before any write:

- `schema` is `project-v4`. A `project-v3` manifest is refused with a message to regenerate it with `--draft`.
- Exactly one item has no `parent`: the root epic named by `scope`. Every other item names an epic as its parent, and the chain reaches the root without cycles.
- At most three levels below the root: root → section → subsection → story. Stories may also sit directly under the root.
- `section_label` is required on, and allowed only on, epics directly under the root. Labels are unique and differ from `general_section_label`.
- Stories carry `work_phase`, `health`, and the evidence gates below. Epics omit `work_phase`; an epic's Health, if given, must equal the value its stories produce.
- An epic cannot be `Current` while a descendant needs reconciliation.
- Attempt IDs and attempt event references are unique across the whole document.
- Rank orders stories across the whole document.
- `observed` records, for every item, the values GitHub showed when the manifest was drafted. `--draft` writes it; do not edit it by hand.

### Release fields on a story

```json
{"number": 120, "work_key": "OWNER/REPOSITORY:PRD-001:S3.1:handshake", "kind": "story",
 "parent": "OWNER/REPOSITORY:PRD-001:S3.1", "work_phase": "Release ready",
 "delivery": {"kind": "release", "release": "v0.2.10"},
 "milestone": "v0.2.10",
 "evidence": {
   "candidate": {"ref": "https://github.com/OWNER/REPOSITORY/commit/CANDIDATE", "work_key": "…", "requirement": "…"},
   "integration": {"ref": "https://github.com/OWNER/REPOSITORY/commit/LANDED", "work_key": "…", "requirement": "…",
                   "candidate": "https://github.com/OWNER/REPOSITORY/commit/CANDIDATE",
                   "commit": "LANDED_40_CHARACTER_SHA", "pr": "https://github.com/OWNER/REPOSITORY/pull/12"}
 }}
```

- `delivery.kind` is `release` (with `release`: a tag or `next`), `merge`, or `other`. A story without `delivery` keeps the pre-0.10 behaviour.
- `milestone` is the release milestone, exactly the tag. An exact `delivery.release` sets it. Omit the key to leave the milestone as awa last recorded it.
- Moving a story to another release needs `milestone_change_reason`; awa posts it once as an issue comment.
- From Release ready on, `release` and `merge` stories need `integration`: `commit` is the full SHA that landed on the default branch, `candidate` must equal the candidate evidence `ref`, and `pr`, if given, must be merged as exactly that commit. The reconciler checks this against GitHub before writing anything.
- Done for a `release` story needs `delivery.release` equal to the milestone, a published, non-draft, non-pre-release Release for that tag, and the tag must contain the integration commit. Done for a `merge` story needs the integration commit on the default branch.
- `integration.branch` (optional) names the branch the commit landed on when that is not the default branch, such as a release integration branch. The commit is checked against that branch; at release close the tag must still contain it.

### Dependencies and Blocked

- `blocked_by`: work keys of managed issues in the same repository that block this story. awa adds missing GitHub blocked-by links and removes only links it added (recorded as `Blocked by:`); links people add are kept and listed in the receipt notes. Omit the key to leave links as awa last recorded them.
- `blocked_reason`: required when Health is Blocked and no blocker is open. Recorded as `Blocked reason:` and cleared once the story is no longer Blocked. A Blocked story with neither is refused before anything is written; an On track story with open blockers is noted.

### What awa records on each issue

The issue's managed block carries facts awa writes and later trusts:

- `Delivery: release v0.2.10` (or `release next`, `merge`, `other`)
- `Release: v0.2.10`: the release milestone awa last set
- `Integration: SHA [via PR]`
- `Delivered: URL`: the Release, or the commit for merge delivery
- `Blocked by: #12, #14` and `Blocked reason: …`

`--draft` reads these back, so a manifest drafted on any machine knows each story's delivery and milestone. A milestone on GitHub that is neither the `Release:` record nor what the manifest asks for was changed outside awa: the run stops, names the issue, and writes nothing. Put it back, or re-draft and move it in the manifest with a reason.

**Closed means accepted.** An issue is closed (as completed) exactly while its story is Release ready or Done, for every delivery kind, so a release milestone's progress bar shows accepted work before the release ships. Accepted stories delivered by a release also carry the `awaiting-release` label until `awa release close` moves them to Done. When a story drops back below Release ready, awa reopens its issue and comments once why. An issue a person closed as not planned or duplicate is left closed and reported in the receipt.

**Won't do.** A Won't do story's issue is closed as *not planned*, its release milestone is removed (the reason is posted once, like any release move), and a `Won't do: REASON (decided by @AUTHOR: REF)` line is recorded in the managed block. Moving the story back out of Won't do reopens the issue with a one-line comment and removes that line. `awa release close` never counts Won't do stories as delivered.

**Write budget.** Each issue gets one write per run (managed block, labels and open/closed state together), confirmed by the write's own response, except that a close or reopen that also changes the managed block is written as two requests, text first: GitHub skips a milestone's open/closed counter when one request changes both an issue's state and its body (reproduced in a clean repository, 2026-10-03). awa logs its writes per account under `$XDG_STATE_HOME/agent-work-accountability/writes/` and, before writing, refuses (exit 75, nothing written) when the run's estimated writes plus those of the last hour would pass `WORK_ACCOUNTABILITY_HOURLY_WRITES` (default 450, under GitHub's roughly 500 an hour). The refusal names the time to rerun. A run that alone needs more than the limit proceeds with a warning; if GitHub stops it, rerunning continues where it stopped.

**Record lines.** awa writes the `Project:`, `Delivery:`, `Release:`, `Integration:`, `Delivered:`, `Won't do:`, `Blocked by:` and `Blocked reason:` lines in a managed block and reads them back as its own record. A hand-typed `Release:` line looks like a milestone awa set; when GitHub disagrees, the run stops, names the line, and says to delete it.

### Upgrading to 0.10

`--draft` takes each story's `delivery` from awa's `Delivery:` record, then from the issue's `Planned delivery:` line (written from an extraction-v3 manifest), and only then proposes one from the `Delivery boundary:` text, listing those proposals to check. Release ready and Done stories delivered by release or merge then need `integration` evidence before the next `--apply`: the draft lists them. Nothing is written until the manifest is complete.

## The tree on GitHub

The reconciler walks native sub-issues recursively from the root and from every epic in the manifest. The manifest must equal that tree exactly: the same issues, and the same parent for each. An omitted sub-issue, an extra issue, a re-parented issue, a sub-issue from another repository, an unmanaged issue inside the tree, or a story that has sub-issues each fail before mutation with the issue numbers.

A parent link the manifest declares but GitHub lacks fails unless `--attach-parents` is passed. With it, the reconciler adds the native sub-issue link, only when the child has no parent yet, and reads it back. The reconciler never creates issues; create the root epic issue with the skill's normal issue rules.

A manifest whose root issue is itself a sub-issue of another managed epic is refused: a section never gets its own Project. Reconcile the document root instead.

## What the Project shows

Fields: Work phase, Health, Source freshness, Priority, Rank, **Section** (single select), **Progress** (text).

- **Section** names each item's section: the epic directly under the root that it belongs to, or `general_section_label` for the root and stories directly under it. Options are only added or renamed, never removed. Each option records its section's work key in its description, so relabelling a section renames the option in place and every card keeps its value.
- **Progress** appears on epics only, for example `7/12 Done · 1 blocked · 2 at risk`, counted over every story below the epic.
- **Epic Health** is the worst Health of the stories below it (Blocked, then At risk, then On track).

Views:

1. **Lifecycle**: board filtered by `has:work-phase`, columns by Work phase, ordered by Priority then Rank. It shows every story in the document and no epics, because epics never carry a Work phase. It is created first, so it is the first tab.
2. **By section**: table grouped by Section, ordered by Rank, showing Title, Work phase, Health, and Priority, filtered to stories (`has:work-phase`). Each group header is the Section option's description, which awa keeps as the section's progress: `3/5 Done · 1 blocked · section managed by github-work-accountability (work-accountability:section KEY)`.
3. **By release**: table grouped by the built-in Milestone field, ordered by Rank, showing Title, Work phase, Health, Section, and Priority, filtered to stories (`has:work-phase`).

GitHub's table **Show hierarchy** nests every item under its parent issue when the parent is in the view too, the API cannot turn it off, and signed-out visitors to a public board cannot expand nested rows. Without these filters the root epic swallowed both tables (observed 2026-10-03). Showing stories only makes both tables read the same signed in and signed out. Tables made by 0.10.6 and earlier get the filter in place on their next run, keeping their view numbers; the key at the end of a Section description lets awa follow a renamed section.

**Project status.** awa posts a status update when the document's status (On track, At risk, Off track, Complete) changes, and otherwise edits its latest one in place when the progress text or open release milestones change, so the status never shows stale numbers.

GitHub refuses to delete a Project's last view, so the reconciler always creates new managed views before deleting the ones they replace; after a crash between the two, a rerun keeps the valid new view and deletes the old one. GitHub has no API to reorder views, and it opens each person's last-visited view. If Lifecycle is ever recreated, By section is recreated after it so Lifecycle stays first. The reconciler never deletes views people made; it deletes only GitHub's initial empty table on a Project it created and managed views it is replacing. A Lifecycle view with an outdated filter is replaced automatically: the 0.7.x `parent-issue:OWNER/REPO#N`, or the quoted `has:"Work phase"` written by 0.8.0–0.8.1, which GitHub's web page rejects (Safari shows a blank Project); any other malformed managed view needs `--repair-lifecycle`.

Value filters on field names that contain a space (`"Work phase":Acceptance`) return nothing through GitHub's API. Build extra views with `has:` filters or grouping instead.

## Verified outcome

A run sets `verified: true` only after it has read all of these back:

- exactly one Project marked for the repository and root work key, linked to the repository;
- every tree issue an active Project item, and no managed issue from another document on it;
- every field value, Section, epic Progress and Health as computed, and no built-in Status;
- Lifecycle and By section configured as above, Lifecycle before By section;
- **the board check**: GitHub's own filter engine (`ProjectV2.items(query:)` with each view's saved filter) returns every story and no epic for Lifecycle and By release, and By section. Cards people added themselves are allowed and listed in the receipt as `unmanaged_items`;
- each issue's Project membership agreeing with the Project's item list; and
- every managed issue block pointing at the Lifecycle view.

## Status updates

After a verified apply, awa compares the document's status with the latest status update it posted: Complete when every story is Done, otherwise Off track (root Health Blocked), At risk, or On track. It posts one update, with the Progress line and the open release milestones, only when that status changed. Status updates people post are left alone and don't count. The dry run lists a pending post.

## Releases: `awa release`

```sh
awa release plan v0.3.1 --description "New features' defects"          # create the release milestone
awa release plan v0.3.1 --due 2026-10-15 --due-source "agreed with the decider, 2026-10-01" --update
awa release status v0.3.1          # members by document, and next-stories that will join
awa release attribute 245          # which full release first shipped this story's landing commit
awa release close v0.3.1 [--move-open-to v0.3.2]
awa release backfill               # read-only proposals for work that already shipped
awa release backfill --accept accepted.json
```

All take `--repo OWNER/REPOSITORY` (default: the current checkout's `origin`) and `--user`.

`awa release close TAG`, after the GitHub Release is published:

1. Refuses if the Release is missing, a draft, or a pre-release.
2. Collects the members: issues in the release milestone, plus `release next` stories whose landing commit is first contained in this release. Refuses, listing everything at once and writing nothing, if any member is unmanaged or in no document, if a `next` story cannot be attributed, if a member is not Release ready or Done (unless `--move-open-to` names the release to move it to), or if this machine lacks a member's candidate or integration evidence.
3. Saves the per-document manifests and a ledger under `$XDG_STATE_HOME/agent-work-accountability/releases/`, then applies each document through the normal reconcile: Release ready stories become Done with the Release as delivery evidence, and their issues close.
4. Adds or refreshes a "Work items delivered" section in the release notes, keeping everything people wrote, and closes the milestone last.

A stop at any point is resumed by rerunning the same command, which continues from the saved ledger; a finished close reruns without writing.

`awa release backfill` lists, for every managed story with an `Integration:` record and no release milestone, the first full release that contains its landing commit, plus the closed stories it cannot place and why. It writes nothing. Save the output, delete the rows you don't accept, and run `--accept FILE`: each row is re-checked, applied through the normal per-document reconcile, and each past release's milestone is closed once everything in it is closed.

## Visibility

Every document Project is private unless someone deliberately makes it public.

- GitHub's create call has no visibility input and GitHub documents no default. The reconciler creates each Project under a neutral title (`work-accountability setup …`), makes it private, and only then gives it its real title and content. It records `Visibility: private` in the README's managed block and checks visibility as part of `verified: true`.
- Visibility is not a manifest setting, and reconcile never changes it. The README's `Visibility:` line, written only by the command below, records the deliberate choice; a board without that line must be private.
- To make a board public or private:

  ```sh
  awa project visibility ROOT_ISSUE public --repo OWNER/REPOSITORY   # or pass the Project URL
  ```

  Going public first prints what outsiders will see: title, short description, README, views, section names, fields shown on views, how many issue cards there are and whether they are hidden (items from a private repository stay hidden from outsiders), and any draft issues, which become fully visible. It then asks for confirmation; without a terminal it exits until rerun with `--yes`. It changes visibility, records `Visibility: …` in the README, and reads both back. Going private needs no preview.
- If an organization only lets its owners change Project visibility, or the account lacks Project admin rights, GitHub refuses; the command reports that and leaves the board unchanged. It never retries.
- If a board's live visibility differs from the README record, someone changed it in GitHub's settings page. The next reconcile stops, names the board, and writes nothing, and a fresh `--draft` does not paper over it: run the command to keep the new value, or change it back in GitHub.
- `awa doctor`, run inside a public repository, notes any of its document Projects that are private.

## Two agents, one board

With one board per document, several agents share it. The lost-update guard stops an older manifest from undoing newer work:

- A field your manifest **changes** (differs from `observed`) is written only if GitHub still shows what you observed. Otherwise the run refuses, lists each item ("#N Work phase: you read 'Ready', GitHub now shows 'Executing', you want 'Designing'"), and writes nothing. Rerun `--draft`, reapply your change, and retry.
- A field your manifest **leaves alone** keeps whatever GitHub shows now, even if someone changed it after you drafted. The receipt lists these under `kept_live_values`.

The repository lock serializes local writers between the check and the writes.

## Evidence gates

- **Backlog** and **Designing** need no transition evidence in the manifest.
- **Ready** adds `design_approval`.
- **Executing** adds an active `attempt` event.
- **Acceptance** adds an immutable `candidate`.
- **Release ready** adds `verdict`; it must name different `author` and `implementer` values and bind its `candidate` to the candidate evidence `ref`.
- **Done** adds `delivery`, whose `candidate` binds to the accepted candidate.
- **Won't do** needs only `decision`: `ref` links the comment on the story's own issue where the decision is recorded, `author` names who decided, and `reason` says why in one line. Any phase may move to Won't do.

Every evidence object contains `ref`, `work_key`, and `requirement`. Attempt evidence additionally contains `ref_kind`, `attempt_id`, `actor`, `started_at`, and `state`. `ref_kind` is `issue_comment`, `communication_event`, or `tracker_event`; `started_at` is timezone-qualified RFC3339. Executing requires `state: active`. Acceptance and later phases require `state: submitted` because the attempt has produced the candidate under review.

An attempt `ref` names the durable attempt-start event. A branch, pull request, source commit, candidate commit, changed file, claim, or agent process is not an attempt-start event and cannot be reused to move several stories to Executing. An `issue_comment` reference must point to the exact story it advances. The reconciler rejects GitHub commit URLs in this field. When an attempt fails, is released, expires, or is cancelled without a submitted candidate, move the story back to Ready and retain the ended attempt in issue history. A later retry gets a new `attempt_id` and a new start event.

The reconciler validates identity, required shape, active/submitted state, candidate binding, and independent reviewer identity. The agent remains responsible for checking that each reference exists and semantically proves the claimed fact before writing the manifest.

## Merging per-epic Projects into one document Project

Skill 0.7.x gave each epic its own Project. Merge a document's boards like this.

1. **Restart every agent session** that works in the repository, so none keeps running 0.7.x instructions. Run `awa doctor` and confirm the skill version is 0.8.0 or later.
2. **Create the root epic issue** if the document has none, with a managed block and the document's root work key (for example `OWNER/REPO:PRD-001`). Leave the section epics where they are.
3. **Draft**, naming the existing section epics that belong directly under the root:

   ```sh
   $RECONCILE --draft --repo OWNER/REPO --root ROOT --include 17,55,61 --user LOGIN > /var/tmp/prd-001.json
   ```

   The draft walks the tree, finds every Project marked for a work key in it, lists them in `supersedes`, copies each story's values from its old board, renumbers Rank across the document, and proposes section labels from the section titles. It reuses evidence from manifests this machine applied before; add `--base OLD_MANIFEST.json` for any other old manifests. Stderr lists stories still missing evidence.
4. **Review the draft.** Complete missing evidence. Shorten section labels if you like. If an old board's phase is not supported by evidence, lower it and record the change:

   ```json
   "acknowledged_changes": [
     {"project": 7, "number": 88, "field": "Work phase", "from": "Executing", "to": "Ready",
      "reason": "no durable attempt-start event exists"}
   ]
   ```

5. **Dry run**, then **apply**:

   ```sh
   $RECONCILE --manifest /var/tmp/prd-001.json --user LOGIN
   $RECONCILE --manifest /var/tmp/prd-001.json --user LOGIN --apply --attach-parents
   ```

What `--apply` does, in order. Each step is safe to rerun.

1. Checks that every Project marked for a tree key is listed in `supersedes`. A board already closed with a "superseded by" note pointing at this document's Project counts as done, which is what lets a rerun resume.
2. Checks the GraphQL and REST budget for the whole run and stops with the reset time if it is short.
3. Compares every old board's Work phase, Health, Source freshness, and Priority with the manifest, per (board, issue). Any disagreement not listed in `acknowledged_changes` stops the run with the full list and nothing written.
4. Snapshots every old board, and the document Project if one already exists, to `$XDG_STATE_HOME/agent-work-accountability/snapshots/HOST/OWNER/REPO/KEY/project-N.json`: fields, views, items, and values, plus each issue's managed block and parent. Once the migration has written anything, later reruns keep comparing against these first snapshots.
5. Attaches the missing parent links.
6. Creates or updates the document Project and runs the full verification above, including the board check.
7. Re-reads every old board. If anything on one changed since the snapshot, it stops: the new board is verified, the old boards stay open, and the message names what changed.
8. Points every managed issue at the new Lifecycle view.
9. For each old board, in one write, replaces its marker with `project-superseded ... -> NEW_URL`, adds "Superseded by NEW_URL on DATE; closed, not deleted.", and closes it. The items and values on old boards are never changed.

The receipt adds `migration_id`, `snapshots` (path and sha256), `superseded`, and `attached_parents`. There is no automatic revert: a closed board can be reopened by hand, and a board can always be rebuilt from the planning document and its issues.

An agent still running 0.7.x does not recognize the superseded marker and may create a new per-section board. The next run of the document stops and names that board; add it to `supersedes` (`--draft` does this) and it is merged and closed like the others.

## Adopting an existing Project

On the first run for a document that already has an unmarked Project, add `--adopt-project NUMBER`. Later runs find the Project through its marker. Adoption keeps views people made. A Project already marked for another work key cannot be adopted; list it in `supersedes` instead.

## Costs and limits

The command serializes local writers by GitHub host and account and by repository, batches independent GraphQL mutations, spaces writes (one second by default; `WORK_ACCOUNTABILITY_MUTATION_INTERVAL` overrides this for local simulation), reports measured GraphQL cost, and persists the manifest before writing. Exit status 75 means a temporary condition such as rate limiting; rerun later with the same manifest.
