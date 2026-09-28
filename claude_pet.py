"""
Clawd Desktop Pet — pixel-perfect Claude Code mascot.
Traced from the actual Clawd sparkle GIF and Claude Code app sprites.

Left-click: Open Claude Code
Right-click: Menu (claude.ai, force animations, quit)
Drag: Move Clawd around your desktop
"""

import sys
import subprocess
import math
import random
from PyQt5.QtWidgets import QApplication, QWidget, QMenu, QSystemTrayIcon
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPainter, QColor, QPixmap, QIcon


# ── Palette — matched from official Clawd (Anthropic social media) ──
# 0=transparent  1=body (single color)  2=eyes  3=closed-eye  4=claw tip
P = {
    1: QColor(212, 127,  90),  # body — warm salmon #D47F5A (official Clawd)
    2: QColor( 20,  20,  20),  # eyes — near-black
    3: QColor(180, 100,  70),  # closed-eye line — slightly darker salmon
    4: QColor( 20,  20,  20),  # claw tip — same as eyes
    5: QColor(212, 127,  90),  # same as body — Clawd is ONE color
    6: QColor(252, 216,  85),  # sparkle gold
    7: QColor(125, 184, 255),  # effect blue
    8: QColor(235, 160, 120),  # highlight — slightly lighter salmon
}

PX = 7  # screen pixels per sprite pixel


def render(grid):
    rows = len(grid)
    cols = max(len(r) for r in grid)
    pm = QPixmap(cols * PX, rows * PX)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    for y, row in enumerate(grid):
        for x, c in enumerate(row):
            if c and c in P:
                p.fillRect(x * PX, y * PX, PX, PX, P[c])
    p.end()
    return pm, cols, rows


# ══════════════════════════════════════════════════════════════════
# SPRITES — traced from clawd_sparkle.gif reference
# Two-tone body: 1=lighter peach (head/body), 5=darker orange (arms/legs)
# Eyes: pure black (2), 1px each
# Grid: 11 wide x 8 tall (idle)
#
# Shape: narrow head (7px, color 1) → wide arms (11px, sides are 5)
#        → narrow body (7px, color 1) → 4 legs (color 5)
# ══════════════════════════════════════════════════════════════════

_ = 0  # shorthand for transparent

# ── IDLE ───────────────────────────────────────────────────────────
IDLE_1 = [
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,2,1,1,1,2,1,_,_],
    [5,5,5,5,5,5,5,5,5,5,5],
    [5,5,5,5,5,5,5,5,5,5,5],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
]

IDLE_2 = [
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,2,1,1,1,2,1,_,_],
    [5,5,5,5,5,5,5,5,5,5,5],
    [5,5,5,5,5,5,5,5,5,5,5],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,_,5,5,_,5,5,_,_,_],
    [_,_,_,5,5,_,5,5,_,_,_],
]

# ── BLINK ──────────────────────────────────────────────────────────
BLINK = [
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,3,1,1,1,3,1,_,_],
    [5,5,5,5,5,5,5,5,5,5,5],
    [5,5,5,5,5,5,5,5,5,5,5],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
]

# ── WALK RIGHT — dark claw extends right ─────────────────────────
WALK_R1 = [
    [_,_,1,1,1,1,1,1,1,_,_,_,_,_],
    [_,_,1,2,1,1,1,2,1,_,_,_,_,_],
    [5,5,5,5,5,5,5,5,5,5,4,_,_,_],
    [5,5,5,5,5,5,5,5,5,5,_,4,_,_],
    [_,_,1,1,1,1,1,1,1,_,_,_,4,_],
    [_,_,1,1,1,1,1,1,1,_,_,_,_,_],
    [_,_,5,_,_,5,_,_,5,_,_,_,_,_],
    [_,5,_,_,_,5,_,_,_,5,_,_,_,_],
]

WALK_R2 = [
    [_,_,1,1,1,1,1,1,1,_,_,_,_,_],
    [_,_,1,2,1,1,1,2,1,_,_,_,_,_],
    [5,5,5,5,5,5,5,5,5,5,_,4,_,_],
    [5,5,5,5,5,5,5,5,5,5,_,_,4,_],
    [_,_,1,1,1,1,1,1,1,_,_,_,_,_],
    [_,_,1,1,1,1,1,1,1,_,_,_,_,_],
    [_,_,_,5,_,_,5,_,_,5,_,_,_,_],
    [_,_,5,_,_,_,_,5,_,_,_,_,_,_],
]

