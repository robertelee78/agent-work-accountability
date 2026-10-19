---
name: execute-integration
description: Open when an accepted story's change has landed, since release- and merge-delivered stories need integration evidence from Release ready on. Teaches integration evidence, the Work-item trailer, and release integration branches.
---

# Record the landing commit

## When to use

From Release ready on, every story whose delivery is `release` or `merge` MUST carry `integration`
evidence: the commit that landed on the default branch, bound to the accepted candidate.

## Steps

1. **Landing.** The session MUST land the change on the default branch, or first on a release integration
   branch (step 2). The session SHOULD give each
   commit a `Work-item: #N` trailer. A session MUST NOT use GitHub closing keywords (`Fixes #N`) on
   release-delivered stories: GitHub would close the issue at merge, before the release ships.
2. **Integration evidence.** The session MUST draft and add, beside the verdict:

   ```json
   "integration": {"ref": "https://github.com/OWNER/REPO/commit/LANDED_SHA",
                   "work_key": "OWNER/REPO:DOC:ITEM", "requirement": "SOURCE_COMMIT:REQUIREMENT_ID",
                   "candidate": "https://github.com/OWNER/REPO/commit/CANDIDATE_SHA",
                   "commit": "LANDED_40_CHARACTER_SHA",
                   "pr": "https://github.com/OWNER/REPO/pull/12"}
   ```

   - `commit` MUST be the full SHA that landed on the default branch.
   - `candidate` MUST equal the candidate evidence `ref`.
   - `pr` is OPTIONAL; when given, that pull request MUST have merged as exactly that commit.
   - When the commit landed on a release integration branch first, the session MUST add
     `"branch": "integrate/v0.2.10"`; the commit is then checked against that branch, and at release close
     the tag MUST contain it.
3. **Dry run and application.** The session MUST dry run, read the plan, then apply. The managed block records
   `Integration: SHA via PR`.

The reconciler checks all of this against GitHub before writing anything.

## What the results mean

| Exit | stderr | Means | Required action |
|---|---|---|---|
| 0 | — | `Integration:` is recorded. | For `release` delivery the session MUST wait for the release ([release/close](../../release/close/SKILL.md)); for `merge` delivery it MAY move the story to Done. |
| 2 | names `integration` and the commit or PR | The commit is not on the branch, the PR merged a different commit, or `candidate` does not match. | The session MUST correct the SHA, PR or candidate; it MUST NOT point the evidence at a different commit to pass. |

## Dropping down a level

When the refusal is unclear, the session SHOULD check each fact directly:

```sh
gh api repos/OWNER/REPO/commits/SHA --jq .sha                         # the commit exists
gh api repos/OWNER/REPO/compare/main...SHA --jq .status               # "behind" or "identical": main contains it
gh api repos/OWNER/REPO/pulls/12 --jq '{merged, merge_commit_sha}'    # the PR merged exactly that commit
```

The reconciler reads a pull request's merge commit through GraphQL.
