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
- [x] Watches the pointer, glances at it; pet him for hearts and a dance
- [x] Ladder between screens of different heights (up and down)
- [x] Checks out desktop icons: puzzles, pokes, magnifying glass, verdict; stands on or hangs off them
- [x] Reads a page from a desktop folder with glasses on, reacting to the story
- [x] Start at login (XDG autostart, toggle in his menu) and "Restart Clawd"
- [x] Walks on windows (hops up, rides them, hops down); ducks out of fullscreen
- [x] Claude Code by tool: reading glasses, magnifying glass, cloud for the web, laptop
- [x] Drop a folder on him: a Claude Code session there (works from Wayland apps too)
- [x] Settings panel and quiet mode
- [x] Hats (Santa, pumpkin, party, nightcap), night and morning routines, New Year
      confetti, Halloween bats
- [x] Water and break reminders, acknowledged with a click; they stay up until Done, on
      screen, and he takes the ladder to the screen you're working on
- [x] Grabs the pointer and dangles from it; spin him round it
- [x] Climbs window sides, jumps between windows, floats down under an umbrella or a
      parachute (skydiving first from high up)
- [x] Startled by a new wallpaper; a birthday party with a cake, balloons and a banner
- [x] Tests (`python -m unittest discover -s tests -v`)

## Possible Improvements
- [ ] Sparkler or a small flourish for subagents
- [ ] Keyboard activity for break reminders (KDE's GetSessionIdleTime is off on Wayland;
      the ext-idle-notify protocol would need a native Wayland helper)
- [ ] Trace the /btw video's poses (profile turn, thought bubble)
- [ ] Windows installer