# ── WALK LEFT — mirror ─────────────────────────────────────────────
WALK_L1 = [
    [_,_,_,_,_,1,1,1,1,1,1,1,_,_],
    [_,_,_,_,_,1,2,1,1,1,2,1,_,_],
    [_,_,_,4,5,5,5,5,5,5,5,5,5,5],
    [_,_,4,_,5,5,5,5,5,5,5,5,5,5],
    [_,4,_,_,_,1,1,1,1,1,1,1,_,_],
    [_,_,_,_,_,1,1,1,1,1,1,1,_,_],
    [_,_,_,_,_,5,_,_,5,_,_,5,_,_],
    [_,_,_,_,5,_,_,_,5,_,_,_,5,_],
]

WALK_L2 = [
    [_,_,_,_,_,1,1,1,1,1,1,1,_,_],
    [_,_,_,_,_,1,2,1,1,1,2,1,_,_],
    [_,_,4,_,5,5,5,5,5,5,5,5,5,5],
    [_,4,_,_,5,5,5,5,5,5,5,5,5,5],
    [_,_,_,_,_,1,1,1,1,1,1,1,_,_],
    [_,_,_,_,_,1,1,1,1,1,1,1,_,_],
    [_,_,_,_,5,_,_,5,_,_,5,_,_,_],
    [_,_,_,_,_,_,5,_,_,_,_,5,_,_],
]

# ── JUMP ───────────────────────────────────────────────────────────
JUMP_UP = [
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,2,1,1,1,2,1,_,_],
    [5,5,5,5,5,5,5,5,5,5,5],
    [5,5,5,5,5,5,5,5,5,5,5],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,_,1,1,1,1,1,_,_,_],
    [_,_,_,_,_,_,_,_,_,_,_],
    [_,_,_,_,_,_,_,_,_,_,_],
]

LAND = [
    [_,_,_,_,_,_,_,_,_,_,_,_,_],
    [_,1,1,1,1,1,1,1,1,1,1,1,_],
    [_,1,1,2,1,1,1,1,1,2,1,1,_],
    [5,5,5,5,5,5,5,5,5,5,5,5,5],
    [5,5,5,5,5,5,5,5,5,5,5,5,5],
    [_,1,1,1,1,1,1,1,1,1,1,1,_],
    [_,5,_,5,_,_,_,_,_,5,_,5,_],
    [5,_,_,_,5,_,_,_,5,_,_,_,5],
]

# ── HAPPY — ^_^ eyes ──────────────────────────────────────────────
HAPPY_1 = [
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,3,1,1,1,3,1,_,_],
    [5,5,5,5,5,5,5,5,5,5,5],
    [5,5,5,5,5,5,5,5,5,5,5],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
]

HAPPY_2 = [
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,3,1,1,1,3,1,_,_],
    [5,5,5,5,5,5,5,5,5,5,5],
    [5,5,5,5,5,5,5,5,5,5,5],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,_,5,5,_,5,5,_,_,_],
    [_,_,_,5,5,_,5,5,_,_,_],
]

# ── THINK — eyes looking up ───────────────────────────────────────
THINK = [
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,1,1,1,1,1,1,_,_],
    [5,5,5,2,5,5,5,2,5,5,5],
    [5,5,5,5,5,5,5,5,5,5,5],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
]

# ── SLEEP — closed eyes ──────────────────────────────────────────
SLEEP_1 = [
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,3,1,1,1,3,1,_,_],
    [5,5,5,5,5,5,5,5,5,5,5],
    [5,5,5,5,5,5,5,5,5,5,5],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
]

SLEEP_2 = [
    [_,_,_,_,_,_,_,_,_,_,_],
    [_,_,1,1,1,1,1,1,1,_,_],
    [5,5,5,3,5,5,5,3,5,5,5],
    [5,5,5,5,5,5,5,5,5,5,5],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,_,5,5,_,5,5,_,_,_],
    [_,_,_,5,5,_,5,5,_,_,_],
]

