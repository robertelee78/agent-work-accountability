#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'python3 - "$TEST_ROOT" <<'"'"'PY'"'"'
from pathlib import Path
import shutil
import sys
path = Path(sys.argv[1])
if path.exists():
    shutil.rmtree(path)
PY' EXIT

export HOME="$TEST_ROOT/home"
export XDG_CONFIG_HOME="$TEST_ROOT/config"
export CODEX_HOME="$TEST_ROOT/codex"
export CLAUDE_CONFIG_DIR="$TEST_ROOT/claude"
export XDG_STATE_HOME="$TEST_ROOT/state"
unset AGENTS_SKILLS_DIR WORK_ACCOUNTABILITY_BACKUP_HOME WORK_ACCOUNTABILITY_REF

snapshot_repo() {
  local destination="$1"
  git clone "$ROOT" "$destination" >/dev/null 2>&1
  # The installer tracks main; name the snapshot's branch main whatever branch is checked out here.
  git -C "$destination" checkout -q -B main
  tar -C "$ROOT" --exclude='./.git' -cf - . | tar -C "$destination" -xf -
  git -C "$destination" config user.name Fixture
  git -C "$destination" config user.email fixture@example.invalid
  git -C "$destination" add -A
  if ! git -C "$destination" diff --cached --quiet; then
    git -C "$destination" commit -m 'fixture current worktree' >/dev/null
  fi
}

install_output="$TEST_ROOT/install.out"
"$ROOT/install.sh" --source "$ROOT" --presets all >"$install_output"
for expected_line in \
  "Source: $ROOT" \
  "Install mode: link" \
  "Skill version: 0.8.0" \
  "Update command:" \
  "Readiness check:" \
  "Source revision:" \
  "Skill-tree digest:"
do
  grep -Fq "$expected_line" "$install_output" || {
    echo "installer omitted GitHub readiness guidance: $expected_line" >&2
    exit 1
  }
done
if grep -Fq "GitHub Projects readiness" "$install_output"; then
  echo "installer repeated readiness instructions instead of delegating to awa doctor" >&2
  exit 1
fi
[[ -L "$HOME/.local/bin/awa" ]] || {
  echo "installer did not install the awa command" >&2
  exit 1
}
awa_version_output="$("$HOME/.local/bin/awa" version)"
grep -Fq "awa 0.8.0" <<< "$awa_version_output" || {
  echo "awa version did not report the installed skill version" >&2
  exit 1
}
if "$HOME/.local/bin/awa" update --check >"$TEST_ROOT/source-update.out" 2>&1; then
  echo "awa update accepted a source-development checkout" >&2
  exit 1
fi
grep -Fq "does not modify a source-development checkout" "$TEST_ROOT/source-update.out" || {
  echo "source-development update refusal was not actionable" >&2
  exit 1
}
grep -Fq "Use for substantive planning, design, implementation, testing, review, release, or handoff work" \
  "$CODEX_HOME/skills/github-work-accountability/SKILL.md" || {
  echo "installed skill does not trigger before managed-work detection" >&2
  exit 1
}
grep -Fq "Do not wait for the user to request a status update" \
  "$CODEX_HOME/skills/github-work-accountability/SKILL.md" || {
  echo "installed skill does not make tracker maintenance a standing responsibility" >&2
  exit 1
}
"$ROOT/install.sh" --source "$ROOT" --presets all
for guidance_file in \
  "$CODEX_HOME/AGENTS.md" \
  "$CLAUDE_CONFIG_DIR/CLAUDE.md" \
  "$XDG_CONFIG_HOME/opencode/AGENTS.md"
do
  [[ -f "$guidance_file" ]] || {
    echo "installer omitted client activation guidance: $guidance_file" >&2
    exit 1
  }
  [[ "$(grep -Fc '<!-- BEGIN agent-work-accountability -->' "$guidance_file")" == "1" ]] || {
    echo "client activation guidance is not idempotent: $guidance_file" >&2
    exit 1
  }
  # shellcheck disable=SC2016
  grep -Fq 'run `awa status --json` once before editing' "$guidance_file" || {
    echo "client activation guidance omitted deterministic detection: $guidance_file" >&2
    exit 1
  }
done

