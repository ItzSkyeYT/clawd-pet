# Clawd Desktop Pet

Desktop pet of Clawd, the Claude Code mascot (PyQt6). Every animation is traced
pixel-for-pixel from Anthropic's official Clawd GIFs, so he moves the way he
does in the Claude apps. With the hooks installed he follows Claude Code: reads,
searches, browses or types depending on the tool it's using, waves with a "!"
when it needs a permission, celebrates when done.

## Run
```bash
python claude_pet.py      # or ./install.sh: PyQt6, a clawd-pet command, menu entry, hooks
```
Needs PyQt6 (CachyOS/Arch: `sudo pacman -S python-pyqt6`; `install.sh` knows the other
distros and falls back to a venv). On Wayland the app runs itself through XWayland,
because a pet has to move its own window and Wayland doesn't allow that. Programs it
launches get the normal environment back. Without XWayland it says so and exits.

**Desktops** (`desktop_kind()`): KDE Wayland gets the pointer and windows from a KWin
script over D-Bus; GNOME Wayland from our extension (`gnome/`) over the same interface;
Hyprland and Sway from their IPC sockets (`PolledFeed`); any X11 session from EWMH via
ctypes libX11 (`X11`), which also gives the work area without xprop. Rows are always
`[x, y, w, h, stacking, fullscreen, active, screen name or "", id]`. Wallpaper frights:
Plasma's config on KDE, `gsettings monitor`/`xfconf-query -m` on GNOME, Cinnamon, MATE,
Budgie and XFCE.

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
name in `PLAYABLE`, including `yawn`, `coffee`, `remind_water`) and
`{"cmd": "set", "pref": "claude", "value": false}` (any setting, type-checked). The
settings dialog shows whether the hooks are installed and can install or remove them.

**Follow Claude Code** (menu and settings, pref `claude`) turns the whole integration
off without touching the hooks: he ignores every event (prompts still count as you
being there, for the reminders) and stops typing or calling you over at once.
Anything you ask for (menu, click, socket play, petting) finishes before he goes back
to what Claude Code is doing.

## Controls
- **Left-click**: the Code tab of the Claude app (`claude://code/continue?session=last`,
  or `claude://code/needs-input` while a session waits on you); a terminal if the app isn't there
- **Right-click**: menu (Claude Code, new session, terminal, claude.ai, Play: every move,
  the desktop and time-of-day scenes, reminder previews and the Claude Code states;
  Hat, Size, Quiet mode, Settings…, start at login, restart, quit)
- **Play goes and does it**: whatever you pick, he sets it up first. Hop down or climb
  down from the floor: up onto a window first (a hop if one's in reach, up its side if
  that reaches the floor, else a ladder leaned against it). Jump to another window: onto
  one with a neighbour, then across, a long leap if that's all there is. Climb a side
  that starts high up: he leaps for it. The ladder, a leap (up or down), an icon or a
  folder: down off the window and over to the screen that has them. Rides and peeking
  start on a floor. With nothing to do it with (no window, no icon), a puzzled "?"
- **Drop a folder on him** (or a file, for its folder): a new Claude Code session there
  (`claude://code/new?folder=...`, or a terminal in it). He holds still with his arms up
  while you hover, and always takes it as a copy, never a move
