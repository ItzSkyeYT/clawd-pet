# Clawd Pet: Architecture

## Asset pipeline (offline)
```
claude.ai/images/clawd/...gif  ─┐
Claude desktop app bundle      ─┼─ tools/fetch_official.py ─→ assets/official/   (git-ignored)
assets/clawd_sparkle.gif       ─┘
        ↓
tools/trace_official.py
  find each file's half-pixel grid from its colour edges
  sample every cell at its centre (immune to dithering and edge rounding)
  one shared palette, crop, merge held frames, locate the idle pose ("home")
  fidelity check: cells whose pixels disagree with the traced value (0% on all but the
  dithered sparkler, 0.2%)
        ↓
sprites/clawd.json   (committed; the only thing the app reads)
```

## Runtime (`claude_pet.py`)
- `Sprites`: JSON rows → one-pixel-per-cell QImages; scaled with nearest-neighbour
  and cached per (frame, mirrored, scale)
- `ClawdPet` window: sized to fit every animation around a fixed "home" point, so
  switching animations never shifts him. The window is shaped to the current frame,
  so clicks around him pass through to whatever is behind
- Behaviours are generators: they set the frame (and walking speed) and yield how
  long to hold it. `advance(dt)` runs physics, steps the behaviour, repaints only
  when something visible changed. A 16 ms QTimer drives it; tests call it directly
- Loops (walking, typing, driving, riding) repeat the traced `loop` segment as long
  as the behaviour wants, then play `outro`
- Physics: dropped or thrown he falls with gravity and a little air drag, bounces off
  walls, and lands with the official Jumping squash. It runs in fixed `PHYS_STEP` (4 ms)
  steps while he flies or skids, so a throw lands in the same place at any frame rate.
  A hard landing (faster than `BOUNCE_MIN`, a fall of ~20 cells) bounces once at
  `BOUNCE`; a sideways one skids to a stop (`SKID`), stopping at walls and dropping off
  edges. A throw's speed is the drag's motion over its last `THROW_WINDOW` ms, timed on
  a clock that isn't reset every tick; let go of a still drag and it's just a drop.
  Coming off the pointer he's flung with his swing (the pendulum's tangential speed)
- Ticks: `_next_tick()` asks for every frame only while he moves (flying, skidding,
  walking, dragged, dangling); otherwise the timer sleeps until his next step, or a
  particle's next cell, and at most `IDLE_TICK` (250 ms). `wake()` ticks at once for the
  pointer near him, Claude Code, window changes, clicks and any new behaviour