# ── SAD — droopy eyes ─────────────────────────────────────────────
SAD = [
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,1,1,1,1,1,1,_,_],
    [5,5,5,5,2,5,2,5,5,5,5],
    [5,5,5,5,5,5,5,5,5,5,5],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
]

# ── DANCE — tilted side to side ───────────────────────────────────
DANCE_L = [
    [_,1,1,1,1,1,1,1,_,_,_],
    [_,1,2,1,1,1,2,1,_,_,_],
    [5,5,5,5,5,5,5,5,5,_,_],
    [5,5,5,5,5,5,5,5,5,_,_],
    [_,1,1,1,1,1,1,1,_,_,_],
    [_,1,1,1,1,1,1,1,_,_,_],
    [_,5,_,5,_,5,_,5,_,_,_],
    [5,_,_,5,_,_,_,5,_,_,_],
]

DANCE_R = [
    [_,_,_,1,1,1,1,1,1,1,_],
    [_,_,_,1,2,1,1,1,2,1,_],
    [_,_,5,5,5,5,5,5,5,5,5],
    [_,_,5,5,5,5,5,5,5,5,5],
    [_,_,_,1,1,1,1,1,1,1,_],
    [_,_,_,1,1,1,1,1,1,1,_],
    [_,_,_,5,_,5,_,5,_,5,_],
    [_,_,_,5,_,_,_,5,_,_,5],
]

# ── WAVE — arm extends out ────────────────────────────────────────
WAVE_1 = [
    [_,_,1,1,1,1,1,1,1,_,_,_,_],
    [_,_,1,2,1,1,1,2,1,_,_,_,_],
    [5,5,5,5,5,5,5,5,5,5,5,_,_],
    [5,5,5,5,5,5,5,5,5,5,_,5,_],
    [_,_,1,1,1,1,1,1,1,_,5,_,_],
    [_,_,1,1,1,1,1,1,1,_,_,_,_],
    [_,_,5,_,5,_,5,_,5,_,_,_,_],
    [_,_,5,_,5,_,5,_,5,_,_,_,_],
]

WAVE_2 = [
    [_,_,1,1,1,1,1,1,1,_,_,5,_],
    [_,_,1,2,1,1,1,2,1,_,5,_,_],
    [5,5,5,5,5,5,5,5,5,5,5,_,_],
    [5,5,5,5,5,5,5,5,5,5,_,_,_],
    [_,_,1,1,1,1,1,1,1,_,_,_,_],
    [_,_,1,1,1,1,1,1,1,_,_,_,_],
    [_,_,5,_,5,_,5,_,5,_,_,_,_],
    [_,_,5,_,5,_,5,_,5,_,_,_,_],
]

# ── SPARKLE — claw extends right, happy eyes (from GIF) ──────────
SPARKLE_1 = [
    [_,_,1,1,1,1,1,1,1,_,_,_,_,_,_],
    [_,_,1,3,1,1,1,3,1,_,_,_,_,_,_],
    [5,5,5,5,5,5,5,5,5,5,5,_,_,_,_],
    [5,5,5,5,5,5,5,5,5,5,_,4,_,_,_],
    [_,_,1,1,1,1,1,1,1,_,_,_,4,_,_],
    [_,_,1,1,1,1,1,1,1,_,_,_,_,_,_],
    [_,_,5,_,5,_,5,_,5,_,_,_,_,_,_],
    [_,_,5,_,5,_,5,_,5,_,_,_,_,_,_],
]

SPARKLE_2 = [
    [_,_,1,1,1,1,1,1,1,_,_,_,_,_,_],
    [_,_,1,3,1,1,1,3,1,_,_,_,_,_,_],
    [5,5,5,5,5,5,5,5,5,5,4,_,_,_,_],
    [5,5,5,5,5,5,5,5,5,5,_,4,_,_,_],
    [_,_,1,1,1,1,1,1,1,_,_,_,_,_,_],
    [_,_,1,1,1,1,1,1,1,_,_,_,_,_,_],
    [_,_,5,_,5,_,5,_,5,_,_,_,_,_,_],
    [_,_,5,_,5,_,5,_,5,_,_,_,_,_,_],
]

# ── LOOK left/right — eye shift ──────────────────────────────────
LOOK_L = [
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,2,1,1,1,2,1,1,_,_],
    [5,5,5,5,5,5,5,5,5,5,5],
    [5,5,5,5,5,5,5,5,5,5,5],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
]