status_bin="$TEST_ROOT/status-bin"
mkdir -p "$status_bin"
cat >"$status_bin/gh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" >"$STUB_STATUS_LOG"
printf '%s\n' "${STUB_STATUS_COUNT:-2}"
SH
chmod +x "$status_bin/gh"
status_output="$(env PATH="$status_bin:$PATH" STUB_STATUS_LOG="$TEST_ROOT/status-gh.log" \
  "$HOME/.local/bin/awa" status --repo IOMachines/repo-to-cve --json)"
python3 - "$status_output" <<'PY'
import json
import sys

payload = json.loads(sys.argv[1])
assert payload == {
    "host": "github.com",
    "managed": True,
    "managed_issue_count": 2,
    "repository": "IOMachines/repo-to-cve",
    "schema": "agent-work-accountability/status-v1",
    "transport": "github-rest-search",
}
PY
grep -Fq 'api --hostname github.com -X GET search/issues' "$TEST_ROOT/status-gh.log" || {
  echo "awa status did not use the GitHub REST search endpoint" >&2
  exit 1
}
grep -Fq 'repo:IOMachines/repo-to-cve is:issue in:body "work-accountability:key"' \
  "$TEST_ROOT/status-gh.log" || {
  echo "awa status did not search for the managed work marker" >&2
  exit 1
}
if env PATH="$status_bin:$PATH" STUB_STATUS_LOG="$TEST_ROOT/status-empty.log" \
  STUB_STATUS_COUNT=0 "$HOME/.local/bin/awa" status \
  --repo IOMachines/repo-to-cve --quiet; then
  echo "awa status --quiet treated an unmanaged repository as managed" >&2
  exit 1
fi

no_guidance_root="$TEST_ROOT/no-guidance"
env HOME="$no_guidance_root/home" CODEX_HOME="$no_guidance_root/codex" \
  WORK_ACCOUNTABILITY_BIN_DIR="$no_guidance_root/bin" \
  "$ROOT/install.sh" --source "$ROOT" --presets codex --no-guidance >/dev/null
[[ ! -e "$no_guidance_root/codex/AGENTS.md" ]] || {
  echo "--no-guidance changed the client instruction file" >&2
  exit 1
}

malformed_root="$TEST_ROOT/malformed-guidance"
mkdir -p "$malformed_root/codex"
printf '%s\n' '<!-- BEGIN agent-work-accountability -->' >"$malformed_root/codex/AGENTS.md"
if env HOME="$malformed_root/home" CODEX_HOME="$malformed_root/codex" \
  WORK_ACCOUNTABILITY_BIN_DIR="$malformed_root/bin" \
  "$ROOT/install.sh" --source "$ROOT" --presets codex >/dev/null 2>"$malformed_root/error"; then
  echo "installer accepted malformed activation guidance" >&2
  exit 1
fi
grep -Fq 'refusing malformed work-accountability guidance' "$malformed_root/error" || {
  echo "malformed activation guidance failure was not actionable" >&2
  exit 1
}
[[ ! -e "$malformed_root/codex/skills/github-work-accountability" ]] || {
  echo "installer changed skill targets before rejecting malformed guidance" >&2
  exit 1
}
[[ ! -e "$malformed_root/bin/awa" ]] || {
  echo "installer changed the CLI before rejecting malformed guidance" >&2
  exit 1
}

# Stock macOS ships Bash 3.2. Empty arrays under `set -u` must not break a
# presets-only install that has no custom target directories.
bash3_root="$TEST_ROOT/bash3"
if [[ -x /bin/bash ]]; then
  env \
    HOME="$bash3_root/home" \
    XDG_CONFIG_HOME="$bash3_root/config" \
    CODEX_HOME="$bash3_root/codex" \
    CLAUDE_CONFIG_DIR="$bash3_root/claude" \
    XDG_STATE_HOME="$bash3_root/state" \
    /bin/bash "$ROOT/install.sh" --source "$ROOT" --presets all
  [[ -L "$bash3_root/config/opencode/skills/github-work-accountability" ]] || {
    echo "Bash 3.2-compatible install did not create the OpenCode skill link" >&2
    exit 1
  }
  if env HOME="$bash3_root/no-target-home" /bin/bash "$ROOT/install.sh" \
    --source "$ROOT" --presets none >"$bash3_root/no-target.out" 2>"$bash3_root/no-target.err"; then
    echo "Bash 3.2 installer accepted an invocation with no target directories" >&2
    exit 1
  fi
  grep -q "no target directories selected" "$bash3_root/no-target.err" || {
    echo "Bash 3.2 no-target error was not actionable" >&2
    exit 1
  }
