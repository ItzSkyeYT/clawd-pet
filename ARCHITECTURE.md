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

## Climbing windows
- `_climb_target()`: a window whose side comes down to within `CLIMB_JUMP` cells of his
  feet, reachable along what he stands on, with a visible top span touching that edge to
  stand on, and a clear column beside the edge (`_column_clear()`: no window stacked above
  it, on a screen). `_climb_down_target()` does the same from the top of the window he's on
- `_jump_target()`: from the window top he's on, another visible top across a gap of at
  most `LEAP_GAP` cells, no more than `LEAP_UP` higher or `LEAP_DOWN` lower; he walks to
  the edge and `_arc_to()`s across, the arc higher for a wider gap
- `_climb_side()` moves him hand over hand beside the edge (scripted), following the
  window if it moves and falling if it goes; up top he hops in over the edge (`_arc_to`),
  going down he stops at the floor below or lets go at the bottom of the side. The side
  and ladder climbs cycle through however many frames the art has (four now, played
  backwards going down)

## Coming down softly
- `_come_down(vx, vy)` replaces the plain drop off a window (`_act_hop_down`, after
  walking to the brink), an icon visit and the reading cloud: `_drop_below()` measures
  what he'd land on; under `FLOAT_FROM` cells (or a quarter of the time) it's `_fall()`,
  else `_float_down()`, with the parachute (and maybe a skydive first) from `SKYDIVE_FROM`
- `_float_down()` waits until he's over the top of the hop and clear of the edge, then
  sets `chute`: `_physics_step` pulls his fall speed to `CHUTE_FALL` (hard, as the canopy
  catches the air), damps his drift, sways him (`CHUTE_SWAY`) and never bounces. The
  canopy is a layer placed by `_held_at()` so the art's `anchor` cell sits in the dangle
  frame's fists. `start()` packs it away if anything interrupts

## The wallpaper
- `plasma_wallpapers()` reads Plasma's desktop config: each containment's wallpaper
  plugin, image and slideshow interval (900 s when unset). Plasma turns slideshows over
  at whole multiples of the interval since 1970 (imagebackend.cpp, to keep screens in
  step), so `next_slide_change()` is exact and a one-shot timer fires just after it. A
  QFileSystemWatcher on the config (re-added after each save, which replaces the file)
  catches wallpapers set by hand; `wallpapers_differ()` ignores anything but the picture
- `wallpaper_changed()` starts the `startled` scene unless he's busy with something that
  matters, at most once a minute; the `back` pose is his silhouette without eyes

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
- `hat()`: the birthday party hat (all day) > the party hat during `PARTIES` scenes > the
  hat you picked > New Year's party hat > the nightcap all night > `season_hat(date)`.
  `wall_clock()` is the only clock read (tests pin it)
- Hanging frames place hats from the art's eye positions (`dangle_head()`): the clasped
  fists are as wide as a head, which fooled `head_of()`. Spin frames have the hat baked
  in before turning (`Sprites.spin_anim(face, hat)`, built on first use), arms in front
- The birthday: `is_birthday()` (29 February kept on the 28th in other years),
  `_birthday_due()` (once a day, remembered in QSettings `birthday_done`, when you're
  there), `_act_birthday()`. He grows with `_resize_in_place()` about his middle; the size
  you chose is kept in `_true_scale` so `save()` never stores the party size. The art is
  `sprites/birthday.py` (`BIRTHDAY`, loaded like extras): the cake's `hold` cell goes on
  his bottom middle so it's held up in front with the flames between his eyes (a test
  checks his eyes stay clear); smoke rises from each wick in step. Balloons start either
  side of him with their strings off the floor and fade out before the window's top
- A hat change is animated (`_work_out_hat()`, `HAT_SWAP`): the old hat lifts off and
  fades, the new one drops on, bounces and sparkles; `settle_hat()` skips it (at start)
- Night (`night_from` to `night_to`, minutes after midnight, 22:00-06:00 by default, may
  or may not cross midnight): sleepy rests, yawns, naps weighted up, lively scenes down.
  Morning (`night_to` to `morning_to`, until 11:00): `_act_morning` once a day, when you're there
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
- `_place_reminder_bits()` runs every tick while one is pending and keeps both wholly on a
  screen (`screen_areas()`), trying `REMINDER_SPOTS` in order: over his right shoulder,
  over his left, lower on his left with the button beside the bubble. A spot that still
  fits is kept; back to the right only with `SPOT_SLACK` cells to spare, so it can't
  flicker at an edge. Off-screen parts of the desktop (the corner under a taller screen)
  count as off
- `_go_to_focus()`: to the middle of `_focus_area()` (the active window's screen, else the
  pointer's), off a window with a hop, then screen by screen: `_act_climb(side)` where
  the floors step (the ladder, up or down; whatever he holds is put away for the climb),
  walking where they meet; one big `_arc_to()` only where no ladder can reach

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
- Swing: a pendulum on a moving pivot, driven by the grip's acceleration both ways
  (theta'' = -((g - a_y) sin theta + a_x cos theta) / L - damping), in 4 substeps a tick and
  free to go all the way round. Up to `SPIN_FROM` it uses the hand-drawn straight / lean_1 /
  lean_2 frames (mirrored for the other side, kicks when calm); past that, `spin_*` frames:
  his straight hang turned about his hands in `SPIN_STEPS` steps by `turned_frames()`
  (RotSprite-style: Scale2x three times, turn, sample back onto the cell grid), built when
  the sprites load. Four half-turns within a few seconds leave him dizzy (orbiting stars)
- Turned over the top he'd be over the pointer's tip, so while he dangles his shape has a
  7 px hole at the tip (under the arrow anyway) and the drop catcher shrinks to 1 px
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
