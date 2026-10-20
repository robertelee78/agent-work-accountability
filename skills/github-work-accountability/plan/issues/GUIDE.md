---
name: plan-issues
description: Open to create or update a managed issue, to find the issue for a work key, or to understand the managed block. Teaches the work-key marker, exact-match lookup across open and closed issues, the managed block format, and which lines only awa may write.
---

# Managed issues and work keys

A session working this leaf MUST also follow the [plan domain rules](../GUIDE.md).

## When to use

A session MUST use this leaf when:

- creating the root epic, section epics or stories of a validated extraction; or
- finding the issue that represents a work key, or updating its source-bound text.

## Steps

1. **Work key.** Every item has one durable identity in its managed block:

   ```html
   <!-- work-accountability:key OWNER/REPOSITORY:SOURCE:ITEM -->
   ```

   The key MUST be repository-qualified and unique across open and closed issues. A session SHOULD prefer an
   existing stable source ID; otherwise it MUST mint a concise logical ID at the first validated extraction
   and MUST keep it forever. Titles, heading numbers, source lines, branches, attempts and issue state are
   aliases or observations and MUST NOT be used as the identity. A shared key MUST NOT collapse work from
   two repositories into one issue.

2. **Lookup before creation.** The session MUST enumerate every open
   and closed issue through the paginated Issues API, exclude pull requests, and match the complete marker
   byte-for-byte. GitHub's search index MAY seed candidates but MUST NOT be used to prove absence: it is
   tokenized and lags writes.

   ```sh
   gh api --paginate "repos/OWNER/REPO/issues?state=all&per_page=100" \
     --jq '.[] | select(.pull_request == null) | select(.body // "" | contains("<!-- work-accountability:key OWNER/REPO:SOURCE:ITEM -->")) | .number'
   ```

   - zero matches: the session MAY create the issue;
   - one match: the session MUST update that issue;
   - more than one: a conflict; the session MUST stop mutating and MUST report the duplicate numbers.

   After creating an issue, the session MUST read it back by number and enumerate exact matches again so a
   concurrent duplicate is detected.

3. **Managed block.** Generated content MUST live between the explicit
   markers, and all human text outside them MUST be preserved:

   ```html
   <!-- work-accountability:begin -->
   <!-- work-accountability:key OWNER/REPOSITORY:SOURCE:ITEM -->
   Storage profile: `project-fields`
   Source: `PATH` at `COMMIT` (`BLOB`)
   Source excerpts:
   > exact source text

   Outcome: one observable deliverable

   Acceptance:
   - criterion

   Validation: how acceptance will be proved

   Delivery boundary: the event that makes this item Done
   Planned delivery: release next
   <!-- work-accountability:end -->
   ```

   - `Planned delivery:` MUST be copied exactly from the extraction report's `planned_delivery` line.
   - `Storage profile:` MUST be one of `organization-fields`, `project-fields` or `repository-labels`
     ([model](../../model/GUIDE.md#storage-profiles)), and MUST NOT say `project-fields` before the Project
     exists.
   - `Delivery boundary:` MUST name the release, publication, enablement, merge or other event that makes
     the item Done.
   - A workstream root with no planning document MUST say `Source: <workstream name> (no planning
     document)`; awa never closes or reopens such a root.

4. **Attachment and reconciliation.** The session MUST create epics and stories as native sub-issues of their
   parent, in the same repository, and MUST then put the document on its Project
   ([board/reconcile](../../board/reconcile/GUIDE.md)). The reconciler never creates issues, so this step
   MUST come first.

## Lines only awa writes

awa writes and later trusts these record lines in the managed block: `Project:`, `Delivery:`, `Release:`,
`Integration:`, `Delivered:`, `Won't do:`, `Blocked by:`, `Blocked reason:`, `Attempt:` and
`Ended attempts:`. A session MUST NOT type them; it MUST declare the facts in the manifest and reconcile.

A hand-typed record line stops the next run. Observed:

```text
reconciliation failed: release milestones (nothing was written): #4's managed block has the line
`Release: v9.9.9`, which says awa set milestone v9.9.9, but GitHub shows no milestone. If a person or agent
typed that line, delete it from the issue body and run again: …
```

After deleting such a line, the session MUST re-run `--draft` before the next apply. A manifest drafted
while the line was present carries its value and recreates it (observed: a stale manifest re-created and
set milestone `v9.9.9` after the line had been removed).

## Rules

- The copied prose MUST NOT be treated as a second editable requirement. On reconciliation the session
  MUST regenerate it from a validated extraction of the current source.
- An adopted repository MAY already use a different hidden marker. During migration the session MUST
  register and search that legacy marker, MUST add the canonical marker to the same issue, and MUST keep its
  key and history. It MUST NOT create a replacement issue merely to normalize marker syntax.
- Renames and source revisions MUST update the existing issue and MUST NOT mint a new key.

## Dropping down a level

```sh
gh issue view N --repo OWNER/REPO --json body --jq .body                    # read the managed block
gh api repos/OWNER/REPO/issues/N -X PATCH --input body.json                 # write a body ({"body": "…"})
gh api repos/OWNER/REPO/issues/N/sub_issues -X POST -F sub_issue_id=CHILD_DATABASE_ID   # attach a child
gh api repos/OWNER/REPO/issues/N/sub_issues --paginate                      # list children
```

The `sub_issue_id` value MUST be the child's database `id` from
`gh api repos/OWNER/REPO/issues/CHILD --jq .id`, not its number. Bodies MUST be passed through a file or
`--input` and MUST NOT be interpolated into a shell command.
