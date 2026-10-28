# shellcheck shell=bash disable=SC2034
# The pinned coding agents and how to run them. Source after common.sh.

OPENCODE_PACKAGE="@opencode/cli"
OPENCODE_VERSION="2.1.1"
PI_PACKAGE="@earendil-works/pi-coding-agent"
PI_VERSION="1.3.0"

# The agents run_agent knows. Codex is not pinned here: it comes from
# brew install codex.
RUN_AGENTS=(opencode pi codex)

# run_agent AGENT DATA_DIR TIMEOUT PROMPT
# Runs AGENT (opencode, pi or codex) in the current folder on PROMPT. DATA_DIR
# holds OpenCode's private data, so runs neither wait on nor land in your real
# session database; Codex runs without saving a session and without your
# ~/.codex/config.toml. perl's alarm ends the run with status 142 after
# TIMEOUT seconds.
run_agent() {
  local agent="$1" data="$2" timeout="$3" prompt="$4"
  case "$agent" in
    opencode)
      XDG_DATA_HOME="$data/share" XDG_STATE_HOME="$data/state" \
        perl -e 'alarm shift; exec @ARGV' "$timeout" \
        "$PUNY_ROOT/bin/opencode-qwen" run --auto "$prompt" < /dev/null
      ;;
    pi)
      perl -e 'alarm shift; exec @ARGV' "$timeout" \
        "$PUNY_ROOT/bin/pi-qwen" -p --no-session "$prompt" < /dev/null
      ;;
    codex)
      perl -e 'alarm shift; exec @ARGV' "$timeout" \
        "$PUNY_ROOT/bin/codex-qwen" exec --skip-git-repo-check --ephemeral --ignore-user-config \
        --sandbox workspace-write "$prompt" < /dev/null
      ;;
    *) die "unknown agent '$agent'; expected ${RUN_AGENTS[*]}" ;;
  esac
}

# Exits unless OpenCode 2.0 or later is installed. The configs this project
# writes use the OpenCode 2 format (providers, package, settings, modelID),
# which OpenCode 1 does not read.
require_opencode_2() {
  require_command opencode "Install it with: $PUNY_ROOT/bin/setup opencode"
  local version
  version="$(installed_version opencode)"
  [[ "$version" =~ ^([0-9]+)\. ]] || die "cannot read the OpenCode version from '$version'"
  (( BASH_REMATCH[1] >= 2 )) \
    || die "OpenCode $version is too old; this project needs OpenCode 2.0 or later. Upgrade with: $PUNY_ROOT/bin/setup opencode"
}

# Prints the installed version of agent command $1, or nothing when it is not
# installed.
installed_version() {
  command -v "$1" >/dev/null 2>&1 || return 0
  "$1" --version | awk '{print $NF}' | sed 's/^v//'
}
