#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="$(mktemp -d)"
trap 'python - "$TEST_ROOT" <<'"'"'PY'"'"'
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

"$ROOT/install.sh" --source "$ROOT" --targets all
"$ROOT/install.sh" --source "$ROOT" --targets all

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
"$ROOT/install.sh" --source "$ROOT" --targets "" --target-dir "$conflict_root" --replace
[[ -L "$conflict_root/github-work-accountability" ]] || {
  echo "replacement did not install a skill link" >&2
  exit 1
}
backup_count="$(find "$XDG_STATE_HOME/agent-work-accountability/backups" -name OLD -type f | wc -l | tr -d ' ')"
[[ "$backup_count" == "1" ]] || {
  echo "replacement did not preserve exactly one backup" >&2
  exit 1
}

echo "Installer test passed."
