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
- Physics: dropped or thrown he falls with gravity, bounces off walls, lands with
  the official Jumping squash

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

## Pointer
- KDE Wayland: `start_kwin_cursor_feed()` registers `org.clawdpet.Pet` on D-Bus and
  loads a KWin script that calls it on `workspace.cursorPosChanged`. Elsewhere he
  polls `QCursor.pos()`. Hovering over him also counts
- Petting: three or more direction changes over his body within 1.5 s
- While typing, `_typing_reaction()` swaps in Clawd-Laptop typing frames with other
  eyes (`typing_eyes()`: looking up left/right, happy) for GLANCE_MS, then cools down

## Props and icons
- The ladder is a separate unmanaged, click-through window (`Prop`), placed on the
  lower screen by the seam and revealed from the top or bottom
- Desktop icons come from Plasma's folder-view config (`positions=`), laid out with
  the same maths as Plasma's FolderView.qml (cell size, extra spacing, icon offset);
  entries that are directories get the reading scene
- Layered frames: base frame (raised by `lift`), then `layers` (props), then `front`
  (the cloud platform), then particles; the window mask covers all of them

## Platform notes
- Unmanaged window (`X11BypassWindowManagerHint`): KWin keeps managed X11 windows
  inside X11's single work area, which pinned him mid-screen on the taller monitor
- Wayland: forced onto XWayland (`QT_QPA_PLATFORM=xcb`) so `move()` works;
  `launch_env()` strips that before starting terminals or browsers
- Launch: Linux uses $TERMINAL or the first of konsole, gnome-terminal, kitty,
  alacritty, wezterm, foot, xfce4-terminal, xterm; macOS uses Terminal via
  osascript; Windows uses cmd
