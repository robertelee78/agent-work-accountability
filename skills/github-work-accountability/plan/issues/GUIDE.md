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

   Why: the problem and why solving it matters

   What: one observable deliverable and essential exclusions

   How: essential design constraints; a few precise references with their relevance
   - `PATH#section` at `COMMIT`: the decision this work must preserve
   - `src/path.py` / `symbol`: the relevant implementation starting point

   Acceptance:
   - observable completion criterion

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
   - New or substantively revised story briefs MUST cover Why, What, How and Acceptance as described in
     [extraction](../extract/GUIDE.md#steps), including work requested directly by the operator.
     The session SHOULD keep each section to a short paragraph or a few bullets; the template is a
     writing aid, not a demand for more background or a step-by-step implementation recipe.
   - References MUST identify the relevant ADR section, code path or symbol, or documentation and
     briefly say why it matters. The session MUST use existing references rather than paste long
     snippets; it MUST NOT invent references, design decisions or exclusions. A short source excerpt
     MAY be included when it adds essential context; exact provenance stays in the extraction.
   - Existing issues and extraction manifests MUST NOT be bulk-rewritten solely to adopt these headings.

4. **Attachment and reconciliation.** The session MUST create epics and stories as native sub-issues of their
   parent, in the same repository, and MUST then put the document on its Project
   ([board/reconcile](../../board/reconcile/GUIDE.md)). The reconciler never creates issues, so this step
   MUST come first.

## Lines only awa writes

awa writes and later trusts these record lines in the managed block: `Project:`, `Delivery:`, `Release:`,
`Integration:`, `Delivered:`, `Won't do:`, `Blocked by:`, `Blocked reason:`, `Attempt:` and
`Ended attempts:`. A session MUST NOT type them; it MUST declare the facts in the manifest and reconcile.

GitHub's live milestone assignment is authoritative for scheduling, even when an issue is unassigned.
A stale `Release:` line alone does not stop reconciliation or justify deleting a generated record.
After an external scheduling change, the session MUST re-draft and inspect the live assignment before
making another deliberate change; an old saved plan MUST NOT restore the former assignment.

## Rules

- The copied prose MUST NOT be treated as a second editable requirement. On reconciliation the session
  MUST regenerate it from a validated extraction of the current source, rebuilding Why and How from
  that bound source and current inspected references rather than retaining stale context.
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
`--input` and MUST NOT be interpolated into a shell command. A session MUST NOT write `-f body=@FILE`: `-f` is
gh's raw string field and sends the text `@FILE` itself, which GitHub accepts; only `-F body=@FILE` or
`--input FILE` reads a file. After any hand-written body the session MUST read the issue back and confirm
the block runs from `work-accountability:begin` to `work-accountability:end` before relying on it.
