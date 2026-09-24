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
  --help               Show this help

Run the installer again to update a managed checkout.
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

TARGET_DIRS=()
add_preset() {
  case "$1" in
    agents) TARGET_DIRS+=("${AGENTS_SKILLS_DIR:-$HOME/.agents/skills}") ;;
    codex) TARGET_DIRS+=("${CODEX_HOME:-$HOME/.codex}/skills") ;;
    claude) TARGET_DIRS+=("${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills") ;;
    opencode) TARGET_DIRS+=("${XDG_CONFIG_HOME:-$HOME/.config}/opencode/skills") ;;
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
    else
      ln -s "$skill_source" "$destination"
    fi
    echo "install  $destination"
    installed=$((installed + 1))
  done
done

[[ $((installed + current)) -gt 0 ]] || {
  echo "no valid skills found under $SOURCE_ROOT/skills" >&2
  exit 1
}

echo "Installed $installed skill target(s); $current already current. Restart running agent sessions to refresh discovery."
print_github_readiness
}

main "$@"