fi

for destination in \
  "$HOME/.agents/skills/github-work-accountability" \
  "$CODEX_HOME/skills/github-work-accountability" \
  "$CLAUDE_CONFIG_DIR/skills/github-work-accountability" \
  "$XDG_CONFIG_HOME/opencode/skills/github-work-accountability"
do
  [[ -L "$destination" ]] || {
    echo "expected skill symlink: $destination" >&2
    exit 1
  }
  [[ -f "$destination/SKILL.md" ]] || {
    echo "skill link is unreadable: $destination" >&2
    exit 1
  }
done

conflict_root="$TEST_ROOT/conflict-skills"
mkdir -p "$conflict_root/github-work-accountability"
printf 'old copy\n' > "$conflict_root/github-work-accountability/OLD"
"$ROOT/install.sh" --source "$ROOT" --presets none --target-dir "$conflict_root" --replace
[[ -L "$conflict_root/github-work-accountability" ]] || {
  echo "replacement did not install a skill link" >&2
  exit 1
}
backup_count="$(find "$XDG_STATE_HOME/agent-work-accountability/backups" -name OLD -type f | wc -l | tr -d ' ')"
[[ "$backup_count" == "1" ]] || {
  echo "replacement did not preserve exactly one backup" >&2
  exit 1
}

atomic_first="$TEST_ROOT/atomic-first"
atomic_conflict="$TEST_ROOT/atomic-conflict"
mkdir -p "$atomic_conflict/github-work-accountability"
printf 'leave me alone\n' > "$atomic_conflict/github-work-accountability/OLD"
if "$ROOT/install.sh" --source "$ROOT" --presets none \
  --target-dir "$atomic_first" --target-dir "$atomic_conflict" >/dev/null 2>&1; then
  echo "installer unexpectedly accepted a conflicting destination" >&2
  exit 1
fi
[[ ! -e "$atomic_first/github-work-accountability" ]] || {
  echo "installer partially changed an earlier target before reporting a conflict" >&2
  exit 1
}
[[ -f "$atomic_conflict/github-work-accountability/OLD" ]] || {
  echo "installer changed a conflict without --replace" >&2
  exit 1
}

pipe_root="$TEST_ROOT/piped"
snapshot_repo "$pipe_root/source"
cat "$ROOT/install.sh" | env \
  WORK_ACCOUNTABILITY_REPO_URL="$pipe_root/source" \
  WORK_ACCOUNTABILITY_HOME="$pipe_root/managed" \
  WORK_ACCOUNTABILITY_BIN_DIR="$pipe_root/bin" \
  bash -s -- --presets none --target-dir "$pipe_root/skills"
[[ -L "$pipe_root/skills/github-work-accountability" ]] || {
  echo "piped installer did not create a skill link" >&2
  exit 1
}
[[ -f "$pipe_root/skills/github-work-accountability/SKILL.md" ]] || {
  echo "piped installer skill link is unreadable" >&2
  exit 1
}
[[ "$(readlink "$pipe_root/skills/github-work-accountability")" == "$pipe_root/managed/skills/github-work-accountability" ]] || {
  echo "piped installer did not link to its managed checkout" >&2
  exit 1
}

update_root="$TEST_ROOT/update"
snapshot_repo "$update_root/seed"
git clone --bare "$update_root/seed" "$update_root/upstream.git" >/dev/null 2>&1
cat "$ROOT/install.sh" | env \
  WORK_ACCOUNTABILITY_REPO_URL="$update_root/upstream.git" \
  WORK_ACCOUNTABILITY_HOME="$update_root/managed" \
  WORK_ACCOUNTABILITY_BIN_DIR="$update_root/bin" \
  bash -s -- --presets none --target-dir "$update_root/skills" >/dev/null