- **Settings…**: opening them is an event: a double take, a start, a bit of sweat, then he
  puts on his reading glasses and reads along, reacting to each change (a dance for
  Lively, a pout for a scene you turn off, a start when you resize him, a hop for a new
  hat), and jumps for joy when you close them. The dialog has: how lively he is
  (Lively: barely a second's rest between scenes and no winding down), size, which scenes he does on his own, pointer,
  petting, fullscreen ducking, time of day, holidays, hat, reminders and their
  intervals, Claude Code hooks, start at login. Changes apply at once (QSettings `prefs/`)
- **Quiet mode**: he stays put and keeps to himself (a nap at most, no reminders or
  celebrations); Claude Code working or needing you still shows
- **Drag**: pick him up; let go and he falls (throw him and he bounces)
- **Pet him** with the pointer (back and forth over him): hearts, happy eyes, eventually a dance
- **He grabs the pointer**: hold it right above him for half a second and he always jumps
  up, grabs the arrow's tail and dangles from it, swinging as you move and kicking his
  legs (from anything but a reminder, his settings or a fall; after he lets go, move the
  pointer off and back for another go). Whirl the pointer round in circles and he spins
  right round it, over the top and all; a good spin leaves him dizzy (stars), and letting
  go mid-spin flings him. Left hanging around near him (off to one side,
  he walks under it) it gets grabbed now and then: 80%, 45 s apart (15 s when lively). He lets go when he's had enough (hanging by one hand first) or when you shake the
  pointer back and forth, and drops with the swing you gave him. Never over the tip, so
  your clicks always go through. Play → "Grab the pointer" sends him after it wherever it
  is; the setting is under Reactions
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
- He walks on windows: hops up onto a window's top edge or goes up its side one of two
  ways: walking straight up it like a gecko (his walk turned on its side, feet on the
  edge, hat and all) or throwing a grappling hook onto the top and climbing the rope hand
  over hand (each fist stays put on it while he hauls himself up), reeling it in after;
  down, head first on foot or rappelling in bounces. Play has each way; on his own it's
  either (`CLIMB_STYLES`). He leaps across gaps from one window top to the next, rides a window
  when you move it (even mid-climb), climbs down again or walks to the brink and hops off;
  ducks out of sight while something is fullscreen on his screen
- **Coming down from high up** (off a window or an icon, after reading on his cloud,
  letting go at the bottom of a window's side, walking off an edge or when the window
  under him closes): from 30 cells up he always floats down under an umbrella or a
  parachute, swaying, and lands softly; from 70 up it's often a skydive first,
  spread-eagled, before he pulls the cord (the pack pops, then the canopy opens). A throw
  is yours, so that stays a plain fall with a bounce
- **A new wallpaper** makes him jump: he glances about, turns round to look (from behind
  he has no eyes), whips back with a "!" and jumps. Plasma slideshows change on the clock
  (whole multiples of the interval, 15 min by default), so he's ready for each one; a
  wallpaper you set yourself is caught from Plasma's config
- **Time and seasons** (from the clock): sleepier at night (yawns, longer naps) and in
  his nightcap all night long, a stretch and a mug of coffee on the first morning visit
  (night 22:00-06:00 and mornings until 11:00 by default; both are set in Settings), a Santa hat in
  December, a party hat with confetti at New Year (and a dance at midnight), a pumpkin
  with bats in the week before Halloween
- **Your birthday** (Settings → Time and seasons): the first time he sees you that day he
  notices the date, puffs up to double size, jumps about in confetti with HAPPY BIRTHDAY!
  popping up above him in rainbow letters and balloons rising on either side, holds up a
  cake with the candles flickering between his eyes (a present at his feet), makes a wish
  with his eyes shut, blows them out (smoke curls up from the wicks), dances and shrinks back. Once a day (remembered across restarts), and the
  party hat stays on all day, whatever else is set. Play → "Birthday party" previews it
- **Hats**, strongest first: your birthday's party hat; the party hat at any celebration
  (Claude Code finishing a job, New Year, the birthday party); a hat you picked; New
  Year's party hat; the nightcap all night; the season's. They stay on while he hangs
  off the pointer, and go round with him when he's spun. A change of hat is animated: the
  old one lifts off and fades, the new one drops on, bounces and sparkles
- **Reminders**: water every 45 min and a break after 60 min at it. He goes to the middle
  of the screen you're working on (your active window's, else the pointer's) the way he
  gets about anyway: down off a window, along the floor, up or down the ladder to the
  other screen (the bottle put away for the climb). There he hops about waving the bottle
  and holds it out; for a break, a look, a wave, a hop. A bubble and a green **Done**
  button stay up until you press the button: through being dragged or thrown,
  fullscreen, a permission request and even a restart (QSettings `reminding`). They
  always stay on a screen, following him: over his right shoulder, over his left at a
  right edge or in the dead corner under a taller screen, lower down beside him when he's
  up against the top.
  Loud for two minutes, then calm. A click on his body only flashes the button. After
  Done he has a drink, or a coffee break with you. New ones don't start while you're away
  (no pointer or prompt for 10 min restarts the count), in quiet mode or in fullscreen
- Tray icon: click to hide/show

## Layout
- `claude_pet.py`: the app (sprite loading, behaviours, physics, hooks socket, launcher)
- `clawd_hook.py`: the Claude Code hook (stdlib only, always exits 0 silently)
- `tools/install_hooks.py`: adds/removes the hooks in `~/.claude/settings.json`
- `install.sh`: installs for the user (PyQt6 from the distro or a venv, `~/.local/bin/clawd-pet`,
  menu and login entries through `claude_pet.py --install [--autostart]`, hooks, the GNOME
  extension); `--uninstall` undoes it
- `gnome/clawd-pet@itzskyeyt.github.io/`: the GNOME Shell helper (GNOME 45+, ESM); `node
  gnome/test.mjs` tests it against a mocked GNOME Shell
- `sprites/clawd.json`: traced frames, palette, timings, loop points. Generated, don't hand-edit
- `sprites/extras.py`: hand-drawn extras on the same grid (hats with anchors, the stretch
  pose with its head position, mug, steam, bottles, bubbles, bats, confetti, droplets,
  dangling from the pointer, climbing a rope (each frame with how far he rises and where
  his fists hold it), rappelling, the rope and its hook, the ladder, the parachute and
  umbrella with the cell his fists hold, the skydive). Plain data, parsed with `ast`,
  never imported
- `sprites/birthday.py`: the party's art the same way (the cake lit twice to flicker and
  blown out, with where it's held and where its flames and wicks are, smoke, the puff,
  balloons, letters with rainbow tints, a present)
- `tools/fetch_official.py`: downloads the official Clawd GIFs from claude.ai into `assets/official/`
- `tools/trace_official.py`: turns them into `sprites/clawd.json` and reports fidelity
- `tests/test_pet.py`: `python -m unittest discover -s tests -v` (offscreen, no window appears)
- `tools/make_media.py`: draws the README's GIFs (`docs/media/`) and settings screenshot,
  offscreen on a mock desktop, each scene with a fixed seed. `--list` for the scenes
- `assets/`: reference media, git-ignored (Anthropic's art and personal screen recordings)
- `LICENSE`: MIT, for the code only. `LICENSE-ASSETS.md`: the artwork (`sprites/`, `docs/media/`)
  stays all rights reserved; Clawd himself is Anthropic's

## Sprites
- All official Clawd art sits on one grid: the idle pose is 12x8 pixels and details
  (eyes, props, sparks) use half-pixels, so sprites are 24x16 cells
- Each animation stores `home`: where the idle pose sits inside it, so every animation lines up
- Blink / look / happy / sleep faces are derived from the idle pose in `pose_rows()`,
  copying how the official art draws them (the laptop wink, the dance's ^ ^ eyes)
- Walking up and down a window's side (`wall_up`, `wall_down`) is the official walk turned
  a quarter by `turned_ccw()` (mirrored first to face down); hats are turned the same way
  from the walk's own heads (`_turned_hat()`)
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
