---
name: release-close
description: Open after a GitHub Release is published, to move its Release ready stories to Done, add the delivered work to the release notes, and close the release milestone. Teaches awa release close, --move-open-to, delivery evidence, and resuming a partial close.
---

# Ship a release (Release ready → Done)

A session working this leaf MUST also follow the [release domain rules](../GUIDE.md).

## When to use

A session MUST run this after the GitHub Release for the tag is published (not draft, not pre-release), and
MUST NOT move release-delivered stories to Done any other way.

## Steps

1. The session SHOULD check membership first ([plan](../plan/GUIDE.md#see-who-is-in-a-release)).
2. The session MUST close the release with awa:

   ```sh
   awa release close v1.0.0 --repo OWNER/REPO
   ```

   Observed receipt:

   ```json
   {"action": "close",
    "applied_mutations": ["add delivered work items to the v1.0.0 release notes", "close release milestone v1.0.0"],
    "delivered": [3], "documents": [{"project": 1, "root": 1, "verified": true}], "moved": [],
    "release_url": "https://github.com/OWNER/REPO/releases/tag/v1.0.0",
    "schema": "github-work-accountability/release-receipt-v1", "tag": "v1.0.0", "verified": true}
   ```

   Stderr shows progress per document (`awa: closing v1.0.0: document 1/1 (#1)`).

3. If stories in the release are not Release ready or Done, the session MUST either finish them or, when the
   operator agrees they slip, move them on:

   ```sh
   awa release close v1.1.0 --move-open-to v1.2.0 --repo OWNER/REPO
   ```

   The target release milestone MUST already exist ([plan](../plan/GUIDE.md)). Observed receipt:

   ```json
   {"action": "close",
    "applied_mutations": ["add delivered work items to the v1.1.0 release notes", "close release milestone v1.1.0"],
    "delivered": [], "documents": [{"project": 1, "root": 1, "verified": true}], "moved": [4],
    "release_url": "https://github.com/OWNER/REPO/releases/tag/v1.1.0",
    "schema": "github-work-accountability/release-receipt-v1", "tag": "v1.1.0", "verified": true}
   ```

   Each moved story's issue gets the comment `Release target moved from v1.1.0 to v1.2.0: not finished when
   v1.1.0 was released; moved to v1.2.0`. With nothing delivered, the notes section lists `- (none)`.

## What close does

The members are the issues in the release milestone plus `release next` stories whose landing commit is
first contained in this release. A closed `next` story that cannot be attributed is refused; an open `next`
story with no `Integration:` record has not landed and is skipped. For each document with members, awa
drafts the manifest and, per story:

- **Done already:** counted as delivered.
- **Release ready, `release` delivery:** checks that the tag contains the story's landing commit (from its
  `integration` evidence, including a release integration `branch`), writes `delivery` evidence (`ref` the
  Release URL, `release` the tag, `candidate` from the candidate evidence), and moves it to Done; the
  `awaiting-release` label is removed.
- **Any other phase:** refused, or moved with `--move-open-to`.

It then applies each document, adds a `## Work items delivered` section to the release notes between
`<!-- work-accountability:delivered:begin -->` and `<!-- work-accountability:delivered:end -->`, and closes
the release milestone. Progress is kept in a ledger under
`$XDG_STATE_HOME/agent-work-accountability/releases/HOST/OWNER/REPO/TAG/`, so a rerun resumes where it
stopped, and a rerun of a finished close writes nothing. All refusals are listed at once, before anything is
written. The release notes section keeps everything people wrote outside its markers.

## What the results mean

| Exit | stderr | Means | Required action |
|---|---|---|---|
| 0 | — | `"verified": true`; stories are Done, notes updated, milestone closed. | The session MUST report the receipt. |
| 2 | `reconciliation failed: v1.0.0 has no published GitHub Release yet; publish it, then close the release` | No published Release for the tag. | The session MUST wait for the Release to be published, and MUST NOT publish one itself without the operator's authorization. |
| 2 | `reconciliation failed: v1.0.0 is a pre-release; only full releases close a release milestone` | Pre-releases never deliver. | The session MUST wait for the full release. |
| 2 | `awa release close v1.1.0: refused, nothing was written. Fix these, or rerun with --move-open-to TAG to move unfinished stories to another release:` followed by lines such as `  - #4 is Ready, not Release ready or Done` | Unfinished stories. | The session MUST finish them or, with the operator's agreement, rerun with `--move-open-to`. |
| 2 | `#N is Release ready but this machine has no candidate/integration evidence for it; run from the machine that applied it, or pass --base` | The evidence lives on another machine. | The session MUST run from that machine, or supply the manifest. |
| 2 | `#N: TAG does not contain its landing commit …` (and `on BRANCH; merge that branch first`) | The landing commit is not in the tag. | The session MUST correct the `integration` evidence or move the story to a later release. |
| 2 | `there is no release milestone TAG; create it with \`awa release plan TAG\`` | No milestone. | The session MUST create it ([plan](../plan/GUIDE.md)). |
| 2 or 75 | `awa release close TAG: stopped at document #ROOT: … already applied: … Fix the problem and rerun the same command; it resumes from here.` | A partial close. | The session MUST fix the problem and rerun the same command. |
| 75 | `… every document is applied, but adding the release notes and closing the milestone must wait …` | Only the last two writes remain. | The session MUST rerun the same command at the named time. |

## Dropping down a level

A session MAY run these read-only checks when a refusal is unclear:

```sh
gh api repos/OWNER/REPO/releases/tags/TAG --jq '{draft, prerelease, published_at, html_url}'   # is it published?
gh api repos/OWNER/REPO/compare/TAG...LANDING_SHA --jq .status                                 # "behind" or "identical": the tag contains it
```

awa reads the Release with `repos/OWNER/REPO/releases/ID`, edits its body with `PATCH` on the same path, and
closes the milestone with `PATCH repos/OWNER/REPO/milestones/NUMBER` (`{"state": "closed"}`), reading each back.
A session MUST NOT perform these writes by hand.