git clone "$update_root/upstream.git" "$update_root/producer" >/dev/null 2>&1
git -C "$update_root/producer" config user.name Fixture
git -C "$update_root/producer" config user.email fixture@example.invalid
printf 'update probe\n' > "$update_root/producer/skills/github-work-accountability/UPDATE_PROBE"
git -C "$update_root/producer" add skills/github-work-accountability/UPDATE_PROBE
git -C "$update_root/producer" commit -m 'fixture update' >/dev/null
git -C "$update_root/producer" push origin main >/dev/null 2>&1
check_output="$(env \
  HOME="$update_root/home" \
  XDG_CONFIG_HOME="$update_root/config" \
  CODEX_HOME="$update_root/codex" \
  CLAUDE_CONFIG_DIR="$update_root/claude" \
  XDG_STATE_HOME="$update_root/state" \
  "$update_root/bin/awa" update --check)"
grep -Fq "awa update available:" <<< "$check_output" || {
  echo "awa update --check did not report the fixture update" >&2
  exit 1
}
update_output="$(env \
  HOME="$update_root/home" \
  XDG_CONFIG_HOME="$update_root/config" \
  CODEX_HOME="$update_root/codex" \
  CLAUDE_CONFIG_DIR="$update_root/claude" \
  XDG_STATE_HOME="$update_root/state" \
  WORK_ACCOUNTABILITY_BIN_DIR="$update_root/bin" \
  "$update_root/bin/awa" update)"
grep -Fq "Updated awa" <<< "$update_output" || {
  echo "awa update did not report its concise update outcome" >&2
  exit 1
}
if grep -Eq "GitHub Projects readiness|current  .*/(skills|AGENTS.md|CLAUDE.md)" <<< "$update_output"; then
  echo "awa update leaked installer noise in default mode" >&2
  exit 1
fi
[[ -f "$update_root/managed/skills/github-work-accountability/UPDATE_PROBE" ]] || {
  echo "managed checkout did not fast-forward to the requested ref" >&2
  exit 1
}
[[ -f "$update_root/managed/.git/awa-install.json" ]] || {
  echo "managed install did not record update ownership" >&2
  exit 1
}
current_output="$(env \
  HOME="$update_root/home" \
  XDG_CONFIG_HOME="$update_root/config" \
  CODEX_HOME="$update_root/codex" \
  CLAUDE_CONFIG_DIR="$update_root/claude" \
  XDG_STATE_HOME="$update_root/state" \
  WORK_ACCOUNTABILITY_BIN_DIR="$update_root/bin" \
  "$update_root/bin/awa" update)"
[[ "$(printf '%s\n' "$current_output" | wc -l | tr -d ' ')" == "1" ]] || {
  echo "already-current awa update was not one line" >&2
  exit 1
}
grep -Fq "is current" <<< "$current_output" || {
  echo "already-current awa update omitted its outcome" >&2
  exit 1
}
doctor_bin="$update_root/doctor-bin"
mkdir -p "$doctor_bin"
cat >"$doctor_bin/gh" <<'SH'
#!/usr/bin/env bash
case "$*" in
  "--version")
    echo "gh version 2.80.0 (fixture)"
    ;;
  "auth token --hostname github.com --user Fixture")
    echo "fixture-token"
    ;;
  *"api user --jq .login"*)
    echo "Fixture"
    ;;
  *"api --hostname github.com"*" user")
    echo '{"login":"Fixture"}'
    ;;
  *"api --hostname github.com"*" rate_limit")
    echo '{"resources":{"graphql":{"limit":5000,"remaining":4999,"reset":1893456000,"used":1}}}'
    ;;
  *"auth status --hostname github.com")
    if [[ "${STUB_PROJECT_SCOPE:-1}" == "1" ]]; then
      echo "Logged in to github.com account Fixture"
      echo "Token scopes: 'project', 'repo'"
    else
      echo "Logged in to github.com account Fixture"
      echo "Token scopes: 'repo'"
    fi
    ;;
  *"auth status --active"*)
    echo "unknown flag: --active" >&2
    exit 2
    ;;
  *"project list --owner Fixture --limit 1 --format json")
    if [[ "${STUB_PROJECT_SCOPE:-1}" == "1" ]]; then
      echo '{"projects":[],"totalCount":0}'
    else
      echo 'error: your authentication token is missing required scopes [read:project]' >&2
      echo 'To request it, run: gh auth refresh -s read:project' >&2
      exit 1
    fi
    ;;
  *)
    echo "unexpected fixture gh invocation: $*" >&2
    exit 2
    ;;
