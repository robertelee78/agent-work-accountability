---
name: access
description: Open first in a repository, or when a run fails on authentication, scopes, permissions, rate limits or the write budget. Teaches awa status, awa doctor, awa version, --diagnose and the stored-account wrapper, and what each failure means.
---

# Access: is this repository managed, and can this session write?

## When to use

- A session MUST use this leaf at the start of substantive work in any GitHub repository (the standing
  check).
- A session MUST use it before its first tracker write, and whenever a write fails on identity, scope,
  permission, rate limit or budget.

## Steps

1. **Is the repository managed?** The session MUST run, once, before editing (read-only, one REST search,
   no GraphQL budget):

   ```sh
   awa status --json            # inside the repository's checkout
   awa status --repo OWNER/REPO --json
   ```

   ```json
   {"awa_revision": "70f0c14…", "awa_version": "0.10.11", "host": "github.com", "managed": true,
    "managed_issue_count": 162, "repository": "OWNER/REPO", "schema": "agent-work-accountability/status-v1",
    "transport": "github-rest-search"}
   ```

   - `managed: false` and no tracker setup was requested: the session MUST stop this workflow and continue
     its assigned work.
   - `awa_version` differs from the loaded skill's `version`: the session MUST read `SKILL.md` from disk
     before acting.
   - Without `--json` it prints `OWNER/REPO has N work-accountability managed issue(s).` and the version.

2. **Can this session write?** The session MUST run `awa doctor` before its first write and MUST NOT write
   tracker state unless it reports `ready` for the account in use:

   ```sh
   awa doctor                       # inside the repository's checkout: checks the repository owner
   awa doctor --owner OWNER [--user LOGIN] [--json]
   ```

   ```text
   awa doctor: ready
     skill 0.10.11 (70f0c14c7c9a, clean)
     GitHub LOGIN @ github.com
     Projects OWNER: project scope verified, access verified
     GraphQL 4979/5000 remaining
   ```

3. **Which installation is running?** The session SHOULD run `awa version` when a result disagrees with this
   skill, and MUST quote its output when reporting a suspected awa defect:

   ```sh
   awa version
   ```

   ```text
   awa 0.10.11
   revision 70f0c14c7c9a6c896048b0c827e527288dc4552b (clean)
   source /path/to/agent-work-accountability
   ```

## What the results mean

| Command | Exit | Output | Means | Required action |
|---|---|---|---|---|
| `awa status` | 0 | `managed: true` | The repository has managed issues. | The session MUST find the story's work key ([plan/issues](../plan/issues/SKILL.md)). |
| `awa status` | 0 | `managed: false` | No managed issues exist. | The session MUST stop the workflow unless tracker setup was requested. |
| `awa status` | 2 | usage message | A bad option, or no `--repo` outside a checkout. | The session MUST fix the arguments and rerun. |
| `awa doctor` | 0 | `awa doctor: ready` | Authentication, project scope, owner access, GraphQL budget and a clean installation all passed. | Writes MAY proceed. |
| `awa doctor` | 2 | `not ready` and `FAIL: …` lines | At least one check failed; each failure is printed with a `FIX:` line where one is known. | The session MUST act on or report the `FIX:` lines and MUST NOT write tracker state until `ready`. |
| `awa doctor` | 0 | `owner access not checked`, `Target: run inside a GitHub repository or pass --owner OWNER` | It ran outside a checkout without `--owner`; owner access was not verified. | The session MUST rerun with `--owner` before relying on it for writes. |
| any write | 75 | `temporary failure: GitHub write limit: …` | awa's hourly write budget would be exceeded; nothing was written. The message names the time to rerun. | The session MUST rerun the same command at or after that time and MUST NOT split the run to evade the limit. |
| any write | 75 | `temporary failure: POST users/OWNER/projectsV2/N/views failed: unexpected end of JSON input. GitHub or the network failed temporarily; rerun the same command, …` | A GitHub server error (5xx or an empty error body), a dropped connection, or a gh call that did not answer within 60 s; the message names the call. | The session MUST rerun the same command and MUST NOT report the run as a refusal. |
| any write | 2 | `reconciliation failed: …` | A refusal; the message names the item and the rule. | The session MUST open the leaf for that event. |

`awa doctor` failures and their fixes:

| `FAIL:` text | Cause | Required action |
|---|---|---|
| `token for LOGIN lacks the write-capable project scope` | The token can read but not write Projects. | The operator MUST run `gh auth refresh --hostname HOST --scopes project` (on a headless host, open the device URL in another browser). A session SHOULD ask the operator to run it rather than start an interactive login itself. |
| `cannot list Projects for OWNER` | The account cannot see the owner's Projects. | The owner MUST grant access; a session MUST NOT work around it. |
| `GraphQL budget is N/LIMIT; wait until reset …` | Fewer than 100 GraphQL points remain. | The session MUST wait for the reset time. |
| `installed skill-pack checkout has local changes` | The checkout `awa` runs from is dirty. | The session MUST run from a clean installation; local edits MUST live in a separate clone. |

`Projects OWNER: … access verified` proves only that the account can list the owner's Projects. The owner
MUST add the account as a collaborator on a user-owned Project before the account writes to it, and the
account MUST hold the repository permissions below to set milestones and blocked-by links. A session MUST NOT infer write access
from `ready` alone when the Project or repository belongs to someone else.

## Permissions that matter

- **Milestones.** Without push access, GitHub silently drops milestone changes on issues while reporting
  success. awa reads every assignment back and fails rather than trust the response.
- **Blocked-by links**: the account MUST hold at least triage permission on the repository.
- **Organization issue fields** affect every repository. The operator MUST explicitly authorize their
  creation or change after the session shows the exact schema.
- **Project visibility** changes are protected ([board/visibility](../board/visibility/SKILL.md)).

## Budgets

- **GraphQL**: about 5,000 points an hour per account. `awa doctor` and `--diagnose` report what remains.
- **Content writes**: GitHub allows about 500 content-creating writes an hour per account. Before writing,
  reconcile and `awa release close` estimate their writes and stop with exit 75, writing nothing, when the
  run plus the last hour's writes would pass awa's limit (450 by default; `WORK_ACCOUNTABILITY_HOURLY_WRITES`
  overrides it). A dry run's `notes` show the estimate, for example `write budget: applying this needs about
  24 writes; awa has made 0 as LOGIN in the last hour`. awa counts only its own writes, logged per account
  under `$XDG_STATE_HOME/agent-work-accountability/writes/`. A session SHOULD read that note before a large
  apply, and MUST NOT raise `WORK_ACCOUNTABILITY_HOURLY_WRITES` above GitHub's limit.
- A run that alone needs more than the limit proceeds with a warning; if GitHub stops it, the session MUST
  rerun the same command, which continues where it stopped.
- awa writes each issue once per run (managed block, labels and open/closed state together), except that a
  close or reopen that also changes the managed block is written as two requests, text first: GitHub skips
  a milestone's open/closed counter when one request changes both an issue's state and its body.
- awa spaces writes about a second apart. Large runs report progress on
  stderr (`awa: issues 40/170 (3m10s left)`); a session MUST NOT interrupt a run because it is slow.

Observed exit-75 refusal:

```text
temporary failure: GitHub write limit: this run needs about 1 write; awa has made 60 as LOGIN in the last
hour (GitHub allows about 500 an hour; awa stops at 10). Run it at 23:19 UTC or later, when enough of the last
hour's writes have aged out. Writes other tools made as this account are not counted. Nothing was written in
this run.
```

## Using a specific stored GitHub account

When a specific stored identity is required without changing the user's global `gh` account, the session
MUST pass `--user LOGIN` to awa commands, or wrap a raw `gh` call:

```sh
python3 "$SKILL_DIR/scripts/gh_account.py" --user LOGIN -- <gh arguments>
```

It exits 2 if the stored token does not resolve to that login. A session MUST verify the required scopes
before any mutation and MUST NOT switch the global `gh` account on the user's behalf.

## Dropping down a level

`--diagnose` prints the installation and GitHub readiness the other checks are built on. A session SHOULD
run it when `awa doctor` itself fails or its output contradicts observed behavior:

```sh
$RECONCILE --diagnose [--user LOGIN]
```

```json
{"gh": "/path/to/gh", "gh_version": "gh version 2.94.0", "git_dirty": false, "git_revision": "70f0c14…",
 "host": "github.com", "login": "LOGIN", "rate": {"limit": 5000, "remaining": 4979, "reset": 1792449627},
 "schema": "github-work-accountability/diagnose-v1", "skill_digest": "7530…", "skill_version": "0.10.11"}
```

The raw checks beneath it, which a session MAY run one by one to isolate a failure:

- token and login: `gh auth status --hostname HOST` (best-effort classic OAuth scope probe);
- owner Project access: `gh project list --owner OWNER --limit 1 --format json`;
- GraphQL budget: `gh api graphql -f query='{rateLimit{limit remaining resetAt}}'`;
- managed issues: `gh api -X GET search/issues -f q='repo:OWNER/REPO "work-accountability:key"' -f per_page=1 --jq .total_count`.
