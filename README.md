# Agent Work Accountability

A portable skill pack for turning planning documents into GitHub epics and stories, maintaining a trustworthy delivery board, and reconciling requirements with implementation and evidence.

The portable artifact is the complete [`skills/github-work-accountability`](skills/github-work-accountability) directory in the open `SKILL.md` format. Its workflow is independent of any product repository, agent client, communication system, ADR manager, or GitHub owner type. It does not require client APIs, hooks, memory, or orchestration services.

ADRs are read directly from repository Git history. The pack supports byte-zero YAML frontmatter and declared Markdown metadata without imposing a global lifecycle. An ADR write from any tool triggers the same interlock: validate with repository-native rules, compare the exact source blob, and reconcile linked GitHub work before reporting the tracker as current.

## Install

The installer requires Bash 3.2 or newer, Git, `tar`, and `diff`. The skill's deterministic inspection and validation helpers require Python 3. GitHub mutations require an authenticated `gh` CLI, but installation itself does not.

The one-line installer places that same artifact in the standard discovery directories for Codex, Claude Code, OpenCode, and compatible Agent Skills clients:

```sh
curl -fsSL https://raw.githubusercontent.com/robertelee78/agent-work-accountability/main/install.sh | bash
```

Restart any running agent sessions so they refresh their skill catalogs. Re-run the same command to update the managed checkout and refresh the links. A successful install prints the source path, install mode, exact source revision and dirty qualification, and the skill-tree digest. Copy installs also carry `.work-accountability-install.json` inside the installed skill.

The installer also adds `awa` to `${WORK_ACCOUNTABILITY_BIN_DIR:-~/.local/bin}`. Future updates use the same command style as Vox and CTM:

```sh
awa update --check
awa update
awa version
awa doctor --user YOUR_GITHUB_LOGIN
awa status --json
```

`awa update` refuses a dirty checkout or unexpected branch, fast-forwards the configured source branch, and refreshes all four global client links and their short activation guidance. Its default output is one status line plus a restart reminder only when something changed; use `awa update --verbose` for Git and installer details. `awa status` uses one read-only GitHub REST search to detect whether the current repository already contains managed work; it does not spend the GraphQL Projects budget. Use `install.sh --no-cli` when only the portable skill files should be installed, `--no-guidance` to skip client-wide activation guidance, or `--bin-dir PATH` to choose another command directory. Use `awa update --no-guidance` or set `WORK_ACCOUNTABILITY_GUIDANCE=0` to preserve that preference during updates.

## Check GitHub readiness

For the most useful check, run `awa doctor` from the repository whose work the agents will maintain:

```sh
cd /path/to/repository
awa doctor
```

With no options, `awa doctor` uses the active GitHub account. Inside a GitHub repository it derives the Project owner from `origin` and verifies access to that owner's Projects. Outside a repository it checks the installation, account, token scope, and API budget without silently assuming that the account itself is the intended Project owner; pass `--owner OWNER` when an owner-specific check is wanted.

Use `--user LOGIN` to inspect a specific stored GitHub account without permanently changing the active account, and `--json` for automation:

```sh
awa doctor --user robertlee-ioactive
awa doctor --user robertlee-ioactive --owner IOMachines
awa doctor --json
```

Creating and maintaining Projects requires the write-capable `project` OAuth scope. `read:project` is insufficient. When the active account lacks it, `awa doctor` prints this repair:

```sh
gh auth refresh --hostname github.com --scopes project
```

When `--user LOGIN` names an inactive stored account, activate that account before refreshing because `gh auth refresh` operates on the active account:

```sh
gh auth switch --hostname github.com --user LOGIN
gh auth refresh --hostname github.com --scopes project
```

On a headless Linux host, `gh` may print an `xdg-open` failure after displaying a one-time code. Open <https://github.com/login/device> in any browser, enter that code, wait for `Authentication complete`, and run `awa doctor` again. The browser does not need to run on the host being authenticated.

An exported `GH_TOKEN` or `GITHUB_TOKEN` takes precedence over the stored active account unless `--user` selects a stored identity explicitly.

The named presets are path adapters only. They do not install different instructions or behavior for different clients. Select presets or install into any skills directory:

```sh
./install.sh --presets codex,claude
./install.sh --presets opencode
./install.sh --presets none --target-dir /path/to/any/skills-directory
```

The default installation links each skill into:

- `${AGENTS_SKILLS_DIR:-~/.agents/skills}`
- `${CODEX_HOME:-~/.codex}/skills`
- `${CLAUDE_CONFIG_DIR:-~/.claude}/skills`
- `${XDG_CONFIG_HOME:-~/.config}/opencode/skills`

All links point to one managed checkout, so installed clients cannot drift onto different copies. Use `--copy` when a target environment cannot follow symlinks. Re-running an unchanged copy install is idempotent; after the source changes, pass `--replace` so the previous copy is preserved as a backup. `--targets` remains as a compatibility alias for `--presets`.

An Agent Skills installer is not required. A client can load or copy `skills/github-work-accountability/` directly. The optional `agents/openai.yaml` file adds interface metadata for clients that understand it; the skill does not depend on that file.

Verify the loaded path, revision, digest, GitHub actor, CLI version, and current GraphQL budget with:

```sh
python3 "${CODEX_HOME:-$HOME/.codex}/skills/github-work-accountability/scripts/reconcile_project.py" \
  --diagnose --user YOUR_GITHUB_LOGIN
```

A running Codex, Claude Code, or OpenCode session keeps the skill instructions it already loaded. Restart it after an update, then run `awa doctor --user YOUR_GITHUB_LOGIN` before live Project work.

## Update ownership and trust

The one-line installer clones a dedicated managed checkout under `${WORK_ACCOUNTABILITY_HOME:-~/.local/share/agent-work-accountability}` and records its exact Git remote and branch inside that clone's Git metadata. `awa update` operates only on this managed channel. It refuses source-development checkouts, a changed remote, a dirty managed checkout, a different branch, non-fast-forward history, and concurrent updates. Global skill and command links point to the managed checkout, so editing a separate development clone cannot break updates.

The managed channel fetches `main` from the recorded HTTPS GitHub repository. TLS, exact remote binding, and fast-forward-only history protect against transport tampering, local remote substitution, accidental downgrade, and local edits. This source channel does not yet carry an independent release signature, so compromise of the GitHub repository or owner account can still publish executable updater code. It is therefore weaker than CTM's pinned Ed25519 release channel; closing that gap requires signed immutable release bundles and a pinned verification key.

## Delivery model

Work moves left to right:

```text
Backlog → Designing → Ready → Executing → Acceptance → Release ready → Done
```

`Blocked` is a Health value, not a delivery phase. Priority, ownership, attempts, attempt outcomes, and planning-source freshness remain separate facts.

Executing requires a durable attempt-start event with a unique attempt ID, actor, start time, and exact work key. Branches, commits, pull requests, claims, and file changes are observations; none can independently place a story in Executing.

The GitHub storage backend is capability-based:

- organization issue fields when available and authorized;
- Project-local fields for user-owned repositories or repository-local configuration; or
- mutually exclusive repository labels when Projects are unavailable.

The meanings and transition gates remain the same in every profile.

For substantive GitHub repository work, agents first use the read-only managed-work check. In an opted-in repository, they treat accountability updates as part of executing managed work. They discover the matching stable work key at session start, record story-specific attempt, blocker, candidate, verdict, and delivery facts as those events occur, and reconcile before handoff or completion without waiting for a separate tracking prompt. Ambiguous work remains unchanged until it can be bound to one story. The installer adds a short, sentinel-delimited activation rule to the global instruction file for each selected client so this check does not depend only on probabilistic skill routing.

For writable GitHub Projects, the bundled desired-state reconciler creates or explicitly adopts one Project per epic or independently managed workstream, links every Project to its repository, requires the epic and all direct native child stories, provisions fields, and makes a story-only Lifecycle Kanban grouped by Work phase the primary view. It migrates managed issue blocks away from fallback labels only after those checks pass. See [`project-reconciliation.md`](skills/github-work-accountability/references/project-reconciliation.md) for the manifest and commands.

## Included skills

- `github-work-accountability` — extract ADRs, PRDs, proposals, and design documents into source-bound GitHub work; maintain phase, health, priority, evidence, acceptance, release, and drift.

## Develop and verify

```sh
./tests/run.sh
```

The tests validate the portable skill structure, exercise installation in arbitrary and known client directories, prove exact Git source binding and dependency validation, inspect multiple repository-native ADR formats, confirm committed and working-tree drift detection, and exercise Project identity, fields, membership, board construction, evidence gates, and batching with a credential-free fake transport.

## Project layout

```text
skills/       Portable Agent Skills
scripts/      Pack validation utilities
tests/        Client-independent behavior tests
install.sh    Portable installer with optional client path presets
```
