"""
Claude Code hook: tells the running Clawd pet what Claude Code is doing.

Installed by tools/install_hooks.py as an async hook on the session, prompt,
tool, permission and stop events. It forwards only the event name, the tool
name and the session id (never tool inputs, which can hold file contents or
commands) over a Unix socket, and always exits 0 without printing anything,
so it can never block, slow or change what Claude Code does.

Stdlib only; run with `python3 -S` for a fast start.
"""

import json
import os
import socket
import sys
import tempfile


def socket_path():
    """Where the pet listens. Keep in sync with claude_pet.socket_path()."""
    if os.environ.get("CLAWD_PET_SOCKET"):
        return os.environ["CLAWD_PET_SOCKET"]
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    return os.path.join(base, "clawd-pet.sock")


def message(payload):
    msg = {
        "event": str(payload.get("hook_event_name", ""))[:40],
        "session": str(payload.get("session_id", ""))[:80],
    }
    if payload.get("tool_name"):
        msg["tool"] = str(payload["tool_name"])[:60]
    if payload.get("notification_type"):
        msg["kind"] = str(payload["notification_type"])[:40]
    return msg


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(0.3)
        s.connect(socket_path())
        s.sendall((json.dumps(message(payload)) + "\n").encode())
        s.close()
    except Exception:
        pass                        # no pet running, or anything else: stay silent


if __name__ == "__main__":
    main()
    sys.exit(0)