- Shape: the window is shaped to a pose, or to all of an animation's frames together
  (so walking doesn't reshape it every step), plus props; flying particles go in as
  `MASK_BLOCK` blocks. Each reshape is a round trip through the X server and KWin
- `status()` has `counts` (ticks, paints, shapes, moves) for checking what he costs

## Multiple monitors
- Each screen's floor is the bottom of its *usable* area. X11 (so XWayland) has one
  work area for the whole desktop, so a taskbar at the bottom of a short monitor
  "cuts" a taller one hundreds of pixels up. `usable_area()` ignores cuts deeper
  than 15% of a screen (real panels are thin); `x11_work_area()` reads
  `_NET_WORKAREA` itself because Qt only applies it at some point after startup
- Moving sideways past a screen edge is allowed when another screen continues at
  his body's height; if the floor there is lower he falls to it. Otherwise it's a wall
- `_act_leap` gets him back up onto a higher neighbouring screen: a real ballistic
  arc that only drifts sideways once his feet clear the higher floor
- Walks are sometimes long trips to a random spot on any screen; rides stay on one
  screen; he only lurks at outer edges, never at the seam between two screens

## Claude Code hooks
```
Claude Code event ─→ clawd_hook.py (async hook) ─→ Unix socket ─→ ClawdPet.claude_event()
```
- Sessions are tracked by id: busy (prompt/tool events) → he types on the laptop;
  waiting (PermissionRequest, permission notifications) → waves with a "!" bubble and
  a click opens `claude://code/needs-input`; Stop → he celebrates (sparkler after long
  work). Stale sessions expire (15 min busy, 30 min waiting)
- Claude Code events only interrupt his own ideas, and only when he's on his feet.
  Anything you asked for (menu Play, a click, socket play, petting) finishes first
  (`start(..., manual=True)`), as do climbs, leaps, icon visits, reading and falls;
  `_next()` then goes straight to what Claude Code needs
- With the `claude` pref off, `claude_event()` drops everything after noting a prompt as
  activity, `claude_mode()` returns None, and turning it off clears the sessions; the hooks
  keep firing (they're async and cheap), so switching back on is instant
- What he does while busy follows the tool (`TOOL_STYLES` on PreToolUse): Read → glasses
  and a page, Grep/Glob/LS → the magnifying glass, WebSearch/WebFetch → out on his cloud,
  anything else → the laptop. A style is held for `STYLE_HOLD` so quick tool switches
  don't make him flicker; a PostToolUseFailure makes him stumble

## Pointer
- KDE Wayland: `start_kwin_cursor_feed()` registers `org.clawdpet.Pet` on D-Bus and
  loads a KWin script that calls it on `workspace.cursorPosChanged`. Elsewhere he
  polls `QCursor.pos()`. Hovering over him also counts
- Petting: three or more direction changes over his body within 1.5 s
- While typing, `_typing_reaction()` swaps in Clawd-Laptop typing frames with other
  eyes (`typing_eyes()`: looking up left/right, happy) for GLANCE_MS, then cools down

## Windows and fullscreen
- The KWin script also reports windows (geometry, stacking, fullscreen, active, output,
  id; never titles). `_surfaces()` = screen floors plus the visible parts of window top
  edges with room above them (split where higher windows cover them), cached per tick
- Landing is continuous: a fall stops on the first surface between where his feet were
  and where they are. Standing on a window he rides it (`standing_on` + its geometry
  delta); if it goes, he drops
- Something fullscreen on his screen: `_act_duck` sinks him out of view, hides the
  window, and pops him back up when it's over

## Drag and drop
- KWin hands drags from Wayland apps only to X11 windows it *manages*
  (`pickDragTarget()` skips `!isClient()`), and Clawd is unmanaged. So on KDE Wayland a
  `DropCatcher` sits under him: a managed window typed `_NET_WM_WINDOW_TYPE_NOTIFICATION`
  (set with xprop before it maps: KWin never clamps special windows to the work area and
  stacks notifications above normal windows), with his exact shape, position and
  visibility (`_sync_catcher()` each tick). Being under him, it never sees the pointer,
  only drags. The KWin script marks it skip-taskbar/pager/switcher and doesn't watch his
  own windows' geometry (he moves every frame). It follows his body and props, not
  particles, and moves at most 10 times a second while he's moving (a drag freezes him)
- A drop is accepted as Copy (or Link), never Move, so the source never deletes anything

## Settings
- `Prefs` wraps QSettings (`prefs/<key>`) with typed defaults (`PREF_DEFAULTS`); an INI
  file returns strings, and a one-item list as a bare string, so reads are coerced by
  the default's type. `SettingsDialog` writes through `set_pref()`, which also reacts
  (quiet mode wraps up a scene he's in)
- Activity scales the rest between scenes (`ACTIVITY`); `scenes_off` filters
  `_pick_action()`; quiet mode reduces it to idle or a nap

## Hats, time of day, holidays
- `head_of()` finds each frame's head when sprites load (`Anim.heads`); `_hat_image()`
  places the hat's anchor there, mirrored with him, and `paint()` redraws whatever
  reaches up past his head (raised arms) over the brim (`_reaching_up()`)
- `hat()`: the menu/settings choice, else a nightcap for night naps, else
  `season_hat(date)`. `wall_clock()` is the only clock read (tests pin it)
- Night (22:00-06:00): sleepy rests, yawns, naps weighted up, lively scenes down.
  Morning (06:00-11:00): `_act_morning` once a day, when you're there
- `_confetti()` bursts when he wears the party hat; `_bats()` are particles that flap
  (`flap` frames) and bob as they cross

## Reminders
- Activity: pointer movement (the KWin feed), prompts and clicks set `_input_at`; 10 min
  with none (`AWAY`) counts as a break and restarts both counts. KDE refuses
  `GetSessionIdleTime` on Wayland, so keyboard-only stretches aren't seen
- A reminder is a pending state (`reminding`, saved in QSettings) that only the Done button
  clears (`confirm_reminder()`). `start()` keeps its bubble and button through any other
  behaviour except a permission request, hanging off the pointer and ducking (they'd
  collide or cover the pointer's tip); `_next()` and `_maybe_remind()` (once a second) go
  back to it first. `REMIND_LOUD` ms of hopping, then calm. New ones come when due, over
  anything that isn't manual, physical or a permission request
- The bubble sits at `BUBBLE_AT`, the button at `DONE_AT`: clear of hats (they droop left),
  the raised and the side-held bottle, and every pose he takes in a reminder (a test checks
  the button against his pixels and props)

## His settings, open
- `open_settings()` starts the manual `settings` scene (Claude Code and reminders wait).
  `set_pref()` (and the size and login controls) report each change with its old value
  to `settings_changed()`, which queues it while the scene runs; `_take_in()` turns each
  into a little reaction (repeats of the same setting are merged, so a spinbox doesn't
  flood him). He comes down first if he was hanging off the pointer (`_land_first()`)

## Hanging off the pointer
- Held right above him (`GRAB_SURE_SIDE`) for `GRAB_HOLD` ms, the pointer is always
  grabbed: no chance, no cooldown, any state but `NEVER_GRAB`, even in quiet mode. After
  a let-go that's disarmed (`_sure_armed`) until the pointer leaves the spot, so shaking
  him off doesn't bounce him straight back on
- Otherwise `_maybe_grab()` counts how long the pointer stays within reach (up to `GRAB_REACH` cells
  above his head, `GRAB_SIDE` to either side) while he's idle or in a light scene
  (`GRAB_WHILE`, on the ground); it must have moved in the last 5 s. After `GRAB_LINGER`
  he rolls `GRAB_CHANCE` once per visit
- `_act_grab`: walk under it if it's along his floor, crouch, `_leap_to_pointer()` (a homing
  arc, so a moving pointer is still caught), then `_dangle()` until he lets go (`_let_go()`
  hands him to the normal fall with the pointer's velocity)
- He holds the arrow's tail: `cursor_grip()` is Breeze's tail offset scaled by `cursorSize`
  from kcminputrc. The dangle frames' grip cell is their topmost cell, so the pointer's tip
  (where clicks land) is always above everything he draws; hats are off while he hangs
- `cursor_moved()` repositions him the moment the pointer moves, not at the next tick.
  The KWin script reports the pointer every 2 px within 400 px of him (found through his
  drop catcher) and only every 24 px further away
- Swing: a pendulum driven by the grip's horizontal acceleration, damped, picks the
  straight / lean_1 / lean_2 frame (mirrored for the other side); calm spells get leg kicks
- Faces are redrawn per frame by `eyes_rows()` at the eye cells the art gives
- Shake: `SHAKE_FLIPS` direction changes faster than `SHAKE_SPEED` within `SHAKE_MS`

## Props and icons
- The ladder is a separate unmanaged, click-through window (`Prop`), placed on the
  lower screen by the seam and revealed from the top or bottom
- Desktop icons come from Plasma's folder-view config (`positions=`), laid out with
  the same maths as Plasma's FolderView.qml (cell size, extra spacing, icon offset);
  entries that are directories get the reading scene
- Layered frames: base frame (raised by `lift`), then his hat (and his raised arms over
  it), then `layers` (props), then `front` (the cloud platform), then particles; the
  window mask covers all of them

## Platform notes
- Unmanaged window (`X11BypassWindowManagerHint`): KWin keeps managed X11 windows
  inside X11's single work area, which pinned him mid-screen on the taller monitor
- Wayland: forced onto XWayland (`QT_QPA_PLATFORM=xcb`) so `move()` works;
  `launch_env()` strips that before starting terminals or browsers
- Launch: Linux uses $TERMINAL or the first of konsole, gnome-terminal, kitty,
  alacritty, wezterm, foot, xfce4-terminal, xterm; macOS uses Terminal via
  osascript; Windows uses cmd
