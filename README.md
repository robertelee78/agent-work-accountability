# Agent Work Accountability

A portable skill pack for turning planning documents into GitHub epics and stories, maintaining a trustworthy delivery board, and reconciling requirements with implementation and evidence.

The pack uses the open `SKILL.md` directory format. Its workflow is independent of any product repository, agent harness, communication system, or GitHub owner type.

## Install

Install for Codex, Claude Code, OpenCode, and compatible Agent Skills clients:

```sh
curl -fsSL https://raw.githubusercontent.com/robertelee78/agent-work-accountability/main/install.sh | bash
```

Restart any running agent sessions so they refresh their skill catalogs. Re-run the same command to update the managed checkout and refresh the links.

To install only selected harness targets:

```sh
./install.sh --targets codex,claude
./install.sh --targets opencode
./install.sh --target-dir /path/to/another/skills-directory
```

The default installation links each skill into:

- `~/.agents/skills`
- `${CODEX_HOME:-~/.codex}/skills`
- `${CLAUDE_CONFIG_DIR:-~/.claude}/skills`
- `${XDG_CONFIG_HOME:-~/.config}/opencode/skills`

All links point to one managed checkout, so the harnesses cannot drift onto different copies. Use `--copy` when a target environment cannot follow symlinks.

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

The tests validate skill structure, exercise installation in isolated harness directories, prove exact git source binding and dependency validation, and confirm changed-source detection.

## Project layout

```text
skills/       Portable Agent Skills
scripts/      Pack validation utilities
tests/        Harness-independent behavior tests
install.sh    Multi-harness installer and updater
```
