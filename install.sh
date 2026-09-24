#!/usr/bin/env bash
set -euo pipefail

main() {
PACK_REPO_URL="${WORK_ACCOUNTABILITY_REPO_URL:-https://github.com/robertelee78/agent-work-accountability.git}"
PACK_REF="${WORK_ACCOUNTABILITY_REF:-main}"
MANAGED_ROOT="${WORK_ACCOUNTABILITY_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/agent-work-accountability}"
BACKUP_HOME="${WORK_ACCOUNTABILITY_BACKUP_HOME:-${XDG_STATE_HOME:-$HOME/.local/state}/agent-work-accountability/backups}"
PRESETS="all"
MODE="link"
REPLACE=0
SOURCE_ROOT=""
CUSTOM_TARGETS=()
BIN_DIR="${WORK_ACCOUNTABILITY_BIN_DIR:-$HOME/.local/bin}"
INSTALL_CLI=1
INSTALL_GUIDANCE="${WORK_ACCOUNTABILITY_GUIDANCE:-1}"
GUIDANCE_FILES=()

[[ "$INSTALL_GUIDANCE" == "0" || "$INSTALL_GUIDANCE" == "1" ]] || {
  echo "WORK_ACCOUNTABILITY_GUIDANCE must be 0 or 1" >&2
  exit 2
}

usage() {
  cat <<'EOF'
Install Agent Work Accountability skills into one or more Agent Skills directories.

Usage: install.sh [options]

  --presets LIST       Comma-separated path presets: all, none, agents, codex, claude, opencode
  --targets LIST       Compatibility alias for --presets
  --target-dir PATH    Add any Agent Skills directory; may be repeated
  --source PATH        Install from an existing checkout instead of cloning
  --copy               Copy skill directories instead of linking them
  --replace            Back up and replace conflicting installed skills
  --bin-dir PATH       Install the awa command in PATH (default: ~/.local/bin)
  --no-cli             Do not install the awa command
  --no-guidance        Do not add the managed-work activation rule to client instructions
  --help               Show this help

After installation, run `awa update` to update the pack and refresh every client.
EOF
}

print_github_readiness() {
  cat <<'EOF'

GitHub Projects readiness (required before the skill can update live work):
  Check the active account and token scopes:
    gh auth status --active --hostname github.com
  Sign in if needed:
    gh auth login --hostname github.com --web --scopes project
  Switch accounts if the wrong one is active:
    gh auth switch --hostname github.com --user YOUR_GITHUB_LOGIN
  Add the required Projects scope to the active account:
    gh auth refresh --hostname github.com --scopes project
  Confirm that account can see the target owner's projects:
    gh project list --owner YOUR_GITHUB_LOGIN
The 'project' token scope is required. Organization projects also require access
granted by that organization. GH_TOKEN or GITHUB_TOKEN overrides the stored account.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --presets|--targets)
      [[ $# -ge 2 ]] || { echo "$1 requires a value" >&2; exit 2; }
      PRESETS="$2"
      shift 2
      ;;
    --target-dir)
      [[ $# -ge 2 ]] || { echo "--target-dir requires a value" >&2; exit 2; }
      CUSTOM_TARGETS+=("$2")
      shift 2
      ;;
    --source)
      [[ $# -ge 2 ]] || { echo "--source requires a value" >&2; exit 2; }
      SOURCE_ROOT="$2"
      shift 2
      ;;
    --copy)
      MODE="copy"
      shift
      ;;
    --replace)
      REPLACE=1
      shift
      ;;
    --bin-dir)
      [[ $# -ge 2 ]] || { echo "--bin-dir requires a value" >&2; exit 2; }
      BIN_DIR="$2"
      shift 2
      ;;
    --no-cli)
      INSTALL_CLI=0
      shift
      ;;
    --no-guidance)
      INSTALL_GUIDANCE=0
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$SOURCE_ROOT" ]]; then
  script_path="${BASH_SOURCE[0]:-}"
  SCRIPT_DIR=""
  if [[ -n "$script_path" && -f "$script_path" ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$script_path")" 2>/dev/null && pwd || true)"
  fi
  if [[ -n "$SCRIPT_DIR" && -d "$SCRIPT_DIR/skills" ]]; then
    SOURCE_ROOT="$SCRIPT_DIR"
  else
    command -v git >/dev/null 2>&1 || {
      echo "git is required for one-line installation" >&2
      exit 1
    }
    if [[ -d "$MANAGED_ROOT/.git" ]]; then
      if [[ -n "$(git -C "$MANAGED_ROOT" status --porcelain)" ]]; then
        echo "managed checkout has local changes: $MANAGED_ROOT" >&2
        echo "commit or remove them before updating" >&2
        exit 1
      fi
      git -C "$MANAGED_ROOT" fetch origin "$PACK_REF"
      git -C "$MANAGED_ROOT" checkout "$PACK_REF"
      git -C "$MANAGED_ROOT" merge --ff-only "origin/$PACK_REF"
    elif [[ -e "$MANAGED_ROOT" ]]; then
      echo "install location exists but is not a git checkout: $MANAGED_ROOT" >&2
      exit 1
    else
      mkdir -p "$(dirname "$MANAGED_ROOT")"
      git clone --depth 1 --branch "$PACK_REF" "$PACK_REPO_URL" "$MANAGED_ROOT"
    fi
    SOURCE_ROOT="$MANAGED_ROOT"
  fi
fi

SOURCE_ROOT="$(cd "$SOURCE_ROOT" && pwd)"
[[ -d "$SOURCE_ROOT/skills" ]] || {
  echo "no skills directory found under $SOURCE_ROOT" >&2
  exit 1
}
command -v python3 >/dev/null 2>&1 || {
  echo "Python 3 is required to install and identify this skill pack" >&2
  exit 1
}

SOURCE_REVISION="unversioned"
SOURCE_QUALIFICATION=""
if git -C "$SOURCE_ROOT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  SOURCE_REVISION="$(git -C "$SOURCE_ROOT" rev-parse HEAD)"
  if [[ -n "$(git -C "$SOURCE_ROOT" status --porcelain --untracked-files=all)" ]]; then
    SOURCE_QUALIFICATION=" (dirty)"
  else
    SOURCE_QUALIFICATION=" (clean)"
  fi
fi
HASH_ROOT="$SOURCE_ROOT/skills/github-work-accountability"
[[ -d "$HASH_ROOT" ]] || HASH_ROOT="$SOURCE_ROOT/skills"
SKILL_DIGEST="$(python3 - "$HASH_ROOT" <<'PY'
from pathlib import Path
import hashlib
import sys

root = Path(sys.argv[1]).resolve()
digest = hashlib.sha256()
for path in sorted(p for p in root.rglob("*") if p.is_file()):
    if "__pycache__" in path.parts or path.name.endswith((".pyc", ".pyo")):
        continue
    digest.update(str(path.relative_to(root)).encode())
    digest.update(b"\0")
    digest.update(path.read_bytes())
    digest.update(b"\0")
print(digest.hexdigest())
PY
)"
SKILL_VERSION="$(python3 - "$SOURCE_ROOT/skills/github-work-accountability/SKILL.md" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
if not path.exists():
    print("unknown")
else:
    match = re.search(r'^\s*version:\s*["\x27]?([^"\x27\s]+)', path.read_text(), re.MULTILINE)
    print(match.group(1) if match else "unknown")
PY
)"

TARGET_DIRS=()
add_preset() {
  case "$1" in
    agents) TARGET_DIRS+=("${AGENTS_SKILLS_DIR:-$HOME/.agents/skills}") ;;
    codex)
      TARGET_DIRS+=("${CODEX_HOME:-$HOME/.codex}/skills")
      GUIDANCE_FILES+=("${CODEX_HOME:-$HOME/.codex}/AGENTS.md")
      ;;
    claude)
      TARGET_DIRS+=("${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills")
      GUIDANCE_FILES+=("${CLAUDE_CONFIG_DIR:-$HOME/.claude}/CLAUDE.md")
      ;;
    opencode)
      TARGET_DIRS+=("${XDG_CONFIG_HOME:-$HOME/.config}/opencode/skills")
      GUIDANCE_FILES+=("${XDG_CONFIG_HOME:-$HOME/.config}/opencode/AGENTS.md")
      ;;
    all)
      add_preset agents
      add_preset codex
      add_preset claude
      add_preset opencode
      ;;
    none|"") ;;
    *) echo "unknown preset: $1" >&2; exit 2 ;;
  esac
}

