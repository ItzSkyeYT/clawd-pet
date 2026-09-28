# Clawd Pet: Tasks

## Done
- [x] Official animations traced from Anthropic's GIFs: walk, wave, jump, happy jump,
      dance, laptop, sparkler, cloud ride, kart race, peek from the edge
- [x] Blink, look around, happy and sleeping faces in the official style
- [x] PyQt6 port; runs on KDE Wayland through XWayland
- [x] Left-click opens Claude Code in a terminal on Linux, macOS and Windows
- [x] Drag and throw with gravity; clicks pass through the space around him
- [x] Walks and rides along the bottom of the screen; sizes Small to Huge (remembered)
- [x] Roams across monitors: walks off a taller screen and drops, leaps back up,
      throws and drops land on the right floor (tests use the real two-screen layout)
- [x] Claude Code hooks: types while it works, "!" + wave for permissions, celebrates
- [x] Left-click opens the Code tab of the Claude app (or the session waiting on you)
- [x] Watches the pointer, glances at it; stroke him for hearts and a dance
- [x] Ladder between screens of different heights (up and down)
- [x] Checks out desktop icons: puzzles, pokes, magnifying glass, verdict; stands on or hangs off them
- [x] Reads a page from a desktop folder with glasses on, reacting to the story
- [x] Start at login (XDG autostart, toggle in his menu) and "Restart Clawd"
- [x] Tests (`python -m unittest discover -s tests -v`)

## Possible Improvements
- [ ] Tool-specific flourishes (cloud for web searches, sparkler for subagents)
- [ ] Auto-hide over fullscreen windows (he's unmanaged, so he floats above everything)
- [ ] Trace the /btw video's poses (profile turn, thought bubble)
- [ ] Windows installer