esac
SH
chmod +x "$doctor_bin/gh"
doctor_output="$(env \
  PATH="$doctor_bin:$PATH" \
  HOME="$update_root/home" \
  XDG_CONFIG_HOME="$update_root/config" \
  CODEX_HOME="$update_root/codex" \
  CLAUDE_CONFIG_DIR="$update_root/claude" \
  XDG_STATE_HOME="$update_root/state" \
  WORK_ACCOUNTABILITY_BIN_DIR="$update_root/bin" \
  "$update_root/bin/awa" doctor --user Fixture --owner Fixture)"
grep -Fq "awa doctor: ready" <<< "$doctor_output" || {
  echo "awa doctor did not report concise readiness" >&2
  exit 1
}
grep -Fq "Projects Fixture: project scope verified, access verified" <<< "$doctor_output" || {
  echo "awa doctor did not verify Projects readiness" >&2
  exit 1
}
doctor_default_output="$(cd "$update_root/home" && env \
  PATH="$doctor_bin:$PATH" \
  HOME="$update_root/home" \
  XDG_CONFIG_HOME="$update_root/config" \
  CODEX_HOME="$update_root/codex" \
  CLAUDE_CONFIG_DIR="$update_root/claude" \
  XDG_STATE_HOME="$update_root/state" \
  "$update_root/bin/awa" doctor)"
grep -Fq "Projects: project scope verified; owner access not checked" <<< "$doctor_default_output" || {
  echo "awa doctor without a repository silently selected a Project owner" >&2
  exit 1
}
grep -Fq "Target: run inside a GitHub repository or pass --owner OWNER" <<< "$doctor_default_output" || {
  echo "awa doctor without a repository did not explain its target scope" >&2
  exit 1
}
if doctor_default_failure="$(cd "$update_root/home" && env \
  PATH="$doctor_bin:$PATH" \
  HOME="$update_root/home" \
  XDG_CONFIG_HOME="$update_root/config" \
  CODEX_HOME="$update_root/codex" \
  CLAUDE_CONFIG_DIR="$update_root/claude" \
  XDG_STATE_HOME="$update_root/state" \
  STUB_PROJECT_SCOPE=0 \
  "$update_root/bin/awa" doctor 2>&1)"; then
  echo "awa doctor accepted an active token without Projects scope" >&2
  exit 1
fi
grep -Fq "FIX: gh auth refresh --hostname github.com --scopes project" \
  <<< "$doctor_default_failure" || {
  echo "awa doctor without --user did not give the direct scope repair" >&2
  exit 1
}
grep -Fq "https://github.com/login/device" <<< "$doctor_default_failure" || {
  echo "awa doctor omitted headless device-flow guidance" >&2
  exit 1
}
if grep -Fq "gh auth switch" <<< "$doctor_default_failure"; then
  echo "awa doctor without --user suggested a redundant account switch" >&2
  exit 1
fi
if doctor_failure="$(env \
  PATH="$doctor_bin:$PATH" \
  HOME="$update_root/home" \
  XDG_CONFIG_HOME="$update_root/config" \
  CODEX_HOME="$update_root/codex" \
  CLAUDE_CONFIG_DIR="$update_root/claude" \
  XDG_STATE_HOME="$update_root/state" \
  STUB_PROJECT_SCOPE=0 \
  "$update_root/bin/awa" doctor --user Fixture --owner Fixture 2>&1)"; then
  echo "awa doctor accepted a token without Projects scope" >&2
  exit 1
fi
grep -Fq "FIX: gh auth switch --hostname github.com --user Fixture" <<< "$doctor_failure" || {
  echo "awa doctor did not name the account to refresh" >&2
  exit 1
}
grep -Fq "FIX: gh auth refresh --hostname github.com --scopes project" <<< "$doctor_failure" || {
  echo "awa doctor did not request write-capable Projects scope" >&2
  exit 1
}
if grep -Fq -- "--active" <<< "$doctor_failure"; then
  echo "awa doctor used the version-dependent gh auth status --active flag" >&2
  exit 1
fi
recorded_remote="$(git -C "$update_root/managed" remote get-url origin)"
git -C "$update_root/managed" remote set-url origin "$update_root/other.git"
if env \
  HOME="$update_root/home" \
  XDG_CONFIG_HOME="$update_root/config" \
  CODEX_HOME="$update_root/codex" \
  CLAUDE_CONFIG_DIR="$update_root/claude" \
  XDG_STATE_HOME="$update_root/state" \
  WORK_ACCOUNTABILITY_BIN_DIR="$update_root/bin" \
  "$update_root/bin/awa" update --check >"$update_root/remote.err" 2>&1; then
  echo "awa update accepted a changed managed remote" >&2
  exit 1