LOOK_R = [
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,1,2,1,1,1,2,_,_],
    [5,5,5,5,5,5,5,5,5,5,5],
    [5,5,5,5,5,5,5,5,5,5,5],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
]

# ── DIZZY — after tripping ───────────────────────────────────────
DIZZY = [
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,3,2,1,1,2,3,1,_,_],
    [5,5,2,3,5,5,3,2,5,5,5],
    [5,5,5,5,5,5,5,5,5,5,5],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,1,1,1,1,1,1,1,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
    [_,_,5,_,5,_,5,_,5,_,_],
]


# ── Canvas size ────────────────────────────────────────────────────
CANVAS_W = 15 * PX + 60   # room for widest sprite + sparkle particles
CANVAS_H = 10 * PX + 50   # room for jump offset + particles


class ClaudePet(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(CANVAS_W, CANVAS_H)

        screen = QApplication.primaryScreen().geometry()
        self.move(screen.width() - CANVAS_W - 50,
                  screen.height() - CANVAS_H - 25)

        # Pre-render sprites
        self.sprites = {}
        for name, grid in {
            'idle_1': IDLE_1, 'idle_2': IDLE_2, 'blink': BLINK,
            'walk_r1': WALK_R1, 'walk_r2': WALK_R2,
            'walk_l1': WALK_L1, 'walk_l2': WALK_L2,
            'jump': JUMP_UP, 'land': LAND,
            'happy_1': HAPPY_1, 'happy_2': HAPPY_2,
            'think': THINK,
            'sleep_1': SLEEP_1, 'sleep_2': SLEEP_2,
            'sad': SAD,
            'dance_l': DANCE_L, 'dance_r': DANCE_R,
            'wave_1': WAVE_1, 'wave_2': WAVE_2,
            'sparkle_1': SPARKLE_1, 'sparkle_2': SPARKLE_2,
            'look_l': LOOK_L, 'look_r': LOOK_R,
            'dizzy': DIZZY,
        }.items():
            self.sprites[name] = render(grid)[0]

        # State
        self.state = 'idle'
        self.frame = 'idle_1'
        self.tick = 0
        self.duration = 0
        self.oy = 0  # y offset
        self.walk_dir = 0
        self.is_blinking = False
        self.particles = []

        # Timers
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(60)

        self.action_timer = QTimer(self)
        self.action_timer.timeout.connect(self._random_action)
        self.action_timer.start(random.randint(1500, 4000))

        self.blink_timer = QTimer(self)
        self.blink_timer.timeout.connect(self._blink)
        self.blink_timer.start(random.randint(2500, 5000))

        # Drag
        self._drag_pos = None
        self._dragged = False

        # Tray
        self.tray = QSystemTrayIcon(QIcon(self.sprites['idle_1']), self)
        self.tray.setToolTip("Clawd")
        m = QMenu()
        m.addAction("Claude Code").triggered.connect(self.open_claude_code)
        m.addAction("claude.ai").triggered.connect(self.open_claude_web)
        m.addSeparator()
        m.addAction("Quit").triggered.connect(QApplication.quit)
        self.tray.setContextMenu(m)
        self.tray.show()

    # ── Tick ────────────────────────────────────────────────────────

    def _tick(self):
        self.tick += 1

        if self.state == 'idle':
            self.frame = 'idle_1' if (self.tick % 10) < 5 else 'idle_2'
            self.oy = int(math.sin(self.tick * 0.1) * 1.5)

        elif self.state == 'walk':
            step = self.tick % 8
            if self.walk_dir > 0:
                self.frame = 'walk_r1' if step < 4 else 'walk_r2'
            else:
                self.frame = 'walk_l1' if step < 4 else 'walk_l2'
            new_x = self.pos().x() + self.walk_dir * 2
            sw = QApplication.primaryScreen().geometry().width()
            self.move(max(0, min(new_x, sw - CANVAS_W)), self.pos().y())
            self.oy = int(abs(math.sin(self.tick * 0.5)) * -2)
            if self.tick > self.duration:
                self._idle()

        elif self.state == 'jump':
            t = self.tick
            if t < 4:
                self.frame = 'land'
                self.oy = 4
            elif t < 14:
                self.frame = 'jump'
                self.oy = -int(math.sin((t - 4) / 10.0 * math.pi) * 35)
            elif t < 18:
                self.frame = 'land'
                self.oy = 4
            else:
                self._idle()

        elif self.state == 'dance':
            b = self.tick % 8
            if b < 2: self.frame = 'dance_l'
            elif b < 4: self.frame = 'idle_1'
            elif b < 6: self.frame = 'dance_r'
            else: self.frame = 'idle_2'
            self.oy = int(abs(math.sin(self.tick * 0.4)) * -4)
            if self.tick > self.duration:
                self._idle()

        elif self.state == 'happy':
            self.frame = 'happy_1' if (self.tick % 6) < 3 else 'happy_2'
            self.oy = int(abs(math.sin(self.tick * 0.3)) * -3)
            if self.tick % 8 == 0:
                self.particles.append({
                    'x': 5*PX + random.choice([-1,1]) * random.randint(2,5)*PX,
                    'y': -random.randint(0,2)*PX, 'age': 0,
                    'char': random.choice(['*', '+']),
                    'color': random.choice([6, 8])
                })
            if self.tick > self.duration:
                self._idle()

        elif self.state == 'think':
            self.frame = 'think'
            if self.tick % 12 == 0:
                self.particles.append({
                    'x': 8*PX, 'y': -PX, 'age': 0,
                    'char': random.choice(['.', '..', '...']),
                    'color': 7
                })
            if self.tick > self.duration:
                self._idle()

        elif self.state == 'sleep':
            self.frame = 'sleep_1' if (self.tick % 14) < 7 else 'sleep_2'
            self.oy = int(math.sin(self.tick * 0.06) * 1)
            if self.tick % 20 == 0:
                self.particles.append({
                    'x': 8*PX, 'y': -PX, 'age': 0, 'char': 'z', 'color': 7
                })
            if self.tick % 20 == 10:
                self.particles.append({
                    'x': 9*PX, 'y': -2*PX, 'age': 0, 'char': 'Z', 'color': 7
                })
            if self.tick > self.duration:
                self._idle()

        elif self.state == 'wave':
            self.frame = 'wave_1' if (self.tick % 8) < 4 else 'wave_2'
            if self.tick > self.duration:
                self._idle()

        elif self.state == 'sparkle':
            # Matches the clawd_sparkle.gif — claw extends, golden sparkles burst
            t = self.tick
            if t < 8:
                self.frame = 'sparkle_1'
            elif t < 16:
                self.frame = 'sparkle_2'
            else:
                self.frame = 'sparkle_1' if (t % 6) < 3 else 'sparkle_2'
            # Emit sparkle particles from claw tip
            if t > 6 and t % 3 == 0:
                cx = 12 * PX  # claw tip area
                cy = 2 * PX
                for _ in range(random.randint(1, 3)):
                    angle = random.uniform(-1.2, 1.2)
                    speed = random.uniform(1.5, 4.0)
                    self.particles.append({
                        'x': cx + random.randint(-PX, PX),
                        'y': cy + random.randint(-PX, PX),
                        'age': 0,
                        'char': random.choice(['*', '+', '.']),
                        'color': random.choice([6, 6, 8]),
                        'vx': math.cos(angle) * speed,
                        'vy': math.sin(angle) * speed - 1.5,
                    })
            if self.tick > self.duration:
                self._idle()

        elif self.state == 'look':
            s = self.tick % 24
            if s < 6: self.frame = 'look_l'
            elif s < 10: self.frame = 'idle_1'
            elif s < 16: self.frame = 'look_r'
            else: self.frame = 'idle_1'
            if self.tick > self.duration:
                self._idle()

        elif self.state == 'trip':
            t = self.tick
            if t < 6:
                self.frame = 'walk_r1'
            elif t < 10:
                self.frame = 'land'
                self.oy = 5
            elif t < 30:
                self.frame = 'dizzy'
                self.oy = 3
            else:
                self._idle()

        elif self.state == 'sad':
            self.frame = 'sad'
            if self.tick % 22 == 0:
                self.particles.append({
                    'x': 8*PX, 'y': 0, 'age': 0, 'char': "'", 'color': 7
                })
            if self.tick > self.duration:
                self._idle()

        # Age particles
        new_particles = []
        for p in self.particles:
            if p['age'] < 50:
                vx = p.get('vx', -0.2)
                vy = p.get('vy', -0.6)
                new_particles.append({
                    **p,
                    'x': p['x'] + vx,
                    'y': p['y'] + vy,
                    'age': p['age'] + 1,
                })
        self.particles = new_particles
        self.update()

    def _idle(self):
        self.state = 'idle'
        self.tick = 0
        self.oy = 0
        self.particles.clear()

    def _random_action(self):
        if self.state != 'idle':
            return
        a = random.choices(
            ['walk','jump','dance','happy','think','sleep','wave','look','trip','sad','sparkle'],
            weights=[18, 15, 12, 10, 10, 8, 12, 18, 4, 3, 14],
            k=1)[0]
        self.state = a
        self.tick = 0
        self.oy = 0
        durations = {
            'walk': (50,120), 'dance': (40,80), 'happy': (30,60),
            'think': (40,70), 'sleep': (80,160), 'wave': (24,48),
            'look': (24,48), 'sad': (30,50), 'sparkle': (40,70),
        }
        if a in durations:
            self.duration = random.randint(*durations[a])
        if a == 'walk':
            self.walk_dir = random.choice([-1, 1])
        self.action_timer.setInterval(random.randint(2000, 5000))

    def _blink(self):
        if self.state == 'idle':
            self.is_blinking = True
            self.update()
            QTimer.singleShot(100, lambda: (setattr(self, 'is_blinking', False), self.update()))
        self.blink_timer.setInterval(random.randint(2000, 5000))

    # ── Paint ───────────────────────────────────────────────────────

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        key = 'blink' if (self.is_blinking and self.state == 'idle') else self.frame
        dx, dy = 15, 25 + int(self.oy)
        p.drawPixmap(dx, dy, self.sprites[key])

        for pt in self.particles:
            alpha = max(0, 255 - int(pt['age'] * 5))
            color = QColor(P[pt['color']])
            color.setAlpha(alpha)
            sz = 9 + (pt['age'] // 12) * 2
            f = p.font()
            f.setPixelSize(sz)
            f.setBold(True)
            p.setFont(f)
            p.setPen(color)
            p.drawText(int(dx + pt['x']), int(dy + pt['y']), pt['char'])

    # ── Mouse ───────────────────────────────────────────────────────

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPos() - self.frameGeometry().topLeft()
            self._dragged = False
        e.accept()

    def mouseMoveEvent(self, e):
        if self._drag_pos and e.buttons() & Qt.LeftButton:
            np_ = e.globalPos() - self._drag_pos
            if (np_ - self.pos()).manhattanLength() > 4:
                self._dragged = True
            self.move(np_)
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            if not self._dragged:
                self.open_claude_code()
            self._drag_pos = None
        e.accept()

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu { background:#2b2b2b; color:#e0e0e0; border:1px solid #555;
                    border-radius:4px; padding:4px; font-size:13px; }
            QMenu::item { padding:6px 20px; }
            QMenu::item:selected { background:#d4845a; }
        """)
        menu.addAction("Claude Code").triggered.connect(self.open_claude_code)
        menu.addAction("claude.ai").triggered.connect(self.open_claude_web)
        menu.addSeparator()
        for label, act, dur in [
            ("Jump!", 'jump', 0), ("Dance!", 'dance', 60),
            ("Wave", 'wave', 36), ("Think", 'think', 50),
            ("Sleep", 'sleep', 120), ("Sparkle!", 'sparkle', 55),
        ]:
            menu.addAction(label).triggered.connect(
                lambda _, a=act, d=dur: self._force(a, d))
        menu.addSeparator()
        menu.addAction("Quit").triggered.connect(QApplication.quit)
        menu.exec_(e.globalPos())

    def _force(self, action, duration):
        self.state = action
        self.tick = 0
        self.duration = duration
        self.oy = 0
        self.particles.clear()
        if action == 'walk':
            self.walk_dir = random.choice([-1, 1])

    def open_claude_code(self):
        subprocess.Popen(["cmd","/c","start","cmd","/k","claude"],
                         creationflags=subprocess.CREATE_NO_WINDOW)

    def open_claude_web(self):
        import webbrowser
        webbrowser.open("https://claude.ai")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    pet = ClaudePet()
    pet.show()
    sys.exit(app.exec_())
