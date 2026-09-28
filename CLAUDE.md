# Clawd Desktop Pet

Desktop pet of Clawd, the Claude Code mascot (PyQt6). Every animation is traced
pixel-for-pixel from Anthropic's official Clawd GIFs, so he moves the way he
does in the Claude apps. With the hooks installed he follows Claude Code: types
while it works, waves with a "!" when it needs a permission, celebrates when done.

## Run
```bash
python claude_pet.py
```
Needs PyQt6 (CachyOS/Arch: `sudo pacman -S python-pyqt6`, elsewhere `pip install PyQt6`).
On Wayland the app runs itself through XWayland, because a pet has to move its
own window and Wayland doesn't allow that. Programs it launches get the normal
environment back.

## Hooks (Claude Code integration)
```bash
python tools/install_hooks.py            # adds async hooks to ~/.claude/settings.json
python tools/install_hooks.py --remove   # takes them out again
```
`clawd_hook.py` forwards event, tool and session names (never tool inputs) over
`$XDG_RUNTIME_DIR/clawd-pet.sock`. The socket also takes `{"cmd": "status"}`,
`{"cmd": "icons"}` and `{"cmd": "play", "action": "climb"}` (handy for testing).

## Controls
- **Left-click**: the Code tab of the Claude app (`claude://code/continue?session=last`,
  or `claude://code/needs-input` while a session waits on you); a terminal if the app isn't there
- **Right-click**: menu (Claude Code, new session, terminal, claude.ai, play any animation, size, quit)
- **Drag**: pick him up; let go and he falls (throw him and he bounces)
- **Stroke him** with the pointer (back and forth): hearts, happy eyes, eventually a dance
- He watches a nearby pointer and glances at it now and then (on KDE Wayland a KWin
  script reports the pointer over D-Bus, since XWayland can't see it over other apps)
- He roams between monitors: walks off a taller screen and drops, climbs a ladder up or
  down, sometimes leaps; he visits desktop icons (rides his cloud up, stands on them,
  hangs off them)
- Tray icon: click to hide/show

## Layout
- `claude_pet.py`: the app (sprite loading, behaviours, physics, hooks socket, launcher)
- `clawd_hook.py`: the Claude Code hook (stdlib only, always exits 0 silently)
- `tools/install_hooks.py`: adds/removes the hooks in `~/.claude/settings.json`
- `sprites/clawd.json`: traced frames, palette, timings, loop points. Generated, don't hand-edit
- `tools/fetch_official.py`: downloads the official Clawd GIFs from claude.ai into `assets/official/`
- `tools/trace_official.py`: turns them into `sprites/clawd.json` and reports fidelity
- `tests/test_pet.py`: `python -m unittest discover -s tests -v` (offscreen, no window appears)
- `assets/`: reference media, git-ignored (Anthropic's art and personal screen recordings)

## Sprites
- All official Clawd art sits on one grid: the idle pose is 12x8 pixels and details
  (eyes, props, sparks) use half-pixels, so sprites are 24x16 cells
- Each animation stores `home`: where the idle pose sits inside it, so every animation lines up
- Blink / look / happy / sleep faces are derived from the idle pose in `pose_rows()`,
  copying how the official art draws them (the laptop wink, the dance's ^ ^ eyes)
- Climbing and hanging poses (`climb_rows`, `hang_rows`) are built from official parts:
  the idle body plus the arms Clawd-Jumping raises. The ladder, hearts, "!" bubble and
  Z's use the official palette
- Scale is screen pixels per cell (Small 3, Medium 4, Large 6, Huge 8), saved in QSettings

## Palette (traced, shared by all animations)
| Key | Color | Role |
|-----|-------|------|
| `#` | #d87756 | Body |
| `@` | #141413 | Eyes, ink |
| `*` | #bf694d | Shaded side (3/4 view) |
| `%` `=` | #c5d3e0, #6a9bcc | Cloud |
| `b` `e` | #eec875, #f2e3d7 | Sparks |
| `&` `a` `c` `d` `f` `+` `g` | greys, ivory, light salmon | Kart and helmet |

## Development Approach

Before implementing any changes, write a test script that validates the expected behavior. For example, if we're calling an API, first write a small script that hits the endpoint and asserts the response shape. Then implement the feature, running the test after each edit until it passes. If a test fails 3 times with the same approach, stop and propose an alternative strategy. Start by analyzing the current task and writing the test. Do this most of the time unless not very helpful — use judgment.
