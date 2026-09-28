# Clawd Desktop Pet

Draggable PyQt5 desktop widget — a pixel-perfect Claude mascot.

## Run
```bash
pip install PyQt5
python claude_pet.py
```

## Controls
- **Left-click**: Open Claude Code
- **Right-click**: Context menu (claude.ai, force animations, quit)
- **Drag**: Move Clawd around the desktop

## Stack
- Python 3 + PyQt5
- All graphics are pixel-art sprites rendered via QPainter (no image files)
- Colors matched from official Clawd sprite (Anthropic social media)

## Architecture
- Single file: `claude_pet.py`
- Sprites defined as 2D grids of palette indices
- `render(grid)` → QPixmap via QPainter
- QTimer drives animation frames
- QSystemTrayIcon for tray integration
- `PX = 7` — screen pixels per sprite pixel

## Development Approach

Before implementing any changes, write a test script that validates the expected behavior. For example, if we're calling an API, first write a small script that hits the endpoint and asserts the response shape. Then implement the feature, running the test after each edit until it passes. If a test fails 3 times with the same approach, stop and propose an alternative strategy. Start by analyzing the current task and writing the test. Do this most of the time unless not very helpful — use judgment.

## Palette
| Key | Color | Role |
|-----|-------|------|
| 1 | #D47F5A (warm salmon) | Body |
| 2 | near-black | Eyes |
| 6 | gold #FCDB55 | Sparkle |
| 7 | blue | Effect |
