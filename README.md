# Agent Work Accountability

A portable skill pack for turning planning documents into GitHub epics and stories, maintaining a trustworthy delivery board, and reconciling requirements with implementation and evidence.

The portable artifact is the complete [`skills/github-work-accountability`](skills/github-work-accountability) directory in the open `SKILL.md` format. Its workflow is independent of any product repository, agent client, communication system, ADR manager, or GitHub owner type. It does not require client APIs, hooks, memory, or orchestration services.

ADRs are read directly from repository Git history. The pack supports byte-zero YAML frontmatter and declared Markdown metadata without imposing a global lifecycle. An ADR write from any tool triggers the same interlock: validate with repository-native rules, compare the exact source blob, and reconcile linked GitHub work before reporting the tracker as current.

## Install

The one-line installer places that same artifact in the standard discovery directories for Codex, Claude Code, OpenCode, and compatible Agent Skills clients:

```sh
curl -fsSL https://raw.githubusercontent.com/robertelee78/agent-work-accountability/main/install.sh | bash
```

Restart any running agent sessions so they refresh their skill catalogs. Re-run the same command to update the managed checkout and refresh the links.

The named presets are path adapters only. They do not install different instructions or behavior for different clients. Select presets or install into any skills directory:

```sh
./install.sh --presets codex,claude
./install.sh --presets opencode
./install.sh --presets none --target-dir /path/to/any/skills-directory
```

The default installation links each skill into:

- `~/.agents/skills`
- `${CODEX_HOME:-~/.codex}/skills`
- `${CLAUDE_CONFIG_DIR:-~/.claude}/skills`
- `${XDG_CONFIG_HOME:-~/.config}/opencode/skills`

All links point to one managed checkout, so installed clients cannot drift onto different copies. Use `--copy` when a target environment cannot follow symlinks. `--targets` remains as a compatibility alias for `--presets`.

An Agent Skills installer is not required. A client can load or copy `skills/github-work-accountability/` directly. The optional `agents/openai.yaml` file adds interface metadata for clients that understand it; the skill does not depend on that file.

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

## Included skills

- `github-work-accountability` — extract ADRs, PRDs, proposals, and design documents into source-bound GitHub work; maintain phase, health, priority, evidence, acceptance, release, and drift.

## Develop and verify

```sh
./tests/run.sh
```

The tests validate the portable skill structure, exercise installation in arbitrary and known client directories, prove exact Git source binding and dependency validation, inspect multiple repository-native ADR formats, and confirm committed and working-tree drift detection.

## Project layout

```text
skills/       Portable Agent Skills
scripts/      Pack validation utilities
tests/        Client-independent behavior tests
install.sh    Portable installer with optional client path presets
```
