---
name: board-reconcile
description: Open whenever a document's Project must be created, updated or verified, and for every story event (approval, attempt, candidate, verdict, integration, blocker, won't do) since each is applied through this draft, dry-run, apply loop. Teaches the project-v4 manifest, the rules checked before any write, receipts, the lost-update guard, and what verified means.
---

# Reconcile a document onto its Project

## When to use

A session MUST use this loop:

- the first time a document's issues are put on its Project;
- for every story event (the event leaf says which field and evidence to change; this leaf applies it);
- to verify that a board is still correct.

## Steps

The session MUST write manifests outside the product repository unless the repository deliberately tracks
operations configuration.

```sh
# 1. Read what GitHub shows now (read-only). Prints a complete manifest.
$RECONCILE --draft --repo OWNER/REPO --root ROOT_ISSUE [--user LOGIN] > doc.json

# 2. The event's edit (step 2 below): one story's phase and evidence, a Health, a Priority.

# 3. Preview the delta (read-only), then apply it.
$RECONCILE --manifest doc.json [--user LOGIN]
$RECONCILE --manifest doc.json [--user LOGIN] --apply
```

1. The session MUST draft immediately before each change; it MUST NOT reuse a manifest drafted before
   another write.
2. In step 2 the session MUST change only the fields the event changes, and MUST NOT edit
   `observed`.
3. The session MUST read the dry run before `--apply`: its `planned_mutations` list is what `--apply` will
   write, issue by issue, including the values awa clears (a built-in Status, an epic's Work phase, a
   story's Progress). If the list contains a mutation the event does not explain, the session MUST NOT
   apply and MUST find out why. GitHub's default Project workflows set the built-in Status on their own, a
   little after an issue is added or closed, so an apply can clear a built-in Status that its dry run did
   not list; the receipt's `notes` then say so, and the session MUST NOT treat that clear as an unexplained
   write.
4. The session MUST report completion only from an `--apply` receipt with `"verified": true`. A dry run never
   reads back its own result, so its receipt always has `"verified": false`.
5. If a run stops, the session MUST rerun the same command: it re-reads GitHub and applies only what is
   missing.

`--draft` takes evidence from manifests this machine applied successfully before and from any `--base FILE`
(repeatable, v3 or v4; these come first), and says so on stderr (`draft: reusing evidence from 5 manifest(s)
this machine applied before`). Every manifest carries every story of its document, so for each story the
draft uses the newest manifest that set the story to the Work phase GitHub shows now and, while the story is
Executing, names the attempt recorded on the issue. A manifest drafted before another agent moved a story,
and applied after it, therefore does not hide that agent's evidence. Evidence is never read from GitHub, so a
story whose phase needs evidence the draft cannot find is listed on stderr, and the session MUST complete
that evidence before `--apply`. The session MUST NOT invent evidence for a story another agent moved; it
MUST draft on the machine that applied that evidence, or pass that machine's manifest with `--base`. All
tracker writes for one document SHOULD therefore happen on one machine.

`--draft` takes each story's `delivery` from awa's `Delivery:` record first, then from the issue's
`Planned delivery:` line, and only then proposes one from the `Delivery boundary:` text, listing each
proposal on stderr. The session MUST check every listed proposal before applying. Release ready and Done
stories delivered by release or merge MUST also carry `integration` evidence; the draft lists those
missing it.

## What the results mean

Receipts are JSON on stdout (`schema: github-work-accountability/receipt-v2`):

| Field | Meaning |
|---|---|
| `planned_mutations` | Every write the run will make (dry run) or made (apply), e.g. `set #3 Work phase=Executing`, `close #3 as completed (accepted)`, `label #3 awaiting-release`, `block #4 by #5`, `post Project status OFF_TRACK`. |
| `applied_mutations` | What `--apply` actually wrote. |
| `verified` | `true` only after an apply passed every read-back below. |
| `estimated_writes`, `notes` | The write-budget estimate (`write budget: applying this needs about 24 writes; …`). |
| `kept_live_values` | Fields the manifest left alone that someone changed after the draft; awa kept GitHub's value. |
| `unmanaged_items` | Cards people added themselves; allowed and listed. |
| `project_url`, `lifecycle_url`, `visibility` | The board and its Lifecycle view; `visibility` is `private` unless deliberately changed. |

| Exit | stderr | Means | Required action |
|---|---|---|---|
| 0 | (dry run) `planned_mutations` non-empty | The delta to apply. | The session MUST check it before `--apply`. |
| 0 | (dry run right after a verified apply) `planned_mutations: []` | GitHub matches the manifest. | No write is needed; the session MUST NOT apply an empty change to "refresh" the board. |
| 0 | (apply) `"verified": true` | Written and read back. | The session MUST report the receipt. |
| 2 | `reconciliation failed: these items changed since the manifest was drafted, so writing it would lose someone else's update: #3 Work phase: you read 'Backlog', GitHub now shows 'Executing', you want 'Ready'. Nothing was written. …` | The lost-update guard (below). | The session MUST re-run `--draft`, reapply its change, and retry. |
| 2 | `reconciliation failed: release milestones (nothing was written): #4's managed block has the line \`Release: v9.9.9\` …` | A hand-typed record line, or a milestone changed outside awa. | The session MUST delete the typed line or restore the milestone, then re-draft ([plan/issues](../../plan/issues/SKILL.md)). |
| 2 | `reconciliation failed: attempts (nothing was written): …` | An open attempt holds the story ([execute/attempt](../../execute/attempt/SKILL.md)). | The session MUST follow the message and MUST NOT work around an attempt. |
| 2 | `reconciliation failed: items[N]…` naming a phase and evidence | An evidence gate failed (below). | The session MUST add the named evidence. |
| 2 | `reconciliation failed: blocked-by (nothing was written): …` | Blocked without a blocker or reason ([execute/blocker](../../execute/blocker/SKILL.md)). | The session MUST add `blocked_by` or `blocked_reason`. |
| 75 | `temporary failure: …` | A rate limit, awa's write budget, or a transient GitHub or network failure (an HTTP 5xx, gh's `unexpected end of JSON input` for an empty error body, a dropped connection, or a gh call that did not answer within 60 s). The message names the time to rerun or the failed call. | The session MUST rerun the same command, at or after any time the message names, and MUST NOT report the run as a refusal. |