copy_is_current() {
  local source="$1"
  local destination="$2"
  [[ -d "$destination" && ! -L "$destination" ]] || return 1
  diff -qr \
    -x '__pycache__' \
    -x '*.pyc' \
    -x '*.pyo' \
    -x '.DS_Store' \
    -x '.work-accountability-install.json' \
    "$source" "$destination" >/dev/null 2>&1
}

copy_skill() {
  local source="$1"
  local destination="$2"
  mkdir -p "$destination"
  tar -C "$source" \
    --exclude='./__pycache__' \
    --exclude='*/__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.DS_Store' \
    -cf - . | tar -C "$destination" -xf -
}

write_copy_receipt() {
  local destination="$1"
  python3 - "$destination/.work-accountability-install.json" \
    "$SOURCE_ROOT" "$SOURCE_REVISION" "$SOURCE_QUALIFICATION" "$SKILL_DIGEST" "$MODE" "$SKILL_VERSION" <<'PY'
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

target, source, revision, qualification, digest, mode, version = sys.argv[1:]
payload = {
    "schema": "github-work-accountability/install-v1",
    "installed_at": datetime.now(timezone.utc).isoformat(),
    "source": source,
    "revision": revision,
    "qualification": qualification.strip(),
    "skill_digest": digest,
    "mode": mode,
    "skill_version": version,
}
Path(target).write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
PY
}

