# Clawd Pet — Architecture

## Single-file design
`claude_pet.py` contains everything: palette, sprites, render engine, widget, animation loop, tray, and event handlers.

## Render Pipeline
```
Sprite grid (2D list of palette ints)
    → render(grid) → QPainter fills PX×PX rects per cell → QPixmap
    → QWidget.paintEvent() draws pixmap to screen
```

## Animation
- QTimer at ~100ms intervals cycles through sprite frames
- Idle animation: 2+ frames alternating
- Sparkle/shimmer: triggered randomly or on demand

## Event Handling
- `mousePressEvent` — left-click (open CC) / right-click (menu) / start drag
- `mouseMoveEvent` — drag to reposition
- `mouseReleaseEvent` — end drag
