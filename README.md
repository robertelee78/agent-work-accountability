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

At the end of every successful install, the script prints the GitHub readiness commands. Before allowing an agent to update live work, confirm the intended account is active and grant the GitHub CLI's required `project` scope:

```sh
gh auth status --active --hostname github.com
gh auth switch --hostname github.com --user YOUR_GITHUB_LOGIN  # when needed
gh auth refresh --hostname github.com --scopes project
gh project list --owner YOUR_GITHUB_LOGIN
```

Use `gh auth login --hostname github.com --web --scopes project` when no account is signed in. Organization-owned projects also require access granted by that organization. An exported `GH_TOKEN` or `GITHUB_TOKEN` takes precedence over the stored active account.

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

A running Codex, Claude Code, or OpenCode session keeps the skill instructions it already loaded. Restart it after an update, then have it report the version and resolved path from `--diagnose` before live Project work.

## Delivery model

Work moves left to right:

```text
Backlog → Designing → Ready → Executing → Acceptance → Release ready → Done
```

`Blocked` is a Health value, not a delivery phase. Priority, ownership, attempts, attempt outcomes, and planning-source freshness remain separate facts.

The GitHub storage backend is capability-based:

- organization issue fields when available and authorized;
- Project-local fields for user-owned repositories or repository-local configuration; or
- mutually exclusive repository labels when Projects are unavailable.

The meanings and transition gates remain the same in every profile.

For writable GitHub Projects, the bundled desired-state reconciler creates or explicitly adopts one Project for the repository, links it to the repository, adds every managed issue, provisions fields, and creates a verified Lifecycle Kanban grouped by Work phase. See [`project-reconciliation.md`](skills/github-work-accountability/references/project-reconciliation.md) for the manifest and commands.

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
