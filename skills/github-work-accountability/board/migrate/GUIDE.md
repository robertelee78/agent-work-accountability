---
name: board-migrate
description: Open when a document still has per-epic Projects from skill 0.7.x that must merge into one document Project, when a document already has an unmarked Project to adopt, or when moving from the repository-label profile to Project fields.
---

# Merge, adopt or migrate Projects

A session working this leaf MUST also follow the [board domain rules](../GUIDE.md).

## Merging per-epic Projects into one document Project

Skill 0.7.x gave each epic its own Project. A session MUST merge a document's boards as follows:

1. **Session restart.** Every agent session that works in the repository MUST be restarted, so none keeps
   running 0.7.x instructions. The session MUST run `awa doctor` and confirm the skill version is 0.8.0 or
   later.
2. **Root epic.** If the document has no root epic issue, the session MUST create one with a
   managed block and the document's root work key (for example `OWNER/REPO:PRD-001`)
   ([plan/issues](../../plan/issues/GUIDE.md)). It MUST leave the section epics where they are.
3. **Drafting.** The session MUST draft, naming the existing section epics that belong directly under the
   root:

   ```sh
   $RECONCILE --draft --repo OWNER/REPO --root ROOT --include 17,55,61 [--user LOGIN] > prd-001.json
   ```

   The draft walks the tree, finds every Project marked for a work key in it, lists them in `supersedes`,
   copies each story's values from its old board, renumbers Rank across the document, and proposes section
   labels from the section titles. It reuses evidence from manifests this machine applied; `--base
   OLD_MANIFEST.json` adds others. Stderr lists stories still missing evidence.
4. **Draft review.** The session MUST complete missing evidence. It MAY shorten section labels. If an old
   board's phase is not supported by evidence, the session MUST lower it and record the change:

   ```json
   "acknowledged_changes": [
     {"project": 7, "number": 88, "field": "Work phase", "from": "Executing", "to": "Ready",
      "reason": "no durable attempt-start event exists"}
   ]
   ```

5. **Dry run and application.** The session MUST read the dry run before applying:

   ```sh
   $RECONCILE --manifest prd-001.json [--user LOGIN]
   $RECONCILE --manifest prd-001.json [--user LOGIN] --apply --attach-parents
   ```

What `--apply` does, in order; each step is safe to rerun:

1. Checks that every Project marked for a tree key is listed in `supersedes`. A board already closed with a
   "superseded by" note pointing at this document's Project counts as done, which is what lets a rerun
   resume.
2. Checks the GraphQL and REST budget for the whole run, and stops with the reset time if it is short.
3. Compares every old board's Work phase, Health, Source freshness and Priority with the manifest, per
   board and issue. Any disagreement not listed in `acknowledged_changes` stops the run with the full list
   and nothing written.
4. Snapshots every old board, and the document Project if one exists, to
   `$XDG_STATE_HOME/agent-work-accountability/snapshots/HOST/OWNER/REPO/KEY/project-N.json`: fields, views,
   items and values, plus each issue's managed block and parent. Later reruns compare against these first
   snapshots.
5. Attaches the missing parent links.
6. Creates or updates the document Project and runs the full verification, including the board check.
7. Re-reads every old board; if anything changed since the snapshot it stops with the new board verified
   and the old ones still open, naming what changed.
8. Points every managed issue at the new Lifecycle view.
9. For each old board, in one write, replaces its marker with
   `<!-- work-accountability:project-superseded HOST:REPOSITORY_NODE_ID OLD_WORK_KEY -> NEW_PROJECT_URL -->`,
   adds "Superseded by NEW_URL on DATE; closed, not deleted.", and closes it. Items and values on old boards
   are never changed.

The receipt adds `migration_id`, `snapshots` (path and sha256), `superseded` and `attached_parents`. There
is no automatic revert: a closed board can be reopened by hand, and a board can always be rebuilt from the
planning document and its issues. A session MUST NOT delete an old board; closing is the supported end
state.

An agent still running 0.7.x can create a new per-section board; the next run of the document stops and
names it. The session MUST then add it to `supersedes` (`--draft` does this) so it merges and closes like the
others. The reconciler never selects or reopens a superseded Project.

## Adopting an existing Project

On the first run for a document that already has an unmarked Project, the session MUST add
`--adopt-project NUMBER`; later runs find the Project through its marker. Adoption keeps views people made.
A Project already marked for another work key cannot be adopted; the session MUST list it in `supersedes`
instead.

## From repository labels to Project fields

The session MUST change the managed block to `project-fields` and record the Project URL, and MUST remove
only the `phase/`, `health/` and `source/` labels, and only after the Project has passed its link,
membership, field and board checks. The session MUST preserve unrelated labels and human prose. This order
keeps labels and fields from ever becoming two writable clocks.

## Dropping down a level

- A Project's README marker is read with GraphQL `ProjectV2.readme`; closing is `updateProjectV2(closed:
  true)`.
- `--repair-lifecycle` replaces one malformed managed view (Lifecycle or By section) that the automatic
  replacement does not recognize. A session MAY pass it, but MUST NOT pass it before a verification failure names
  that view. Lifecycle views with the 0.7.x `parent-issue:` filter or the quoted `has:"Work phase"` filter of
  0.8.0–0.8.1 are replaced automatically and need no flag.
