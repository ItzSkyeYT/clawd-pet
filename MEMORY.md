# Clawd Pet: Memory

## Decisions
- **Traced official art instead of hand-drawn sprites** (Sept 2026): the hand-drawn 11x8
  sprite was off-model (7-wide body, evenly spaced legs, wrong colour). The official GIFs
  on claude.ai and the desktop app's Clawd-Laptop animation share one pixel grid, so
  they trace exactly
- **PyQt6 over PyQt5**: PyQt5 isn't installed on the CachyOS machine and is legacy; PyQt6 is
- **XWayland on Wayland**: a desktop pet must place its own window, which Wayland forbids
- **Sprites as text data** (`sprites/clawd.json`, rows of palette keys), not image files:
  diffable, and still no image loading at runtime
- **assets/ is git-ignored**: it holds Anthropic's files and personal screen recordings.
  `tools/fetch_official.py` re-downloads the official set
- **Window shaped to the sprite**: otherwise the transparent area around him blocks clicks
- **Per-screen floors, not Qt's availableGeometry** (multi-monitor): X11's single
  work area put his floor on the laptop screen at the HDMI taskbar's height,
  488px up. Cuts deeper than 15% of a screen are ignored (see ARCHITECTURE.md)
- **Unmanaged window** (X11BypassWindowManagerHint): KWin clamps managed X11 windows
  to the single work area; that, not the floor maths, was why he floated on the laptop
- **Hooks are async and send names only**: they can't slow or change Claude Code, and
  tool inputs (file contents, commands) never leave the hook
- **Pointer via KWin script + D-Bus**: XWayland only sees the pointer over X11 windows
- **Desktop icons from Plasma's config + FolderView.qml maths**: nothing else exposes
  them; the default units (gridUnit 18, smallSpacing 4) are assumed
- **Faces derived from the idle pose** (blink, look, happy), copying official shapes:
  the Clawd-Laptop wink for blinks, the Clawd-Dancing ^ ^ for happy

## Where the official animations live
- `https://claude.ai/images/clawd/core/Clawd-{CrabWalking,Waving,Lurking,Jumping,JumpingHappy}.gif`
- `https://claude.ai/images/clawd/persona/Clawd-{RacingCar,Cloud-once}.gif`
- `https://claude.ai/images/spotlights/claude-code-celebration/Clawd-Dancing.gif`
- `/usr/lib/claude-desktop/resources/ion-dist/images/install-hub/clawd-laptop.webm` (+ a Lottie)
- Found by grepping the desktop app's web bundle for `/images/clawd`; a new app version
  may reference new ones