Every failed GitHub call is named in the message, whichever exit code it gets: `POST
users/OWNER/projectsV2/N/views failed: …` or `GraphQL mutation createProjectV2 failed: …`. The session MUST
quote that call when it reports the failure.

## The manifest (`project-v4`)

```json
{
  "schema": "github-work-accountability/project-v4",
  "repository": "OWNER/REPO",
  "scope": {"root_number": 100, "root_work_key": "OWNER/REPO:PRD-001",
            "source": {"kind": "prd", "path": "docs/prd/PRD-001-example.md"}},
  "project": {"owner": "OWNER", "title": "REPO — PRD-001 Example",
              "priority_options": ["High", "Medium", "Low"], "general_section_label": "PRD-001 (general)"},
  "supersedes": [],
  "acknowledged_changes": [],
  "observed": {"120": {"Work phase": "Backlog", "Health": "On track", "Source freshness": "Current",
                       "Priority": "High", "Rank": 2}},
  "items": [
    {"number": 100, "work_key": "OWNER/REPO:PRD-001", "kind": "epic", "source_freshness": "Current",
     "priority": "High", "rank": 0},
    {"number": 101, "work_key": "OWNER/REPO:PRD-001:S3.1", "kind": "epic", "parent": "OWNER/REPO:PRD-001",
     "section_label": "§3.1 Direct mode", "source_freshness": "Current", "priority": "High", "rank": 1},
    {"number": 120, "work_key": "OWNER/REPO:PRD-001:S3.1:handshake", "kind": "story",
     "parent": "OWNER/REPO:PRD-001:S3.1", "work_phase": "Ready", "health": "On track",
     "source_freshness": "Current", "priority": "High", "rank": 2,
     "delivery": {"kind": "release", "release": "v0.2.10"},
     "evidence": {"design_approval": {"ref": "docs/design.md@COMMIT",
                  "work_key": "OWNER/REPO:PRD-001:S3.1:handshake", "requirement": "SOURCE_COMMIT:REQUIREMENT_ID"}}}
  ]
}
```

The reconciler refuses, before any write, a manifest that breaks these rules; a session MUST satisfy them:

