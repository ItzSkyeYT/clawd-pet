# Clawd Desktop Pet

Desktop pet of Clawd, the Claude Code mascot (PyQt6). Every animation is traced
pixel-for-pixel from Anthropic's official Clawd GIFs, so he moves the way he
does in the Claude apps. With the hooks installed he follows Claude Code: reads,
searches, browses or types depending on the tool it's using, waves with a "!"
when it needs a permission, celebrates when done.

## Run
```bash
python claude_pet.py
```
Needs PyQt6 (CachyOS/Arch: `sudo pacman -S python-pyqt6`, elsewhere `pip install PyQt6`).
On Wayland the app runs itself through XWayland, because a pet has to move its
own window and Wayland doesn't allow that. Programs it launches get the normal
environment back.

- **Start at login**: tick "Start at login" in his menu (writes
  `~/.config/autostart/clawd-pet.desktop`, and a "Clawd" entry in the app launcher)
- **Restart**: "Restart Clawd" in his menu, or `{"cmd": "restart"}` on the socket; he
  re-execs himself in place (same process), so it also picks up code changes
- Only one Clawd runs at a time: a second copy sees the socket in use and exits

## Hooks (Claude Code integration)
```bash
python tools/install_hooks.py            # adds async hooks to ~/.claude/settings.json
python tools/install_hooks.py --remove   # takes them out again
```
`clawd_hook.py` forwards event, tool and session names (never tool inputs) over
`$XDG_RUNTIME_DIR/clawd-pet.sock`. The socket also takes `{"cmd": "status"}`,
`{"cmd": "icons"}` and `{"cmd": "play", "action": "climb"}` (handy for testing; any
name in `PLAYABLE`, including `yawn`, `coffee`, `remind_water`). The settings dialog
shows whether the hooks are installed and can install or remove them.
Anything you ask for (menu, click, socket play, petting) finishes before he goes back
to what Claude Code is doing.

## Controls
- **Left-click**: the Code tab of the Claude app (`claude://code/continue?session=last`,
  or `claude://code/needs-input` while a session waits on you); a terminal if the app isn't there
- **Right-click**: menu (Claude Code, new session, terminal, claude.ai, Play: every move,
  the desktop and time-of-day scenes, reminder previews and the Claude Code states;
  Hat, Size, Quiet mode, Settings…, start at login, restart, quit)
- **Drop a folder on him** (or a file, for its folder): a new Claude Code session there
  (`claude://code/new?folder=...`, or a terminal in it). He holds still with his arms up
  while you hover, and always takes it as a copy, never a move
- **Settings…**: how lively he is, size, which scenes he does on his own, pointer,
  petting, fullscreen ducking, time of day, holidays, hat, reminders and their
  intervals, Claude Code hooks, start at login. Changes apply at once (QSettings `prefs/`)
- **Quiet mode**: he stays put and keeps to himself (a nap at most, no reminders or
  celebrations); Claude Code working or needing you still shows
- **Drag**: pick him up; let go and he falls (throw him and he bounces)
- **Stroke him** with the pointer (back and forth): hearts, happy eyes, eventually a dance
- He watches a nearby pointer and glances at it now and then (on KDE Wayland a KWin
  script reports the pointer over D-Bus, since XWayland can't see it over other apps).
  Even while coding he looks up at a pointer that comes close (briefly, with a cooldown)
  and beams if you pet him, without stopping typing
- His own filler (walks, dances, visits, reading...) only happens when Claude Code isn't
  working and you're not playing with him; it gives way to Claude Code at once. After
  5 minutes with nothing happening he winds down (longer rests, more naps)
- He roams between monitors: walks off a taller screen and drops, climbs a ladder up or
  down, sometimes leaps; he checks out desktop icons: floats up beside one on his cloud,
  puzzles over it ("?", a double take "!"), leans in and pokes it, goes over it with a
  magnifying glass, makes up his mind (likes it / still puzzled / spooked), and sometimes
  hops onto it or hangs off it
- Desktop **folders**: he floats up beside one and reaches over its rim into the top (where
  folders open), rummages (paper scraps fly up out of it),
  pulls out a page, puts on reading glasses and reads it like a story, reacting as he
  goes (surprised, teary, laughing, in love, scared, confused), then puts it all back
- He walks on windows: hops up onto a window's top edge, rides it when you move the
  window, hops down again; ducks out of sight while something is fullscreen on his screen
- **Time and seasons** (from the clock): sleepier at night (yawns, longer naps in a
  nightcap), a stretch and a mug of coffee on the first morning visit, a Santa hat in
  December, a party hat with confetti at New Year (and a dance at midnight), a pumpkin
  with bats in the week before Halloween
- **Reminders**: water every 45 min (he hops up and down waving a bottle, then holds it
  out with a water bubble) and a break after 60 min at it (coffee bubble, stretches and
  waves). Click him to say you've seen it: he has a drink, or a coffee break with you.
  Ignored, he tries again in 10 min. Nothing while you're away (no pointer or prompt
  for 10 min restarts the count), in quiet mode or in fullscreen
- Tray icon: click to hide/show

## Layout
- `claude_pet.py`: the app (sprite loading, behaviours, physics, hooks socket, launcher)
- `clawd_hook.py`: the Claude Code hook (stdlib only, always exits 0 silently)
- `tools/install_hooks.py`: adds/removes the hooks in `~/.claude/settings.json`
- `sprites/clawd.json`: traced frames, palette, timings, loop points. Generated, don't hand-edit
- `sprites/extras.py`: hand-drawn extras on the same grid (hats with anchors, mug, steam,
  bottle, bubbles, bats, confetti, droplets). Plain data, parsed with `ast`, never imported
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
- Scenes can layer extras over the base frame: `lift` (sitting up in his cloud),
  `layers` (props like the glasses and page, `PROPS`) and `front` (the cloud, lifted out
  of Clawd-Cloud by its two blues in `cloud_platform()`, drawn over his legs)
- Scale is screen pixels per cell (Small 3, Medium 4, Large 6, Huge 8), saved in QSettings
- Hats: each frame's head is found by `head_of()` (top row with 8+ body cells, or a run
  cut off by the frame edge when he peeks in); a hat's `anchor` cell sits on the cell just
  right of the head's centre line. Arms raised past his head are drawn over the brim

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
