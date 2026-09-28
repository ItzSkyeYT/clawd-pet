"""
Install (or remove) the Claude Code hooks that let Clawd follow what Claude
Code is doing: typing while it works, calling you over when it needs a
permission, celebrating when it finishes.

Adds async hooks to ~/.claude/settings.json that run clawd_hook.py. Async
hooks run in the background, so they never slow Claude Code down; the hook
only forwards event names (never tool inputs) and always exits 0. Everything
else in settings.json is left exactly as it was, and the first run keeps a
backup at settings.json.clawd-backup.

Usage:  python tools/install_hooks.py            # install or update
        python tools/install_hooks.py --remove   # take them out again
        python tools/install_hooks.py --dry-run  # print the hooks, change nothing
"""

import copy
import json
import os
import shlex
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOOK = os.path.join(ROOT, "clawd_hook.py")
SETTINGS = os.path.expanduser("~/.claude/settings.json")
MARK = "clawd_hook.py"             # how our entries are recognised

# event -> matcher ("*" = every tool; None = the event takes no matcher)
EVENTS = {
    "SessionStart": None,
    "SessionEnd": None,
    "UserPromptSubmit": None,
    "PreToolUse": "*",
    "PostToolUse": "*",
    "PostToolUseFailure": "*",
    "PermissionRequest": "*",
    "Notification": None,
    "SubagentStart": None,
    "SubagentStop": None,
    "PreCompact": None,
    "Stop": None,
    "StopFailure": None,
}


def command(hook=HOOK):
    return "python3 -S " + shlex.quote(hook)


def strip(settings):
    """Remove our hooks (and any groups or events left empty by that)."""
    settings = copy.deepcopy(settings)
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    for event in list(hooks):
        groups = []
        for group in hooks[event]:
            kept = [h for h in group.get("hooks", []) if MARK not in h.get("command", "")]
            if kept:
                groups.append({**group, "hooks": kept})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if not hooks:
        del settings["hooks"]
    return settings


def install(settings, hook=HOOK):
    """Our hooks added to (fresh copies of) whatever hooks are already there."""
    settings = strip(settings)
    hooks = settings.setdefault("hooks", {})
    for event, matcher in EVENTS.items():
        group = {"hooks": [{"type": "command", "command": command(hook), "async": True, "timeout": 5}]}
        if matcher is not None:
            group = {"matcher": matcher, **group}
        hooks.setdefault(event, []).append(group)
    return settings


def main():
    remove, dry = "--remove" in sys.argv, "--dry-run" in sys.argv
    current = {}
    if os.path.exists(SETTINGS):
        with open(SETTINGS) as f:
            current = json.load(f)          # refuse to touch a file we can't parse
    updated = strip(current) if remove else install(current)
    if dry:
        print(json.dumps(updated.get("hooks", {}), indent=2))
        return 0
    if updated == current:
        print("nothing to change")
        return 0
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    backup = SETTINGS + ".clawd-backup"
    if os.path.exists(SETTINGS) and not os.path.exists(backup):
        shutil.copy2(SETTINGS, backup)
    # write to a temp file and swap it in, so a crash can't leave half a file
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(SETTINGS), prefix=".settings.")
    with os.fdopen(fd, "w") as f:
        json.dump(updated, f, indent=2)
        f.write("\n")
    if os.path.exists(SETTINGS):
        shutil.copymode(SETTINGS, tmp)
    os.replace(tmp, SETTINGS)
    n = sum(len(g["hooks"]) for groups in updated.get("hooks", {}).values() for g in groups)
    print(("removed Clawd's hooks" if remove else f"installed Clawd's hooks for {len(EVENTS)} events")
          + f"; {SETTINGS} now has {n} hook(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