- `schema` MUST be `project-v4`; a `project-v3` manifest is refused with a message to regenerate it with
  `--draft`.
- Exactly one item MUST have no `parent`: the root named by `scope`. Every other item MUST name an epic as
  parent, and the chain MUST reach the root without cycles, at most three levels below it.
- `section_label` MUST appear on, and only on, epics directly under the root; labels MUST be unique and
  differ from `general_section_label`.
- Stories MUST carry `work_phase`, `health` and the evidence their phase needs. Epics MUST omit
  `work_phase`; an epic's Health, if given, MUST equal the rollup of its stories. An epic MUST NOT be
  `Current` while a descendant needs reconciliation.
- Attempt IDs and attempt event references MUST be unique across the document. Rank orders stories across
  the whole document.
- The manifest MUST equal the native sub-issue tree exactly: an omitted sub-issue, an extra issue, a
  re-parented issue, a sub-issue from another repository, an unmanaged issue inside the tree, or a story
  that has sub-issues each fail with the issue numbers. A parent link the manifest declares but GitHub lacks
  fails unless `--attach-parents` is passed, which adds the link only when the child has no parent yet.
- A manifest's root MUST NOT itself be a sub-issue of another managed epic: a section never gets its own
  Project.

## Evidence gates

A story MUST carry the evidence listed for its phase, in addition to the earlier phases' evidence.

| Phase | Needs, in addition to the earlier phases' evidence |
|---|---|
| Backlog, Designing | nothing |
| Ready | `design_approval` |
| Executing | `attempt` with `state: active` |
| Acceptance | `candidate`; `attempt.state` becomes `submitted` |
| Release ready | `verdict` (independent of the implementer); for `release` and `merge` delivery also `integration` |
| Done | `delivery` (written by `awa release close` for release delivery) |
| Won't do | only `decision` |

Every evidence object MUST contain `ref`, `work_key` and `requirement`; the event leaves list the extra
fields. The reconciler validates identity, shape, attempt state, candidate binding and reviewer
independence. The agent MUST still check that each reference exists and semantically proves the claimed
fact.

## Two agents, one board

- A field the manifest **changes** (differs from `observed`) is written only if GitHub still shows what was
  observed; otherwise the run refuses, lists each item, and writes nothing.
- A field the manifest **leaves alone** keeps whatever GitHub shows now, even if someone changed it after
  the draft; the receipt lists these under `kept_live_values`.
- A repository lock serializes local writers between the check and the writes. Just before writing an issue
  awa re-reads its `Attempt:` and `Ended attempts:` lines; GitHub has no conditional write for issue text, so
  a gap of about a second remains between agents on different machines. A coordinator running several
  agents SHOULD therefore route their tracker writes through one session.

## What the Project shows, and what verified means

Fields: Work phase, Health, Source freshness, Priority, Rank, **Section** (single select) and **Progress**
(text, epics only, e.g. `7/12 Done · 1 blocked · 2 at risk`). Section options are only added or renamed,
never removed; each records its section's work key in its description, so relabelling renames in place.

Views awa creates and keeps:

1. **Lifecycle**: a board filtered by `has:work-phase`, columns by Work phase, ordered by Priority then
   Rank. It shows every story and no epic, and is created first so it is the first tab.
2. **By section**: a table grouped by Section, ordered by Rank, filtered to stories; each group header is the
   section's progress (`3/5 Done · 1 blocked · section managed by github-work-accountability …`).
3. **By release**: a table grouped by the built-in Milestone field, ordered by Rank, filtered to stories.

The two tables are filtered to stories because GitHub's table **Show hierarchy** nests every item under its
parent when the parent is in the view, the API cannot turn it off, and signed-out visitors to a public board
cannot expand nested rows; without the filter the root epic swallows both tables.