fi
grep -Fq "remote URL changed" "$update_root/remote.err" || {
  echo "changed managed remote refusal was not actionable" >&2
  exit 1
}
git -C "$update_root/managed" remote set-url origin "$recorded_remote"
printf 'local change\n' >> "$update_root/managed/README.md"
if env \
  HOME="$update_root/home" \
  XDG_CONFIG_HOME="$update_root/config" \
  CODEX_HOME="$update_root/codex" \
  CLAUDE_CONFIG_DIR="$update_root/claude" \
  XDG_STATE_HOME="$update_root/state" \
  WORK_ACCOUNTABILITY_BIN_DIR="$update_root/bin" \
  "$update_root/bin/awa" update >/dev/null 2>&1; then
  echo "awa update changed a managed checkout with local changes" >&2
  exit 1
fi

portable_root="$TEST_ROOT/arbitrary-client/skills"
"$ROOT/install.sh" --source "$ROOT" --presets none --target-dir "$portable_root" --copy
"$ROOT/install.sh" --source "$ROOT" --presets none --target-dir "$portable_root" --copy
[[ -f "$portable_root/github-work-accountability/SKILL.md" ]] || {
  echo "portable copy is missing SKILL.md" >&2
  exit 1
}
receipt="$portable_root/github-work-accountability/.work-accountability-install.json"
[[ -f "$receipt" ]] || {
  echo "portable copy is missing its install revision receipt" >&2
  exit 1
}
python3 - "$receipt" "$ROOT" <<'PY'
import json
from pathlib import Path
import sys

receipt = json.loads(Path(sys.argv[1]).read_text())
assert receipt["schema"] == "github-work-accountability/install-v1"
assert receipt["source"] == sys.argv[2]
assert receipt["mode"] == "copy"
assert receipt["skill_version"] == "0.8.0"
assert len(receipt["skill_digest"]) == 64
PY
if find "$portable_root/github-work-accountability" -name '__pycache__' -o -name '*.pyc' -o -name '*.pyo' | grep -q .; then
  echo "portable copy contains generated Python cache files" >&2
  exit 1
fi
python3 "$portable_root/github-work-accountability/scripts/validate_extraction.py" --help >/dev/null

mode_root="$TEST_ROOT/mode-change"
"$ROOT/install.sh" --source "$ROOT" --presets none --target-dir "$mode_root"
if "$ROOT/install.sh" --source "$ROOT" --presets none --target-dir "$mode_root" --copy \
  >/dev/null 2>&1; then
  echo "copy mode silently accepted an existing link-mode install" >&2
  exit 1
fi
[[ -L "$mode_root/github-work-accountability" ]] || {
  echo "mode mismatch changed the existing install without --replace" >&2
  exit 1
}
"$ROOT/install.sh" --source "$ROOT" --presets none --target-dir "$mode_root" --copy --replace
[[ -d "$mode_root/github-work-accountability" && ! -L "$mode_root/github-work-accountability" ]] || {
  echo "--replace did not convert a link-mode install to copy mode" >&2
  exit 1
}

space_root="$TEST_ROOT/space target"
"$ROOT/install.sh" --source "$ROOT" --presets none --target-dir "$space_root"
[[ -L "$space_root/github-work-accountability" ]] || {
  echo "installer did not support a target path containing spaces" >&2
  exit 1
}

whitespace_root="$TEST_ROOT/whitespace"
env \
  HOME="$whitespace_root/home" \
  CODEX_HOME="$whitespace_root/codex" \
  CLAUDE_CONFIG_DIR="$whitespace_root/claude" \
  "$ROOT/install.sh" --source "$ROOT" --presets "codex, claude"
[[ -L "$whitespace_root/codex/skills/github-work-accountability" ]] || {
  echo "installer did not trim preset whitespace" >&2
  exit 1
}
[[ -L "$whitespace_root/claude/skills/github-work-accountability" ]] || {
  echo "installer did not install the whitespace-trimmed Claude preset" >&2
  exit 1
}

echo "Installer test passed."
