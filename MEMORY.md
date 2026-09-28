# Clawd Pet — Memory

## Decisions
- **PyQt5 over tkinter**: Better native window transparency support, proper system tray, richer painter API
- **Sprites as code (not image files)**: No file loading, easy to edit colors and shapes in code
- **`PX = 7`**: 7 screen pixels per sprite pixel gives readable size without being oversized
- **Single file**: Self-contained, easy to share and run anywhere with PyQt5 installed