install_activation_guidance() {
  local destination="$1"
  local backup="$2"
  python3 - "$destination" "$backup" <<'PY'
from pathlib import Path
import os
import shutil
import sys
import tempfile

destination = Path(sys.argv[1]).expanduser()
backup = Path(sys.argv[2]).expanduser()
begin = "<!-- BEGIN agent-work-accountability -->"
end = "<!-- END agent-work-accountability -->"
block = """<!-- BEGIN agent-work-accountability -->
## GitHub work accountability

For substantive planning, design, implementation, testing, review, release, or handoff work in a GitHub repository, load the installed `github-work-accountability` skill and run `awa status --json` once before editing. When it reports managed work, maintaining the matching issue and Project through the skill is part of execution and reconciliation is required before handoff or completion. Do not wait for a separate tracking request.
<!-- END agent-work-accountability -->"""

current = destination.read_text(encoding="utf-8") if destination.exists() else ""
start_count = current.count(begin)
end_count = current.count(end)
if start_count != end_count or start_count > 1:
    raise SystemExit(f"refusing malformed work-accountability guidance in {destination}")
if start_count == 1:
    start = current.index(begin)
    finish = current.index(end, start) + len(end)
    proposed = current[:start] + block + current[finish:]
else:
    separator = "" if not current else ("\n" if current.endswith("\n") else "\n\n")
    proposed = current + separator + block + "\n"

if proposed == current:
    print(f"current  {destination}")
    raise SystemExit(0)

destination.parent.mkdir(parents=True, exist_ok=True)
if destination.exists():
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(destination, backup)

target = destination.resolve() if destination.is_symlink() else destination
target.parent.mkdir(parents=True, exist_ok=True)
fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(proposed)
    if target.exists():
        os.chmod(temporary_name, target.stat().st_mode)
    os.replace(temporary_name, target)
except BaseException:
    try:
        os.unlink(temporary_name)
    except FileNotFoundError:
        pass
    raise
print(f"install  {destination}")
PY
}

validate_activation_guidance() {
  local destination="$1"
  python3 - "$destination" <<'PY'
from pathlib import Path
import sys

destination = Path(sys.argv[1]).expanduser()
if not destination.exists():
    raise SystemExit(0)
current = destination.read_text(encoding="utf-8")
begin = "<!-- BEGIN agent-work-accountability -->"
end = "<!-- END agent-work-accountability -->"
start_count = current.count(begin)
end_count = current.count(end)
if start_count != end_count or start_count > 1:
    raise SystemExit(f"refusing malformed work-accountability guidance in {destination}")
PY
}

IFS=',' read -r -a REQUESTED_PRESETS <<< "$PRESETS"
for preset in ${REQUESTED_PRESETS[@]+"${REQUESTED_PRESETS[@]}"}; do
  preset="${preset//[[:space:]]/}"
  add_preset "$preset"
done
for target in ${CUSTOM_TARGETS[@]+"${CUSTOM_TARGETS[@]}"}; do
  TARGET_DIRS+=("$target")
done

