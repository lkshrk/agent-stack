#!/usr/bin/env bash
# Health-check skhd + yabai: install, launchd service, responsiveness,
# Accessibility permission, config, and (yabai) scripting-addition prereqs.
# Read-only — it never changes anything; it prints findings + a fix path.
set -u

UID_NUM="$(id -u)"
YABAI_LABEL="com.koekeishiya.yabai"
SKHD_LABEL="com.koekeishiya.skhd"
FIXES=()

c_ok="✓"; c_bad="✗"; c_warn="?"

note_fix() { FIXES+=("$1"); }

have() { command -v "$1" >/dev/null 2>&1; }

# launchctl list row: "<PID>\t<LAST_EXIT>\t<LABEL>"; PID '-' means not running.
svc_pid()  { launchctl list 2>/dev/null | awk -v l="$1" '$3==l{print $1}'; }
svc_exit() { launchctl list 2>/dev/null | awk -v l="$1" '$3==l{print $2}'; }

first_existing() { for f in "$@"; do [ -f "$f" ] && { echo "$f"; return; }; done; }

section() { printf '\n=== %s ===\n' "$1"; }

check_service() {
  local name="$1" label="$2" bin="$3"; shift 3
  local configs=("$@")
  section "$name"

  if ! have "$bin"; then
    printf '%s not installed (no %s on PATH)\n' "$c_bad" "$bin"
    note_fix "$name: install — brew install koekeishiya/formulae/$bin"
    return
  fi
  local ver; ver="$("$bin" --version 2>&1 | head -1)"
  printf '%s installed: %s\n' "$c_ok" "${ver:-version unknown}"

  local pid exit_code
  pid="$(svc_pid "$label")"; exit_code="$(svc_exit "$label")"
  if [ -z "$pid" ]; then
    if pgrep -x "$bin" >/dev/null 2>&1; then
      printf '%s running but NOT managed by launchd (started manually)\n' "$c_warn"
      note_fix "$name: running unmanaged — it won't auto-start at login. To manage it: $bin --start-service (stop the manual instance first)"
    else
      printf '%s launchd service not registered\n' "$c_bad"
      note_fix "$name: register + start service — $bin --start-service"
    fi
  elif [ "$pid" = "-" ]; then
    printf '%s service registered but not running (last exit %s)\n' "$c_bad" "${exit_code:-?}"
    note_fix "$name: start service — $bin --restart-service"
    [ "${exit_code:-0}" != "0" ] && note_fix "$name: nonzero exit usually = missing Accessibility permission (see below)"
  else
    printf '%s service running (pid %s, last exit %s)\n' "$c_ok" "$pid" "${exit_code:-0}"
  fi

  local cfg; cfg="$(first_existing "${configs[@]}")"
  if [ -n "$cfg" ]; then
    printf '%s config: %s\n' "$c_ok" "$cfg"
  else
    printf '%s no config found (looked: %s)\n' "$c_warn" "${configs[*]}"
    note_fix "$name: create a config at ${configs[0]}"
  fi
}

check_yabai_ax() {
  # If yabai answers a query, it is both running and has Accessibility.
  if yabai -m query --displays >/dev/null 2>&1; then
    printf '%s yabai Accessibility: granted (query responded)\n' "$c_ok"
  else
    printf '%s yabai Accessibility: NOT responding to queries\n' "$c_bad"
    note_fix "yabai: grant Accessibility — System Settings ▸ Privacy & Security ▸ Accessibility ▸ enable yabai, then: yabai --restart-service"
  fi
}

check_skhd_ax() {
  # skhd has no query interface; infer from its err log.
  local log="/tmp/skhd_$(id -un).err.log"
  if [ -f "$log" ] && tail -50 "$log" 2>/dev/null | grep -qi 'accessibilit\|authoriz'; then
    printf '%s skhd Accessibility: log shows permission errors\n' "$c_bad"
    printf '    %s\n' "$(tail -50 "$log" | grep -i 'accessibilit\|authoriz' | tail -1)"
    note_fix "skhd: grant Accessibility — System Settings ▸ Privacy & Security ▸ Accessibility ▸ enable skhd, then: skhd --restart-service"
  elif [ -n "$(svc_pid "$SKHD_LABEL")" ] && [ "$(svc_pid "$SKHD_LABEL")" != "-" ]; then
    printf '%s skhd Accessibility: likely granted (running, no recent errors)\n' "$c_ok"
  else
    printf '%s skhd Accessibility: unknown (service not running)\n' "$c_warn"
  fi
}

check_yabai_sa() {
  # Scripting addition is optional; only relevant if the user wants its features.
  section "yabai scripting addition (optional)"
  local sip; sip="$(csrutil status 2>/dev/null | head -1 | sed 's/.*status: //')"
  printf 'SIP: %s\n' "${sip:-unknown}"
  case "$sip" in
    enabled.*|enabled)
      printf '%s scripting addition needs SIP partially disabled; it is fully enabled\n' "$c_warn"
      note_fix "yabai SA (optional): only if you want SA features — partially disable SIP per https://github.com/koekeishiya/yabai/wiki/Disabling-System-Integrity-Protection" ;;
  esac
  if sudo -n test -f /private/etc/sudoers.d/yabai 2>/dev/null; then
    printf '%s sudoers entry present (/private/etc/sudoers.d/yabai)\n' "$c_ok"
  else
    printf '%s sudoers entry for passwordless --load-sa not verified\n' "$c_warn"
    note_fix "yabai SA (optional): add sudoers entry so the service can load-sa without a password — yabai --check-sa and see the yabai wiki 'Loading the scripting addition on startup'"
  fi
}

echo "skhd + yabai doctor — $(sw_vers -productName 2>/dev/null) $(sw_vers -productVersion 2>/dev/null)"

check_service "yabai" "$YABAI_LABEL" "yabai" \
  "$HOME/.config/yabai/yabairc" "$HOME/.yabairc"
have yabai && check_yabai_ax

check_service "skhd" "$SKHD_LABEL" "skhd" \
  "$HOME/.config/skhd/skhdrc" "$HOME/.skhdrc"
have skhd && check_skhd_ax

have yabai && check_yabai_sa

section "Proposed fix path"
if [ "${#FIXES[@]}" -eq 0 ]; then
  echo "$c_ok everything looks healthy — no action needed."
else
  i=1
  for f in "${FIXES[@]}"; do printf '%d. %s\n' "$i" "$f"; i=$((i+1)); done
  echo
  echo "After granting any Accessibility permission, restart the affected service"
  echo "(yabai --restart-service / skhd --restart-service). macOS will not apply a"
  echo "newly-granted permission to an already-running process."
fi
