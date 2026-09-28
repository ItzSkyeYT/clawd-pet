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
- Physics: walks and rides clamp to the current screen's available area; dropped
  or thrown he falls with gravity, bounces off the sides, lands with the official
  Jumping squash

## Platform notes
- Wayland: forced onto XWayland (`QT_QPA_PLATFORM=xcb`) so `move()` works;
  `launch_env()` strips that before starting terminals or browsers
- Launch: Linux uses $TERMINAL or the first of konsole, gnome-terminal, kitty,
  alacritty, wezterm, foot, xfce4-terminal, xterm; macOS uses Terminal via
  osascript; Windows uses cmd