[[ ${#TARGET_DIRS[@]} -gt 0 ]] || {
  echo "no target directories selected" >&2
  exit 2
}

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
installed=0
current=0
CLI_SOURCE="$SOURCE_ROOT/bin/awa"
CLI_DESTINATION="$BIN_DIR/awa"

if [[ $INSTALL_CLI -eq 1 ]]; then
  [[ -x "$CLI_SOURCE" ]] || {
    echo "awa command is missing or not executable: $CLI_SOURCE" >&2
    exit 1
  }
  if [[ -L "$CLI_DESTINATION" && "$CLI_DESTINATION" -ef "$CLI_SOURCE" ]]; then
    :
  elif [[ ( -e "$CLI_DESTINATION" || -L "$CLI_DESTINATION" ) && $REPLACE -ne 1 ]]; then
    echo "conflict $CLI_DESTINATION" >&2
    echo "rerun with --replace to preserve it as a timestamped backup" >&2
    exit 1
  fi
fi

# Detect every conflict before changing any target. A failed install must not
# leave only the earlier targets updated.
for target_root in "${TARGET_DIRS[@]}"; do
  for skill_source in "$SOURCE_ROOT"/skills/*; do
    [[ -d "$skill_source" && -f "$skill_source/SKILL.md" ]] || continue
    skill_name="$(basename "$skill_source")"
    destination="$target_root/$skill_name"

    if [[ "$MODE" == "link" && -L "$destination" && "$destination" -ef "$skill_source" ]]; then
      continue
    fi
    if [[ "$MODE" == "copy" ]] && copy_is_current "$skill_source" "$destination"; then
      continue
    fi
    if [[ ( -e "$destination" || -L "$destination" ) && $REPLACE -ne 1 ]]; then
      echo "conflict $destination" >&2
      echo "rerun with --replace to preserve it as a timestamped backup" >&2
      exit 1
    fi
  done
done

if [[ $INSTALL_GUIDANCE -eq 1 ]]; then
  for guidance_file in ${GUIDANCE_FILES[@]+"${GUIDANCE_FILES[@]}"}; do
    validate_activation_guidance "$guidance_file"
  done
fi

if [[ $INSTALL_CLI -eq 1 ]]; then
  mkdir -p "$BIN_DIR"
  if [[ -L "$CLI_DESTINATION" && "$CLI_DESTINATION" -ef "$CLI_SOURCE" ]]; then
    echo "current  $CLI_DESTINATION"
  else
    if [[ -e "$CLI_DESTINATION" || -L "$CLI_DESTINATION" ]]; then
      cli_backup="$BACKUP_HOME/$timestamp/bin/awa"
      cli_backup_base="$cli_backup"
      cli_backup_number=1
      while [[ -e "$cli_backup" || -L "$cli_backup" ]]; do
        cli_backup="${cli_backup_base}.${cli_backup_number}"
        cli_backup_number=$((cli_backup_number + 1))
      done
      mkdir -p "$(dirname "$cli_backup")"
      mv "$CLI_DESTINATION" "$cli_backup"
      echo "backup   $cli_backup"
    fi
    ln -s "$CLI_SOURCE" "$CLI_DESTINATION"
    echo "install  $CLI_DESTINATION"
  fi
fi

for target_root in "${TARGET_DIRS[@]}"; do
  mkdir -p "$target_root"
  for skill_source in "$SOURCE_ROOT"/skills/*; do
    [[ -d "$skill_source" && -f "$skill_source/SKILL.md" ]] || continue
    skill_name="$(basename "$skill_source")"
    destination="$target_root/$skill_name"

    if [[ "$MODE" == "link" && -L "$destination" && "$destination" -ef "$skill_source" ]]; then
      echo "current  $destination"
      current=$((current + 1))
      continue
    fi
    if [[ "$MODE" == "copy" ]] && copy_is_current "$skill_source" "$destination"; then
      echo "current  $destination"
      current=$((current + 1))
      continue
    fi

    if [[ -e "$destination" || -L "$destination" ]]; then
      target_id="$(printf '%s' "$target_root" | tr '/ ' '__')"
      backup="$BACKUP_HOME/$timestamp/$target_id/$skill_name"
      backup_base="$backup"
      backup_number=1
      while [[ -e "$backup" || -L "$backup" ]]; do
        backup="${backup_base}.${backup_number}"
        backup_number=$((backup_number + 1))
      done
      mkdir -p "$(dirname "$backup")"
      mv "$destination" "$backup"
      echo "backup   $backup"
    fi

    if [[ "$MODE" == "copy" ]]; then
      copy_skill "$skill_source" "$destination"
      write_copy_receipt "$destination"
    else
      ln -s "$skill_source" "$destination"
    fi
    echo "install  $destination"
    installed=$((installed + 1))
  done
done

if [[ $INSTALL_GUIDANCE -eq 1 ]]; then
  guidance_index=0
  for guidance_file in ${GUIDANCE_FILES[@]+"${GUIDANCE_FILES[@]}"}; do
    guidance_index=$((guidance_index + 1))
    guidance_backup="$BACKUP_HOME/$timestamp/guidance/$guidance_index/$(basename "$guidance_file")"
    install_activation_guidance "$guidance_file" "$guidance_backup"
  done
fi

[[ $((installed + current)) -gt 0 ]] || {
  echo "no valid skills found under $SOURCE_ROOT/skills" >&2
  exit 1
}

echo "Installed $installed skill target(s); $current already current. Restart running agent sessions to refresh discovery."
echo "Source: $SOURCE_ROOT"
echo "Install mode: $MODE"
echo "Skill version: $SKILL_VERSION"
echo "Source revision: $SOURCE_REVISION$SOURCE_QUALIFICATION"
echo "Skill-tree digest: $SKILL_DIGEST"
if [[ $INSTALL_CLI -eq 1 ]]; then
  echo "Update command: $CLI_DESTINATION update"
fi
print_github_readiness
}

main "$@"
