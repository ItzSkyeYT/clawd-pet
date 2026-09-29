#!/usr/bin/env bash
# Installs Clawd for you (no root, except to install PyQt6 if you say yes):
# a `clawd-pet` command, an entry in your app menu, and if you like: start at
# login, the Claude Code hooks, and on GNOME the small helper extension that
# tells him where the pointer and your windows are.
#
# Nothing is copied: he runs from this folder, so `git pull` updates him.
# Running it again is fine.
#
#   ./install.sh               install (asks before each optional part)
#   ./install.sh --yes         install, yes to everything
#   ./install.sh --uninstall   take it all out again (your settings stay)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA="${XDG_DATA_HOME:-$HOME/.local/share}/clawd-pet"
BIN="$HOME/.local/bin/clawd-pet"
UUID="clawd-pet@itzskyeyt.github.io"
EXTENSIONS="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions"
YES=0

say() { printf '%s\n' "$*"; }
ask() {                                   # ask "question" default(y/n)
    local answer
    if [ "$YES" = 1 ]; then return 0; fi
    if [ ! -t 0 ]; then [ "$2" = y ]; return; fi
    read -r -p "$1 [$( [ "$2" = y ] && echo Y/n || echo y/N )] " answer
    answer="${answer:-$2}"
    [[ "$answer" =~ ^[Yy] ]]
}

stop_running() {                          # a running Clawd, asked nicely to quit
    local sock="${XDG_RUNTIME_DIR:-/tmp}/clawd-pet.sock"
    [ -S "$sock" ] || return 0
    python3 - "$sock" <<'EOF' 2>/dev/null || true
import socket, sys
s = socket.socket(socket.AF_UNIX)
s.settimeout(1)
s.connect(sys.argv[1])
s.sendall(b'{"cmd": "quit"}\n')
EOF
}

uninstall() {
    stop_running
    local py=python3
    [ -x "$DATA/venv/bin/python" ] && py="$DATA/venv/bin/python"
    "$py" "$HERE/claude_pet.py" --uninstall 2>/dev/null || true
    if [ -d "$EXTENSIONS/$UUID" ]; then
        gnome-extensions disable "$UUID" 2>/dev/null || true
        rm -rf "${EXTENSIONS:?}/$UUID"
        say "Removed the GNOME extension."
    fi
    if ask "Remove the Claude Code hooks too?" y; then
        "$py" "$HERE/tools/install_hooks.py" --remove || true
    fi
    rm -f "$BIN"
    rm -rf "${DATA:?}/venv"
    say "Clawd is uninstalled. His settings are still in ~/.config/clawd-pet, and this folder is untouched."
}

# How to install PyQt6 with the system's package manager, or nothing.
pyqt_package_command() {
    local id like
    id="$(. /etc/os-release 2>/dev/null; echo "${ID:-}")"
    like="$(. /etc/os-release 2>/dev/null; echo "${ID_LIKE:-}")"
    case " $id $like " in
        *" arch "*)                 echo "sudo pacman -S --needed python-pyqt6" ;;
        *" debian "*|*" ubuntu "*)  echo "sudo apt install -y python3-pyqt6" ;;
        *" fedora "*|*" rhel "*)    echo "sudo dnf install -y python3-pyqt6" ;;
        *" suse "*|*" opensuse "*)  echo "sudo zypper install -y python3-PyQt6" ;;
        *" alpine "*)               echo "sudo apk add py3-pyqt6" ;;
        *" gentoo "*)               echo "sudo emerge --noreplace dev-python/pyqt6" ;;
        *) echo "" ;;
    esac
}

has_pyqt6() { "$1" -c 'import PyQt6.QtWidgets' >/dev/null 2>&1; }

find_python() {                           # prints the python to run him with
    if ! command -v python3 >/dev/null; then
        say "Clawd needs Python 3. Install it with your package manager, then run this again." >&2
        exit 1
    fi
    if has_pyqt6 python3; then command -v python3; return; fi
    if [ -x "$DATA/venv/bin/python" ] && has_pyqt6 "$DATA/venv/bin/python"; then
        echo "$DATA/venv/bin/python"; return
    fi
    local cmd
    cmd="$(pyqt_package_command)"
    if [ -n "$cmd" ] && ask "Clawd needs PyQt6. Install it with: $cmd ?" y >&2; then
        if $cmd >&2 && has_pyqt6 python3; then command -v python3; return; fi
    fi
    say "Setting up PyQt6 just for Clawd, in $DATA/venv (a few minutes)..." >&2
    if ! python3 -m venv "$DATA/venv" >&2; then
        say "Couldn't make a virtual environment. On Debian and Ubuntu: sudo apt install python3-venv" >&2
        exit 1
    fi
    "$DATA/venv/bin/pip" install --quiet --upgrade pip PyQt6 >&2
    echo "$DATA/venv/bin/python"
}

install() {
    local py
    py="$(find_python)"
    say "Using $py"

    if [ -n "${WAYLAND_DISPLAY:-}" ] && [ -z "${DISPLAY:-}" ]; then
        say "Heads up: on Wayland Clawd runs through XWayland, which doesn't seem to be running."
        say "Install it (xorg-xwayland on Arch, xwayland on Debian, Ubuntu and Fedora) and log in again."
    fi

    mkdir -p "$(dirname "$BIN")"
    printf '#!/bin/sh\nexec "%s" "%s" "$@"\n' "$py" "$HERE/claude_pet.py" > "$BIN"
    chmod +x "$BIN"
    say "Added the clawd-pet command ($BIN)."
    case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) say "  (~/.local/bin isn't on your PATH: the app menu entry works anyway)";; esac

    local auto=""
    if ask "Start Clawd when you log in?" y; then auto="--autostart"; fi
    "$py" "$HERE/claude_pet.py" --install $auto
    say "Added Clawd to your app menu${auto:+, and to start at login}."

    if command -v claude >/dev/null || [ -d "$HOME/.claude" ]; then
        if ask "Let him follow Claude Code (adds hooks to ~/.claude/settings.json)?" y; then
            "$py" "$HERE/tools/install_hooks.py"
        fi
    fi

    case "${XDG_CURRENT_DESKTOP:-}" in
        *GNOME*|*gnome*)
            if [ -n "${WAYLAND_DISPLAY:-}" ] && [ -d "$HERE/gnome/$UUID" ] &&
               ask "Install the GNOME Shell helper, so he can see the pointer and your windows?" y; then
                mkdir -p "$EXTENSIONS"
                rm -rf "${EXTENSIONS:?}/$UUID"
                cp -r "$HERE/gnome/$UUID" "$EXTENSIONS/$UUID"
                gnome-extensions enable "$UUID" 2>/dev/null || true
                say "Installed. GNOME only loads new extensions at login: log out and back in once."
            fi ;;
    esac

    if ask "Start him now?" y; then
        stop_running
        sleep 0.5
        nohup "$BIN" >/dev/null 2>&1 &
        say "Here he comes."
    fi
    say "Done. Run ./install.sh --uninstall to take him out again."
}

for arg in "$@"; do
    case "$arg" in
        --yes|-y) YES=1 ;;
        --uninstall) uninstall; exit 0 ;;
        -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) say "Unknown option: $arg (try --help)"; exit 1 ;;
    esac
done
install
