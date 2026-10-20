---
name: release-backfill
description: Open when stories landed before release milestones were tracked and the operator wants them attributed to the releases that shipped them, or to ask which release shipped one story. Teaches awa release backfill, --accept, and awa release attribute.
---

# Attribute already-shipped work to releases

## Which release shipped one story?

The session MAY ask for one issue (read-only):

```sh
awa release attribute 3 --repo OWNER/REPO
```

Observed receipt:

```json
{"action": "attribute", "issue": 3, "reason": "first full release containing 000000000000",
 "schema": "github-work-accountability/release-receipt-v1", "status": "released", "tag": "v1.0.0"}
```

| `status` | Means |
|---|---|
| `released` | The first full release (by publication time) whose tag contains the story's landing commit is `tag`. |
| `unknown`, observed: `"reason": "no Integration record (the landing commit was never recorded)"` | The story never recorded `integration` evidence. |
| `pending` | The landing commit is on the default branch but in no full release yet. |
| `unknown` | No `Integration:` record, the commit is unknown to GitHub, or it is not on the default branch (a backport or unmerged change); `reason` says which. |

## Backfill many stories

1. **Proposal (read-only).** The session MUST run the proposal first:

   ```sh
   awa release backfill --repo OWNER/REPO > proposals.json
   ```

   ```json
   {"action": "backfill", "applied_mutations": [], "proposals": [], "schema": "github-work-accountability/release-receipt-v1",
    "tag": "", "unknown": []}
   ```

   `proposals` lists `{issue, title, release}` for stories with no release milestone whose landing commit
   shipped; `unknown` lists stories that cannot be attributed and why (for example `closed, but no
   Integration record (landing commit never recorded)`).

2. **Operator review.** The session MUST show the proposals to the operator and MUST keep only the
   rows the operator accepts. It MUST NOT apply proposals the operator has not seen.

3. **Accepted rows.** The session MUST apply only the rows the operator accepted:

   ```sh
   awa release backfill --accept accepted.json --repo OWNER/REPO
   ```

   `accepted.json` MUST hold the accepted `proposals` list; it MAY instead hold the whole receipt with rows
   removed. awa re-checks every row, reconciles each document with the release milestones set, and closes a milestone when it now
   holds no open issue and its Release is published.

## What the results mean

| Exit | Output | Means | Required action |
|---|---|---|---|
| 0 | `proposals` / `unknown` (no `--accept`) | Nothing was written. | The session MUST review them with the operator. |
| 0 | `documents[].verified: true`, `closed_milestones` | Accepted rows were applied and read back. | The session MUST report the receipt. |
| 2 | `awa release backfill: refused, nothing was written:` then `#N: accepted TAG, but it now attributes to OTHER (…)` | The facts changed since the proposal. | The session MUST re-run the proposal and review again. |
| 2 | `row … is not a managed issue with a release tag` / `#N belongs to no document Project` | A bad row, or a story outside any document Project. | The session MUST remove or fix the row, or reconcile the document first. |
| 2 | `reconciliation failed: --accept must hold a list of {issue, release} rows (the proposals output)` | The file is malformed. | The session MUST pass the proposals output format. |
| 2 | `reconciliation failed: cannot read --accept FILE: …` | The file is unreadable or not JSON. | The session MUST fix the path or the file. |

## Dropping down a level

Attribution walks the repository's full releases in publication order and checks whether each tag contains
the landing commit. A session MAY verify one row by hand:

```sh
gh api "repos/OWNER/REPO/releases?per_page=100" --jq '.[] | select(.draft == false and .prerelease == false) | {tag_name, published_at}'
gh api repos/OWNER/REPO/compare/TAG...LANDING_SHA --jq .status     # "behind" or "identical": TAG contains it
```