Views people add are kept. awa never deletes a view people made; it replaces its own managed views by
creating the new one before deleting the old (GitHub refuses to delete a Project's last view). A session
MUST NOT edit or delete awa's managed views by hand. A session MAY add these optional views; their absence
does not affect verification:

- **Needs attention**: Health At risk or Blocked.
- **Ready queue**: Ready stories that are Current, not Blocked and unclaimed.
- **Acceptance**: stories in Acceptance with candidate and evidence visible.
- **Reconciliation**: Source freshness Reconciliation needed.

Extra views MUST use `has:` filters or grouping, not value filters on fields whose names contain a space.

`"verified": true` means all of these were read back: exactly one Project marked for the repository and root
work key, linked to the repository; every tree issue an active item, and no managed issue from another
document on it; every field value, Section, epic Progress and Health as computed, and no built-in Status;
the views configured as above in order; GitHub's own filter engine (`ProjectV2.items(query:)` with each
view's saved filter) returning every story and no epic for Lifecycle, By section and By release; each
issue's Project membership agreeing with the Project's item list; every managed block pointing at the
Lifecycle view; and visibility matching the README's record.

After a verified apply awa compares the document's status with the latest status update it posted:
Complete when every story is Done, otherwise Off track (root Health Blocked), At risk, or On track. It posts
one update, with the Progress line and the open release milestones, only when that status changed, and
otherwise edits its latest one in place. Status updates people post are left alone and do not count. A
session MUST NOT post status updates by hand to fix the board; it MUST change the stories.

## Dropping down a level

When a run fails with a message this leaf does not explain, the session MAY inspect the API layer the
reconciler uses. It MUST NOT write through these calls to work around a refusal.

- Project create and link: GraphQL `createProjectV2` (no visibility input), then `linkProjectV2ToRepository`;
  the link is read back through `repository { projectsV2 }`, which is what makes the board appear on the
  repository's Projects tab. Visibility is set explicitly with `updateProjectV2(public:)`.
- Items and values: `addProjectV2ItemById`, `updateProjectV2ItemFieldValue`,
  `clearProjectV2ItemFieldValue`, `deleteProjectV2Item`; read with `ProjectV2.items`.
- Fields: `createProjectV2Field`, `updateProjectV2Field`.
- Views: the versioned REST endpoint `{users|orgs}/OWNER/projectsV2/NUMBER/views` with header
  `X-GitHub-Api-Version: 2026-03-10`, numeric field database IDs in `vertical_group_by` (board) or
  `group_by` (table); `deleteProjectV2View` removes a managed view; configuration is read back through
  GraphQL.
- Status updates: `createProjectV2StatusUpdate`, `updateProjectV2StatusUpdate`.
- The board check: `ProjectV2.items(query: "<the view's saved filter>")`.
- Filters on GitHub: a field with a space is named by its hyphenated lowercase form (`has:work-phase`); the
  web page rejects `has:"Work phase"` even though the API accepts it, and value filters such as
  `"Work phase":Acceptance` return nothing through the API. `parent-issue:OWNER/REPO#N` matches only direct
  sub-issues. GitHub has no API to reorder views and opens each person's last-visited view.
- Costs: the reconciler serializes local writers per host, account and repository, batches independent
  GraphQL mutations, spaces writes one second apart (`WORK_ACCOUNTABILITY_MUTATION_INTERVAL` overrides it),
  reports measured GraphQL cost, and persists the manifest before writing.

Read-only probes a session MAY run:

```sh
gh api graphql -f query='query($o:String!,$n:Int!){user(login:$o){projectV2(number:$n){title public closed readme}}}' -F o=OWNER -F n=NUMBER
gh api graphql -f query='query($o:String!,$r:String!){repository(owner:$o,name:$r){projectsV2(first:20){nodes{number title}}}}' -F o=OWNER -F r=REPO
gh api -H 'X-GitHub-Api-Version: 2026-03-10' users/OWNER/projectsV2/NUMBER/views
```

For an organization owner, the session MUST replace `user(login:)` with `organization(login:)` and
`users/` with `orgs/`. Issue field values belong to issues, while Project-local field values belong to Project items.

GitHub's own documentation for the surfaces above:

- [About issue fields in Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/understanding-fields/about-issue-fields)
- [Adding items to a Project](https://docs.github.com/en/issues/planning-and-tracking-with-projects/managing-items-in-your-project/adding-items-to-your-project)
- [Adding sub-issues](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues)
- [Creating issue dependencies](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/creating-issue-dependencies)
- [Filtering Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/customizing-views-in-your-project/filtering-projects)
- [Listing repository issues through the REST API](https://docs.github.com/en/rest/issues/issues#list-repository-issues)
