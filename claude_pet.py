"""
Clawd Desktop Pet: the Claude Code mascot, living on your desktop.

Every animation is traced from Anthropic's official Clawd art
(tools/trace_official.py turns their GIFs into sprites/clawd.json), so he
moves the way he does in the Claude apps: crab-walking, jumping, waving,
typing on a laptop, riding a cloud, racing a kart, peeking in from the edge
of the screen. With the hooks installed (tools/install_hooks.py) he follows
Claude Code: typing while it works, calling you over when it needs a
permission, celebrating when it's done.

Left-click: open Claude Code (the Code tab of the Claude app)
Right-click: menu    Drag: pick him up and throw him    Stroke him: he likes it
"""

import ast
import calendar
import collections
import datetime
import json
import math
import os
import random
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import webbrowser

# A desktop pet is a window that moves itself, which Wayland doesn't allow,
# so run through XWayland unless QT_QPA_PLATFORM is already set. Programs we
# launch get the original environment back (see launch_env).
_FORCED_XCB = False
if (sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY")
        and "QT_QPA_PLATFORM" not in os.environ):
    os.environ["QT_QPA_PLATFORM"] = "xcb"
    _FORCED_XCB = True

from PyQt6.QtCore import (QDate, QElapsedTimer, QFileSystemWatcher, QObject, QPoint, QRect, QSettings, Qt,
                          QTime, QTimer,
                          pyqtClassInfo, pyqtSlot)
from PyQt6.QtGui import (QActionGroup, QBitmap, QCursor, QGuiApplication, QIcon, QImage, QPainter,
                         QPixmap, QRegion)
from PyQt6.QtNetwork import QLocalServer
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout,
                             QDateEdit, QGroupBox, QHBoxLayout, QLabel, QMenu, QPushButton, QSpinBox,
                             QSystemTrayIcon, QTimeEdit, QVBoxLayout, QWidget)

HERE = os.path.dirname(os.path.abspath(__file__))
SPRITES = os.path.join(HERE, "sprites", "clawd.json")

SCALES = {"Small": 3, "Medium": 4, "Large": 6, "Huge": 8}   # screen px per sprite pixel
DEFAULT_SCALE = 4
TICK_MS = 16
IDLE_TICK = 250               # ms between ticks while nothing on screen needs moving
PHYS_STEP = 4                 # ms: physics runs in steps this small, whatever the frame rate
BOUNCE = 0.3                  # a hard landing bounces back up with this much of its speed...
BOUNCE_MIN = 180              # ...if it's faster than this (cells/s): a fall of ~20 cells or more
SKID = 6.0                    # 1/s: how quickly a sideways landing skids to a stop
AIR_DRAG = 0.35               # 1/s: the air slowing him sideways
CHUTE_FALL = 32               # cells/s: how fast he comes down under a parachute or umbrella
CHUTE_SWAY = (2.5, 0.9)       # cells, Hz: swaying from side to side on the way down
THROW_WINDOW = 100            # ms: a throw goes as fast as the drag moved in its last moments
MASK_BLOCK = 4                # cells: moving particles shape the window in blocks this big
PIXMAP_CACHE = 120            # scaled frames kept ready (the ones used most recently)

# Speeds in sprite pixels per second, so they grow with the pet. The tempos
# speed up the official walk cycle so his legs keep up with the pace.
WALK_SPEED, WALK_TEMPO = 15, 0.7
TRIP_SPEED, TRIP_TEMPO = 24, 0.5
CLOUD_SPEED = 30
CLOUD_RISE = 28              # riding the cloud up to a desktop icon
CLOUD_LIFT = 7               # sprite pixels he sits up in his cloud (as in Clawd-Cloud)
RACE_SPEED = 45
CLIMB_SPEED = 14
GRAVITY = 700

# What he does on his own, and how often.
WEIGHTS = {
    "walk": 30, "wave": 8, "jump": 5, "jump_happy": 5, "dance": 5, "laptop": 8,
    "sparkler": 4, "cloud": 5, "race": 4, "lurk": 7, "sleep": 3,
}
ACTIONS = list(WEIGHTS)
BETWEEN_SCREENS = ["climb", "leap"]        # only when he's next to another screen
LABELS = {
    "walk": "Walk", "wave": "Wave", "jump": "Jump", "jump_happy": "Happy jump",
    "dance": "Dance", "laptop": "Laptop", "sparkler": "Sparkler",
    "cloud": "Ride the cloud", "race": "Go karting", "lurk": "Peek from the edge",
    "sleep": "Nap", "climb": "Climb to the other screen", "leap": "Leap to the other screen",
    "visit": "Check out a desktop icon", "read": "Read something from a desktop folder",
    "work": "Typing (Claude Code working)", "attention": "Calling you over (a permission)",
    "celebrate": "Celebrating (Claude Code done)",
    "perch_window": "Hop up onto a window", "hop_down": "Hop down from a window",
    "climb_window": "Climb up the side of a window", "climb_down": "Climb down the side of a window",
    "window_jump": "Jump to another window",
    "yawn": "Yawn and stretch", "morning": "Good morning (stretch and coffee)", "coffee": "Coffee",
    "birthday": "Birthday party",
    "remind_break": "Reminder: take a break", "remind_water": "Reminder: drink some water",
    "grab": "Grab the pointer",
}
ON_A_WINDOW = ["walk", "wave", "jump", "jump_happy", "dance", "laptop", "sparkler", "sleep", "yawn"]
LIVELY = ("dance", "race", "sparkler", "jump", "jump_happy", "cloud")   # calmer at night
BOUNCY = {"walk", "dance", "jump", "jump_happy", "celebrate", "race", "wave"}   # a hat's pom-pom swings
PARTIES = ("celebrate", "new_year", "birthday")   # celebrations: the party hat, confetti
AROUND_THE_DESKTOP = BETWEEN_SCREENS + ["perch_window", "hop_down", "climb_window", "climb_down", "window_jump",
                                        "visit", "read"]
CLAUDE_PREVIEWS = ["work", "attention", "celebrate"]
TIME_SCENES = ["yawn", "morning", "coffee", "birthday", "remind_break", "remind_water"]
POINTER_SCENES = ["grab"]
PLAYABLE = set(ACTIONS + AROUND_THE_DESKTOP + CLAUDE_PREVIEWS + TIME_SCENES + POINTER_SCENES + ["idle"])

EXTRAS_FILE = os.path.join(HERE, "sprites", "extras.py")
HATS = {"santa_hat": "Santa hat", "pumpkin_hat": "Pumpkin", "party_hat": "Party hat", "nightcap": "Nightcap"}

# ── Settings ────────────────────────────────────────────────────────

PREF_DEFAULTS = {
    "activity": "normal",     # calm | normal | lively: how long he rests between scenes
    "scenes_off": [],         # scenes he doesn't do on his own (the Play menu still has them all)
    "quiet": False,           # stays put and keeps to himself; Claude Code still shows
    "claude": True,           # follows Claude Code through its hooks
    "pointer": True,          # watches the pointer
    "petting": True,          # stroke him for hearts
    "grab": True,             # jumps up and hangs off a pointer that hangs around above him
    "duck": True,             # drops out of sight for fullscreen windows
    "day_cycle": True,        # yawns and naps at night, coffee in the morning
    "night_from": 22 * 60,    # minutes after midnight: night starts...
    "night_to": 6 * 60,       # ...and ends (and the morning begins)...
    "morning_to": 11 * 60,    # ...and the morning ends
    "seasons": True,          # hats and extras on holidays
    "hat": "auto",            # auto (by date and time) | none | one of HATS
    "celebrate": False,       # a party on your birthday...
    "birthday": "",           # ...this day ("MM-DD")
    "breaks": True,           # break reminders...
    "break_every": 60,        # ...after this many minutes at the computer
    "water": True,            # water reminders...
    "water_every": 45,        # ...this often (minutes)
}
ACTIVITY = {"calm": 2.0, "normal": 1.0, "lively": 0.08}     # multiplies the rest between scenes
OWN_SCENES = [["walk", "wave", "jump", "jump_happy", "dance", "laptop"],
              ["sparkler", "cloud", "race", "lurk", "sleep"],
              ["climb", "leap", "perch_window", "climb_window", "window_jump", "visit", "read"]]
OWN_SCENE_ACTIONS = ({k for col in OWN_SCENES for k in col} - {"sleep"}) | {"yawn", "morning", "coffee"}


class Prefs:
    """His settings with their defaults, kept in QSettings (in memory without one)."""

    def __init__(self, settings=None):
        self.settings = settings
        self._mem = {}
        self.version = 0                       # goes up with every change

    def __getitem__(self, key):
        default = PREF_DEFAULTS[key]
        if self.settings is None:
            return self._mem.get(key, default)
        v = self.settings.value("prefs/" + key, default)
        # An INI file hands everything back as strings, and a one-item list as its item.
        if isinstance(default, bool):
            return v if isinstance(v, bool) else str(v).lower() in ("true", "1")
        if isinstance(default, int):
            try:
                return int(v)
            except (TypeError, ValueError):
                return default
        if isinstance(default, list):
            return [] if v in (None, "") else [v] if isinstance(v, str) else list(v)
        return str(v)

    def __setitem__(self, key, value):
        self.version += 1
        if self.settings is None:
            self._mem[key] = value
        else:
            self.settings.setValue("prefs/" + key, value)

# Where the eyes sit in the idle pose (top-left of each 2x2 eye).
EYES = ((6, 2), (16, 2))

BODY = (216, 119, 86)         # Clawd's own colour, #d87756
INK = (20, 20, 19)            # Anthropic's near-black, as in the official art
IVORY = (250, 249, 245)
BLUE = (106, 155, 204)        # Claude Code's own "professional blue"
PINK = (232, 91, 106)
GOLD = (238, 200, 117)        # the sparkler's sparks
OLIVE = (120, 140, 93)        # Anthropic's olive green: the Done button
OLIVE_DARK = (92, 108, 70)
GREY = (156, 154, 146)
RAIL_DARK = (77, 76, 72)      # the kart's greys
RAIL_LIGHT = (156, 154, 146)

# Little extras drawn on the same pixel grid as Clawd.
GLYPHS = {
    "z_small": (["##.", ".#.", ".##"], {"#": BLUE}),
    "z_big": (["####", "..#.", ".#..", "####"], {"#": BLUE}),
    "heart": ([".#.#.", "#####", "#####", ".###.", "..#.."], {"#": PINK}),
    # a reminder's Done button: ink edge like the bubbles, olive face, ivory tick
    "done_button": ([".##########.",
                     "#=======++=#",
                     "#======++==#",
                     "#=++==++===#",
                     "#==++++====#",
                     "#===++=====#",
                     "#==========#",
                     ".##########."], {"#": INK, "=": OLIVE, "+": IVORY}),
    "done_button_pressed": ([".##########.",
                             "#==========#",
                             "#=======++=#",
                             "#======++==#",
                             "#=++==++===#",
                             "#==++++====#",
                             "#===++=====#",
                             ".##########."], {"#": INK, "=": OLIVE_DARK, "+": IVORY}),
    "bubble": ([".#######.",
                "#+++++++#",
                "#+++@+++#",
                "#+++@+++#",
                "#+++@+++#",
                "#+++++++#",
                "#+++@+++#",
                "#+++++++#",
                ".###+###.",
                "...#+#...",
                "....#...."], {"#": INK, "+": IVORY, "@": INK}),
    # reading a story
    "drop": ([".#.", "###", "###", ".#."], {"#": BLUE}),
    "excl": (["##", "##", "##", "..", "##"], {"#": INK}),
    "question": ([".###.", "#...#", "...#.", "..#..", ".....", "..#.."], {"#": INK}),
    "haha": (["#.#..#..#.#..#.", "#.#.#.#.#.#.#.#", "###.###.###.###", "#.#.#.#.#.#.#.#",
              "#.#.#.#.#.#.#.#"], {"#": INK}),
    "spark": ([".#.", "###", ".#."], {"#": GOLD}),
    "scrap": (["+++", "+c+", "+++"], {"+": IVORY, "c": GREY}),
}
# Props he holds or wears, positioned in sprite pixels from the idle pose's top-left.
PROPS = {
    "glasses": (["########..########",      # a pixel wider than his head each side
                 "#......####......#",
                 "#......#..#......#",
                 "#......#..#......#",
                 "########..########"], {"#": INK}),
    "page": (["+++++c..",
              "++++++c.",
              "+cccc+++",
              "++++++++",
              "+ccccc++",
              "++++++++",
              "+cccc+++",
              "++++++++",
              "+ccc++++",
              "++++++++"], {"+": IVORY, "c": GREY}),
    "magnifier": (["..####....",
                   ".#....#...",
                   "#.++...#..",
                   "#.+....#..",
                   "#......#..",
                   "#......#..",
                   ".#....#...",
                   "..####h...",
                   "......hh..",
                   ".......hh.",
                   "........hh"], {"#": INK, "+": IVORY, "h": RAIL_DARK}),
}
MAGNIFIER_LENS = (3.5, 3.5)   # the middle of the magnifying glass's lens, in its own pixels
GLASSES_AT = (3, 1)           # on his eyes
PAGE_AT = (8, 6)              # held in front of him, below the glasses
EMOTIONS = ["surprised", "sad", "laugh", "love", "scared", "confused"]
LADDER_W = 10
LADDER_PALETTE = {"d": RAIL_DARK, "l": RAIL_LIGHT}

CURSOR_NEAR = 70              # sprite pixels: how close the pointer must be for him to watch it
CLIMB_JUMP = 20               # cells: a window's side ending this far above his feet, he jumps up to
LEAP_GAP = 80                 # cells: how far across he'll jump to another window top...
LEAP_UP = 50                  # ...at most this much higher...
LEAP_DOWN = 150               # ...or this much lower
WINDOW_CLIMB = 1.3            # up a window's side a little quicker than a ladder
GLANCE_MS = 1400
ARMS_UP = ("anim", "jump", 2, False)   # a folder is being dragged over him: "for me?"
GRAB_REACH = 45               # cells above his head he'll jump up to the pointer from
GRAB_SIDE = 40                # ...and to either side of him (he walks under it first)
GRAB_LINGER = 700             # ms the pointer hangs around near him before he might grab it
GRAB_CHANCE = 0.8             # ...and then he does, this often
GRAB_COOLDOWN = 45_000        # before he grabs it again on his own (a third of that when lively)
GRAB_WHILE = {"idle", "walk", "wave", "jump", "jump_happy", "dance", "sparkler", "laptop", "yawn"}
GRAB_WALK = 80                # cells he'll walk to get under the pointer; further, he leaps for it
GRAB_SURE_SIDE = 4            # held right above him (this close to over his body)...
GRAB_HOLD = 450               # ...for this long (ms): he always goes for it
DANGLE_FOR = (15_000, 45_000) # how long he hangs on, unless you shake him off
SHAKE_FLIPS = 4               # quick back-and-forths within SHAKE_MS shake him off
SHAKE_MS = 1300
SHAKE_SPEED = 700             # px/s: how fast a move must be to count toward a shake
STRAIGHT, LEAN_1, LEAN_2, KICK_A, KICK_B, ONE_HAND = range(6)   # the dangle frames
SPIN_STEPS = 48               # frames all the way round when he swings (7.5 degrees apart)
SPIN_FROM = 0.52              # radians: past this he's spinning, not just swinging
SWING_DAMP = 1.5              # 1/s: how quickly his swinging dies down
AWAY = 10 * 60_000            # no pointer movement or prompt for this long: you're away (a break)
PRESENT = 90_000              # reminders only come while you've done something this recently
REMIND_LOUD = 120_000         # a reminder's first two minutes are loud; then he just holds it up
REMINDERS = ("remind_water", "remind_break")
NEVER_GRAB = REMINDERS + ("settings", "grab", "duck", "attention", "held", "fall")
REMINDER_BITS = {"water_bubble", "break_bubble", "done_button", "done_button_pressed"}
BUBBLE_AT = (23, -16)         # a reminder's bubble: over his right shoulder, clear of hats and bottles
DONE_AT = (28, -1)            # its Done button: under the bubble, clear of the bottle at his side
BUBBLES = {"bubble", "water_bubble", "break_bubble"}
MUG_AT = (22, 1)              # the mug in his right hand, handle in his grip (idle cells)
BOTTLE_UP = (16, -15)         # the bottle held up high in the cheering frame
BOTTLE_SIDE = (21, -1)        # ...and at his side, standing
BOTTLE_CROUCH = (21, 3)       # ...and at his side, crouching
DRAG_FORGET = 10_000                   # ms without drag events before he stops waiting              # a look up from the laptop at a nearby pointer lasts this long
GLANCE_COOLDOWN = 6000        # ...and won't happen again for this long (just a moment each time)
TYPING_EYES = ((12, 12), (20, 12))    # the eyes in Clawd-Laptop's typing frames (3/4 view)
REST = (8000, 20000)          # idle time between the things he does on his own
REST_SLEEPY = (15000, 35000)  # ...once nothing has happened for WIND_DOWN
WIND_DOWN = 5 * 60_000

# Deep links the Claude desktop app itself uses (its launcher actions).
CLAUDE_LINKS = {
    "continue": "claude://code/continue?session=last",   # your most recent Code session
    "new": "claude://code/new",
    "needs-input": "claude://code/needs-input",          # the session waiting on a permission
}

# Claude Code hook events, grouped by what they mean for Clawd.
BUSY_EVENTS = {"UserPromptSubmit", "PreToolUse", "PostToolUse", "PostToolUseFailure",
               "SubagentStart", "SubagentStop", "PreCompact", "PostCompact"}
ATTENTION_KINDS = {"permission_prompt", "elicitation_dialog"}
# What he does while Claude Code works, by the tool it's using (everything else: the laptop).
TOOL_STYLES = {"Read": "read", "NotebookRead": "read",
               "Grep": "search", "Glob": "search", "LS": "search",
               "WebSearch": "web", "WebFetch": "web"}
STYLE_HOLD = 4000             # ms a working style is kept before switching (no flickering)

DBUS_SERVICE = "org.clawdpet.Pet"
KWIN_SCRIPT = "clawd-pet-cursor"


def runtime_dir():
    return os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()


def socket_path():
    """Where the pet listens for clawd_hook.py. Keep in sync with the hook."""
    return os.environ.get("CLAWD_PET_SOCKET") or os.path.join(runtime_dir(), "clawd-pet.sock")


# ── Sprites ─────────────────────────────────────────────────────────

def grid_image(rows, palette):
    """Rows of palette keys -> QImage with one pixel per sprite pixel."""
    h, w = len(rows), len(rows[0])
    buf = bytearray(w * h * 4)
    for y, row in enumerate(rows):
        base = y * w * 4
        for x, ch in enumerate(row):
            if ch != ".":
                r, g, b = palette[ch]
                buf[base + x * 4:base + x * 4 + 4] = bytes((r, g, b, 255))
    return QImage(bytes(buf), w, h, w * 4, QImage.Format.Format_RGBA8888).copy()


def load_extras(path=EXTRAS_FILE):
    """sprites/extras.py's EXTRAS (hats and props), read as plain data: the
    file is parsed, never imported or run."""
    with open(path) as f:
        tree = ast.parse(f.read(), path)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "EXTRAS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise ValueError("no EXTRAS in " + path)


PLASMA_DESKTOP_RC = os.path.expanduser("~/.config/plasma-org.kde.plasma.desktop-appletsrc")


def plasma_wallpapers(path=PLASMA_DESKTOP_RC):
    """{containment: (wallpaper plugin, image, slideshow interval in s or None)}
    for Plasma's desktops, from its config (empty if there's none)."""
    import configparser
    cfg = configparser.ConfigParser(strict=False, interpolation=None, delimiters=("=",))
    cfg.optionxform = str
    try:
        cfg.read(path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError):
        return {}
    out = {}
    for sec in cfg.sections():
        m = re.fullmatch(r"Containments\]\[(\d+)", sec)
        if not m or "wallpaperplugin" not in cfg[sec]:
            continue
        cid, plugin = m.group(1), cfg[sec]["wallpaperplugin"]
        general = f"Containments][{cid}][Wallpaper][{plugin}][General"
        opts = cfg[general] if cfg.has_section(general) else {}
        interval = None
        if plugin == "org.kde.slideshow":
            try:
                interval = int(opts.get("SlideInterval", 900))      # Plasma's default: 15 min
            except ValueError:
                interval = 900
        out[cid] = (plugin, opts.get("Image", ""), interval)
    return out


def next_slide_change(walls, now):
    """When the next slideshow picture comes up (epoch seconds), or None. Plasma
    turns slideshows over on the clock: at whole multiples of the interval
    since 1970, the same moment on every screen."""
    times = [(now // iv + 1) * iv for _, _, iv in walls.values() if iv]
    return float(min(times)) if times else None


def wallpapers_differ(before, after):
    """A new picture or wallpaper type on some desktop (not icons moving about)."""
    return {k: v[:2] for k, v in before.items()} != {k: v[:2] for k, v in after.items()}


def wall_clock():
    return datetime.datetime.now()


def season_hat(day):
    """The hat for a date: a party hat for New Year, a Santa hat in December,
    a pumpkin in the week before Halloween."""
    if (day.month, day.day) in ((12, 31), (1, 1)):
        return "party_hat"
    if day.month == 12:
        return "santa_hat"
    if day.month == 10 and day.day >= 24:
        return "pumpkin_hat"
    return None


def rgb(hex_colour):
    return int(hex_colour[1:3], 16), int(hex_colour[3:5], 16), int(hex_colour[5:7], 16)


HEAD_W = 16                   # cells across the top of his head in the idle pose


def head_of(rows, body, home_y=0):
    """Where a hat goes on a frame: (the cell just right of his head's centre
    line, his head's top row), from the widest run of body cells on the
    topmost row that has one of 8 or more (arms are narrower). Peeking in from
    the side, his head is cut off by the frame's edge: then any run touching
    the edge near where his head belongs counts, as a full head's width
    across. None if no head shows."""
    for y, row in enumerate(rows):
        best, x = None, 0
        while x < len(row):
            if row[x] not in body:
                x += 1
                continue
            x0 = x
            while x < len(row) and row[x] in body:
                x += 1
            cut = (x0 == 0 or x == len(row)) and x - x0 >= 2 and y <= home_y + 4
            if (x - x0 >= 8 or cut) and (best is None or x - x0 > best[1] - best[0]):
                best = (x0, x)
        if best is not None:
            x0, x1 = best
            if x0 == 0 and x1 - x0 < HEAD_W:
                x0 = x1 - HEAD_W
            elif x1 == len(row) and x1 - x0 < HEAD_W:
                x1 = x0 + HEAD_W
            return (x0 + x1) // 2, y
    return None


def flipped(img):
    if hasattr(img, "flipped"):                      # Qt 6.9+
        return img.flipped(Qt.Orientation.Horizontal)
    return img.mirrored(True, False)


def pose_rows(idle, kind):
    """Expressions for the idle pose, drawn the way the official art draws them."""
    g = [list(r) for r in idle]
    body, ink = idle[0][4], idle[EYES[0][1]][EYES[0][0]]

    def clear_eyes():
        for ex, ey in EYES:
            for dy in (0, 1):
                g[ey + dy][ex] = g[ey + dy][ex + 1] = body

    if kind == "blink":
        # Clawd-Laptop's wink: a 3-pixel line on the eye's lower row, reaching inward
        clear_eyes()
        (lx, ly), (rx, ry) = EYES
        for x in (lx, lx + 1, lx + 2):
            g[ly + 1][x] = ink
        for x in (rx - 1, rx, rx + 1):
            g[ry + 1][x] = ink
    elif kind in ("look_l", "look_r"):
        clear_eyes()
        d = -1 if kind == "look_l" else 1
        for ex, ey in EYES:
            for dy in (0, 1):
                g[ey + dy][ex + d] = g[ey + dy][ex + d + 1] = ink
    elif kind == "happy":
        # the ^ ^ eyes from Clawd-Dancing
        clear_eyes()
        for ex, ey in EYES:
            g[ey][ex] = g[ey][ex + 1] = ink
            g[ey + 1][ex - 1] = g[ey + 1][ex + 2] = ink
    elif kind in ("read", "read_l", "read_r"):
        # looking down at a page (and along its lines)
        clear_eyes()
        d = {"read": 0, "read_l": -1, "read_r": 1}[kind]
        for ex, ey in EYES:
            for dy in (1, 2):
                g[ey + dy][ex + d] = g[ey + dy][ex + d + 1] = ink
    elif kind == "surprised":
        # tall eyes, like Clawd-Jumping's at the top of a leap
        clear_eyes()
        for ex, ey in EYES:
            for dy in (0, 1, 2):
                g[ey + dy][ex] = g[ey + dy][ex + 1] = ink
    elif kind == "back":
        # turned round: from behind he's his silhouette, no eyes
        clear_eyes()
    elif kind == "sad":
        # eyes slanting down at the outer corners, a little lower
        clear_eyes()
        (lx, ly), (rx, ry) = EYES
        g[ly + 1][lx + 1] = g[ly + 2][lx] = ink
        g[ry + 1][rx] = g[ry + 2][rx + 1] = ink
    return ["".join(r) for r in g]


def climb_rows(idle):
    """A climbing pose from official parts: the idle body with its left arm
    raised the way Clawd-Jumping raises both, the right arm lower down on a
    rung, and the right legs lifted."""
    body = idle[0][4]
    w = len(idle[0])
    rows = [list("." * w) for _ in range(4)] + [list(r) for r in idle]
    for y in range(8, 12):             # the arm rows, 4 lower now
        for x in range(0, 4):
            rows[y][x] = "."
    for y in range(0, 4):              # ...and the left arm up above the head
        for x in range(5, 9):
            rows[y][x] = body
    for x in range(w - 4, w):          # right arm two pixels lower
        rows[8][x] = rows[9][x] = "."
        rows[12][x] = rows[13][x] = body
    for x in (14, 15, 18, 19):
        rows[-1][x] = "."
    return ["".join(r) for r in rows]


def typing_eyes(rows, kind, body, ink):
    """A Clawd-Laptop typing frame with different eyes (3/4 view, so the far
    eye sits on the edge of his face and can't move further right)."""
    g = [list(r) for r in rows]
    for ex, ey in TYPING_EYES:
        for dx in (0, 1):
            for dy in (0, 1):
                g[ey + dy][ex + dx] = body
    (lx, ly), (rx, ry) = TYPING_EYES
    if kind == "happy":
        g[ly][lx] = g[ly][lx + 1] = g[ly + 1][lx - 1] = g[ly + 1][lx + 2] = ink
        g[ry][rx] = g[ry][rx + 1] = g[ry + 1][rx - 1] = ink
    else:                                      # looking up from the screen, left or right
        d = -1 if kind == "look_l" else 0
        for ex, ey in TYPING_EYES:
            for dx in (0, 1):
                for dy in (-1, 0):
                    g[ey + 1 + dy][ex + dx + d] = ink
    return ["".join(r) for r in g]


def dangle_head(eyes):
    """Where a hat goes on a frame with these eyes (their 2x2 top-left cells):
    his head's top is 2 rows above them, its centre line between them."""
    (lx, ly), (rx, ry) = eyes
    return (lx + rx + 2) // 2, min(ly, ry) - 2


def eyes_rows(rows, eyes, kind, body, ink):
    """A frame with its eyes (2x2, top-left cells in `eyes`) redrawn as an
    expression, the way pose_rows draws them on the idle pose."""
    g = [list(r) for r in rows]
    for ex, ey in eyes:
        for dx in (0, 1):
            for dy in (0, 1):
                g[ey + dy][ex + dx] = body

    def put(*cells):
        for x, y in cells:
            g[y][x] = ink
    (lx, ly), (rx, ry) = eyes
    for ex, ey in eyes:
        if kind == "happy":                       # ^ ^
            put((ex, ey), (ex + 1, ey), (ex - 1, ey + 1), (ex + 2, ey + 1))
        elif kind == "surprised":                 # tall
            put(*[(ex + dx, ey + dy) for dx in (0, 1) for dy in (0, 1, 2)])
        elif kind in ("look_l", "look_r"):
            d = -1 if kind == "look_l" else 1
            put(*[(ex + d + dx, ey + dy) for dx in (0, 1) for dy in (0, 1)])
        elif kind != "squeezed":
            put(*[(ex + dx, ey + dy) for dx in (0, 1) for dy in (0, 1)])
    if kind == "squeezed":                        # > <
        put((lx, ly), (lx + 1, ly + 1), (lx, ly + 2), (rx + 1, ry), (rx, ry + 1), (rx + 1, ry + 2))
    return ["".join(r) for r in g]


def scale2x(g):
    """Scale2x (EPX): twice the size, smoothing stair-steps without new colours."""
    h, w = len(g), len(g[0])
    out = [[None] * (2 * w) for _ in range(2 * h)]
    for y in range(h):
        up, row, down = g[y - 1] if y else g[y], g[y], g[y + 1] if y + 1 < h else g[y]
        top, bottom = out[2 * y], out[2 * y + 1]
        for x in range(w):
            p, a, d = row[x], up[x], down[x]
            c = row[x - 1] if x else p
            b = row[x + 1] if x + 1 < w else p
            top[2 * x] = a if c == a and c != d and a != b else p
            top[2 * x + 1] = b if a == b and a != c and b != d else p
            bottom[2 * x] = c if d == c and d != b and c != a else p
            bottom[2 * x + 1] = d if b == d and b != a and d != c else p
    return out


def turned_frames(rows, pivot, steps, size):
    """`rows` turned all the way round about `pivot` (cells) in `steps` steps,
    RotSprite-style: scaled up 8x with Scale2x, turned, and sampled back onto
    the cell grid, so he stays crisp pixel art at any angle. Angle k*360/steps
    swings what hangs below the pivot out to the right; each frame is size x
    size cells with the pivot at its centre."""
    up = [list(r) for r in rows]
    for _ in range(3):
        up = scale2x(up)
    h8, w8, half = len(up), len(up[0]), size / 2
    px, py = pivot
    frames = []
    for k in range(steps):
        a = 2 * math.pi * k / steps
        c, sn = math.cos(a), math.sin(a)
        out = []
        for oy in range(size):
            dy = oy + 0.5 - half
            line = []
            for ox in range(size):
                dx = ox + 0.5 - half
                x = int(math.floor((c * dx - sn * dy + px) * 8))
                y = int(math.floor((sn * dx + c * dy + py) * 8))
                line.append(up[y][x] if 0 <= x < w8 and 0 <= y < h8 else ".")
            out.append("".join(line))
        frames.append(out)
    return frames


def cursor_grip(path=os.path.expanduser("~/.config/kcminputrc")):
    """Where on the pointer he holds on, from its tip: the tail of the arrow
    (measured on Breeze's arrow: tip at (4, 4), tail at about (11, 20) at
    size 24), scaled to the configured cursor size."""
    size = 24
    try:
        with open(path) as f:
            m = re.search(r"^\[Mouse\][^\[]*?^cursorSize=(\d+)", f.read(), re.M | re.S)
        if m:
            size = int(m.group(1))
    except (OSError, ValueError):
        pass
    return round(size * 7 / 24), round(size * 16 / 24)


def poke_rows(idle):
    """Poking with the right arm stretched out: 3 px further, a pixel thinner."""
    body = idle[0][4]
    rows = [list(r) + ["."] * 3 for r in idle]
    for y in (5, 6):
        for x in range(len(idle[0]), len(idle[0]) + 3):
            rows[y][x] = body
    return ["".join(r) for r in rows]


def hang_rows(idle):
    """Hanging by both hands: the idle body with both arms raised the way
    Clawd-Jumping raises them."""
    body = idle[0][4]
    w = len(idle[0])
    rows = [list("." * w) for _ in range(4)] + [list(r) for r in idle]
    for y in range(8, 12):
        for x in list(range(0, 4)) + list(range(w - 4, w)):
            rows[y][x] = "."
    for y in range(0, 4):
        for x in list(range(5, 9)) + list(range(w - 9, w - 5)):
            rows[y][x] = body
    return ["".join(r) for r in rows]


def ladder_rows(height):
    rows = []
    for y in range(height):
        r = ["."] * LADDER_W
        r[0] = r[-1] = "d"
        r[1] = r[-2] = "l"
        if y % 5 == 2:
            r[2:-2] = ["l"] * (LADDER_W - 4)
        elif y % 5 == 3:
            r[2:-2] = ["d"] * (LADDER_W - 4)
        rows.append("".join(r))
    return rows


class Anim:
    """One animation: frames, their timing, and where the idle Clawd stands
    inside them, so every animation lines up with every other."""

    def __init__(self, name, data, palette):
        self.name = name
        self.w, self.h = data["size"]
        self.home = tuple(data["home"])
        self.loop = tuple(data["loop"]) if "loop" in data else None
        self.outro = tuple(data["outro"]) if "outro" in data else None
        self.frames = [grid_image(f["rows"], palette) for f in data["frames"]]
        self.ms = [f["ms"] for f in data["frames"]]
        body = {k for k, v in palette.items() if v == BODY}
        self.heads = [head_of(f["rows"], body, self.home[1]) for f in data["frames"]]


class Sprites:
    def __init__(self, data):
        palette = {k: (int(v[1:3], 16), int(v[3:5], 16), int(v[5:7], 16))
                   for k, v in data["palette"].items()}
        self.iw, self.ih = data["idle_size"]
        self.anims = {n: Anim(n, a, palette) for n, a in data["animations"].items()}
        # The idle pose is the first frame of Clawd-Jumping.
        jump = data["animations"]["jump"]
        hx, hy = jump["home"]
        idle = [r[hx:hx + self.iw] for r in jump["frames"][0]["rows"][hy:hy + self.ih]]
        self.poses = {k: grid_image(pose_rows(idle, k), palette)
                      for k in ("idle", "blink", "look_l", "look_r", "happy",
                                "read", "read_l", "read_r", "surprised", "sad", "back")}
        left = climb_rows(idle)
        right = ["".join(reversed(r)) for r in left]
        self.anims["climb"] = Anim("climb", {
            "size": [self.iw, self.ih + 4], "home": [0, 4],
            "frames": [{"ms": 130, "rows": left}, {"ms": 130, "rows": right}]}, palette)
        # Clawd-Laptop's typing loop with other eyes: looking up at you (left
        # or right) and happy, for reacting to the pointer without stopping
        laptop = data["animations"]["laptop"]
        lo, hi = laptop["loop"]
        typing = [laptop["frames"][i] for i in range(lo, hi + 1)]
        body, ink = idle[0][4], idle[EYES[0][1]][EYES[0][0]]
        for kind in ("look_l", "look_r", "happy"):
            self.anims["type_" + kind] = Anim("type_" + kind, {
                "size": laptop["size"], "home": laptop["home"], "loop": [0, len(typing) - 1],
                "frames": [{"ms": f["ms"], "rows": typing_eyes(f["rows"], kind, body, ink)} for f in typing]},
                palette)
        # Clawd-Jumping's arms-up frame brought down to the floor, for cheering
        up = jump["frames"][2]["rows"]
        drop = hy + self.ih - 1 - max(y for y, r in enumerate(up) if r.strip("."))
        self.anims["cheer"] = Anim("cheer", {"size": jump["size"], "home": jump["home"], "frames": [
            {"ms": 300, "rows": ["." * len(up[0])] * drop + up[:len(up) - drop]}]}, palette)
        self.anims["poke"] = Anim("poke", {
            "size": [self.iw + 3, self.ih], "home": [0, 0],
            "frames": [{"ms": 180, "rows": poke_rows(idle)}]}, palette)
        self.anims["hang"] = Anim("hang", {
            "size": [self.iw, self.ih + 4], "home": [0, 4],
            "frames": [{"ms": 350, "rows": hang_rows(idle)}]}, palette)
        self.glyphs = {k: grid_image(rows, pal) for k, (rows, pal) in GLYPHS.items()}
        self.props = {k: grid_image(rows, pal) for k, (rows, pal) in PROPS.items()}
        self.props["cloud"], self.cloud_at = cloud_platform(data, palette)
        self.pose_head = head_of(idle, {idle[0][4]})
        # Hats and seasonal props from sprites/extras.py
        extras = load_extras()

        def images(name):
            e = extras[name]
            pal = {k: rgb(v) for k, v in e["palette"].items()}
            return [grid_image(rows, pal) for rows in e["frames"]]
        # The stretch: arms up in a V, clear of any hat, on tiptoe, eyes > <.
        # Its head is given (the arms reach the frame's edges, which would
        # read as a head peeking in).
        st = extras["stretch"]
        st_pal = {k: rgb(v) for k, v in st["palette"].items()}
        self.anims["stretch"] = Anim("stretch", {
            "size": [len(st["frames"][0][0]), len(st["frames"][0])], "home": st["home"],
            "frames": [{"ms": 300, "rows": rows} for rows in st["frames"]]}, st_pal)
        self.anims["stretch"].heads = [tuple(st["head"])] * len(st["frames"])
        # Dangling from the pointer: both hands on one grip point at the frame's
        # middle, frames STRAIGHT..ONE_HAND, redrawn with each expression.
        self.dangle_drawn = "dangle" in extras      # grabbing the pointer waits for its art
        if self.dangle_drawn:
            dg = extras["dangle"]
            dg_pal = {k: rgb(v) for k, v in dg["palette"].items()}
            dg_frames, grip, eyes = dg["frames"], dg["grip"], dg["eyes"]
        else:                                       # stand-in until it's drawn
            dg_pal, dg_frames = palette, [hang_rows(idle)] * 6
            grip, eyes = [self.iw // 2 - 1, 0], [[[EYES[0][0], EYES[0][1] + 4], [EYES[1][0], EYES[1][1] + 4]]] * 6
        self.dangle_grip = tuple(grip)
        (lx, ly), _ = eyes[STRAIGHT]
        dg_home = [lx - EYES[0][0], ly - EYES[0][1]]      # his head where the idle pose has it
        for kind in ("idle", "happy", "surprised", "squeezed", "look_l", "look_r"):
            name = "dangle" if kind == "idle" else "dangle_" + kind
            self.anims[name] = Anim(name, {
                "size": [len(dg_frames[0][0]), len(dg_frames[0])], "home": dg_home,
                "frames": [{"ms": 100, "rows": eyes_rows(f, eyes[i], kind, "#", "@")}
                           for i, f in enumerate(dg_frames)]}, dg_pal)
            self.anims[name].grip = (grip[0] + 1, grip[1])
            # his head from his eyes: the clasped fists are as wide as a head
            self.anims[name].heads = [dangle_head(e) for e in eyes]
        # Whirled right round: his straight hang turned about his hands, in
        # SPIN_STEPS steps, with the pivot where the hand-drawn frames hold on
        straight = dg_frames[STRAIGHT]
        pivot = (grip[0] + 1, grip[1])
        reach = max(((x + 0.5 - pivot[0]) ** 2 + (y + 0.5 - pivot[1]) ** 2) ** 0.5
                    for y, r in enumerate(straight) for x, ch in enumerate(r) if ch != ".")
        size = 2 * (int(reach) + 2)
        spin_home = [size // 2 - (pivot[0] - dg_home[0]), size // 2 - (pivot[1] - dg_home[1])]
        self._spin = {"rows": straight, "eyes": eyes[STRAIGHT], "pivot": pivot, "size": size,
                      "home": spin_home, "palette": dg_pal, "home_y": dg_home[1], "extras": extras}
        for kind in ("idle", "happy", "surprised"):
            self.spin_anim(kind, None)

        # which way the lean frames swing him (+1: to the frame's right)
        lean = dg_frames[LEAN_2]
        cells = [x for r in lean for x, c in enumerate(r) if c != "."]
        self.dangle_lean = 1 if sum(cells) / max(1, len(cells)) >= grip[0] + 1 else -1
        # Climbing: up a window's side (the edge along his right, mirrored for the
        # other side) and up the ladder between screens
        for name in ("climb_side", "climb_ladder"):
            if name in extras:
                cs = extras[name]
                self.anims[name] = Anim(name, {
                    "size": [len(cs["frames"][0][0]), len(cs["frames"][0])], "home": cs["home"],
                    "frames": [{"ms": 140, "rows": rows} for rows in cs["frames"]]},
                    {k: rgb(v) for k, v in cs["palette"].items()})
        # nightcap_stretch: the nightcap with its tail flipped up, out of the left arm's way
        self.hats = {n: (images(n), tuple(extras[n]["anchor"])) for n in list(HATS) + ["nightcap_stretch"]}
        self.hats_flipped = {n: [flipped(img) for img in frames] for n, (frames, _) in self.hats.items()}
        for name in ("mug", "water_bottle", "break_bubble", "water_bubble"):
            self.props[name] = images(name)[0]
        self.props["mug_held"] = flipped(self.props["mug"])         # handle toward his hand
        if "water_bottle_tilt" in extras:
            self.props["water_bottle_tilt"] = images("water_bottle_tilt")[0]
            self.bottle_cap = tuple(extras["water_bottle_tilt"]["cap"])
        for name in ("break_bubble", "water_bubble"):
            self.glyphs[name] = self.props[name]
        for name in ("steam", "bat", "confetti", "droplet"):
            for i, img in enumerate(images(name)):
                (self.props if name == "steam" else self.glyphs)[f"{name}_{i}"] = img

    def spin_anim(self, face, hat):
        """The frames of him whirled right round (see turned_frames), with
        `face`, and `hat` baked in so it goes round with him. Built the first
        time they're wanted."""
        name = "spin_" + ("" if face == "idle" else face) + ("_" + hat if hat else "")
        name = name.replace("spin__", "spin_").rstrip("_")
        if name in self.anims:
            return name
        sp = self._spin
        rows = eyes_rows(sp["rows"], sp["eyes"], face, "#", "@")
        pal, pivot = dict(sp["palette"]), sp["pivot"]
        if hat:
            e = sp["extras"][hat]
            free = iter(k for k in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789" if k not in pal)
            remap = {k: next(free) for k in e["palette"]}
            pal.update({remap[k]: rgb(v) for k, v in e["palette"].items()})
            (cx, top), (ax, ay) = dangle_head(sp["eyes"]), e["anchor"]
            pad = max(0, ay - top)
            grid = [["."] * len(rows[0]) for _ in range(pad)] + [list(r) for r in rows]
            top, pivot = top + pad, (pivot[0], pivot[1] + pad)
            for y, row in enumerate(e["frames"][0]):
                for x, ch in enumerate(row):
                    gx, gy = cx - ax + x, top - ay + y
                    if ch != "." and 0 <= gx < len(grid[0]) and 0 <= gy < len(grid):
                        grid[gy][gx] = remap[ch]
            for y in range(top):                            # his arms go up in front of the hat
                for x, ch in enumerate(rows[y - pad] if y >= pad else ""):
                    if ch != ".":
                        grid[y][x] = ch
            rows = ["".join(r) for r in grid]
        size = sp["size"]
        self.anims[name] = Anim(name, {"size": [size, size], "home": sp["home"], "frames": [
            {"ms": 100, "rows": r} for r in turned_frames(rows, pivot, SPIN_STEPS, size)]}, pal)
        self.anims[name].grip = (size // 2, size // 2)
        self.anims[name].heads = [None] * SPIN_STEPS        # the hat's already on
        return name


def cloud_platform(data, palette):
    """The cloud from Clawd-Cloud on its own, so he can sit in it while doing
    other things. Its two blues make it easy to lift out of a riding frame;
    the wind streaks trailing behind are left out and the holes his legs left
    are filled. Returns the image and where it sits relative to the idle pose
    (bottom on the idle pose's feet line, centred under him)."""
    cloud = data["animations"]["cloud"]
    rows = cloud["frames"][cloud["loop"][0]]["rows"]
    hx, hy = cloud["home"]
    blues = {k for k, v in data["palette"].items() if v in ("#c5d3e0", "#6a9bcc")}
    light = next(k for k, v in data["palette"].items() if v == "#c5d3e0")
    cells = {(x, y): ch for y, row in enumerate(rows) for x, ch in enumerate(row)
             if ch in blues and x >= hx + 3}
    x0, x1 = min(x for x, _ in cells), max(x for x, _ in cells)
    y0, y1 = min(y for _, y in cells), max(y for _, y in cells)
    grid = [["."] * (x1 - x0 + 1) for _ in range(y1 - y0 + 1)]
    for (x, y), ch in cells.items():
        grid[y - y0][x - x0] = ch
    for x in range(x1 - x0 + 1):              # fill the holes under the top of each column
        col = [grid[y][x] for y in range(len(grid))]
        if any(c != "." for c in col):
            top = next(y for y, c in enumerate(col) if c != ".")
            for y in range(top, len(grid)):
                if grid[y][x] == ".":
                    grid[y][x] = light
    img = grid_image(["".join(r) for r in grid], palette)
    iw, ih = data["idle_size"]
    return img, ((iw - img.width()) // 2, ih - img.height())


def load_sprites(path=SPRITES):
    with open(path) as f:
        return Sprites(json.load(f))


# ── Launching things ────────────────────────────────────────────────

TERMINALS = [          # executable, arguments that go before the command
    ("konsole", ["-e"]),
    ("gnome-terminal", ["--"]),
    ("kitty", []),
    ("alacritty", ["-e"]),
    ("wezterm", ["start", "--"]),
    ("foot", []),
    ("xfce4-terminal", ["-x"]),
    ("xterm", ["-e"]),
]


def terminal_command(cmd, which=shutil.which, env=None):
    """A command line that runs `cmd` in a new terminal window, or None."""
    env = os.environ if env is None else env
    names = [env["TERMINAL"]] if env.get("TERMINAL") else []
    names += [t for t, _ in TERMINALS]
    known = dict(TERMINALS)
    for name in names:
        path = which(name)
        if path:
            return [path, *known.get(os.path.basename(name), ["-e"]), *cmd]
    return None


def launch_env(env=None):
    """Our environment minus the XWayland override, for programs we start."""
    env = dict(os.environ if env is None else env)
    if _FORCED_XCB:
        env.pop("QT_QPA_PLATFORM", None)
    return env


def spawn(cmd):
    subprocess.Popen(cmd, cwd=os.path.expanduser("~"), env=launch_env(),
                     start_new_session=True, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_url_command(url, platform=None, which=shutil.which):
    platform = platform or sys.platform
    if platform == "win32":
        return ["cmd", "/c", "start", "", url]
    if platform == "darwin":
        return ["open", url]
    return [which("xdg-open") or "xdg-open", url]


def claude_app_installed():
    """Is something registered for claude:// links (the Claude desktop app)?"""
    if not sys.platform.startswith("linux"):
        return True
    if not shutil.which("xdg-mime"):
        return False
    try:
        out = subprocess.run(["xdg-mime", "query", "default", "x-scheme-handler/claude"],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(out.strip())


def open_in_terminal():
    """Claude Code in a terminal. Returns False if no terminal was found."""
    if sys.platform == "win32":
        subprocess.Popen(["cmd", "/c", "start", "cmd", "/k", "claude"],
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    if sys.platform == "darwin":
        subprocess.Popen(["osascript", "-e", 'tell application "Terminal" to do script "claude"',
                          "-e", 'tell application "Terminal" to activate'])
        return True
    claude = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    cmd = terminal_command([claude])
    if cmd is None:
        return False
    spawn(cmd)
    return True


def open_claude_code(kind="continue"):
    """The Code tab of the Claude app, falling back to a terminal without the app."""
    if claude_app_installed():
        cmd = open_url_command(CLAUDE_LINKS[kind])
        if sys.platform == "win32":
            subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            spawn(cmd)
        return True
    return open_in_terminal()


# ── Starting, restarting, starting at login ─────────────────────────

SCRIPT = os.path.join(HERE, "claude_pet.py")
AUTOSTART = os.path.expanduser("~/.config/autostart/clawd-pet.desktop")
LAUNCHER = os.path.expanduser("~/.local/share/applications/clawd-pet.desktop")
DATA_DIR = os.path.expanduser("~/.local/share/clawd-pet")


def restart_command():
    return [sys.executable, SCRIPT] + sys.argv[1:]


def env_for_restart(env=None):
    """The environment to restart in: without the XWayland override we set,
    so the new process decides (and strips it for what it launches) itself."""
    env = dict(os.environ if env is None else env)
    if _FORCED_XCB:
        env.pop("QT_QPA_PLATFORM", None)
    return env


def already_running(path=None):
    """Is another Clawd listening on the socket? (A stale socket file doesn't count.)"""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(path or socket_path())
        s.close()
        return True
    except (OSError, AttributeError):
        return False


def _exec_arg(arg):
    """Quote an Exec= argument the way the desktop entry spec wants."""
    if re.search(r'[\s"\\`$]', arg):
        return '"' + re.sub(r'(["\\`$])', r"\\\1", arg) + '"'
    return arg


def desktop_entry(icon, autostart=False):
    lines = ["[Desktop Entry]", "Type=Application", "Name=Clawd", "GenericName=Desktop pet",
             "Comment=Clawd, the Claude Code mascot, living on your desktop",
             "Exec=" + " ".join(_exec_arg(a) for a in [sys.executable, SCRIPT]),
             "Icon=" + icon, "Terminal=false", "StartupNotify=false", "Categories=Utility;"]
    if autostart:
        lines.append("X-GNOME-Autostart-enabled=true")
    return "\n".join(lines) + "\n"


def write_icon(folder):
    """Clawd's idle pose as a 256px icon for the launcher and autostart entries."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "clawd.png")
    idle = load_sprites().poses["idle"]
    s = 256 // max(idle.width(), idle.height())
    img = QImage(256, 256, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    w, h = idle.width() * s, idle.height() * s
    p.drawImage(QRect((256 - w) // 2, (256 - h) // 2, w, h), idle)
    p.end()
    img.save(path)
    return path


def hook_installer():
    """tools/install_hooks.py, loaded as a module."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("install_hooks", os.path.join(HERE, "tools", "install_hooks.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def hooks_installed(path=None):
    """(events that run Clawd's hook, events it wants) in Claude Code's settings."""
    inst = hook_installer()
    try:
        with open(path or inst.SETTINGS) as f:
            hooks = json.load(f).get("hooks") or {}
        have = sum(1 for event in inst.EVENTS
                   if any(inst.MARK in h.get("command", "") for g in hooks.get(event, []) for h in g.get("hooks", [])))
    except (OSError, ValueError, AttributeError, TypeError):
        have = 0
    return have, len(inst.EVENTS)


def run_hook_installer(remove=False):
    """Install (or remove) the hooks with tools/install_hooks.py; True if it worked."""
    cmd = [sys.executable, os.path.join(HERE, "tools", "install_hooks.py")] + (["--remove"] if remove else [])
    try:
        return subprocess.run(cmd, timeout=15, capture_output=True).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def autostart_enabled(entry=AUTOSTART):
    return os.path.exists(entry)


def set_autostart(on, entry=AUTOSTART, launcher=LAUNCHER, icon_dir=DATA_DIR):
    """Start at login (an XDG autostart entry, which Plasma and GNOME both read).
    Turning it on also puts Clawd in the app launcher, which stays when it's
    turned off so he can still be started by hand."""
    if not on:
        if os.path.exists(entry):
            os.remove(entry)
        return
    icon = write_icon(icon_dir)
    for path, auto in ((entry, True), (launcher, False)):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(desktop_entry(icon, autostart=auto))


def claude_link_for_folder(folder):
    return CLAUDE_LINKS["new"] + "?folder=" + urllib.parse.quote(folder, safe="")


def open_claude_code_in(folder):
    """A new Claude Code session in `folder`: the app's Code tab, or a terminal there."""
    if claude_app_installed():
        cmd = open_url_command(claude_link_for_folder(folder))
        if sys.platform == "win32":
            subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            spawn(cmd)
        return True
    claude = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    cmd = terminal_command([claude])
    if cmd is None:
        return False
    subprocess.Popen(cmd, cwd=folder, env=launch_env(), start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return True


def open_claude_web():
    if sys.platform.startswith("linux") and shutil.which("xdg-open"):
        spawn(["xdg-open", "https://claude.ai"])
    else:
        webbrowser.open("https://claude.ai")


# ── Where the pointer is ────────────────────────────────────────────
# Under XWayland an X11 app only sees the pointer while it is over an X11
# window, so on KDE we ask the compositor itself: a tiny KWin script reports
# the pointer position to us over D-Bus.

def kwin_desktop_script():
    """A KWin script reporting the pointer and the windows (position, size,
    stacking, fullscreen, active, screen, id; never titles) over D-Bus."""
    return f"""
const SERVICE = "{DBUS_SERVICE}";
const NEAR = 400;             // px: closer than this to him, the pointer is reported in detail
const FAR_STEP = 24;          // further off, only every this many px of movement
let last = {{x: -1e6, y: -1e6}};
let pet = null;               // his drop target, which sits exactly under him
function sendCursor() {{
    const p = workspace.cursorPos;
    let step = 4;
    if (pet) {{
        const g = pet.frameGeometry;
        const off = Math.max(g.x - p.x, 0, p.x - g.x - g.width) + Math.max(g.y - p.y, 0, p.y - g.y - g.height);
        step = off > NEAR ? FAR_STEP : 2;
    }}
    if (Math.abs(p.x - last.x) + Math.abs(p.y - last.y) < step) return;
    last = {{x: p.x, y: p.y}};
    callDBus(SERVICE, "/Pet", SERVICE, "Cursor", p.x + "," + p.y);
}}
function onThisDesktop(w) {{
    if (w.onAllDesktops) return true;
    try {{
        for (const d of w.desktops) if (d.id === workspace.currentDesktop.id) return true;
        return false;
    }} catch (e) {{ return true; }}
}}
function sendWindows() {{
    const out = [];
    for (const w of workspace.windowList()) {{
        if (String(w.resourceClass) === "clawd-pet") continue;          // himself and his ladder
        if (!(w.normalWindow || w.dialog) || w.minimized || w.hidden || !onThisDesktop(w)) continue;
        const g = w.frameGeometry;
        out.push([Math.round(g.x), Math.round(g.y), Math.round(g.width), Math.round(g.height),
                  w.stackingOrder, w.fullScreen ? 1 : 0, w.active ? 1 : 0,
                  w.output ? String(w.output.name) : "", String(w.internalId)]);
    }}
    callDBus(SERVICE, "/Pet", SERVICE, "Windows", JSON.stringify(out));
}}
function watch(w) {{
    if (String(w.resourceClass) === "clawd-pet") return;             // he moves every frame
    w.frameGeometryChanged.connect(sendWindows);
    w.fullScreenChanged.connect(sendWindows);
    w.minimizedChanged.connect(sendWindows);
}}
function tuck(w) {{
    // his drop target is a managed window (the only kind KWin hands drags to):
    // keep it off the taskbar, the pager and Alt+Tab
    if (String(w.resourceClass) === "clawd-pet" && w.notification) {{
        w.skipTaskbar = true;
        w.skipPager = true;
        w.skipSwitcher = true;
        pet = w;
    }}
}}
for (const w of workspace.windowList()) {{ tuck(w); watch(w); }}
workspace.windowAdded.connect(function (w) {{ tuck(w); watch(w); sendWindows(); }});
workspace.windowRemoved.connect(function (w) {{ if (w === pet) pet = null; sendWindows(); }});
workspace.windowActivated.connect(sendWindows);
workspace.currentDesktopChanged.connect(sendWindows);
workspace.cursorPosChanged.connect(sendCursor);
sendCursor();
sendWindows();
"""


@pyqtClassInfo("D-Bus Interface", DBUS_SERVICE)
class DesktopFeed(QObject):
    def __init__(self, on_cursor, on_windows):
        super().__init__()
        self._on_cursor, self._on_windows = on_cursor, on_windows

    @pyqtSlot(str)
    def Cursor(self, pos):                       # noqa: N802 (D-Bus method name)
        try:
            x, y = pos.split(",")
            self._on_cursor(int(float(x)), int(float(y)))
        except ValueError:
            pass

    @pyqtSlot(str)
    def Windows(self, data):                     # noqa: N802
        try:
            rows = json.loads(data)
        except ValueError:
            return
        if isinstance(rows, list):
            self._on_windows(rows)


def start_kwin_feed(on_cursor, on_windows):
    """Start the KWin feed; returns it, or None if this isn't KDE Wayland."""
    if not (os.environ.get("WAYLAND_DISPLAY") and "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "")):
        return None
    try:
        from PyQt6.QtDBus import QDBusConnection, QDBusInterface
    except ImportError:
        return None
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected() or not bus.registerService(DBUS_SERVICE):
        return None
    feed = DesktopFeed(on_cursor, on_windows)
    if not bus.registerObject("/Pet", feed, QDBusConnection.RegisterOption.ExportAllSlots):
        return None
    path = os.path.join(runtime_dir(), "clawd-pet-desktop.js")
    with open(path, "w") as f:
        f.write(kwin_desktop_script())
    kwin = QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", bus)
    if not kwin.isValid():
        return None
    kwin.call("unloadScript", KWIN_SCRIPT)
    args = kwin.call("loadScript", path, KWIN_SCRIPT).arguments()
    if not args or not isinstance(args[0], int) or args[0] < 0:
        return None
    QDBusInterface("org.kde.KWin", f"/Scripting/Script{args[0]}", "org.kde.kwin.Script", bus).call("run")
    return feed


def stop_kwin_feed():
    try:
        from PyQt6.QtDBus import QDBusConnection, QDBusInterface
    except ImportError:
        return
    bus = QDBusConnection.sessionBus()
    QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", bus).call(
        "unloadScript", KWIN_SCRIPT)


# ── Screens ─────────────────────────────────────────────────────────

PANEL_MAX = 0.15       # a real panel never takes more than this share of a screen


def usable_area(geo, avail):
    """The part of a screen he can use, from its geometry and Qt's available area.

    X11 (so XWayland too) publishes ONE work area for the whole desktop, so a
    taskbar at the bottom of a short monitor also "cuts" a taller monitor next
    to it, hundreds of pixels above its real bottom. Real panels are thin: a
    cut deeper than PANEL_MAX of the screen is that artefact, and is ignored.
    """
    cuts = [avail.left() - geo.left(), avail.top() - geo.top(),
            geo.left() + geo.width() - avail.left() - avail.width(),
            geo.top() + geo.height() - avail.top() - avail.height()]
    limits = [geo.width() * PANEL_MAX, geo.height() * PANEL_MAX] * 2
    left, top, right, bottom = [c if 0 <= c <= m else 0 for c, m in zip(cuts, limits)]
    return QRect(geo.left() + left, geo.top() + top,
                 geo.width() - left - right, geo.height() - top - bottom)


_work_area = {"checked": -1e9, "rect": None}


def x11_work_area():
    """The single, desktop-wide work area X11 publishes, or None off X11.

    Qt applies it too, but only once it notices the property, some time after
    startup; reading it ourselves (every 10 s at most) keeps the floors steady.
    """
    if "xprop" not in _work_area:
        _work_area["xprop"] = shutil.which("xprop")
    if QApplication.platformName() != "xcb" or not _work_area["xprop"]:
        return None
    now = time.monotonic()
    if now - _work_area["checked"] > 10:
        _work_area["checked"] = now
        try:
            out = subprocess.run(["xprop", "-root", "_NET_WORKAREA"], capture_output=True,
                                 text=True, timeout=2).stdout
            _work_area["rect"] = QRect(*[int(v) for v in out.split("=")[1].split(",")[:4]])
        except (OSError, IndexError, ValueError, subprocess.SubprocessError):
            _work_area["rect"] = None
    return _work_area["rect"]


_screens = {"at": -1.0, "areas": None}


def screen_areas():
    """Each screen's usable area (looked up afresh at most twice a second)."""
    now = time.monotonic()
    if _screens["areas"] is not None and now - _screens["at"] < 0.5:
        return _screens["areas"]
    work = x11_work_area()
    areas = []
    for sc in QApplication.screens():
        geo = sc.geometry()
        avail = geo.intersected(work) if work is not None else sc.availableGeometry()
        areas.append(usable_area(geo, avail))
    _screens["areas"], _screens["at"] = areas, now
    return areas


def area_at(x, y, areas=None):
    for a in screen_areas() if areas is None else areas:
        if a.left() <= x < a.left() + a.width() and a.top() <= y < a.top() + a.height():
            return a
    return None


def _distance(r, x, y):
    dx = max(r.left() - x, 0, x - r.left() - r.width())
    dy = max(r.top() - y, 0, y - r.top() - r.height())
    return (dx * dx + dy * dy) ** 0.5


# ── Desktop icons (KDE Plasma) ──────────────────────────────────────
# Plasma saves where each desktop icon sits in its grid; the grid itself is
# computed the way Plasma's folder view does it (FolderView.qml), with its
# default units at the default font and scale.

ICON_PX = [22, 32, 48, 64, 96, 128, 256]        # Plasma's iconSize setting 0..6
GRID_UNIT, SMALL_SPACING, SMALL_ICON = 18, 4, 16
PLASMA_CONFIG = os.path.expanduser("~/.config/plasma-org.kde.plasma.desktop-appletsrc")


def desktop_dir():
    try:
        out = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=2).stdout.strip()
        if out:
            return out
    except (OSError, subprocess.SubprocessError):
        pass
    return os.path.expanduser("~/Desktop")


def _extra(cell, container):
    """Plasma spreads leftover space evenly over the columns (calcExtraSpacing)."""
    n = container // cell
    return (container - n * cell) // n if n > 0 else 0


def plasma_desktop_icons(config=PLASMA_CONFIG, desktop=None, screens=None):
    """[(file name, QRect of the icon image, is a folder)] for Plasma's desktop icons.

    `screens` are QRects (or (geometry, usable area) pairs); a folder view's
    saved positions are keyed by the resolution of the screen it belongs to.
    """
    try:
        with open(config) as f:
            text = f.read()
    except OSError:
        return []
    desktop = desktop or desktop_dir()
    if screens is None:
        screens = [(sc.geometry(), usable_area(sc.geometry(), sc.availableGeometry()))
                   for sc in QApplication.screens()]
    screens = [s if isinstance(s, tuple) else (s, s) for s in screens]
    sections, current = {}, None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line, {})
        elif current is not None and "=" in line:
            key, value = line.split("=", 1)
            current[key] = value
    icons = []
    for name, keys in sections.items():
        if not re.fullmatch(r"\[Containments\]\[\d+\]", name) or keys.get("plugin") != "org.kde.plasma.folder":
            continue
        general = sections.get(name + "[General]", {})
        try:
            positions = json.loads(general.get("positions", "{}"))
            size = ICON_PX[min(max(int(general.get("iconSize", 3)), 0), len(ICON_PX) - 1)]
            label_width = int(general.get("labelWidth", 1))
            lines = int(general.get("textLines", 2))
        except (ValueError, TypeError):
            continue
        for resolution, items in positions.items():
            match = [area for geo, area in screens if f"{geo.width()}x{geo.height()}" == resolution]
            if not match or not isinstance(items, list):
                continue
            area = match[0]
            min_w = max(size + 2 * GRID_UNIT + 2 * SMALL_SPACING, SMALL_ICON * (label_width * 2 + 4))
            cell_w = min_w + _extra(min_w, area.width())
            icon_h = size + GRID_UNIT * lines + SMALL_SPACING * 3
            cell_h = icon_h + _extra(icon_h, area.height())
            for i in range(2, len(items) - 2, 3):       # after the rows/columns header
                url = str(items[i])
                if not url.startswith("desktop:/"):
                    continue
                fname = url[len("desktop:/"):]
                if not os.path.exists(os.path.join(desktop, fname)):
                    continue                            # stale: the file is gone
                try:
                    row, col = int(items[i + 1]), int(items[i + 2])
                except ValueError:
                    continue
                icons.append((fname, QRect(area.left() + col * cell_w + (cell_w - size) // 2,
                                           area.top() + row * cell_h + 2 * SMALL_SPACING, size, size),
                              os.path.isdir(os.path.join(desktop, fname))))
    return icons


_icons_cache = {"at": -1e9, "icons": []}


def desktop_icons():
    """Plasma's desktop icons, re-read at most every 30 s (you may move them)."""
    if os.environ.get("CLAWD_NO_DESKTOP_ICONS"):
        return []
    now = time.monotonic()
    if now - _icons_cache["at"] > 30:
        _icons_cache["at"] = now
        _icons_cache["icons"] = plasma_desktop_icons()
    return _icons_cache["icons"]


# ── Props ───────────────────────────────────────────────────────────

class Prop(QWidget):
    """A click-through overlay for props too big for Clawd's own window (the
    ladder). `reveal` shows only part of it, growing from the top or bottom."""

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowDoesNotAcceptFocus
                         | Qt.WindowType.X11BypassWindowManagerHint
                         | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.image = None
        self.reveal = 1.0
        self.from_top = False

    def paintEvent(self, _event):
        if self.image is None:
            return
        p = QPainter(self)
        h = self.height()
        shown = int(h * self.reveal)
        top = 0 if self.from_top else h - shown
        p.drawPixmap(0, top, self.image, 0, top, self.width(), shown)
        p.end()


def set_window_type(widget, kind):
    """Give an X11 window an EWMH type Qt has no flag for. Only works before
    the window is first shown: the window manager reads it when it maps."""
    if QGuiApplication.platformName() != "xcb" or not shutil.which("xprop"):
        return False
    try:
        subprocess.run(["xprop", "-id", str(int(widget.winId())), "-f", "_NET_WM_WINDOW_TYPE", "32a",
                        "-set", "_NET_WM_WINDOW_TYPE", kind], timeout=2, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


class DropCatcher(QWidget):
    """An invisible twin under Clawd that takes drops for him.

    KWin only hands drags from Wayland apps (Dolphin, the desktop) to X11
    windows it manages, and Clawd is unmanaged. This one is managed, typed as a
    notification so KWin neither pushes it into the work area nor stacks it
    under ordinary windows, and shaped like him. He sits on top of it, so the
    pointer only ever reaches it mid-drag."""

    def __init__(self, pet):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAcceptDrops(True)
        self.setWindowTitle("Clawd drop target")
        self.pet = pet
        self.typed = set_window_type(self, "_NET_WM_WINDOW_TYPE_NOTIFICATION")

    def dragEnterEvent(self, e):
        self.pet.dragEnterEvent(e)

    def dragMoveEvent(self, e):
        self.pet.dragMoveEvent(e)

    def dragLeaveEvent(self, e):
        self.pet.dragLeaveEvent(e)

    def dropEvent(self, e):
        self.pet.dropEvent(e)


# ── The pet ─────────────────────────────────────────────────────────

class ClawdPet(QWidget):
    """
    Behaviours are generators: each one says what to show (and how fast he
    is moving) and yields how many milliseconds to hold it. advance() runs
    the current one, moves him, and picks the next behaviour when it ends.
    """

    def __init__(self, sprites, settings=None):
        super().__init__()
        self.sp = sprites
        self.settings = settings
        self.iw, self.ih = sprites.iw, sprites.ih
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool
                            | Qt.WindowType.WindowDoesNotAcceptFocus    # never steal your typing
                            # Unmanaged, like a tooltip. KWin otherwise keeps X11 windows
                            # inside X11's single work area, which on a taller second
                            # monitor pinned him hundreds of pixels above its floor.
                            | Qt.WindowType.X11BypassWindowManagerHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setMouseTracking(True)            # hovering over him counts as the pointer being near
        self.setAcceptDrops(True)              # drop a folder on him: a Claude Code session there
        self.prefs = Prefs(settings)
        self.wall = wall_clock                 # the date and time (tests set their own)
        self._hat_on = False                   # is a hat drawn on this frame
        self.grip_offset = cursor_grip()       # where on the pointer he holds on
        self._dangling = False
        self._release = False                  # let go of the pointer (asked from outside)
        self._swing = self._swing_v = 0.0      # his swing under the pointer (radians, rad/s)
        self._linger = 0.0                     # how long the pointer has hung around above him
        self._grab_cool = 0.0
        self._sure_armed = True                # a pointer held right above him gets grabbed
        self._morning = None                   # the day he last had his morning coffee
        self._startled_at = -1e9               # when the wallpaper last gave him a fright
        self._party_done = None                # (without QSettings) the day of the last birthday party
        self._true_scale = None                # your size, while he's blown up big for a party
        self._new_year = None                  # the year he last saw in
        self._input_at = 0.0                   # when you last moved the pointer or sent a prompt
        self._streak_at = 0.0                  # since when you've been at it without a break
        self._water_at = 0.0                   # when you last had water (or he started)
        self._snooze = {"water": 0.0, "break": 0.0}
        self._remind_checked = 0.0
        # a reminder waiting for its Done button ("water" or "break"); kept across restarts
        pending = settings.value("reminding") if settings is not None else None
        self.reminding = pending if pending in ("water", "break") else None
        self._remind_since = -REMIND_LOUD if self.reminding else 0.0   # back after a restart: calmly
        self._button_down = False
        self._nudge_until = 0.0
        self.catcher = None                    # his DropCatcher, on KDE Wayland
        self._drag_over = False
        self._drag_seen = 0.0
        self._before_drag = None
        self._catcher_want, self._catcher_at = None, -1e9
        self.setWindowTitle("Clawd")

        scale = DEFAULT_SCALE
        if settings is not None:
            scale = int(settings.value("scale", DEFAULT_SCALE))
        self.scale = scale if scale in SCALES.values() else DEFAULT_SCALE
        self._pixmaps, self._masks = collections.OrderedDict(), {}
        self._layout()

        self.now = 0.0                 # ms of simulated time, advanced by advance()
        self.x = self.y = 0.0          # window top-left, as floats for smooth motion
        self.vx = self.vy = 0.0        # px/s
        self.airborne = False
        self.dragging = False
        self.offscreen = False         # lurking: allowed past the screen edge
        self.scripted = False          # leaping or climbing: the behaviour moves him, not physics
        self.pad = (0, 0)              # how far the current prop sticks out, in pixels
        self.particles = []            # Z's, hearts, the "!" bubble, tears...
        self.lift = 0                  # sprite px he's drawn above his feet line (sitting in his cloud)
        self.layers = {}               # props he holds or wears: name -> (x, y) in sprite px from home
        self.front = None              # the cloud he sits in, drawn over his legs
        self.frame = ("pose", "idle", False)
        self.action = None
        self.manual = False
        self.script = None
        self.wait = 0.0
        self._press = None
        self._trail = []
        self._shown = None
        self._mask = None
        self.ladder = Prop()
        self.icon_source = desktop_icons
        self.window_list = []          # other apps' windows, from KWin (dicts)
        self.standing_on = None        # id of the window whose top he's standing on
        self.ducked = False            # hidden while something is fullscreen on his screen

        self.sessions = {}             # Claude Code sessions: state, since, seen
        self.tool_style = "type"       # what the latest tool calls for (see TOOL_STYLES)
        self.work_style = None         # what he's actually doing about it right now
        self._stumble = False
        self.celebrate = None          # ms of work to celebrate once he's free
        self.greet = False
        self.last_message = None
        self.server = None

        self.cursor = None             # (x, y, when) of the pointer, when known
        self._glance_until = self._glance_cooldown = -1.0
        self._glance_dir = "look_l"
        self._last_activity = 0.0      # last time you or Claude Code did anything
        self._poll_cursor = False
        self._polled = 0.0
        self._tracking = False
        self._pet_dir = 0
        self._pet_flips = []
        self._petting_until = -1.0
        self._was_petting = False
        self._pet_ms = 0.0
        self._last_heart = -1e9
        self._skid = 0.0                       # sideways speed left over from a landing
        self.chute = None                      # "parachute" or "umbrella" while one's open
        self._chute_t = 0.0
        self._body_key = self._shape_key = None
        self._body_mask = None                 # his shape without flying particles (the catcher's)
        self._tiny = QRegion(0, 0, 1, 1)
        self._catcher_src = None
        self._hat_cache = (None, None)
        self.counts = {"ticks": 0, "paints": 0, "shapes": 0, "moves": 0}   # for status: what costs

        self._place_initially()
        self.start("idle")
        QApplication.instance().screenRemoved.connect(
            lambda _screen: QTimer.singleShot(300, self._rescue))

        self.clock = QElapsedTimer()
        self.clock.start()
        self._mono = QElapsedTimer()           # never restarted: for timing drags
        self._mono.start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer)
        self.timer.start(TICK_MS)

    # ── Geometry ──────────────────────────────────────────────────

    def _layout(self):
        """Size the window to fit every animation, mirrored or not."""
        left, right, top, bottom = 0, self.iw, 14, self.ih   # 14: room for Z's and hearts
        hat_up = max(anchor[1] for _, anchor in self.sp.hats.values()) + 1
        for a in self.sp.anims.values():
            hx, hy = a.home
            for lft in (hx, a.w - hx - self.iw):
                left = max(left, lft)
                right = max(right, a.w - lft)
            top = max(top, hy)
            bottom = max(bottom, a.h - hy)
            for head in a.heads:                 # and a hat on his head, however high it gets
                if head is not None:
                    top = max(top, hy - head[1] + hat_up)
        top = max(top, -BOTTLE_UP[1] + 5 + 4)   # a water bottle held up high, mid-hop, splashing
        s = self.scale
        # Room beside him for an icon he's inspecting and the magnifying glass
        # going over it (the window is click-through outside what's drawn).
        stage = -(-64 // s) + 12
        left, right = max(left, stage), max(right, self.iw + stage)
        self.setFixedSize((left + right) * s, (top + bottom) * s)
        self.home_px = QPoint(left * s, top * s)      # idle pose's top-left in the window

    def frame_rect(self, anim, idx, mirror):
        s = self.scale
        hx, hy = anim.home
        left = anim.w - hx - self.iw if mirror else hx
        return QRect(self.home_px.x() - left * s, self.home_px.y() - hy * s, anim.w * s, anim.h * s)

    def screen_geometry(self):
        """Usable area of the screen he's in (the nearest one if he's in none)."""
        left, right = self.box_span()
        cx, cy = (left + right) / 2, self._mid()
        areas = screen_areas()
        here = area_at(cx, cy, areas)
        if here is not None:
            return here
        # In a gap, e.g. dropped below a shorter monitor: prefer the screen
        # straight above or below him, else whichever is nearest.
        column = [a for a in areas if a.left() <= cx < a.left() + a.width()]
        return min(column or areas, key=lambda a: _distance(a, cx, cy))

    def _mid(self):
        """Height of the middle of his body, on screen."""
        return self.y + self.home_px.y() + self.ih * self.scale / 2

    def _feet(self):
        return self.y + self.home_px.y() + self.ih * self.scale

    def box_span(self):
        """Left and right edges of the idle pose, on screen."""
        left = self.x + self.home_px.x()
        return left, left + self.iw * self.scale

    def set_box_left(self, left):
        self.x = left - self.home_px.x()

    def _surfaces(self):
        """What he can stand on: each screen's floor, and the visible parts of
        window top edges that have room above them for him. (y, x0, x1, id)
        Worked out at most once per tick."""
        key = (self.now, id(self.window_list), self.scale)
        if getattr(self, "_surfaces_key", None) != key:
            self._surfaces_key, self._surfaces_cache = key, self._find_surfaces()
        return self._surfaces_cache

    def _find_surfaces(self):
        s = self.scale
        areas = screen_areas()
        out = [(a.top() + a.height(), a.left(), a.left() + a.width(), None) for a in areas]
        wins = sorted(self.window_list, key=lambda w: -w["stack"])          # topmost first
        for i, w in enumerate(wins):
            if w["fs"]:
                continue
            y, spans = w["y"], [(w["x"], w["x"] + w["w"])]
            for above in wins[:i]:                 # a window in front hides that stretch of edge
                if above["y"] <= y < above["y"] + above["h"]:
                    a0, a1 = above["x"], above["x"] + above["w"]
                    spans = [part for x0, x1 in spans
                             for part in ((x0, min(x1, a0)), (max(x0, a1), x1)) if part[1] > part[0]]
            for x0, x1 in spans:
                for a in areas:
                    if a.top() + (self.ih + 4) * s <= y < a.top() + a.height() - 4 * s:
                        c0, c1 = max(x0, a.left()), min(x1, a.left() + a.width())
                        if c1 - c0 >= self.iw * s * 0.6:
                            out.append((y, c0, c1, w["id"]))
        return out

    def _support(self, feet=None):
        """The surface under his middle at or below `feet` (default: where his feet are)."""
        feet = self._feet() if feet is None else feet
        cx = sum(self.box_span()) / 2
        best = None
        for surface in self._surfaces():
            y, x0, x1, _ = surface
            if x0 <= cx < x1 and y >= feet - 2 and (best is None or y < best[0]):
                best = surface
        return best

    def ground_y(self):
        sup = self._support()
        if sup is not None:
            floor = sup[0]
        else:                                      # in a gap between screens: the nearest floor
            geo = self.screen_geometry()
            floor = geo.top() + geo.height()
        return floor - self.home_px.y() - self.ih * self.scale

    def windows_changed(self, rows):
        self.wake()
        self._windows_changed(rows)

    def _windows_changed(self, rows):
        """KWin's window list changed: ride along with the window he's on, and
        duck out of the way if something goes fullscreen on his screen."""
        new = []
        for r in rows:
            try:
                x, y, w, h, stack, fs, active, output, wid = r[:9]
                new.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h), "stack": int(stack),
                            "fs": bool(fs), "active": bool(active), "output": str(output), "id": str(wid)})
            except (ValueError, TypeError):
                continue
        if self.standing_on and not (self.airborne or self.dragging or self.scripted):
            old = next((w for w in self.window_list if w["id"] == self.standing_on), None)
            now = next((w for w in new if w["id"] == self.standing_on), None)
            if old and now:
                self.x += now["x"] - old["x"]
                self.y += now["y"] - old["y"]
        self.window_list = new
        if (self.prefs["duck"] and self._fullscreen_here() and self.action not in ("duck", "grab")
                and not self.dragging):
            self.start("duck")

    def _screen_name(self):
        screen = QApplication.screenAt(QPoint(int(sum(self.box_span()) / 2), int(self._mid())))
        return screen.name() if screen is not None else None

    def _fullscreen_here(self, name=None):
        name = name or self._screen_name()
        return any(w["fs"] and (w["output"] == name or not w["output"]) for w in self.window_list)

    def _limits(self):
        geo = self.screen_geometry()
        s = self.scale
        lo = geo.left() + self.pad[0] - self.home_px.x()
        hi = geo.left() + geo.width() - self.pad[1] - self.home_px.x() - self.iw * s
        return lo, hi

    def _pads(self, name, mirror):
        """How far (px) an animation sticks out past the idle pose, left and right."""
        a = self.sp.anims[name]
        hx, _ = a.home
        left = a.w - hx - self.iw if mirror else hx
        return max(0, left) * self.scale, max(0, a.w - left - self.iw) * self.scale

    def _room(self):
        """Space left and right of him on what he's standing on (a window or the screen)."""
        left, right = self.box_span()
        on = self._window_under()
        if on is not None:
            return left - on[1], on[2] - right
        geo = self.screen_geometry()
        return left - geo.left(), geo.left() + geo.width() - right

    def _window_under(self):
        """The window top he's standing on, as a surface, or None."""
        sup = self._support()
        if sup is not None and sup[3] is not None and abs(sup[0] - self._feet()) < 2:
            return sup
        return None

    def _reach(self):
        """Like _room, but a neighbouring screen that carries on at his height
        (so he can walk over and drop in) counts as more room."""
        geo = self.screen_geometry()
        room_l, room_r = self._room()
        if area_at(geo.left() - 1, self._mid()) is not None:
            room_l += 300
        if area_at(geo.left() + geo.width(), self._mid()) is not None:
            room_r += 300
        return room_l, room_r

    def _seam(self, want, near=300):
        """A screen edge where the floor steps: "up" when the next screen's floor
        is higher than his, "down" when it's much lower and carries on at his
        height. Nearer edges first. Returns (seam x, this area, other area)."""
        geo = self.screen_geometry()
        room_l, room_r = self._room()
        feet, mid = self._feet(), self._mid()
        s = self.scale
        for side, room in sorted(((-1, room_l), (1, room_r)), key=lambda t: t[1]):
            if near is not None and room > near:
                continue
            seam = geo.left() if side < 0 else geo.left() + geo.width()
            probe = seam - 1 if side < 0 else seam
            for a in screen_areas():
                if a == geo or not a.left() <= probe < a.left() + a.width():
                    continue
                floor = a.top() + a.height()
                if want == "up" and geo.top() + self.ih * s < floor < feet - 8:
                    return seam, geo, a
                if want == "down" and floor > feet + 2 * self.ih * s and a.top() <= mid <= floor:
                    return seam, geo, a
        return None

    def _place_initially(self):
        screen, left = QApplication.primaryScreen(), None
        if self.settings is not None:
            name = self.settings.value("screen")
            for sc in QApplication.screens():
                if sc.name() == name:
                    screen = sc
            if self.settings.value("left") is not None:
                left = float(self.settings.value("left"))
        geo = usable_area(screen.geometry(), screen.availableGeometry())
        width = self.iw * self.scale
        if left is None or not geo.left() <= left <= geo.left() + geo.width() - width:
            left = geo.left() + geo.width() - width - 60
        self.set_box_left(left)
        self.y = self.ground_y()
        self.move(int(self.x), int(self.y))

    def _rescue(self):
        """After a monitor is unplugged, bring him back if he was on it."""
        left, right = self.box_span()
        if area_at((left + right) / 2, self._mid()) is None:
            self._place_initially()
            self.start("idle")

    def set_scale(self, scale):
        left, _ = self.box_span()
        bottom = self._feet()
        self.scale = scale
        self._pixmaps.clear()
        self._masks.clear()
        self._layout()
        self.set_box_left(left)
        self.y = bottom - self.home_px.y() - self.ih * scale
        self.move(int(self.x), int(self.y))
        self._shown = None
        self._refresh()
        self.save()

    def save(self):
        if self.settings is None:
            return
        self.settings.setValue("scale", self._true_scale or self.scale)
        self.settings.setValue("left", self.box_span()[0])
        screen = QApplication.screenAt(QPoint(int(self.box_span()[0]), int(self._mid())))
        if screen is not None:
            self.settings.setValue("screen", screen.name())

    # ── Driving behaviours ────────────────────────────────────────

    def start(self, action, manual=False, **kw):
        """Switch to a behaviour. `manual` marks one you asked for (menu, click,
        socket, petting): Claude Code events wait for it to finish."""
        self.manual = manual
        if manual:
            self._last_activity = self.now
        if self.action in REMINDERS:
            self.lift = 0                       # stopped mid-hop: he lands, rather than hanging there
        self._dangling = False                  # off the pointer, whatever comes next
        self._leave_cloud()                     # interrupted mid-scene: drop the props
        self.layers = {}
        self.vx = 0.0 if not self.airborne else self.vx
        self.pad = (0, 0)
        self.offscreen = False
        self.scripted = False
        if self.ladder.isVisible():
            self.ladder.hide()                  # interrupted mid-climb: pack it away
        keep = {"heart"}
        if self.reminding and action not in ("attention", "grab", "duck"):
            keep |= REMINDER_BITS                # a reminder stays up until you press Done
        if action != "sleep":
            self.particles = [q for q in self.particles if q["kind"] in keep]
        self.action = action
        self.script = getattr(self, "_act_" + action)(**kw)
        self.wait = 0.0
        self.wake()

    def _next(self):
        mode = self.claude_mode()
        if self.prefs["quiet"]:
            self.celebrate, self.greet = None, False
        if self.prefs["duck"] and self._fullscreen_here():
            self.start("duck")
        elif mode == "attention":
            self.start("attention")
        elif self.reminding:
            self.start("remind_" + self.reminding)
        elif self._due_reminder():
            self._begin_reminder(self._due_reminder())
        elif mode == "busy":
            self.start("work")
        elif self.celebrate is not None:
            self.start("celebrate")
        elif self.greet:
            self.greet = False
            self.start("wave")
        elif self.action == "idle" and self._birthday_due():
            self.start("birthday", manual=True)
        elif self.action == "idle" and self._new_year_due():
            self.start("new_year")
        elif self.action == "idle":
            self.start("morning" if self._morning_due() else self._pick_action())
        else:
            self.start("idle")

    def _rest_range(self):
        lively = self.prefs["activity"] == "lively"            # lively never winds down
        sleepy = ((self.now - self._last_activity > WIND_DOWN and not lively)
                  or (self.prefs["day_cycle"] and self.is_night()))
        lo, hi = REST_SLEEPY if sleepy else REST
        k = ACTIVITY.get(self.prefs["activity"], 1.0)
        return lo * k, hi * k

    def _engaged(self):
        """Is the pointer near him and moving (you're playing with him)?"""
        if self.now < self._petting_until:
            return True
        c = self.cursor
        if c is None or self.now - c[2] > 3000:
            return False
        left, right = self.box_span()
        near = CURSOR_NEAR * self.scale
        return abs(c[0] - (left + right) / 2) < near and abs(c[1] - self._mid()) < near

    def _pick_action(self):
        off = set(self.prefs["scenes_off"])
        lively = self.prefs["activity"] == "lively"
        drowsy = self.now - self._last_activity > WIND_DOWN and not lively
        if self.prefs["quiet"]:                    # keeps to himself: a nap at most
            return "sleep" if drowsy and "sleep" not in off and random.random() < 0.3 else "idle"
        weights = self._scene_weights(drowsy)
        if self.prefs["day_cycle"] and self.is_night():   # late: yawns, naps, nothing too wild
            for k in LIVELY:
                if k in weights:
                    weights[k] *= 0.3
            weights["sleep"] = max(weights.get("sleep", 0), 6 if lively else 20)
            weights["yawn"] = 10
        weights = {k: v for k, v in weights.items() if v > 0 and (k not in off or k in ("hop_down", "climb_down"))}
        if not weights:
            return "idle"
        return random.choices(list(weights), weights=list(weights.values()))[0]

    def _scene_weights(self, drowsy):
        weights = dict(WEIGHTS)
        if self._window_under() is not None:       # up on a window: things that fit up there
            weights = {k: v for k, v in weights.items() if k in ON_A_WINDOW}
            weights["hop_down"] = 12
            if self._climb_down_target() is not None:
                weights["climb_down"] = 10
            if self._jump_target() is not None:
                weights["window_jump"] = 14             # across to the next one
            return weights
        if self._window_target() is not None:
            weights["perch_window"] = 10
        if self._climb_target() is not None:
            weights["climb_window"] = 8
        if drowsy:
            weights["sleep"] = 25                 # nothing's happened for a while: nap time
        if self._lurk_side() is None:
            weights["lurk"] = 0
        if self._icons_here():
            weights["visit"] = 6
        if self._folders_here():
            weights["read"] = 5
        if self._seam("up") is not None:
            weights["climb"] = 18        # otherwise he'd pile up on the lower screen
            weights["leap"] = 6
        elif self._seam("down") is not None:
            weights["climb"] = 10
        return weights

    def _lurk_side(self):
        """The nearer screen edge if he's close to it and nothing lies beyond it."""
        geo = self.screen_geometry()
        room_l, room_r = self._room()
        side = -1 if room_l <= room_r else 1
        if min(room_l, room_r) > 250:
            return None
        edge = geo.left() - 5 if side < 0 else geo.left() + geo.width() + 5
        return side if area_at(edge, self._mid()) is None else None

    def _on_timer(self):
        self.counts["ticks"] += 1
        self.advance(min(self.clock.restart(), 1000))
        self.timer.start(self._next_tick())

    def _ms(self):
        return self._mono.elapsed()

    def _next_tick(self):
        """Every frame while he moves; otherwise only when something on screen
        is due to change (his next step, a particle crossing a cell), and at
        least every IDLE_TICK: standing still costs next to nothing."""
        if self.airborne or self.dragging or self._dangling or self.vx or self._skid:
            return TICK_MS
        wait = max(TICK_MS, min(self.wait, IDLE_TICK))
        if self.now < self._nudge_until:
            wait = min(wait, 75)
        for q in self.particles:
            if q.get("flap") or q.get("g") or q.get("orbit") or q.get("wave"):
                return TICK_MS
            speed = max(abs(q["vx"]), abs(q["vy"]))          # cells/s: redraws needed per second
            wait = min(wait, 500 / speed if speed else 200)
        return int(max(TICK_MS, wait))

    def wake(self):
        """Something happened (the pointer came by, Claude Code, a click): tick now."""
        timer = getattr(self, "timer", None)
        if timer is not None and timer.isActive() and timer.remainingTime() > TICK_MS:
            timer.start(0)

    def advance(self, dt):
        """Move the simulation on by dt milliseconds."""
        self.now += dt
        if self._drag_over and self.now - self._drag_seen > DRAG_FORGET:
            self._drag_over = False              # the drag went away without a leave event
        waiting = self._drag_over                # something's being dragged over him: hold still
        if not (self.dragging or self.scripted or waiting):
            self._physics(dt)
        if not waiting:
            self.wait -= dt
        for _ in range(100):                     # a script can take several steps at once
            if self.wait > 0 or waiting:
                break
            try:
                self.wait += next(self.script)
            except StopIteration:
                self._next()
        if self._poll_cursor and self.now - self._polled > 100:
            self._polled = self.now
            p = QCursor.pos()
            if self.cursor is None or (p.x(), p.y()) != self.cursor[:2]:
                self.cursor_moved(p.x(), p.y())
        if self.now - self._remind_checked >= 1000:
            self._remind_checked = self.now
            self._maybe_remind()
        if waiting:
            self.frame = ARMS_UP                 # "for me?"
        else:
            self._petting(dt)
            self._watch_cursor()
            self._maybe_grab(dt)
        self._age_particles(dt)
        if (int(self.x), int(self.y)) != (self.pos().x(), self.pos().y()):
            self.move(int(self.x), int(self.y))
            self.counts["moves"] += 1
        self._refresh()
        self._sync_catcher()

    def _sync_catcher(self):
        """Keep the drop target exactly under him: same place, size, shape and visibility."""
        c = self.catcher
        if c is None:
            return
        if not self.isVisible() or self._body_mask is None:
            if c.isVisible():
                c.hide()
            return
        body = self._body_mask                   # his body and props: particles can't take a drop
        if self._dangling:                       # up on the pointer: out of the way of its clicks
            body = self._tiny
        if body is not self._catcher_src:
            mask = body
            if self._drag_over and not c.mask().isEmpty():
                mask = body.united(c.mask())     # only grow mid-drag: a shrinking target would flicker
            c.setMask(mask)
            self._catcher_src = body
        want = self.geometry()
        moving = self.vx or self.airborne or self._dangling or self.dragging or self._skid
        if c.geometry() != want and self.now - self._catcher_at >= (100 if moving else 0):
            c.setGeometry(want)                  # on the move, ten times a second is plenty
            self._catcher_want, self._catcher_at = want, self.now
        if not c.isVisible():
            c.show()

    def _physics(self, dt):
        """In fixed small steps while he flies or skids, so a throw lands in the
        same place however fast (or slowly) the frames come."""
        if not (self.airborne or self._skid):
            self._physics_step(dt)
            return
        steps = max(1, math.ceil(dt / PHYS_STEP))
        for _ in range(steps):
            self._physics_step(dt / steps)

    def _physics_step(self, dt):
        sec = dt / 1000
        s = self.scale
        if self.airborne:
            if self.chute:                               # held up by it: a gentle fall, swaying
                self.vy += (CHUTE_FALL * s - self.vy) * min(1.0, 4 * sec)
                self.vx *= math.exp(-1.5 * sec)
                self._chute_t += sec
                w = 2 * math.pi * CHUTE_SWAY[1]
                sway = CHUTE_SWAY[0] * s * w * math.cos(w * self._chute_t)
            else:
                self.vy = min(self.vy + GRAVITY * s * sec, 250 * s)
                self.vx *= math.exp(-AIR_DRAG * sec)
                sway = 0.0
            was = self._feet()
            self._slide((self.vx + sway) * sec, bounce=True)
            self.y += self.vy * sec
            ceiling = self.screen_geometry().top() - self.home_px.y()
            if self.y < ceiling:
                self.y, self.vy = ceiling, abs(self.vy) * 0.3
            # the first surface between where his feet were and where they are
            below = self._support(was) if self.vy >= 0 else None
            ground = (below[0] - self.home_px.y() - self.ih * s) if below else self.ground_y()
            if self.y >= ground:
                self.y = ground
                if self.vy > BOUNCE_MIN * s and not self.chute:   # a hard landing: a little bounce
                    self.vy = -self.vy * BOUNCE
                    self.vx *= 0.7
                else:                                   # down: skid off what's left sideways
                    self.airborne = False
                    self._skid, self.vx, self.vy = self.vx, 0.0, 0.0
            return
        if self._skid:
            x = self.x
            self._slide(self._skid * sec)
            self._skid *= math.exp(-SKID * sec)
            if abs(self._skid) < 4 * s or self.x == x:  # stopped, or up against a wall
                self._skid = 0.0
        if self.vx:
            self._slide(self.vx * sec)
        if not self.offscreen:
            sup = self._support()
            self.standing_on = sup[3] if sup and abs(sup[0] - self._feet()) < 2 else None
            ground = self.ground_y()
            if self.y < ground - 1:
                self.drop((self._skid or self.vx) * 0.6)   # walked (or skidded) off an edge
                self._skid = 0.0
            elif self.y > ground:
                self.y = ground                  # screens changed under him

    def _slide(self, dx, bounce=False):
        """Move sideways. Past this screen's edge is fine if another screen
        carries on at his height; otherwise it's a wall (or a bounce)."""
        self.x += dx
        if self.offscreen or not dx:
            return
        lo, hi = self._limits()
        left, right = self.box_span()
        if dx > 0 and self.x > hi:
            if self.pad != (0, 0) or area_at(right - 1, self._mid()) is None:
                self.x = hi
                self.vx = -abs(self.vx) * 0.4 if bounce else 0.0
        elif dx < 0 and self.x < lo:
            if self.pad != (0, 0) or area_at(left, self._mid()) is None:
                self.x = lo
                self.vx = abs(self.vx) * 0.4 if bounce else 0.0

    # ── Claude Code ───────────────────────────────────────────────

    def listen(self, path=None):
        """Accept messages from clawd_hook.py (and play/status commands)."""
        path = path or socket_path()
        QLocalServer.removeServer(path)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self.server.listen(path):
            return False
        self.server.newConnection.connect(self._accept)
        return True

    def _accept(self):
        while self.server.hasPendingConnections():
            conn = self.server.nextPendingConnection()
            conn.pending = b""
            conn.readyRead.connect(lambda c=conn: self._read(c))
            conn.disconnected.connect(conn.deleteLater)

    def _read(self, conn):
        conn.pending += bytes(conn.readAll())
        *lines, conn.pending = conn.pending.split(b"\n")
        for line in lines:
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("cmd") == "play" and msg.get("action") in PLAYABLE:
                self.play(msg["action"])
            elif msg.get("cmd") == "set" and self.pref_ok(msg.get("pref"), msg.get("value")):
                self.set_pref(msg["pref"], msg["value"])
            elif msg.get("cmd") == "restart":
                self.restart()
            elif msg.get("cmd") == "icons":
                icons = [[n, r.x(), r.y(), r.width(), r.height(), d] for n, r, d in self.icon_source()]
                conn.write((json.dumps(icons) + "\n").encode())
                conn.flush()
            elif msg.get("cmd") == "status":
                conn.write((json.dumps(self.status()) + "\n").encode())
                conn.flush()
            elif "event" in msg:
                self.claude_event(msg)

    def status(self):
        left, _ = self.box_span()
        geo = self.screen_geometry()
        return {"action": self.action, "frame": list(self.frame), "box_left": round(left),
                "feet": round(self._feet()), "screen": [geo.left(), geo.top(), geo.width(), geo.height()],
                "mode": self.claude_mode(), "cursor": list(self.cursor) if self.cursor else None,
                "ladder": self.ladder.isVisible(), "scale": self.scale,
                "windows": len(self.window_list), "standing_on": self.standing_on, "ducked": self.ducked,
                "ledges": sum(1 for sf in self._surfaces() if sf[3] is not None),
                "catcher": None if self.catcher is None else
                [self.catcher.x(), self.catcher.y(), self.catcher.width(), self.catcher.height(),
                 self.catcher.isVisible()],
                "at": [self.pos().x(), self.pos().y(), self.width(), self.height()],
                "claude": self.prefs["claude"], "quiet": self.prefs["quiet"], "dangling": self._dangling,
                "reminding": self.reminding, "counts": dict(self.counts)}

    def claude_event(self, msg):
        self.last_message = msg
        self.wake()
        ev, sid = msg.get("event", ""), msg.get("session") or "?"
        if ev == "UserPromptSubmit":
            self._user_active()                    # you're here, followed or not
        if not self.prefs["claude"]:
            return                                 # not following Claude Code: nothing else to do
        self._last_activity = self.now
        s = self.sessions.setdefault(sid, {"state": "idle", "since": self.now, "seen": self.now})
        s["seen"] = self.now
        if ev in BUSY_EVENTS:
            if s["state"] != "busy":
                s["since"] = self.now
            s["state"] = "busy"
            if ev == "PreToolUse" and msg.get("tool"):
                self.tool_style = TOOL_STYLES.get(msg["tool"], "type")
            if ev == "PostToolUseFailure":
                self._stumble = True
        elif ev == "PermissionRequest" or (ev == "Notification" and (
                msg.get("kind") in ATTENTION_KINDS or (not msg.get("kind") and s["state"] == "busy"))):
            s["state"] = "waiting"
        elif ev in ("Stop", "StopFailure"):
            if ev == "Stop" and s["state"] != "idle":
                self.celebrate = self.now - s["since"]
            s["state"] = "idle"
        elif ev == "SessionStart":
            self.greet = True
        elif ev == "SessionEnd":
            self.sessions.pop(sid, None)
        self._react()

    def claude_mode(self):
        """"attention" if a session waits on you, "busy" if one is working, else None."""
        if not self.prefs["claude"]:
            return None
        mode = None
        for s in self.sessions.values():
            age = self.now - s["seen"]
            if (s["state"] == "busy" and age > 15 * 60_000) or (s["state"] == "waiting" and age > 30 * 60_000):
                s["state"] = "idle"                 # forgotten: Claude Code was probably closed
            if s["state"] == "waiting":
                return "attention"
            if s["state"] == "busy":
                mode = "busy"
        return mode

    def _react(self):
        """Switch to what Claude Code needs now, unless he's doing something you
        asked for or is in the middle of something physical; either way _next()
        catches up the moment he's done."""
        if self.manual or self.dragging or self.airborne or self.action in ("held", "fall", "duck", "grab"):
            return
        # Anything else he's doing is his own idea, so Claude Code comes first,
        # even halfway up a ladder: the props vanish and he drops to the floor.
        mode = self.claude_mode()
        if self.action in REMINDERS and mode != "attention":
            return                                 # a reminder is for you: only a permission cuts in
        if mode == "attention" and self.action != "attention":
            self.start("attention")
        elif mode == "busy" and self.action not in ("work", "attention"):
            self.start("work")
        elif mode is None and self.greet and self.action == "idle" and not self.prefs["quiet"]:
            self.greet = False
            self.start("wave")

    def play(self, action):
        """Something you asked for (menu or socket): runs to the end."""
        if action in ("work", "attention"):
            self.start(action, manual=True, demo=True)
        elif action in REMINDERS:
            self._begin_reminder(action)
        else:
            self.start(action, manual=True)

    def click_kind(self):
        return "needs-input" if self.claude_mode() == "attention" else "continue"

    def click_link(self):
        return CLAUDE_LINKS[self.click_kind()]

    # ── The pointer ───────────────────────────────────────────────

    def cursor_moved(self, x, y):
        prev = self.cursor
        self.cursor = (x, y, self.now)
        left, right = self.box_span()
        if abs(x - (left + right) / 2) < 2 * CURSOR_NEAR * self.scale and abs(y - self._mid()) < 3 * CURSOR_NEAR * self.scale:
            self.wake()                          # near him: eyes, petting and grabs want a prompt tick
        if prev is None or (x, y) != prev[:2]:
            self._user_active()
        if self._dangling:                       # hanging on: keep up with it right away
            self._place_on_pointer()
            self.move(int(self.x), int(self.y))
            return
        left, right = self.box_span()
        top = self.y + self.home_px.y()
        m = 2 * self.scale
        over = left - m <= x <= right + m and top - m <= y <= top + self.ih * self.scale + m
        if over and prev is not None and self.now - prev[2] < 400 and abs(x - prev[0]) >= 3:
            d = 1 if x > prev[0] else -1
            if self._pet_dir and d != self._pet_dir:
                self._pet_flips.append(self.now)
            self._pet_dir = d
        self._pet_flips = [t for t in self._pet_flips if self.now - t < 1500]
        if over and len(self._pet_flips) >= 3 and self.prefs["petting"]:   # stroked back and forth
            self._petting_until = self.now + 500
        if self._engaged():
            self._last_activity = self.now

    def _petting(self, dt):
        petting = self.now < self._petting_until
        if petting:
            self._pet_ms += dt
            if self.now - self._last_heart > 320:
                self._last_heart = self.now
                self._emit("heart", self.iw / 2 - 2 + random.uniform(-6, 6), -3.0,
                           vx=random.uniform(-1, 1), vy=-4.0, life=1400)
            if self.action in ("idle", "visit") and self.frame[0] == "pose":
                self.pose("happy")
            if self._pet_ms > 3500 and self.action == "idle":
                self._pet_ms = 0.0
                self.start("dance", manual=True)    # he loves it
        else:
            self._pet_ms = max(0.0, self._pet_ms - dt)
            if self._was_petting and self.action in ("idle", "visit") and self.frame == ("pose", "happy", False):
                self.pose("idle")
        self._was_petting = petting

    def _watch_cursor(self):
        """While idle, follow a nearby, moving pointer with his eyes."""
        if (self.action not in ("idle", "visit", "attention") or self.frame[0] != "pose"
                or self.now < self._petting_until or not self.prefs["pointer"]):
            self._tracking = False
            return
        eyes = None
        if self.cursor is not None and self.now - self.cursor[2] < 2500:
            x, y, _ = self.cursor
            left, right = self.box_span()
            cx, cy = (left + right) / 2, self._mid()
            near = CURSOR_NEAR * self.scale
            if abs(x - cx) < near and abs(y - cy) < near:
                half = self.iw * self.scale / 2
                eyes = "look_l" if x < cx - half else "look_r" if x > cx + half else "idle"
        if eyes is not None:
            if self.frame[1] in ("idle", "look_l", "look_r"):
                self.frame = ("pose", eyes, False)
            self._tracking = True
        elif self._tracking:
            self._tracking = False
            if self.frame[1] in ("look_l", "look_r"):
                self.frame = ("pose", "idle", False)

    def _glance(self):
        """Which way to look: usually toward the pointer, if we know where it is."""
        if self.cursor is not None and self.prefs["pointer"] and random.random() < 0.7:
            return "look_l" if self.cursor[0] < sum(self.box_span()) / 2 else "look_r"
        return random.choice(("look_l", "look_r"))

    # ── What to show ──────────────────────────────────────────────

    def show_frame(self, name, idx, mirror=False):
        self.frame = ("anim", name, idx, mirror)

    def pose(self, name):
        self.frame = ("pose", name, False)

    def _emit(self, kind, x, y, vx=0.0, vy=0.0, life=3000.0, g=0.0, flap=None, orbit=None, wave=None):
        self.particles.append({"kind": kind, "x": x, "y": y, "x0": x, "y0": y, "age": 0.0,
                               "life": life, "vx": vx, "vy": vy, "g": g, "flap": flap, "orbit": orbit,
                               "wave": wave})

    def _bubble(self, on, kind="bubble"):
        """A speech bubble over his right shoulder ("!", coffee, water), moved
        clear of his hat. The bigger ones sit clear of a raised right arm too."""
        self.particles = [q for q in self.particles if q["kind"] != kind]
        if on:
            g = self.sp.glyphs[kind]
            x = self.iw - 1 if kind != "bubble" else self.iw - (3 if self.hat() else 8)
            self._emit(kind, x, -2.0 - g.height(), life=float("inf"))

    def _age_particles(self, dt):
        for q in self.particles:
            q["age"] += dt
            if q["kind"] in BUBBLES:
                q["y"] = q["y0"] - (q["age"] // 400) % 2      # a gentle bob
            elif q["kind"] in ("done_button", "done_button_pressed"):
                flash = self.now < self._nudge_until and int(self.now // 150) % 2 == 0
                q["kind"] = "done_button_pressed" if self._button_down or flash else "done_button"
            elif q.get("wave"):                                # bobbing letters, swaying balloons
                ax, ay, w, phase = q["wave"]
                t = q["age"] / 1000
                q["x"] = q["x0"] + q["vx"] * t + ax * math.sin(phase + w * t)
                q["y"] = q["y0"] + q["vy"] * t + ay * math.sin(phase + w * t)
            elif q.get("orbit"):                               # circling his head (dizzy stars)
                cx, cy, rx, ry, w, phase = q["orbit"]
                a = phase + w * q["age"] / 1000
                q["x"], q["y"] = cx + rx * math.cos(a), cy + ry * math.sin(a)
            elif q.get("flap"):                                # a bat flapping, a curl of smoke
                q["kind"] = q["flap"][int(q["age"] // 130) % len(q["flap"])]
                q["x"] += q["vx"] * dt / 1000
                q["y"] = q["y0"] + q["vy"] * q["age"] / 1000 + (1.5 * math.sin(q["age"] / 160) if not q["vy"] else 0)
            else:
                q["vy"] += q.get("g", 0.0) * dt / 1000
                q["x"] += q["vx"] * dt / 1000
                q["y"] += q["vy"] * dt / 1000
        self.particles = [q for q in self.particles if q["age"] < q["life"]]

    def _fade(self, q):
        if q["life"] == float("inf"):
            return 1.0
        return max(0.0, min(1.0, round((q["life"] - q["age"]) / 800 * 4) / 4))

    def _pixmap(self, key):
        pm = self._pixmaps.get(key)
        if pm is not None:
            self._pixmaps.move_to_end(key)
        else:
            img = self.sp.anims[key[1]].frames[key[2]] if key[0] == "anim" else self.sp.poses[key[1]]
            if key[-1]:
                img = flipped(img)
            s = self.scale
            pm = QPixmap.fromImage(img.scaled(img.width() * s, img.height() * s,
                                              Qt.AspectRatioMode.IgnoreAspectRatio,
                                              Qt.TransformationMode.FastTransformation))
            self._pixmaps[key] = pm
            if len(self._pixmaps) > PIXMAP_CACHE:
                self._pixmaps.popitem(last=False)   # the one unused the longest
        return pm

    def _frame_pos(self, key):
        lifted = QPoint(0, self.lift * self.scale)
        if key[0] == "anim":
            return self.frame_rect(self.sp.anims[key[1]], key[2], key[3]).topLeft() - lifted
        return self.home_px - lifted

    def _prop_rect(self, img, x, y):
        s = self.scale
        return QRect(self.home_px.x() + int(round(x)) * s, self.home_px.y() + int(round(y)) * s,
                     img.width() * s, img.height() * s)

    def _extras(self):
        """His hat, props, then the cloud in front of him, as (image, rect)."""
        out = [(self.sp.props[n], self._prop_rect(self.sp.props[n], x, y)) for n, (x, y) in self.layers.items()]
        hat = self._hat_image()
        if hat is not None:
            out.insert(0, hat)
        if self.front is not None:
            cloud = self.sp.props["cloud"]
            out.append((cloud, self._prop_rect(cloud, *self.front)))
        return out

    # ── Hats ──────────────────────────────────────────────────────

    def _minute(self):
        t = self.wall()
        return t.hour * 60 + t.minute

    def is_night(self):
        m, start, end = self._minute(), self.prefs["night_from"], self.prefs["night_to"]
        return (m >= start or m < end) if start > end else start <= m < end

    def is_morning(self):
        m, start, end = self._minute(), self.prefs["night_to"], self.prefs["morning_to"]
        return (m >= start or m < end) if start > end else start <= m < end

    def hat(self):
        """The hat he's wearing right now, or None. Your birthday: the party hat
        all day, whatever else is set. Any celebration: the party hat. Then the
        hat you picked; else New Year's party hat; else, all night, his
        nightcap; else the season's (Santa, pumpkin)."""
        if self.prefs["celebrate"] and self.is_birthday():
            return "party_hat"
        if self.action in PARTIES:                 # any celebration: the party hat goes on
            return "party_hat"
        choice = self.prefs["hat"]
        if choice in HATS:
            return choice
        if choice != "auto":
            return None
        season = season_hat(self.wall().date()) if self.prefs["seasons"] else None
        if season == "party_hat":                  # New Year's Eve and Day: party, night or not
            return season
        if self.prefs["day_cycle"] and self.is_night():
            return "nightcap"                      # all night long, asleep or not
        return season

    def _hat_key(self):
        """Which hat, and which of its frames: the Santa hat's pom-pom and the
        nightcap's tail swing as he moves (slowly while he sleeps)."""
        when, key = self._hat_cache
        now = (self.now, self.frame, self.action, self._dangling, self.prefs.version)
        if when == now:
            return key
        key = self._work_out_hat()
        self._hat_cache = (now, key)
        return key

    def _work_out_hat(self):
        name = self.hat()
        if name is None:
            return None
        if name == "nightcap" and self.frame[:2] == ("anim", "stretch"):
            name = "nightcap_stretch"
        n = len(self.sp.hats[name][0])
        if self.action == "sleep":
            k = int(self.now // 1400) % n
        elif self.vx or self.action in BOUNCY:
            k = int(self.now // 260) % n
        else:
            k = 0
        return name, k

    def _head(self):
        """(his head in the current frame's cells, the frame's width, mirrored?)"""
        kind = self.frame
        if kind[0] == "anim":
            anim = self.sp.anims[kind[1]]
            return anim.heads[kind[2]], anim.w, kind[3]
        return self.sp.pose_head, self.iw, kind[2]

    def _reaching_up(self):
        """Where the frame reaches up past his head: everything above its top,
        and beside it for the next 4 rows (arms raised), in window pixels."""
        (cx, top), w, mirror = self._head()
        head_left = w - cx - HEAD_W // 2 if mirror else cx - HEAD_W // 2
        s = self.scale
        at = self._frame_pos(self.frame)
        region = QRegion(0, 0, w * s, (top + 4) * s).subtracted(
            QRegion(head_left * s, top * s, HEAD_W * s, 4 * s))
        return region.translated(at)

    def _hat_image(self):
        key = self._hat_key()
        self._hat_on = False
        if key is None:
            return None
        frames, (ax, ay) = self.sp.hats[key[0]]
        img = frames[key[1]]
        kind = self.frame
        head, w, mirror = self._head()
        if head is None:
            return None
        self._hat_on = True
        cx, top = head
        x = cx - ax                               # in the unmirrored frame's cells
        if mirror:
            x, img = w - x - img.width(), self.sp.hats_flipped[key[0]][key[1]]
        s = self.scale
        at = self._frame_pos(kind)
        return img, QRect(at.x() + x * s, at.y() + (top - ay) * s, img.width() * s, img.height() * s)

    def _glyph_rect(self, q):
        s = self.scale
        g = self.sp.glyphs[q["kind"]]
        return QRect(self.home_px.x() + int(q["x"]) * s, self.home_px.y() + int(q["y"]) * s,
                     g.width() * s, g.height() * s)

    def _refresh(self):
        """Repaint (and re-shape the window) only when something visible changed."""
        extras = self._extras()
        rects = tuple((r.x(), r.y(), r.width(), r.height()) for _, r in extras)
        state = (self.frame, self.lift, self._hat_key(), rects,
                 tuple((q["kind"], int(q["x"]), int(q["y"]), self._fade(q)) for q in self.particles))
        if state == self._shown:
            return
        shape = self._shape_of(self.frame)
        body_key = (shape, self.lift, rects)
        if body_key != self._body_key:
            body = self._masks.get((shape, self.lift))
            if body is None:
                body = self._frame_region(self.frame)
                if shape[0] == "anim":               # all its frames at once: walking doesn't reshape him
                    for i in range(len(self.sp.anims[shape[1]].frames)):
                        body = body.united(self._frame_region(("anim", shape[1], i, shape[2])))
                self._masks[(shape, self.lift)] = body
            for r in rects:
                body = body.united(QRect(*r))
            self._body_mask, self._body_key = body, body_key
        # The window is shaped to Clawd (plus his Z's, hearts and bubble): the
        # empty space around him lets clicks through to whatever is behind.
        # The shape clips painting too, which is why the extras are part of it.
        # Flying particles go in as coarse blocks, so the shape (a round trip
        # to the X server and KWin) changes now and then, not every frame.
        blocks = self._particle_blocks()
        hole = None
        if self._dangling:                       # whirled over the top he'd be over the pointer's
            gx, gy = self._grip_in_window()      # tip: keep a hole there so clicks still go through
            hole = (gx - self.grip_offset[0] - 3, gy - self.grip_offset[1] - 3, 7, 7)
        if (body_key, blocks, hole) != self._shape_key:
            mask = self._body_mask
            for b in blocks:
                mask = mask.united(QRect(*b))
            if hole is not None:
                mask = mask.subtracted(QRegion(*hole))
            if mask.isEmpty():
                mask = QRegion(0, 0, 1, 1)           # an empty mask would mean "no mask"
            self.setMask(mask)
            self.counts["shapes"] += 1
            self._mask, self._shape_key = mask, (body_key, blocks, hole)
        self._shown = state
        self.update()

    @staticmethod
    def _shape_of(frame):
        """What his window is shaped to for this frame: a pose on its own, an
        animation as all its frames together (so it doesn't change every step)."""
        return ("anim", frame[1], frame[3]) if frame[0] == "anim" else frame

    def _frame_region(self, key):
        bitmap = QBitmap.fromImage(self._pixmap(key).toImage().createAlphaMask())
        return QRegion(bitmap).translated(self._frame_pos(key))

    def _particle_blocks(self):
        b = MASK_BLOCK * self.scale
        out = set()
        for q in self.particles:
            r = self._glyph_rect(q)
            x0, y0 = r.left() // b * b, r.top() // b * b
            out.add((x0, y0, -(-(r.right() + 1) // b) * b - x0, -(-(r.bottom() + 1) // b) * b - y0))
        return tuple(sorted(out))

    def paint(self, p):
        at, pm = self._frame_pos(self.frame), self._pixmap(self.frame)
        p.drawPixmap(at, pm)
        extras = self._extras()
        for k, (img, r) in enumerate(extras):
            p.drawImage(r, img)
            if k == 0 and self._hat_on:
                # arms raised past his head come up in front of the hat's brim
                p.save()
                p.setClipRegion(self._reaching_up())
                p.drawPixmap(at, pm)
                p.restore()
        for q in self.particles:
            p.setOpacity(self._fade(q))
            p.drawImage(self._glyph_rect(q), self.sp.glyphs[q["kind"]])
        p.setOpacity(1.0)

    def paintEvent(self, _event):
        self.counts["paints"] += 1
        p = QPainter(self)
        self.paint(p)
        p.end()

    @property
    def canvas(self):
        img = QImage(self.size(), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        self.paint(p)
        p.end()
        return img

    # ── Behaviours ────────────────────────────────────────────────

    def _play(self, name, frames, mirror=False):
        a = self.sp.anims[name]
        for i in frames:
            self.show_frame(name, i, mirror)
            yield a.ms[i]

    def _loop(self, name, mirror=False, tempo=1.0):
        """The animation's loop segment, forever (the caller decides when to stop)."""
        a = self.sp.anims[name]
        lo, hi = a.loop
        while True:
            for i in range(lo, hi + 1):
                self.show_frame(name, i, mirror)
                yield a.ms[i] * tempo

    def _mirror_for(self, name):
        """Face whichever way keeps the animation on screen."""
        room_l, room_r = self._room()
        if self._pads(name, False)[1] <= room_r:
            return False
        return self._pads(name, True)[0] <= room_l

    def _come_back(self):
        """If he was left partly off-screen (a peek got interrupted), walk back into view."""
        left, right = self.box_span()
        geo = self.screen_geometry()
        if left < geo.left() or right > geo.left() + geo.width():
            yield from self._walk_to(min(max(left, geo.left() + 20),
                                         geo.left() + geo.width() - self.iw * self.scale - 20))
            self.pose("idle")

    def _act_idle(self):
        self.pose("idle")
        if self.hat() == "pumpkin_hat" and random.random() < 0.3:
            self._bats()
        yield from self._come_back()
        end, t = random.uniform(*self._rest_range()), 0.0
        while t < end or self._engaged():         # while you're playing with him, he stays with you
            hold = min(random.uniform(1500, 4000), max(250.0, end - t))
            yield hold
            t += hold
            r = random.random()
            if r < 0.5:
                for _ in range(2 if random.random() < 0.2 else 1):
                    self.pose("blink")
                    yield 110
                    self.pose("idle")
                    yield 150
            elif r < 0.88:
                self.pose(self._glance())
                look = random.uniform(500, 1400)
                yield look
                t += look
                self.pose("idle")
            else:
                self.pose("happy")
                yield 900
                t += 900
                self.pose("idle")

    def _walk_to(self, target, speed=None, tempo=None):
        """Crab-walk until the idle pose's left edge reaches `target` (at the
        faster trip pace when it's far)."""
        far = abs(target - self.box_span()[0]) > 600
        speed = speed or (TRIP_SPEED if far else WALK_SPEED)
        tempo = tempo or (TRIP_TEMPO if far else WALK_TEMPO)
        direction = 1 if target > self.box_span()[0] else -1
        self.vx = direction * speed * self.scale
        steps = self._loop("walk", mirror=direction < 0, tempo=tempo)
        while self.vx and (target - self.box_span()[0]) * direction > 0:
            yield next(steps)
        self.vx = 0.0
        if self.offscreen:
            self.set_box_left(target)

    def _act_walk(self, target=None):
        speed, tempo = WALK_SPEED, WALK_TEMPO
        if target is None:
            if random.random() < 0.25 and len(screen_areas()) > 1:
                # a trip: anywhere on the desktop, across screens if need be
                a = random.choice(screen_areas())
                target = a.left() + random.uniform(0, a.width() - self.iw * self.scale)
                speed, tempo = TRIP_SPEED, TRIP_TEMPO
            else:
                room_l, room_r = self._reach()
                direction = 1 if random.uniform(0, room_l + room_r) < room_r else -1
                room = room_r if direction > 0 else room_l
                dist = min(room, random.uniform(60, 420))
                if dist < 8:
                    return
                target = self.box_span()[0] + direction * dist
        for _ in range(4):                 # a few seams at most
            yield from self._walk_to(target, speed, tempo)
            left = self.box_span()[0]
            if abs(left - target) < 4:
                return
            # Stopped short: a wall. If a higher screen is what's in the way,
            # get up there (by ladder, mostly).
            seam = self._seam("up")
            if seam is None or (seam[0] > left) != (target > left):
                return
            yield from (self._act_climb() if random.random() < 0.7 else self._act_leap())

    def _arc_to(self, box_left, floor, peak, hold=False):
        """A hop under real GRAVITY landing with his box at `box_left` and his
        feet on `floor`, peaking `peak` px above the higher end. Going up, he
        only drifts sideways once his feet have risen past the landing height,
        so he never clips the corner where two screens meet."""
        s = self.scale
        g = GRAVITY * s
        x0, y0 = self.x, self.y
        x1 = box_left - self.home_px.x()
        y1 = floor - self.home_px.y() - self.ih * s
        apex = min(y0, y1) - peak
        t_up = (2 * (y0 - apex) / g) ** 0.5
        t_down = (2 * (y1 - apex) / g) ** 0.5
        total = t_up + t_down
        t_clear = t_up - t_down if y1 < y0 else 0.0
        self.scripted = True
        t = 0.0
        while t < total:
            yield TICK_MS
            t = min(total, t + TICK_MS / 1000)
            if t < t_up:
                self.y = apex + 0.5 * g * (t_up - t) ** 2
            else:
                self.y = apex + 0.5 * g * (t - t_up) ** 2
            u = 0.0 if t <= t_clear else (t - t_clear) / (total - t_clear)
            self.x = x0 + (x1 - x0) * u * u * (3 - 2 * u)
        self.x, self.y = x1, y1
        self.scripted = hold

    def _act_leap(self):
        """Jump up onto the higher screen next to this one."""
        seam = self._seam("up")
        if seam is None:
            return
        x, _, other = seam
        s = self.scale
        width = self.iw * s
        side = 1 if other.left() >= x else -1
        yield from self._walk_to(x - width - 3 * s if side > 0 else x + 3 * s)
        self.pose("idle")
        yield 200
        land = random.uniform(10, 40) * s
        self.show_frame("jump", 1)           # crouch
        yield 120
        self.show_frame("jump", 2)           # arms up
        yield from self._arc_to(x + land if side > 0 else x - width - land,
                                other.top() + other.height(), 10 * s)
        yield from self._play("jump", range(8, len(self.sp.anims["jump"].frames)))

    def _act_climb(self):
        """Pull out a ladder and climb to the neighbouring screen, up or down."""
        left = self.box_span()[0]
        options = [(abs(seam[0] - left), seam, up)
                   for seam, up in ((self._seam("up", near=None), True), (self._seam("down", near=None), False))
                   if seam is not None]
        if not options:
            return
        _, (seam, here, other), up = min(options, key=lambda o: o[0])
        s = self.scale
        width, lw = self.iw * s, LADDER_W * s
        lower, upper = (here, other) if up else (other, here)
        low = lower.top() + lower.height()
        high = upper.top() + upper.height()
        lower_left = lower.left() < seam            # is the lower screen left of the seam?
        # The ladder stands on the lower screen by the seam, just far enough
        # in that Clawd (wider than it) fits on it without poking past the
        # edge, and pokes up a little past the higher floor like a real one.
        margin = s + (width - lw) / 2
        ladder_x = seam - margin - lw if lower_left else seam + margin
        on_ladder = ladder_x + lw / 2 - width / 2
        rect = QRect(int(ladder_x), int(high - 5 * s), lw, int(low - high + 5 * s))
        if up:
            yield from self._walk_to(on_ladder)
            self.pose("idle")
            yield 250
            yield from self._ladder_out(rect, from_top=False)
            yield from self._climb_to(high)
            self.show_frame("jump", 2)
            yield from self._arc_to(seam + 4 * s if lower_left else seam - width - 4 * s, high, 4 * s)
        else:
            yield from self._walk_to(seam + s if lower_left else seam - width - s)
            self.pose("idle")
            yield 250
            yield from self._ladder_out(rect, from_top=True)
            self.show_frame("jump", 2)
            yield from self._arc_to(on_ladder, high, 3 * s, hold=True)
            yield from self._climb_to(low)
            self.scripted = False
        self.pose("idle")
        yield from self._ladder_in()

    def _ladder_out(self, rect, from_top):
        s = self.scale
        img = grid_image(ladder_rows(max(1, rect.height() // s)), LADDER_PALETTE)
        pm = QPixmap.fromImage(img.scaled(img.width() * s, img.height() * s,
                                          Qt.AspectRatioMode.IgnoreAspectRatio,
                                          Qt.TransformationMode.FastTransformation))
        self.ladder.image = pm
        self.ladder.from_top = from_top
        self.ladder.reveal = 0.0
        # feet of the ladder exactly on the lower floor
        self.ladder.setGeometry(rect.x(), rect.y() + rect.height() - pm.height(), pm.width(), pm.height())
        self.ladder.show()
        self.raise_()                            # Clawd climbs in front of it
        for k in range(1, 13):                   # it telescopes out
            self.ladder.reveal = k / 12
            self.ladder.update()
            yield 35

    def _ladder_in(self):
        for k in range(11, -1, -1):
            self.ladder.reveal = k / 12
            self.ladder.update()
            yield 30
        self.ladder.hide()

    def _climb_to(self, feet):
        """Hand over hand up (or down) the ladder until his feet are at `feet`."""
        s = self.scale
        target = feet - self.home_px.y() - self.ih * s
        direction = -1 if target < self.y else 1
        self.scripted = True
        i = 0
        name = "climb_ladder" if "climb_ladder" in self.sp.anims else "climb"
        steps = len(self.sp.anims[name].frames)
        while (target - self.y) * direction > 0.5:
            # climbing down, the cycle runs backwards
            self.show_frame(name, i % steps if direction < 0 else (-i - 1) % steps)
            i += 1
            for _ in range(8):                   # about 130 ms per hand-over-hand step
                yield TICK_MS
                self.y += direction * CLIMB_SPEED * s * TICK_MS / 1000
                if (target - self.y) * direction <= 0:
                    break
        self.y = target

    def _ride(self, name, speed):
        room_l, room_r = self._room()
        direction = 1 if room_r >= room_l else -1
        mirror = direction < 0
        pad = self._pads(name, mirror)
        if max(room_l, room_r) < max(pad) + 150:
            yield from self._act_walk()          # not enough room to get going
            return
        self.pad = pad
        # step in from the edge if the vehicle would poke off the screen
        lo, hi = self._limits()
        self.x = min(max(self.x, lo), hi)
        a = self.sp.anims[name]
        lo_frame, _ = a.loop
        yield from self._play(name, range(0, lo_frame), mirror)       # climb aboard
        start, dist = self.x, random.uniform(250, 900)
        self.vx = direction * speed * self.scale
        steps = self._loop(name, mirror)
        while self.vx and abs(self.x - start) < dist:
            yield next(steps)
        self.vx = 0.0
        c, d = a.outro
        yield from self._play(name, range(c, d + 1), mirror)          # hop off

    def _act_cloud(self):
        yield from self._ride("cloud", CLOUD_SPEED)

    def _act_race(self):
        yield from self._ride("race", RACE_SPEED)

    def _act_laptop(self, until_idle=False, duration=None):
        mirror = self._mirror_for("laptop")
        self.pad = self._pads("laptop", mirror)
        a = self.sp.anims["laptop"]
        yield from self._play("laptop", range(0, a.loop[0]), mirror)
        lo, hi = a.loop
        i, typed = lo, 0.0
        end = duration if duration is not None else random.uniform(3000, 9000)
        while (self.claude_mode() == "busy") if until_idle else typed < end:
            # Still typing, but he looks up at you, or beams while you pet him.
            react = self._typing_reaction()
            if react is None:
                self.show_frame("laptop", i, mirror)
            else:
                if mirror and react in ("look_l", "look_r"):
                    react = "look_r" if react == "look_l" else "look_l"
                self.show_frame("type_" + react, i - lo, mirror)
            yield a.ms[i]
            typed += a.ms[i]
            i = lo if i == hi else i + 1
        c, d = a.outro
        yield from self._play("laptop", range(c, d + 1), mirror)

    def _typing_reaction(self):
        """While typing: happy eyes while you pet him, and a quick look at a
        pointer that comes close, just for a moment (then a cooldown)."""
        if self.now < self._petting_until:
            return "happy"
        if self.now < self._glance_until:
            return self._glance_dir
        c = self.cursor
        if (c is None or self.now - c[2] > 700 or self.now < self._glance_cooldown
                or not self.prefs["pointer"]):
            return None
        left, right = self.box_span()
        cx, near = (left + right) / 2, CURSOR_NEAR * self.scale
        if abs(c[0] - cx) < near and abs(c[1] - self._mid()) < near:
            self._glance_dir = "look_l" if c[0] < cx else "look_r"
            self._glance_until = self.now + GLANCE_MS
            self._glance_cooldown = self.now + GLANCE_COOLDOWN
            return self._glance_dir
        return None

    def _act_work(self, demo=False):
        """While Claude Code works, do what it's doing: read with glasses on,
        search with the magnifying glass, go out on the cloud for the web,
        type on the laptop for everything else. As a preview, a bit of each."""
        yield from self._come_back()
        if demo:
            for style in ("type", "read", "search", "web"):
                started = self.now
                yield from self._work_in(style, lambda: self.now - started > 3500)
            self.work_style = None
            return
        while self.claude_mode() == "busy":
            style, started = self.tool_style, self.now

            def done(style=style, started=started):
                return self.claude_mode() != "busy" or (
                    self.tool_style != style and self.now - started >= STYLE_HOLD)
            yield from self._work_in(style, done)
        self.work_style = None

    def _work_in(self, style, done):
        self.work_style = style
        if style == "read":
            yield from self._work_read(done)
        elif style == "search":
            yield from self._work_search(done)
        elif style == "web":
            yield from self._work_web(done)
        else:
            yield from self._work_type(done)

    def _work_type(self, done):
        mirror = self._mirror_for("laptop")
        self.pad = self._pads("laptop", mirror)
        a = self.sp.anims["laptop"]
        yield from self._play("laptop", range(0, a.loop[0]), mirror)
        lo, hi = a.loop
        i = lo
        while not done():
            yield from self._maybe_stumble()
            react = self._typing_reaction()
            if react is None:
                self.show_frame("laptop", i, mirror)
            else:
                if mirror and react in ("look_l", "look_r"):
                    react = "look_r" if react == "look_l" else "look_l"
                self.show_frame("type_" + react, i - lo, mirror)
            yield a.ms[i]
            i = lo if i == hi else i + 1
        c, d = a.outro
        yield from self._play("laptop", range(c, d + 1), mirror)

    def _face(self, default):
        """His face while working with props: happy while petted, a quick look
        at a nearby pointer, else `default`."""
        react = self._typing_reaction()
        return default if react is None else react

    def _work_read(self, done):
        aside, page = (18, 6), PAGE_AT
        yield from self._move_layer("page", aside, page, 5, 60)
        yield from self._move_layer("glasses", (18, 3), GLASSES_AT, 5, 50)
        k = 0
        while not done():
            yield from self._maybe_stumble()
            self.pose(self._face(("read_l", "read_r")[k % 2]))
            k += 1
            yield 420
        yield from self._move_layer("glasses", GLASSES_AT, (18, 3), 5, 50)
        del self.layers["glasses"]
        yield from self._move_layer("page", page, aside, 5, 50)
        del self.layers["page"]
        self.pose("idle")

    def _work_search(self, done):
        """Sweeping the magnifying glass over the ground in front of him."""
        lens_y = self.ih - 5 - MAGNIFIER_LENS[1]
        spots = [(x - MAGNIFIER_LENS[0], lens_y) for x in (4, 12, 20, 12)]
        yield from self._move_layer("magnifier", (20, 4), spots[0], 5, 60)
        at, k = spots[0], 0
        while not done():
            yield from self._maybe_stumble()
            nxt = spots[(k + 1) % len(spots)]
            for step in range(1, 7):
                t = step / 6
                self.layers["magnifier"] = (at[0] + (nxt[0] - at[0]) * t, at[1] + (nxt[1] - at[1]) * t)
                self.pose(self._face("read_l" if nxt[0] < at[0] else "read_r"))
                yield 90
            at, k = nxt, k + 1
            if random.random() < 0.15:
                self._emit("excl", 11, -8, vy=-2, life=900)
        yield from self._move_layer("magnifier", at, (20, 4), 5, 50)
        del self.layers["magnifier"]
        self.pose("idle")

    def _work_web(self, done):
        """Out on the web: up on his cloud, bobbing and looking around."""
        a = self.sp.anims["cloud"]
        yield from self._play("cloud", range(0, a.loop[0]))              # hop on
        self.lift, self.front = CLOUD_LIFT, self.sp.cloud_at
        self.y -= 2 * self.scale                                          # float up a touch
        base = self.front
        self.scripted = True
        k = 0
        while not done():
            yield from self._maybe_stumble()
            self.front = (base[0], base[1] + (k // 3) % 2)                 # a gentle bob
            self.pose(self._face(("look_l", "idle", "look_r", "idle")[(k // 2) % 4]))
            k += 1
            yield 300
        self.front = base
        self._leave_cloud()
        self.y += 2 * self.scale
        self.scripted = False
        c, d = a.outro
        yield from self._play("cloud", range(c, d + 1))                  # hop off

    def _maybe_stumble(self):
        """A tool just failed: a stumble with a sweat drop, then back to work."""
        if not self._stumble:
            return
        self._stumble = False
        s, head = self.scale, -self.lift
        frame, base = self.frame, self.x
        self.pose("surprised")
        self._emit("drop", 21, head + 1, vy=1, g=20, life=1000)
        for k in range(8):
            self.x = base + (s if k % 2 else -s)
            yield 70
        self.x = base
        self.frame = frame

    def _act_attention(self, demo=False):
        """Claude Code needs you: wave with a "!" until it's answered (or a while, as a preview)."""
        yield from self._come_back()
        self._bubble(True)
        start = self.now
        waiting = lambda: self.claude_mode() == "attention" or (demo and self.now - start < 6000)  # noqa: E731
        while waiting():
            yield from self._once("wave")
            self.pose("idle")
            t = 0
            while t < 2500 and waiting():
                yield 100
                t += 100
        self._bubble(False)

    def _act_celebrate(self):
        took, self.celebrate = self.celebrate or 0, None
        self._confetti()
        yield from self._once("sparkler" if took > 90_000 else "jump_happy")

    def _act_dance(self):
        self._confetti()
        a = self.sp.anims["dance"]
        lo, hi = a.loop
        yield from self._play("dance", range(0, lo))
        for _ in range(random.randint(1, 3)):
            yield from self._play("dance", range(lo, hi + 1))
        c, d = a.outro
        yield from self._play("dance", range(c, d + 1))

    def _once(self, name):
        mirror = self._mirror_for(name)
        self.pad = self._pads(name, mirror)
        yield from self._play(name, range(len(self.sp.anims[name].frames)), mirror)

    def _act_wave(self):
        yield from self._once("wave")

    def _act_jump(self):
        yield from self._once("jump")

    def _act_jump_happy(self):
        self._confetti()
        yield from self._once("jump_happy")

    # ── Holidays ──────────────────────────────────────────────────

    def _confetti(self, n=14):
        """A burst of confetti, when he's in his party hat."""
        if self.hat() != "party_hat":
            return
        for _ in range(n):
            self._emit(f"confetti_{random.randrange(5)}", self.iw / 2 + random.uniform(-7, 7),
                       random.uniform(-6, -2), vx=random.uniform(-10, 10), vy=random.uniform(-15, -7),
                       g=30, life=1800)

    def _bats(self):
        """A bat or two flapping past, in his pumpkin week."""
        for k in range(random.randint(1, 2)):
            side = random.choice((-1, 1))
            x = -18.0 if side > 0 else self.iw + 16.0
            self._emit("bat_0", x - side * k * 7, random.uniform(-12, -5), vx=side * random.uniform(14, 20),
                       life=3200, flap=("bat_0", "bat_1"))

    def _new_year_due(self):
        t = self.wall()
        return (self.prefs["seasons"] and (t.month, t.day, t.hour) == (1, 1, 0) and t.minute < 15
                and self._new_year != t.year)

    def _act_new_year(self):
        """Midnight on New Year's Eve: party hat, confetti and a dance."""
        self._new_year = self.wall().year
        yield from self._come_back()
        for _ in range(3):
            self._confetti(20)
            yield from self._once("jump_happy")
        yield from self._act_dance()

    def _act_sparkler(self):
        yield from self._once("sparkler")

    def _act_lurk(self):
        """Walk off the nearer edge, peek back in, then come back."""
        geo = self.screen_geometry()
        room_l, room_r = self._room()
        width = self.iw * self.scale
        side = self._lurk_side() or (-1 if room_l <= room_r else 1)
        right_edge = geo.left() + geo.width()
        outside = geo.left() - width - self.scale if side < 0 else right_edge + self.scale
        self.offscreen = True
        yield from self._walk_to(outside)
        # Clawd-Lurking peeks in from its left edge; mirrored, from the right.
        self.set_box_left(geo.left() if side < 0 else right_edge - width)
        yield from self._play("lurk", range(len(self.sp.anims["lurk"].frames)), mirror=side > 0)
        self.set_box_left(outside)
        back = random.uniform(40, 200)
        yield from self._walk_to(geo.left() + back if side < 0 else right_edge - width - back)
        self.offscreen = False

    # ── Your birthday ─────────────────────────────────────────────

    def _resize_in_place(self, scale):
        """Bigger or smaller about his middle, feet on the floor, not saved."""
        centre, bottom = sum(self.box_span()) / 2, self._feet()
        self.scale = scale
        self._pixmaps.clear()
        self._masks.clear()
        self._layout()
        self.set_box_left(centre - self.iw * scale / 2)
        self.y = bottom - self.home_px.y() - self.ih * scale
        self.move(int(self.x), int(self.y))
        self._shown = None

    def _grow(self, to):
        """Puff up (or back down) a size at a time, with a springy overshoot."""
        step = 1 if to > self.scale else -1
        for sc in list(range(self.scale + step, to + step, step)) + ([to + step, to] if 3 <= to + step <= 8 else []):
            self._resize_in_place(sc)
            yield 70

    def _act_birthday(self):
        """It's your birthday: he notices the date, puffs up to double size in a
        party hat, jumps about in confetti with HAPPY BIRTHDAY popping up above
        him and balloons floating up, brings out a cake, makes a wish, blows out
        the candles, dances, and shrinks back. Once a day; the hat stays on."""
        today = self.wall().date().isoformat()
        if self.settings is not None:
            self.settings.setValue("birthday_done", today)
        self._party_done = today
        yield from self._come_back()
        base = self._true_scale or self.scale
        self._true_scale = base
        # it's today!
        self.pose("look_l")
        yield 300
        self.pose("look_r")
        yield 300
        self.pose("surprised")
        self._emit("excl", 11, -8, vy=-2, life=900)
        yield 700
        # ta-da: twice the size
        self.pose("happy")
        yield from self._grow(min(8, base * 2))
        yield 300
        for k in range(3):
            self._confetti(18)
            yield from self._once("jump_happy")
        yield from self._birthday_words()
        yield from self._balloons()
        yield from self._birthday_cake()
        self._confetti(24)
        yield from self._act_dance()
        yield from self._grow(base)
        self._true_scale = None
        self._resize_in_place(base)
        self.pose("happy")
        yield 900

    def _birthday_words(self):
        """HAPPY / BIRTHDAY! popping up letter by letter above him, then bobbing."""
        letters = getattr(self.sp, "letters", None)
        if not letters:
            yield 0
            return
        gap = 1
        lines = ("HAPPY", "BIRTHDAY!")
        h = max(img.height() for img in letters.values())
        base_y = -14 - 2 * (h + 2)                             # above his head and party hat
        k = 0
        for row, word in enumerate(lines):
            width = sum(letters[ch].width() for ch in word) + gap * (len(word) - 1)
            x = self.iw / 2 - width / 2
            y = base_y + row * (h + 2)
            for ch in word:
                self._emit("letter_" + ch, x, y, life=16_000, wave=(0.0, 0.8, 5.0, k * 0.55))
                x += letters[ch].width() + gap
                k += 1
                yield 70
        yield 600

    def _balloons(self):
        n = sum(1 for k in self.sp.glyphs if k.startswith("balloon_"))
        for k in range(5 if n else 0):
            self._emit(f"balloon_{k % n}", random.uniform(-10, self.iw + 4), random.uniform(0, 8),
                       vy=-random.uniform(3.5, 5.5), life=9000, wave=(1.2, 0.0, random.uniform(2, 3), k))
            yield 220
        yield 300

    def _birthday_cake(self):
        """The cake on the floor beside him: a wish, a big breath, candles out."""
        if "cake_0" not in self.sp.props:
            yield 0
            return
        hx, hy = self.sp.cake_hold
        at = (self.iw + 2 + self.sp.props["cake_0"].width() / 2 - hx, self.ih - hy)   # standing beside him
        for k in range(8):                                  # lit, the flames flickering
            self.layers = {f"cake_{k % 2}": at}
            if k == 3:
                self.pose("blink")                          # eyes shut: a wish
            yield 160
        self.pose("happy")
        yield 300
        face = (self.iw - 4, 4)
        for k in range(4):                                  # a big puff at the candles
            self._emit(f"puff_{k % 2}", face[0] + k * 2, face[1] + random.uniform(-1, 1), vx=12, life=450)
            yield 90
        self.layers = {"cake_2": at}                         # out
        for fx, fy in self.sp.cake_flames:
            self._emit("smoke_0", at[0] + fx - 1, at[1] + fy - 4, vy=-3, life=1600,
                       flap=("smoke_0", "smoke_1", "smoke_2"))
        for k in range(3):
            self._emit("heart", self.iw / 2 - 2 + random.uniform(-6, 6), -3.0,
                       vx=random.uniform(-1, 1), vy=-4.0, life=1400)
        yield 1400
        self.layers = {}

    # ── The wallpaper ─────────────────────────────────────────────

    def watch_wallpaper(self, path=PLASMA_DESKTOP_RC):
        """Be ready for the wallpaper changing: slideshows on the clock, and
        Plasma's config for a picture you set yourself."""
        self._wall_path = path
        self._walls = plasma_wallpapers(path)
        self._wall_timer = QTimer(self)
        self._wall_timer.setSingleShot(True)
        self._wall_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._wall_timer.timeout.connect(self._slide_turned)
        self._wall_watch = QFileSystemWatcher(self)
        if os.path.exists(path):
            self._wall_watch.addPath(path)
        self._wall_watch.fileChanged.connect(lambda _p: QTimer.singleShot(500, self._wall_config_changed))
        self._schedule_slide()

    def _schedule_slide(self):
        at = next_slide_change(self._walls, time.time())
        if at is not None:
            self._wall_timer.start(int((at - time.time()) * 1000) + 300)   # just as the new one fades in

    def _slide_turned(self):
        self.wallpaper_changed()
        self._schedule_slide()

    def _wall_config_changed(self):
        if self._wall_path not in self._wall_watch.files() and os.path.exists(self._wall_path):
            self._wall_watch.addPath(self._wall_path)        # it's saved by replacing the file
        walls = plasma_wallpapers(self._wall_path)
        changed = wallpapers_differ(self._walls, walls)
        self._walls = walls
        self._schedule_slide()
        if changed:
            self.wallpaper_changed()

    def wallpaper_changed(self):
        """A new wallpaper behind him: he spins round to look, and jumps out of his skin."""
        if (self.manual or self.dragging or self.airborne or self._dangling or self.ducked
                or self.action in ("startled", "attention", "settings", "held", "fall", "duck", "grab")
                or self.action in REMINDERS or self.action == "work"
                or self.now - self._startled_at < 60_000):
            return
        self._startled_at = self.now
        self.start("startled")

    def _act_startled(self):
        look = random.choice(("look_l", "look_r"))
        self.pose(look)                                     # huh? something changed
        yield 260
        self.pose("idle")
        yield 140
        self.pose("back")                                   # turns round to look
        yield 800
        self.pose("surprised")                              # whoa!
        self._emit("excl", 11, -8, vy=-2, life=900)
        yield 120
        yield from self._once("jump")
        for k in (-1, 1):
            self._emit("drop", 2 if k < 0 else 19, 0.0, vx=5 * k, vy=-7, g=40, life=900)
        self.pose("back")                                   # another look
        yield 700
        self.pose("happy")                                  # ...nice, actually
        yield 900
        self.pose("idle")
        yield 300

    # ── His settings, open ────────────────────────────────────────

    def _toward_settings(self):
        d = getattr(self, "_settings_dialog", None)
        if d is None:
            return "look_r"
        left, right = self.box_span()
        return "look_l" if d.geometry().center().x() < (left + right) / 2 else "look_r"

    def _land_first(self):
        """Up in the air (he was hanging off the pointer, say)? Come down first."""
        if self.airborne or self.y < self.ground_y() - 1:
            self.airborne = True
            yield from self._fall()

    def _act_settings(self):
        """Somebody's in his settings! A double take, a start, a bit of sweat;
        then on go his reading glasses and out comes a page, to read along
        with you and take each change as it comes. Closed: relief."""
        yield from self._land_first()
        yield from self._come_back()
        look = self._toward_settings()
        self.pose(look)
        yield 450
        self.pose("idle")
        yield 250
        self.pose("surprised")                              # wait, what?
        self._emit("excl", 11, -8, vy=-2, life=900)
        yield 350
        yield from self._once("jump")
        for k in (-1, 1):                                   # a bit of sweat flies off
            self._emit("drop", 2 if k < 0 else 19, 0.0, vx=5 * k, vy=-7, g=40, life=900)
        self.pose(look)
        yield 800
        yield from self._reading_props(True)
        k = 0
        while self._settings_open():
            if self._settings_news:
                key, old, new = self._settings_news.pop(0)
                self._settings_news = [n for n in self._settings_news if n[0] != key]
                yield from self._take_in(key, old, new)
                continue
            self.pose(("read_l", "read_r")[k % 2])
            k += 1
            yield 420
        yield from self._reading_props(False)
        self.pose("happy")                                  # phew
        self._emit("heart", self.iw / 2 - 2, -3.0, vy=-4.0, life=1400)
        yield 500
        yield from self._once("jump_happy")

    def _reading_props(self, on):
        aside = [(18, 6), (18, 3)]
        spots = [PAGE_AT, GLASSES_AT]
        for name, off, at in zip(("page", "glasses"), aside, spots):
            if on:
                yield from self._move_layer(name, off, at, 5, 55)
            elif name in self.layers:
                yield from self._move_layer(name, self.layers[name], off, 5, 50)
                del self.layers[name]

    def _take_in(self, key, old, new):
        """How he takes a change to his settings."""
        if key == "activity" and new in ("lively", "calm"):
            yield from self._reading_props(False)
            if new == "lively":                             # yes!
                a = self.sp.anims["dance"]
                yield from self._play("dance", range(a.loop[0], a.loop[1] + 1))
            else:                                           # a big, slow stretch
                yield from self._stretch(900)
            yield from self._reading_props(True)
        elif key == "scale":                                # whoa: look at me
            self.pose("surprised")
            self._emit("excl", 11, -8, vy=-2, life=800)
            yield 500
            for side in ("look_l", "look_r", "look_l"):
                self.pose(side)
                yield 260
            self.pose("happy")
            yield 500
        elif key == "hat" and new != "none":                # ooh, a hat
            yield from self._reading_props(False)
            for k in range(3):
                self._emit("spark", 6 + 6 * k, -4, vy=-5, life=700)
            yield from self._once("jump_happy")
            yield from self._reading_props(True)
        elif key == "quiet" and new:                        # shh
            self.pose("blink")
            x, y = self._z_spot()
            self._emit("z_small", x, y, vx=0.7, vy=-2.0, life=1600)
            yield 1000
        elif ((key == "scenes_off" and len(new) > len(old)) or new is False
              or (key == "hat" and new == "none")):         # aww
            self.pose("sad")
            self._emit("drop", EYES[0][0], EYES[0][1] + 2, vy=2, g=40, life=1100)
            yield 1300
        elif key in ("break_every", "water_every"):         # noted
            self.pose("happy")
            yield 400
        else:                                               # nice
            self.pose("happy")
            self._emit("heart", self.iw / 2 - 2, -3.0, vy=-4.0, life=1400)
            yield 900

    # ── Hanging off the pointer ───────────────────────────────────

    def _grip_in_window(self, key=None):
        """Where his hands are in the window, for a dangle or spin frame: the
        point he holds on by (between the frame's two middle columns, at the
        top of his hands, which is also what a spin frame turns about)."""
        key = key or self.frame
        a = self.sp.anims[key[1]]
        at = self._frame_pos(key)
        gx, gy = a.grip
        return at.x() + (a.w - gx if key[3] else gx) * self.scale, at.y() + gy * self.scale

    def _pointer_spot(self, key=None):
        """The window position that puts his hands on the pointer's tail."""
        x, y, _ = self.cursor
        gx, gy = self._grip_in_window(key)
        return x + self.grip_offset[0] - gx, y + self.grip_offset[1] - gy

    def _place_on_pointer(self):
        if self.cursor is not None:
            self.x, self.y = self._pointer_spot()

    def _can_reach(self, x, y, side=10):
        """Is the pointer above his head (give or take `side` cells), where he can jump up to it?"""
        left, right = self.box_span()
        s = self.scale
        top = self.y + self.home_px.y() - self.lift * s
        return left - side * s <= x <= right + side * s and top - GRAB_REACH * s <= y <= top - 2 * s

    def _maybe_grab(self, dt):
        """Held right above him, the pointer always gets grabbed (whatever he's
        up to on his own). Hanging around near him, it often does: one chance
        each time it comes by, and not again for a while."""
        c = self.cursor
        if (c is None or self.airborne or self.scripted or self.lift or self.offscreen or self.dragging
                or not self.prefs["grab"] or not self.sp.dangle_drawn):
            self._linger = 0.0
            return
        if self._can_reach(c[0], c[1], GRAB_SURE_SIDE):
            if self.manual or self.action in NEVER_GRAB or not self._sure_armed:
                self._linger = 0.0
                return
            self._linger = max(self._linger, 0.0) + dt
            if self._linger >= GRAB_HOLD:
                self._linger = 0.0
                self.start("grab")
            return
        self._sure_armed = True                    # moved off: next time it's held above him, he grabs
        if (self.action not in GRAB_WHILE or self.manual or self.prefs["quiet"]
                or self.now < self._grab_cool or self.now - c[2] > 5000
                or not self._can_reach(c[0], c[1], GRAB_SIDE)):
            self._linger = 0.0
            return
        self._linger += dt
        if self._linger >= GRAB_LINGER:
            self._linger = -float("inf")          # rolled for this visit
            if random.random() < GRAB_CHANCE:
                self.start("grab")

    def _act_grab(self):
        """Jump up and grab the pointer, dangle from it while it moves
        (swinging, kicking his legs), and let go when he's had enough or you
        shake him off."""
        if self.cursor is None:
            return
        yield from self._come_back()
        c = self.cursor
        left, right = self.box_span()
        if not self._can_reach(c[0], c[1]):     # a few steps to get under it; any further, he just leaps
            room_l, room_r = self._room()
            w = right - left
            target = min(max(c[0] - w / 2, left - room_l + 10), right + room_r - w - 10)
            if 30 < abs(target - left) <= GRAB_WALK * self.scale:
                yield from self._walk_to(target)
        self.show_frame("jump", 1)                  # crouch
        yield 160
        yield from self._leap_to_pointer()
        yield from self._dangle()

    def _leap_to_pointer(self):
        """Up to the pointer, homing in on it if it moves, arms up."""
        self.scripted = True
        x0, y0 = self.x, self.y
        catch = ("anim", "dangle", STRAIGHT, False)
        tx, ty = self._pointer_spot(catch)
        dist = ((tx - x0) ** 2 + (ty - y0) ** 2) ** 0.5
        dur = 320 + min(700.0, dist * 0.9)
        arc = 6 * self.scale + 0.12 * dist            # a long leap arcs higher
        self.show_frame("cheer", 0)
        t = 0.0
        while t < dur:
            yield TICK_MS
            t = min(dur, t + TICK_MS)
            u = t / dur
            tx, ty = self._pointer_spot(catch)
            e = u * u * (3 - 2 * u)
            self.x = x0 + (tx - x0) * e
            self.y = y0 + (ty - y0) * e - arc * 4 * u * (1 - u)

    def _dangle(self):
        self._dangling, self._release = True, False
        self.scripted = True
        self._swing = self._swing_v = 0.0
        s = self.scale
        length = 14 * s                             # grip to his middle
        end = self.now + random.uniform(*DANGLE_FOR)
        prev_x, vx, vy, prev_y = None, 0.0, 0.0, None
        ax_f = ay_f = 0.0
        turned, dizzy = 0.0, False
        flips, shake_dir = [], 0
        happy_until = self.now + 2500
        kick_at, kick_until = self.now + random.uniform(3000, 7000), 0.0
        face, face_until = "idle", 0.0
        tired_at = None
        while True:
            c = self.cursor
            if c is None:
                break
            dt = TICK_MS / 1000
            gx, gy = c[0] + self.grip_offset[0], c[1] + self.grip_offset[1]
            nvx = 0.0 if prev_x is None else (gx - prev_x) / dt
            nvy = 0.0 if prev_y is None else (gy - prev_y) / dt
            ax = max(-40_000.0, min(40_000.0, (nvx - vx) / dt))
            ay = max(-40_000.0, min(40_000.0, (nvy - vy) / dt))
            ax_f, ay_f = (ax_f + ax) / 2, (ay_f + ay) / 2      # the pointer's jerks, smoothed a little
            vx, vy, prev_x, prev_y = nvx, nvy, gx, gy
            # A pendulum on a moving pivot: gravity pulls him back under the
            # pointer, and its moves, up and down as well as sideways, swing
            # him; whirl it round and he goes right over the top.
            before = self._swing
            for _ in range(4):
                h = dt / 4
                th = self._swing
                acc = (-((GRAVITY * s - ay_f) * math.sin(th) + ax_f * math.cos(th)) / length
                       - SWING_DAMP * self._swing_v)
                self._swing_v = max(-30.0, min(30.0, self._swing_v + acc * h))
                self._swing = th + self._swing_v * h
            self._swing = (self._swing + math.pi) % (2 * math.pi) - math.pi
            turned = turned * math.exp(-dt / 4) + abs((self._swing - before + math.pi) % (2 * math.pi) - math.pi)
            if turned > 4 * math.pi:
                dizzy = True                              # that was a good spin
            # shaken back and forth: he can't hold on
            if abs(nvx) > SHAKE_SPEED:
                d = 1 if nvx > 0 else -1
                if shake_dir and d != shake_dir:
                    flips.append(self.now)
                shake_dir = d
            flips = [t for t in flips if self.now - t < SHAKE_MS]
            if len(flips) >= SHAKE_FLIPS or self._release:
                vx += length * math.cos(self._swing) * self._swing_v      # flung off with his swing
                vy -= length * math.sin(self._swing) * self._swing_v
                break
            spinning = abs(self._swing) > SPIN_FROM or abs(self._swing_v) > 3
            if tired_at is None and self.now >= end:
                if spinning:                        # had enough mid-spin: off he flies
                    vx += length * math.cos(self._swing) * self._swing_v
                    vy -= length * math.sin(self._swing) * self._swing_v
                    break
                tired_at = self.now                 # had enough: one hand, then off
            if tired_at is not None and self.now - tired_at > 1400:
                break
            if dizzy and not spinning and abs(self._swing) < 0.3:
                dizzy, turned = False, 0.0          # round and round: stars
                for k in range(3):
                    self._emit("spark", 0, 0, life=2600,
                               orbit=(self.iw / 2 - 1, -1.5, 8.0, 2.0, 5.0, 2 * math.pi * k / 3))
                face, face_until = "squeezed", self.now + 2600
            # what he looks like: his face, then which frame
            swing = abs(self._swing)
            idx = None
            if tired_at is not None:
                idx, face = ONE_HAND, "squeezed"
            else:
                if self.now < face_until and face == "squeezed":
                    pass                                # still seeing stars
                elif swing > 0.5 or abs(ax) > 25_000:
                    face, face_until = "surprised", self.now + 600
                elif self.now < happy_until:
                    face = "happy"
                elif self.now >= face_until:
                    face = "idle" if random.random() < 0.97 else random.choice(("look_l", "look_r", "happy"))
                    face_until = self.now + (900 if face != "idle" else 0)
                if swing < math.pi / SPIN_STEPS and abs(self._swing_v) < 1.5:   # hanging still
                    if self.now < kick_until:
                        idx = KICK_A if int(self.now // 220) % 2 == 0 else KICK_B
                    else:
                        idx = STRAIGHT
                        if self.now >= kick_at:
                            kick_until, kick_at = self.now + 1300, self.now + random.uniform(4000, 9000)
            if idx is None:
                # swinging: his hang turned about his hands, all the way round if
                # need be, always the same drawing so nothing jumps as he goes
                k = round(self._swing / (2 * math.pi / SPIN_STEPS)) % SPIN_STEPS
                self.show_frame(self.sp.spin_anim(face, self.hat()), k)
            else:
                mirror = idx == ONE_HAND and (1 if self._swing >= 0 else -1) != self.sp.dangle_lean
                self.show_frame("dangle" if face == "idle" else "dangle_" + face, idx, mirror)
            self._place_on_pointer()
            yield TICK_MS
        yield from self._let_go(vx, vy)

    def _let_go(self, vx, vy):
        """Off the pointer: he drops, carrying its swing, and lands."""
        self._dangling = False
        self._sure_armed = False                   # not straight back on: wait for it to move off
        self._grab_cool = self.now + GRAB_COOLDOWN * (0.35 if self.prefs["activity"] == "lively" else 1)
        limit = 300 * self.scale
        self.vx = max(-limit, min(limit, vx))
        self.vy = max(-limit, min(limit, vy))
        self.scripted = False
        self.airborne = True
        yield from self._fall()

    # ── Time of day ───────────────────────────────────────────────

    def _stretch(self, hold=1300):
        """Crouch, arms right up with his eyes squeezed shut, reaching higher
        and back a few times, and down again."""
        self.show_frame("jump", 1)
        yield 180
        t, k = 0, 0
        while t < hold:
            self.show_frame("stretch", k % 2)
            step = 260 if k % 2 == 0 else 380
            yield step
            t, k = t + step, k + 1
        self.show_frame("jump", 1)
        yield 160

    def _act_yawn(self):
        yield from self._stretch()
        self.pose("blink")                         # still half asleep
        yield 700
        if self.is_night():
            x, y = self._z_spot()
            self._emit("z_small", x, y, vx=0.7, vy=-2.0, life=2500)
        self.pose("idle")
        yield 600

    def is_birthday(self):
        """Is today your birthday? (A 29 February one is kept on the 28th in other years.)"""
        b = self.prefs["birthday"]
        if not b:
            return False
        today = self.wall().date()
        if b == "02-29" and not calendar.isleap(today.year):
            b = "02-28"
        return today.strftime("%m-%d") == b

    def _birthday_due(self):
        today = self.wall().date().isoformat()
        done = self.settings.value("birthday_done") if self.settings is not None else self._party_done
        return (self.prefs["celebrate"] and self.is_birthday() and done != today
                and self.now - self._input_at < PRESENT)

    def _morning_due(self):
        return (self.prefs["day_cycle"] and not self.prefs["quiet"] and self.is_morning()
                and self._morning != self.wall().date() and self.now - self._input_at < PRESENT)

    def _act_morning(self):
        """Good morning: a big stretch, a happy face, then coffee."""
        self._morning = self.wall().date()
        yield from self._come_back()
        yield from self._stretch(1600)
        self.pose("happy")
        yield 900
        yield from self._act_coffee()

    def _act_coffee(self, stay=None):
        """A steaming mug in his right hand; a sip now and then, a look around."""
        yield from self._come_back()
        end = stay if stay is not None else random.uniform(25_000, 45_000)
        mx, my = MUG_AT
        self.pose("idle")
        t, k, face_until, next_sip = 0.0, 0, 0.0, random.uniform(2000, 4000)
        while t < end:
            self.layers = {"mug_held": (mx, my), f"steam_{k % 3}": (mx + 2, my - 5)}
            k += 1
            if t >= next_sip:
                self.pose("happy")                 # a sip: mmm
                face_until, next_sip = t + 1300, t + random.uniform(4000, 8000)
            elif t >= face_until and self.frame[1] != "idle":
                self.pose("idle")
            elif t >= face_until and random.random() < 0.04:
                self.pose(self._glance())
                face_until = t + 900
            yield 330
            t += 330
        self.layers = {}
        self.pose("idle")
        yield 300

    # ── Reminders ─────────────────────────────────────────────────

    def _user_active(self):
        """You did something: moved the pointer, sent a prompt, clicked him."""
        if self.now - self._input_at > AWAY:       # back from a break: start counting afresh
            self._streak_at = self._water_at = self.now
        self._input_at = self.now

    def _due_reminder(self):
        if (self.prefs["quiet"] or self.ducked or not self.isVisible()
                or self.now - self._input_at > PRESENT):
            return None
        if (self.prefs["water"] and self.now >= self._snooze["water"]
                and self.now - self._water_at >= self.prefs["water_every"] * 60_000):
            return "remind_water"
        if (self.prefs["breaks"] and self.now >= self._snooze["break"]
                and self.now - self._streak_at >= self.prefs["break_every"] * 60_000):
            return "remind_break"
        return None

    def _maybe_remind(self):
        """Break into whatever he's doing on his own (or for Claude Code) for a
        reminder: a new one that's due, or one still waiting for its Done button."""
        if (self.manual or self.dragging or self.airborne or self.claude_mode() == "attention"
                or self.action in REMINDERS + ("held", "fall", "duck", "attention", "grab")):
            return
        if self.reminding:
            self.start("remind_" + self.reminding)
        elif self._due_reminder():
            self._begin_reminder(self._due_reminder())

    def _begin_reminder(self, action):
        """A reminder goes up, and stays up (through anything, even a restart) until you press Done."""
        self.reminding = action.split("_", 1)[1]
        self._remind_since = self.now
        if self.settings is not None:
            self.settings.setValue("reminding", self.reminding)
        self.start(action)

    def _reminder_bits(self, on):
        """The waiting reminder's bubble and its Done button, over his right shoulder."""
        self.particles = [q for q in self.particles if q["kind"] not in REMINDER_BITS]
        if on and self.reminding:
            self._emit(self.reminding + "_bubble", *BUBBLE_AT, life=float("inf"))
            self._emit("done_button", *DONE_AT, life=float("inf"))

    def _on_done_button(self, pos):
        pos = pos.toPoint() if hasattr(pos, "toPoint") else pos
        return any(self._glyph_rect(q).contains(pos) for q in self.particles
                   if q["kind"] in ("done_button", "done_button_pressed"))

    def _loud(self):
        return self.now - self._remind_since < REMIND_LOUD and not self.prefs["quiet"]

    def confirm_reminder(self):
        """Done: it's taken care of. He has a drink, or a coffee break with you."""
        kind, self.reminding = self.reminding, None
        if self.settings is not None:
            self.settings.remove("reminding")
        self._reminder_bits(False)
        other = "break" if kind == "water" else "water"
        self._snooze[kind] = 0.0
        self._snooze[other] = max(self._snooze[other], self.now + 5 * 60_000)   # one at a time
        if kind == "water":
            self._water_at = self.now
            self.start("drink", manual=True)
        else:
            self._streak_at = self.now
            self.start("coffee", manual=True, stay=random.uniform(12_000, 20_000))

    def _focus_area(self):
        """The screen you're working on: your active window's, else the pointer's."""
        names = [sc.name() for sc in QApplication.screens()]
        areas = screen_areas()
        active = next((w for w in self.window_list if w["active"] and w["output"] in names), None)
        if active is not None:
            return areas[names.index(active["output"])]
        if self.cursor is not None:
            a = area_at(self.cursor[0], self.cursor[1])
            if a is not None:
                return a
        return self.screen_geometry()

    def _go_to_focus(self):
        """To the middle of the screen you're working on: a trot if he's on its
        floor already, one big leap across if he's on the other screen (or up
        on a window)."""
        area = self._focus_area()
        s, wpx = self.scale, self.iw * self.scale
        target = area.left() + area.width() / 2 - wpx / 2
        floor = area.top() + area.height()
        left = self.box_span()[0]
        if abs(self._feet() - floor) < 2 and self.screen_geometry() == area:
            if abs(target - left) > 30 * s:
                yield from self._walk_to(target)
                self.pose("idle")
            return
        self.show_frame("jump", 1)                          # over there: one big leap
        yield 160
        self.show_frame("jump", 2)
        yield from self._arc_to(target, floor, 30 * s)
        yield from self._play("jump", range(8, len(self.sp.anims["jump"].frames)))
        self.pose("idle")

    def _act_remind_water(self):
        """Water time. For the first couple of minutes as loud as he gets: he
        runs over to you (and follows), hops up and down waving a bottle, holds
        it out, looking at you. Then he just holds it up, with a hop now and
        then. The bubble and its Done button stay up until you press it."""
        yield from self._come_back()
        self._reminder_bits(True)
        self.layers = {"water_bottle": BOTTLE_SIDE}
        n = 0
        while True:
            if self._loud():
                if n % 2 == 0:
                    yield from self._go_to_focus()      # the middle of the screen you're on
                n += 1
                for k in range(3):                     # jumping about, side to side
                    yield from self._hop_with_bottle(12 * self.scale * (1 if (n + k) % 2 else -1))
                self.layers = {"water_bottle": BOTTLE_SIDE}
                self.pose(self._glance())
                yield 1900
            else:
                self.layers = {"water_bottle": BOTTLE_SIDE}
                self.pose(self._glance() if random.random() < 0.3 else "idle")
                yield random.uniform(3000, 6000)
                if random.random() < 0.2:
                    yield from self._hop_with_bottle()

    def _hop_with_bottle(self, dx=0.0):
        bx, by = BOTTLE_UP
        self.show_frame("jump", 1)                 # crouch, bottle at his side
        self.layers = {"water_bottle": BOTTLE_CROUCH}
        yield 110
        self.show_frame("cheer", 0)
        for lift in (2, 4, 5, 5, 4, 2, 0):
            self.lift = lift
            self.x += dx / 7                        # a hop to the side
            self.layers = {"water_bottle": (bx, by - lift)}
            if lift == 5 and random.random() < 0.8:   # a splash from the top of the bottle
                for _ in range(2):
                    self._emit(f"droplet_{random.randrange(3)}", bx + 3 + random.uniform(-1, 1), by - lift - 1,
                               vx=random.uniform(-5, 5), vy=random.uniform(-9, -5), g=40, life=700)
            yield 55
        self.show_frame("jump", 1)
        self.layers = {"water_bottle": BOTTLE_CROUCH}
        yield 110

    def _act_drink(self):
        """Glug, glug: the bottle tipped up to his face, eyes happily shut."""
        self.pose("happy")
        tilt = self.sp.props.get("water_bottle_tilt")
        if tilt is not None:
            cx, cy = self.sp.bottle_cap
            self.layers = {"water_bottle_tilt": (12 - cx, 5 - cy)}
        else:
            self.layers = {"water_bottle": BOTTLE_SIDE}
        yield 2200
        self.layers = {}
        for k in range(3):
            self._emit("heart", self.iw / 2 - 2 + random.uniform(-6, 6), -3.0,
                       vx=random.uniform(-1, 1), vy=-4.0, life=1400)
            yield 250
        yield 600
        self.pose("idle")
        yield 300

    def _act_remind_break(self):
        """Break time: the coffee bubble and its Done button, a look at you, a
        wave and a hop (calmer after a couple of minutes), until you press Done."""
        yield from self._come_back()
        self._reminder_bits(True)
        if self._loud():
            yield from self._go_to_focus()
        while True:
            loud = self._loud()
            self.pose(self._glance())
            yield 1400 if loud else random.uniform(3000, 6000)
            if loud or random.random() < 0.3:
                yield from self._once("wave")
            self.pose("idle")
            if loud:
                yield 500
                yield from self._once("jump")
            yield 900 if loud else 400

    def _act_sleep(self):
        night = self.prefs["day_cycle"] and self.is_night()
        if night:
            yield from self._stretch()             # nightcap on, a big yawn, then off he goes
        self.pose("blink")
        end, t, n = random.uniform(60_000, 180_000) if night else random.uniform(20000, 60000), 0.0, 0
        while t < end:
            n += 1
            x, y = self._z_spot()
            self._emit("z_big" if n % 2 else "z_small", x, y, vx=0.7, vy=-2.0, life=3000)
            yield 1300
            t += 1300
        self.pose("idle")
        yield 400

    def _z_spot(self):
        """Where his Z's start: off his right shoulder, clear of any hat."""
        if self.hat() is not None:
            return self.iw - 2 + random.random(), -2.0
        return self.iw - 6 + random.random() * 2, -1.0

    def _window_target(self):
        """A window top he could hop up onto: visible, above him, not too far."""
        s = self.scale
        feet = self._feet()
        left, right = self.box_span()
        cx = (left + right) / 2
        best = None
        for y, x0, x1, wid in self._surfaces():
            if wid is None or not (feet - 9 * self.ih * s <= y <= feet - self.ih * s):
                continue
            spot = min(max(cx, x0 + self.iw * s * 0.6), x1 - self.iw * s * 0.6)
            cost = abs(spot - cx) + (feet - y) / 2
            if abs(spot - cx) <= 500 and (best is None or cost < best[0]):
                best = (cost, y, spot)
        return best

    def _act_perch_window(self):
        """Walk under a window and jump up onto its top edge."""
        target = self._window_target()
        if target is None:
            return
        _, top, spot = target
        s = self.scale
        box_left = spot - self.iw * s / 2
        yield from self._walk_to(box_left)
        self.pose("look_l" if random.random() < 0.5 else "look_r")
        yield 500
        self.show_frame("jump", 1)               # crouch
        yield 140
        self.show_frame("jump", 2)
        yield from self._arc_to(box_left, top, 6 * s)
        yield from self._play("jump", range(8, len(self.sp.anims["jump"].frames)))

    def _window(self, wid):
        return next((w for w in self.window_list if w["id"] == wid), None)

    def _column_clear(self, win, box, y0, y1):
        """Is the column beside a window's side (his box, from y0 down to y1)
        clear of windows stacked above it, and on a screen?"""
        wpx = self.iw * self.scale
        if area_at(box + 1, (y0 + y1) / 2) is None or area_at(box + wpx - 1, (y0 + y1) / 2) is None:
            return False
        for w in self.window_list:
            if w["stack"] > win["stack"] and not w["fs"]:
                if w["x"] < box + wpx and w["x"] + w["w"] > box and w["y"] < y1 and w["y"] + w["h"] > y0:
                    return False
        return True

    def _climb_target(self):
        """A window he can climb up the side of from where he stands: its side
        comes down to near his feet, it's in view, and there's room to stand
        up top at that end. (window id, side: -1 its left edge, box left)."""
        s, wpx = self.scale, self.iw * self.scale
        feet = self._feet()
        left, right = self.box_span()
        room_l, room_r = self._room()
        lo, hi = left - room_l, right + room_r
        tops = [sf for sf in self._surfaces() if sf[3] is not None]
        best = None
        for win in self.window_list:
            top, bottom = win["y"], win["y"] + win["h"]
            if (win["fs"] or win["id"] == self.standing_on or top > feet - 2 * self.ih * s
                    or bottom < feet - CLIMB_JUMP * s):
                continue
            for side in (-1, 1):
                edge = win["x"] if side < 0 else win["x"] + win["w"]
                box = edge - wpx if side < 0 else edge
                if box < lo or box + wpx > hi:
                    continue                                    # can't walk there from here
                if not any(sf[0] == top and sf[3] == win["id"] and (sf[1] == edge if side < 0 else sf[2] == edge)
                           and sf[2] - sf[1] >= wpx for sf in tops):
                    continue                                    # nowhere to stand at the top
                if not self._column_clear(win, box, top, min(feet, bottom)):
                    continue
                cost = abs(box - left)
                if best is None or cost < best[0]:
                    best = (cost, win["id"], side, box)
        return best[1:] if best else None

    def _climb_down_target(self):
        """From the window he's on: a side he can climb down (side, box left
        to climb at), or None."""
        on = self._window_under()
        if on is None:
            return None
        top, x0, x1, wid = on
        win = self._window(wid)
        if win is None:
            return None
        wpx = self.iw * self.scale
        left = self.box_span()[0]
        best = None
        for side, end, box in ((-1, x0, x0 - wpx), (1, x1, x1)):
            if end != (win["x"] if side < 0 else win["x"] + win["w"]):
                continue                                        # the span ends at a window in front, not the edge
            floor = self._floor_below(box)
            if floor is None or not self._column_clear(win, box, top, floor):
                continue
            cost = abs((x0 if side < 0 else x1 - wpx) - left)
            if best is None or cost < best[0]:
                best = (cost, side, box)
        return best[1:] if best else None

    def _floor_below(self, box):
        """The first surface below the window top he's on, beside it at `box`."""
        cx, feet = box + self.iw * self.scale / 2, self._feet()
        ys = [y for y, x0, x1, _ in self._surfaces() if x0 <= cx < x1 and y > feet + 2]
        return min(ys) if ys else None

    def _jump_target(self):
        """Another window top he can jump to from where he stands (a window top
        or the floor): above, below or beside, in view, not too far. The
        nearest spot on the nearest one: (landing box left, its top, its id)."""
        s, wpx = self.scale, self.iw * self.scale
        on = self._window_under()
        if on is None:
            return None
        feet = self._feet()
        left = self.box_span()[0]
        room_l, room_r = self._room()
        lo, hi = left - room_l, left + room_r              # where he can take off from
        best = None
        for y, a, b, wid in self._surfaces():
            if wid is None or wid == on[3] or b - a < wpx + 4 * s:
                continue
            rise = feet - y
            if rise > LEAP_UP * s or -rise > LEAP_DOWN * s or abs(rise) < 2 * s:
                continue
            land = min(max(left, a + 2 * s), b - wpx - 2 * s)   # the nearest spot on it
            takeoff = min(max(land, lo), hi)
            across = abs(land - takeoff)
            if across > LEAP_GAP * s or area_at(land + wpx / 2, y - 1) is None:
                continue
            cost = across + abs(rise) / 2 + abs(takeoff - left) / 4
            if best is None or cost < best[0]:
                best = (cost, land, y, wid)
        return best[1:] if best else None

    def _act_window_jump(self):
        """A crouch and a leap onto another window top: across a gap, up onto
        one standing higher, or down onto one lower down. If it's a long way
        across he walks nearer first."""
        if self._window_under() is None:                     # from the floor: up onto one
            yield from self._act_perch_window()
            return
        target = self._jump_target()
        if target is None:
            return
        land, top, wid = target
        s, wpx = self.scale, self.iw * self.scale
        left = self.box_span()[0]
        room_l, room_r = self._room()
        near = min(max(land, left - room_l), left + room_r)   # as near as what he's on goes
        if abs(near - left) > 4 * s:
            yield from self._walk_to(near)
            target = self._jump_target() or target
            land, top, wid = target
        self.pose("look_r" if land > self.box_span()[0] else "look_l")   # eyeing it up
        yield 600
        self.show_frame("jump", 1)                          # crouch...
        yield 180
        self.show_frame("jump", 2)                          # ...and go
        across = abs(land - self.box_span()[0])
        yield from self._arc_to(land, top, 10 * s + 0.2 * across)
        yield from self._play("jump", range(8, len(self.sp.anims["jump"].frames)))
        self.pose("happy")
        yield 500

    def _act_climb_window(self):
        """Walk to the side of a window and climb it, hand over hand up its
        edge, then pull himself up onto the top."""
        target = self._climb_target()
        if target is None:
            return
        wid, side, box = target
        yield from self._walk_to(box)
        self.set_box_left(box)
        self.pose("look_r" if side < 0 else "look_l")      # sizing it up
        yield 450
        win = self._window(wid)
        if win is None:
            return
        s = self.scale
        self.scripted = True
        bottom = win["y"] + win["h"]
        if bottom < self._feet():                           # the side starts above him: jump for it
            self.show_frame("jump", 1)
            yield 140
            self.show_frame("jump", 2)
            rise = self._feet() - bottom + self.ih * s / 2
            for k in range(1, 9):
                self.y -= rise / 8
                yield TICK_MS
        done = yield from self._climb_side(wid, side, up=True)
        if not done:
            return
        win = self._window(wid)
        edge = win["x"] if side < 0 else win["x"] + win["w"]
        inward = edge + 2 * s if side < 0 else edge - self.iw * s - 2 * s
        self.show_frame("jump", 2)                          # and over the top
        yield from self._arc_to(inward, win["y"], 3 * s)
        yield from self._play("jump", range(8, len(self.sp.anims["jump"].frames)))
        self.pose("happy")
        yield 500

    def _act_climb_down(self):
        """Down the side of the window he's on: along to its end, over the edge,
        and hand over hand down its side, dropping off the bottom if it stops
        short of the floor."""
        target = self._climb_down_target()
        if target is None:
            return
        side, box = target
        wid = self.standing_on or self._window_under()[3]
        wpx = self.iw * self.scale
        yield from self._walk_to(box + wpx if side < 0 else box - wpx)
        self.pose("look_l" if side < 0 else "look_r")      # peering over the edge
        yield 500
        self.scripted = True
        self.set_box_left(box)                              # over the edge, holding on
        yield from self._climb_side(wid, side, up=False, goal=self._floor_below(box))

    def _climb_side(self, wid, side, up, goal=None):
        """Hand over hand up (or down) beside a window's side, moving with the
        window if it's moved. Down, stops at `goal` (feet) or the bottom of the
        side, then lets go. If the window goes, he falls. True if he made it."""
        s = self.scale
        win = self._window(wid)
        last = (win["x"], win["y"]) if win else None
        k = 0
        while True:
            win = self._window(wid)
            if win is None:                                 # gone: nothing to hold on to
                self.scripted = False
                self.airborne = True
                yield from self._fall()
                return False
            if (win["x"], win["y"]) != last:                # moved: along with it
                self.x += win["x"] - last[0]
                self.y += win["y"] - last[1]
                last = (win["x"], win["y"])
            feet = self._feet()
            if up and feet <= win["y"]:
                return True
            if not up and (feet >= (goal if goal is not None else feet)
                           or feet - self.ih * s / 2 >= win["y"] + win["h"]):
                self.scripted = False                       # down (or off the bottom: drop the rest)
                if self.y < self.ground_y() - 1:
                    self.airborne = True
                    yield from self._fall()
                else:
                    self.y = self.ground_y()
                    self.pose("idle")
                return True
            if k % 8 == 0:                                  # the edge beside him (or the ladder frames)
                name = "climb_side" if "climb_side" in self.sp.anims else "climb"
                steps = len(self.sp.anims[name].frames)
                step = (k // 8) % steps if up else (-(k // 8) - 1) % steps   # down: the cycle backwards
                self.show_frame(name, step, name == "climb_side" and side > 0)
            k += 1
            step = CLIMB_SPEED * WINDOW_CLIMB * s * TICK_MS / 1000
            self.y += -step if up else step
            if not up and goal is not None:
                self.y = min(self.y, goal - self.home_px.y() - self.ih * s)
            yield TICK_MS

    def _act_hop_down(self):
        """Hop off the window he's on, off whichever end is nearer."""
        on = self._window_under()
        if on is None:
            return
        room_l, room_r = self._room()
        side = -1 if room_l < room_r else 1
        self.show_frame("jump", 1)
        yield 140
        self.airborne = True
        self.vx, self.vy = side * 40.0 * self.scale, -40.0 * self.scale
        yield from self._fall()

    def _act_duck(self):
        """Something went fullscreen on his screen: drop out of sight until it's over."""
        s = self.scale
        screen = self._screen_name()             # remembered: he'll be out of sight in a moment
        self.scripted = True
        self.show_frame("jump", 1)
        yield 120
        home_y = self.y
        for _ in range(8):                       # sink out of view
            self.y += self.ih * s / 5
            yield TICK_MS
        self.ducked = True
        self.hide()
        while self.prefs["duck"] and self._fullscreen_here(screen):
            yield 400
        self.ducked = False
        self.show()
        self.y = home_y + self.ih * s
        self.show_frame("jump", 2)
        for _ in range(8):                       # pop back up
            self.y -= self.ih * s / 8
            yield TICK_MS
        self.y = home_y
        self.scripted = False
        yield from self._play("jump", range(8, len(self.sp.anims["jump"].frames)))

    def _act_held(self):
        self.show_frame("jump", 2)               # arms up, eyes wide
        while True:
            yield 1000

    def _act_fall(self):
        yield from self._fall()

    def _fall(self):
        self.show_frame("jump", 2)
        while self.airborne:
            yield TICK_MS
        # Clawd-Jumping's landing: squash, wobble, back to standing
        yield from self._play("jump", range(8, len(self.sp.anims["jump"].frames)))

    # ── Desktop icons ─────────────────────────────────────────────

    def _icons_here(self):
        geo = self.screen_geometry()
        return [(n, r) for n, r, _ in self.icon_source() if geo.contains(r.center())]

    def _folders_here(self):
        geo = self.screen_geometry()
        return [(n, r) for n, r, is_dir in self.icon_source() if is_dir and geo.contains(r.center())]

    def _act_visit(self, hang=None, stay=None):
        """Float up beside a desktop icon and check it out properly: puzzle over
        it, lean in, poke it, go over it with a magnifying glass, make up his
        mind. Then sometimes hop onto it (or hang off it), else drop down."""
        icons = self._icons_here()
        if not icons:
            return
        _, icon = random.choice(icons)
        s = self.scale
        width = self.iw * s
        geo = self.screen_geometry()
        # beside it, on whichever side has room, eyes level with the icon
        sides = [side for side, room in ((1, icon.left() - geo.left()),
                                         (-1, geo.left() + geo.width() - icon.left() - icon.width()))
                 if room >= width + 4 * s]
        if not sides:
            return
        side = random.choice(sides)                  # +1: the icon is on his right
        box_left = icon.left() - 3 * s - width if side > 0 else icon.left() + icon.width() + 3 * s
        home_top = icon.center().y() - 3 * s + CLOUD_LIFT * s
        yield from self._walk_to(box_left)
        if abs(self.box_span()[0] - box_left) > 4 * s:
            return
        yield from self._cloud_up(home_top - self.home_px.y(), hop_off=False)
        self.lift, self.front = CLOUD_LIFT, self.sp.cloud_at
        yield from self._inspect(icon, side)
        # and then...
        stand = icon.top() - geo.top() >= (self.ih + 4) * s          # room on top of it
        stay = random.random() < 0.4 if stay is None else stay
        if not stay:
            yield from self._cloud_slips_away()
            self.scripted = False
            self.airborne, self.vx, self.vy = True, 0.0, 0.0
            yield from self._fall()
            return
        hang = (random.random() < 0.5 if hang is None else hang) or not stand
        room_r = geo.left() + geo.width() - (icon.left() + icon.width()) >= icon.left() - geo.left()
        hang_left = icon.left() + icon.width() - 7 * s if room_r else icon.left() - 17 * s
        hang_feet = icon.top() + 2 * s + self.ih * s                  # hands just over the top edge
        yield from self._cloud_slips_away()
        if stand:
            yield from self._arc_to(icon.center().x() - width / 2, icon.top(), 4 * s, hold=True)
            yield from self._perch(random.uniform(4000, 9000))
            if hang:
                self.show_frame("jump", 2)
                yield from self._arc_to(hang_left, hang_feet, 2 * s, hold=True)
        else:
            yield from self._arc_to(hang_left, hang_feet, 3 * s, hold=True)
        if hang:
            yield from self._hang(random.uniform(3000, 7000))
        self.scripted = False                                         # let go
        self.airborne = True
        self.vx, self.vy = random.uniform(-10, 10) * s, (0.0 if hang else -15.0 * s)
        yield from self._fall()

    def _inspect(self, icon, side):
        """The investigation, floating beside `icon` (side +1: it's on his right)."""
        s, head = self.scale, -self.lift
        toward, away = ("look_r", "look_l") if side > 0 else ("look_l", "look_r")
        # what's this?
        self.pose(toward)
        yield 800
        self._emit("question", 12, head - 8, vy=-1.5, life=1600)
        yield 1000
        # a double take
        self.pose(away)
        yield 280
        self.pose("surprised")
        self._emit("excl", 11, head - 8, vy=-2, life=1200)
        yield 900
        # lean in and poke it
        base = self.x
        for _ in range(3):
            self.x += side * s
            yield 90
        self.pose(toward)
        yield 500
        tip = self.iw + 2 if side > 0 else -3
        for _ in range(random.randint(2, 3)):
            self.show_frame("poke", 0, side < 0)
            self._emit("spark", tip, head + 4, life=260)
            yield 180
            self.pose(toward)
            yield 280
        self._emit("question", 12, head - 8, vy=-1.5, life=1400)
        yield 700
        for _ in range(3):
            self.x -= side * s
            yield 90
        self.x = base
        # out comes the magnifying glass, over the icon in a little sweep
        held = (self.iw - 2 if side > 0 else -6, head + 5)

        def over(fx, fy):
            px = icon.left() + fx * icon.width()
            py = icon.top() + fy * icon.height()
            return ((px - self.x - self.home_px.x()) / s - MAGNIFIER_LENS[0],
                    (py - self.y - self.home_px.y()) / s - MAGNIFIER_LENS[1])
        spots = [over(*f) for f in ((0.3, 0.3), (0.72, 0.3), (0.7, 0.72), (0.3, 0.7), (0.5, 0.5))]
        yield from self._move_layer("magnifier", held, spots[0], 5, 60)
        at = spots[0]
        for k, spot in enumerate(spots):
            yield from self._move_layer("magnifier", at, spot, 4, 70)
            at = spot
            self.pose(("surprised", toward, "read_r" if side > 0 else "read_l")[k % 3])
            if k == 2:
                self._emit("excl", 11, head - 8, vy=-2, life=900)
            yield random.uniform(350, 650)
        # the verdict
        verdict = random.choice(("like", "puzzled", "spooked"))
        if verdict == "like":
            self.pose("happy")
            for _ in range(2):
                self._emit("heart", 9 + random.uniform(-4, 4), head - 3, vx=random.uniform(-1, 1),
                           vy=-4, life=1400)
                self._emit("spark", at[0] + MAGNIFIER_LENS[0] + random.uniform(-4, 4),
                           at[1] + MAGNIFIER_LENS[1] + random.uniform(-4, 4), life=500)
                yield 450
        elif verdict == "puzzled":
            for eyes in (away, toward, away, toward):
                self.pose(eyes)
                yield 300
            self._emit("question", 12, head - 8, vy=-1.5, life=1500)
            yield 900
        else:                                        # spooked: sweat drop, backs off
            self.pose("surprised")
            self._emit("drop", 21 if side < 0 else 1, head + 1, vy=1, g=20, life=1100)
            for _ in range(4):
                self.x -= side * s
                yield 50
            yield 600
        yield from self._move_layer("magnifier", at, held, 5, 60)
        del self.layers["magnifier"]
        self.pose("idle")
        yield 300

    def _cloud_slips_away(self):
        """The cloud slides out from under him: a split second of cartoon physics."""
        self.show_frame("jump", 2)
        cx, cy = self.front
        for k in range(1, 9):
            self.front = (cx + 3 * k, cy)
            yield 30
        self._leave_cloud()

    def _cloud_up(self, target_y, hop_off=True):
        """Hop on the official cloud, rise to `target_y`, and hop off (it flies
        away) or stay aboard, hovering."""
        a = self.sp.anims["cloud"]
        yield from self._play("cloud", range(0, a.loop[0]))
        self.scripted = True
        steps = self._loop("cloud")
        while self.y > target_y + 0.5:
            ms = next(steps)
            for _ in range(max(1, int(ms // TICK_MS))):
                self.y = max(target_y, self.y - CLOUD_RISE * self.scale * TICK_MS / 1000)
                yield TICK_MS
        self.y = target_y
        if hop_off:
            c, d = a.outro
            yield from self._play("cloud", range(c, d + 1))

    def _leave_cloud(self):
        """Stop sitting in the cloud without moving on screen."""
        if self.lift:
            self.y -= self.lift * self.scale
            self.lift = 0
        self.front = None

    def _move_layer(self, name, a, b, steps, ms):
        for k in range(steps + 1):
            t = k / steps
            self.layers[name] = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
            yield ms

    def _act_read(self):
        """Float up on the cloud under a desktop folder, rummage in it, pull out
        a page, put on reading glasses and read it like a story (with all the
        feelings), then put everything back and hop off."""
        folders = self._folders_here()
        if not folders:
            return
        _, icon = random.choice(folders)
        s = self.scale
        geo = self.screen_geometry()
        # Folders open at the top: he floats beside the folder and reaches up
        # over its rim, with the left hand from its right side if there's room.
        need = (self.iw + 2) * s
        mirror = (geo.left() + geo.width() - icon.left() - icon.width() >= need
                  or icon.left() - geo.left() < need)
        hand = 6 if mirror else 18                       # Clawd-Waving's raised hand, in sprite px
        hand_x = icon.left() + icon.width() - 3 * s if mirror else icon.left() + 3 * s
        box_left = hand_x - hand * s
        # sitting up in his cloud, the top of that hand a pixel above the rim
        home_top = icon.top() + (CLOUD_LIFT + 3) * s
        yield from self._walk_to(box_left)
        if abs(self.box_span()[0] - box_left) > 4 * s:
            return
        yield from self._cloud_up(home_top - self.home_px.y(), hop_off=False)
        self.lift, self.front = CLOUD_LIFT, self.sp.cloud_at
        self.pose("idle")
        yield 400
        lift = self.lift
        # rummage: hand in over the rim, scraps of paper flying up out of the top
        rim = (icon.top() - self.y - self.home_px.y()) / s
        for k in range(10):
            self.show_frame("wave", 4 + k % 2, mirror)
            if k % 2 == 0:
                self._emit("scrap", hand - 1 + random.uniform(-3, 3), rim - 1,
                           vx=random.uniform(-7, 7), vy=random.uniform(-16, -8), g=30, life=1600)
            yield 150
        # pull a page up out of the folder and bring it down in front of him
        hand_xy = (hand - 4, rim - 7)
        page_xy = (PAGE_AT[0], PAGE_AT[1] - lift)
        yield from self._move_layer("page", hand_xy, page_xy, 6, 70)
        self.pose("idle")
        yield 300
        # reading glasses, from out of nowhere onto his nose
        aside = (-12 if mirror else 18, 3 - lift)
        on_nose = (GLASSES_AT[0], GLASSES_AT[1] - lift)
        yield from self._move_layer("glasses", aside, on_nose, 5, 60)
        # the story
        for feeling in random.sample(EMOTIONS, random.randint(3, 4)):
            yield from self._scan(random.uniform(1200, 2400))
            yield from self._feel(feeling)
        yield from self._scan(800)
        # glasses away, page back into the folder
        self.pose("idle")
        yield from self._move_layer("glasses", on_nose, aside, 5, 50)
        del self.layers["glasses"]
        self.show_frame("wave", 4, mirror)
        yield from self._move_layer("page", page_xy, hand_xy, 6, 70)
        del self.layers["page"]
        for k in range(3):
            self.show_frame("wave", 4 + k % 2, mirror)
            yield 150
        self.pose("happy")
        yield 700
        # the cloud slips away: a split second of cartoon physics, then down he goes
        yield from self._cloud_slips_away()
        self.scripted = False
        self.airborne, self.vx, self.vy = True, 0.0, 0.0
        yield from self._fall()

    def _scan(self, ms):
        """Eyes going along the lines of the page."""
        t, k = 0.0, 0
        while t < ms:
            self.pose(("read_l", "read_r")[k % 2])
            k += 1
            yield 420
            t += 420

    def _feel(self, feeling):
        s, head = self.scale, -self.lift              # his head's top row, in sprite px from home
        if feeling == "surprised":
            self.pose("surprised")
            self._emit("excl", 11, head - 8, vy=-2, life=1400)
            yield 1400
        elif feeling == "sad":
            self.pose("sad")
            for _ in range(2):
                self._emit("drop", 5, head + 5, vy=2, g=40, life=1100)
                yield 650
            yield 300
        elif feeling == "laugh":
            self.pose("happy")
            self._emit("haha", 4, head - 8, vy=-3, life=1500)
            base = self.x
            for k in range(12):
                self.x = base + (s if k % 2 else -s)
                yield 90
            self.x = base
        elif feeling == "love":
            self.pose("happy")
            for _ in range(3):
                self._emit("heart", 9 + random.uniform(-4, 4), head - 3,
                           vx=random.uniform(-1, 1), vy=-4, life=1400)
                yield 380
        elif feeling == "scared":
            self.pose("surprised")
            self._emit("drop", 21, head + 1, vy=1, g=20, life=1200)
            base = self.x
            for k in range(14):
                self.x = base + (s if k % 2 else 0)
                yield 60
            self.x = base
        elif feeling == "confused":
            self.pose("read")
            self._emit("question", 12, head - 8, vy=-1.5, life=1500)
            yield 700
            self.pose("look_l")
            yield 400
            self.pose("look_r")
            yield 400

    def _perch(self, duration):
        """Standing on an icon: blinking, looking about, watching the pointer."""
        self.scripted = True
        self.pose("idle")
        t = 0.0
        while t < duration:
            hold = random.uniform(1200, 3000)
            yield hold
            t += hold
            if random.random() < 0.5:
                self.pose("blink")
                yield 110
            else:
                self.pose(self._glance())
                yield 900
            self.pose("idle")
            t += 500

    def _hang(self, duration):
        """Dangling from the icon's top edge, swinging a little."""
        self.scripted = True
        base, s = self.x, self.scale
        t, k = 0.0, 0
        while t < duration:
            self.show_frame("hang", 0)
            self.x = base + (s, 0, -s, 0)[k % 4]
            k += 1
            yield 350
            t += 350
        self.x = base

    # ── Mouse ─────────────────────────────────────────────────────

    def drop(self, vx=0.0, vy=0.0):
        self.vx, self.vy = vx, vy
        self.airborne = True
        self.start("fall")

    def mousePressEvent(self, e):
        self.wake()
        self._user_active()
        if e.button() == Qt.MouseButton.LeftButton:
            self._button_down = self._on_done_button(e.position())
            self._press = e.globalPosition().toPoint()
            self._grab = self._press - QPoint(int(self.x), int(self.y))
            self._trail = [(self._ms(), self._press)]
        e.accept()

    def mouseMoveEvent(self, e):
        pos = e.globalPosition().toPoint()
        if self._press is None:
            self.cursor_moved(pos.x(), pos.y())  # just hovering: maybe a stroke
            return
        if not self.dragging and (pos - self._press).manhattanLength() > 6:
            self.dragging = True
            self.airborne = False
            self.vx = self.vy = 0.0
            self.start("held")
        if self.dragging:
            self.x, self.y = pos.x() - self._grab.x(), pos.y() - self._grab.y()
            self.move(int(self.x), int(self.y))
            self._trail = (self._trail + [(self._ms(), pos)])[-40:]
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        on_button, self._button_down = self._button_down and self._on_done_button(e.position()), False
        if self.dragging:
            self.dragging = False
            self._trail.append((self._ms(), e.globalPosition().toPoint()))
            t1, p1 = self._trail[-1]
            t0, p0 = next((t, p) for t, p in self._trail if t1 - t <= THROW_WINDOW)
            dt = max(t1 - t0, 8) / 1000
            limit = 400 * self.scale
            vx = max(-limit, min(limit, (p1.x() - p0.x()) / dt))
            vy = max(-limit, min(limit, (p1.y() - p0.y()) / dt))
            self.drop(vx, vy)
        elif self._dangling:
            self._release = True
        elif self.reminding and on_button:
            self.confirm_reminder()
        elif self.reminding:
            self._nudge_until = self.now + 900     # "the button!": it flashes, he looks at it
            if self.frame[0] == "pose":
                self.pose("look_r")
        elif not self.airborne:
            waking = self.action == "sleep"
            kind = self.click_kind()
            if self.action not in ("work", "attention"):
                self.start("jump_happy", manual=True)
            if not waking:                         # a click on a sleeping Clawd just wakes him
                self.launch_claude_code(kind)
        self._press = None
        e.accept()

    def restart(self):
        """Start afresh (same process, new code): remember where he is, let
        KWin forget the pointer script, and replace this process."""
        self.save()
        if self.settings is not None:
            self.settings.sync()
        stop_kwin_feed()
        cmd = restart_command()
        os.execve(cmd[0], cmd, env_for_restart())

    # ── Drag and drop ─────────────────────────────────────────────

    @staticmethod
    def _dropped_folder(mime):
        if mime is None or not mime.hasUrls():
            return None
        for url in mime.urls():
            if url.isLocalFile():
                path = url.toLocalFile().rstrip("/") or "/"
                return path if os.path.isdir(path) else os.path.dirname(path)
        return None

    @staticmethod
    def _take(e):
        """Accept a drag as a copy (or a link), never a move: the folder stays where it is."""
        for action in (Qt.DropAction.CopyAction, Qt.DropAction.LinkAction):
            if e.possibleActions() & action:
                e.setDropAction(action)
                e.accept()
                return True
        e.ignore()
        return False

    def dragEnterEvent(self, e):
        if self._dropped_folder(e.mimeData()) is None or not self._take(e):
            e.ignore()
            return
        self._drag_seen = self.now
        if not self._drag_over:
            self._drag_over = True
            self._before_drag = self.frame
            self._emit("excl", 11, -8, vy=-2, life=1000)

    def dragMoveEvent(self, e):
        if self._drag_over and self._take(e):
            self._drag_seen = self.now
        else:
            e.ignore()

    def dragLeaveEvent(self, _e):
        if self._drag_over and self._before_drag is not None:
            self.frame = self._before_drag       # carry on as he was
        self._drag_over = False

    def dropEvent(self, e):
        self._drag_over = False
        folder = self._dropped_folder(e.mimeData())
        if folder is None or not self._take(e):
            return
        self._last_activity = self.now
        self.start("jump_happy", manual=True)
        for k in range(3):
            self._emit("spark", 6 + 6 * k, -4, vy=-5, life=700)
        if not open_claude_code_in(folder) and getattr(self, "tray", None) is not None:
            self.tray.showMessage("Clawd", "Couldn't open Claude Code for " + folder)

    def launch_claude_code(self, kind="continue"):
        if not open_claude_code(kind) and getattr(self, "tray", None) is not None:
            self.tray.showMessage("Clawd", "No terminal found to run Claude Code in.")

    def launch_terminal(self):
        if not open_in_terminal() and getattr(self, "tray", None) is not None:
            self.tray.showMessage("Clawd", "No terminal found to run Claude Code in.")

    # ── Settings ──────────────────────────────────────────────────

    def set_pref(self, key, value):
        old = self.prefs[key]
        self.prefs[key] = value
        self.settings_changed(key, old, value)
        if key == "quiet" and value and not self.manual and self.action in OWN_SCENE_ACTIONS:
            self.start("idle")                   # hush: wrap up whatever he was doing
        if key == "grab" and not value and self._dangling:
            self._release = True
        if key == "claude" and not value:        # stop following Claude Code: forget it all
            self.sessions.clear()
            self.celebrate, self.greet, self._stumble = None, False, False
            if not self.manual and self.action in ("work", "attention", "celebrate"):
                self.start("idle")

    @staticmethod
    def pref_ok(key, value):
        """Is this a value the setting can take? (For settings changed over the socket.)"""
        if key not in PREF_DEFAULTS:
            return False
        default = PREF_DEFAULTS[key]
        if type(value) is not type(default):     # type(), not isinstance(): a bool isn't a number
            return False
        if key == "activity":
            return value in ACTIVITY
        if key == "hat":
            return value in ("auto", "none") or value in HATS
        if key in ("break_every", "water_every"):
            return 10 <= value <= 240
        if key == "scenes_off":
            return all(isinstance(v, str) for v in value)
        return True

    def open_settings(self):
        d = getattr(self, "_settings_dialog", None)
        if d is None or not d.isVisible():
            d = self._settings_dialog = SettingsDialog(self)
            self._settings_news = []
            self.start("settings", manual=True)    # somebody's in his settings!
        d.show()
        d.raise_()
        d.activateWindow()

    def _settings_open(self):
        d = getattr(self, "_settings_dialog", None)
        return d is not None and d.isVisible()

    def settings_changed(self, key, old, new):
        """Something changed in the open settings: he'll take it in, in a moment."""
        if self.action == "settings" and old != new:
            self._settings_news.append((key, old, new))

    # ── Menus ─────────────────────────────────────────────────────

    def fill_menu(self, m):
        m.addAction("Open Claude Code").triggered.connect(
            lambda _=False: self.launch_claude_code(self.click_kind()))
        m.addAction("New Claude Code session").triggered.connect(
            lambda _=False: self.launch_claude_code("new"))
        m.addAction("Claude Code in a terminal").triggered.connect(lambda _=False: self.launch_terminal())
        m.addAction("Open claude.ai").triggered.connect(lambda _=False: open_claude_web())
        m.addSeparator()
        play = m.addMenu("Play")
        for group in (ACTIONS, AROUND_THE_DESKTOP, POINTER_SCENES, TIME_SCENES, CLAUDE_PREVIEWS):
            if play.actions():
                play.addSeparator()
            for key in group:
                if key == "grab" and not self.sp.dangle_drawn:
                    continue
                play.addAction(LABELS[key]).triggered.connect(lambda _=False, k=key: self.play(k))
        hats = m.addMenu("Hat")
        hat_group = QActionGroup(hats)
        for key, label in [("auto", "By the date and time"), ("none", "No hat")] + list(HATS.items()):
            act = hats.addAction(label)
            act.setCheckable(True)
            act.setChecked(self.prefs["hat"] == key)
            hat_group.addAction(act)
            act.triggered.connect(lambda _=False, k=key: self.set_pref("hat", k))
        size = m.addMenu("Size")
        group = QActionGroup(size)
        for label, s in SCALES.items():
            act = size.addAction(label)
            act.setCheckable(True)
            act.setChecked(s == self.scale)
            group.addAction(act)
            act.triggered.connect(lambda _=False, s=s: self.set_scale(s))
        m.addSeparator()
        quiet = m.addAction("Quiet mode")
        quiet.setCheckable(True)
        quiet.setChecked(self.prefs["quiet"])
        quiet.triggered.connect(lambda on: self.set_pref("quiet", on))
        follow = m.addAction("Follow Claude Code")
        follow.setCheckable(True)
        follow.setChecked(self.prefs["claude"])
        follow.triggered.connect(lambda on: self.set_pref("claude", on))
        m.addAction("Settings…").triggered.connect(lambda _=False: self.open_settings())
        login = m.addAction("Start at login")
        login.setCheckable(True)
        login.setChecked(autostart_enabled())
        login.triggered.connect(lambda on: set_autostart(on))
        m.addAction("Restart Clawd").triggered.connect(lambda _=False: self.restart())
        m.addAction("Quit").triggered.connect(lambda _=False: QApplication.quit())
        return m

    def contextMenuEvent(self, e):
        self._menu = self.fill_menu(QMenu(self))
        self._menu.exec(e.globalPos())


class SettingsDialog(QDialog):
    """Clawd's settings. Every change applies straight away."""

    def __init__(self, pet):
        super().__init__(None)
        self.pet = pet
        self.checks, self.scenes, self.spins = {}, {}, {}
        self.setWindowTitle("Clawd settings")
        self.setWindowIcon(tray_icon(pet.sp))
        root = QVBoxLayout(self)

        box = QGroupBox("How he behaves")
        grid = QGridLayout(box)
        grid.addWidget(QLabel("Activity"), 0, 0)
        self.activity = QComboBox()
        for key, label in (("calm", "Calm: long rests between scenes"), ("normal", "Normal"),
                           ("lively", "Lively: always up to something")):
            self.activity.addItem(label, key)
        self.activity.setCurrentIndex(max(0, self.activity.findData(pet.prefs["activity"])))
        self.activity.currentIndexChanged.connect(
            lambda _i: pet.set_pref("activity", self.activity.currentData()))
        grid.addWidget(self.activity, 0, 1)
        grid.addWidget(QLabel("Size"), 1, 0)
        self.scale_box = QComboBox()
        for label, scale in SCALES.items():
            self.scale_box.addItem(label, scale)
        self.scale_box.setCurrentIndex(max(0, self.scale_box.findData(pet.scale)))
        self.scale_box.currentIndexChanged.connect(lambda _i: self._resize(self.scale_box.currentData()))
        grid.addWidget(self.scale_box, 1, 1)
        grid.addWidget(self._check("quiet", "Quiet mode: he stays put and keeps to himself "
                                            "(Claude Code still shows)"), 2, 0, 1, 2)
        root.addWidget(box)

        box = QGroupBox("What he does on his own (the Play menu still has everything)")
        grid = QGridLayout(box)
        off = set(pet.prefs["scenes_off"])
        for col, keys in enumerate(OWN_SCENES):
            for row, key in enumerate(keys):
                cb = QCheckBox(LABELS[key])
                cb.setChecked(key not in off)
                cb.toggled.connect(lambda on, k=key: self._scene(k, on))
                grid.addWidget(cb, row, col)
                self.scenes[key] = cb
        root.addWidget(box)

        box = QGroupBox("Reactions")
        lay = QVBoxLayout(box)
        lay.addWidget(self._check("pointer", "Watch the pointer"))
        lay.addWidget(self._check("petting", "Enjoy being petted (stroke him back and forth)"))
        lay.addWidget(self._check("grab", "Grab onto the pointer when it hangs around above him "
                                          "(shake it to get him off)"))
        lay.addWidget(self._check("duck", "Duck out of sight while something is fullscreen"))
        root.addWidget(box)

        box = QGroupBox("Time and seasons")
        lay = QVBoxLayout(box)
        lay.addWidget(self._check("day_cycle", "Time of day: yawns and naps at night, coffee in the morning"))
        lay.addWidget(self._check("seasons", "Holidays: Santa hat in December, pumpkin at Halloween, "
                                             "party hat at New Year"))
        self.times = {}
        row = QHBoxLayout()
        for key, before in (("night_from", "Night from"), ("night_to", "to"), ("morning_to", "; mornings until")):
            row.addWidget(QLabel(before))
            edit = QTimeEdit()
            edit.setDisplayFormat("HH:mm")
            minutes = pet.prefs[key]
            edit.setTime(QTime(minutes // 60, minutes % 60))
            edit.timeChanged.connect(lambda t, k=key: pet.set_pref(k, t.hour() * 60 + t.minute()))
            row.addWidget(edit)
            self.times[key] = edit
        row.addStretch(1)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(self._check("celebrate", "A party on my birthday:"))
        self.birthday = QDateEdit()
        self.birthday.setDisplayFormat("d MMMM")
        b = pet.prefs["birthday"] or "01-01"
        self.birthday.setDate(QDate(2000, int(b[:2]), int(b[3:])))
        self.birthday.dateChanged.connect(lambda d: pet.set_pref("birthday", d.toString("MM-dd")))
        row.addWidget(self.birthday)
        row.addStretch(1)
        lay.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Hat"))
        self.hat = QComboBox()
        for key, label in [("auto", "By the date and time"), ("none", "No hat")] + list(HATS.items()):
            self.hat.addItem(label, key)
        self.hat.setCurrentIndex(max(0, self.hat.findData(pet.prefs["hat"])))
        self.hat.currentIndexChanged.connect(lambda _i: pet.set_pref("hat", self.hat.currentData()))
        row.addWidget(self.hat, 1)
        lay.addLayout(row)
        root.addWidget(box)

        box = QGroupBox("Reminders (click him when you've seen one)")
        grid = QGridLayout(box)
        self._reminder(grid, 0, "breaks", "break_every", "Take a break every", "minutes at the computer")
        self._reminder(grid, 1, "water", "water_every", "Drink some water every", "minutes")
        root.addWidget(box)

        box = QGroupBox("Claude Code")
        grid = QGridLayout(box)
        grid.addWidget(self._check("claude", "Follow what Claude Code is doing (typing, reading, "
                                             "calling you over, celebrating)"), 0, 0, 1, 2)
        self.hooks_label = QLabel()
        self.hooks_label.setWordWrap(True)
        self.hooks_button = QPushButton()
        self.hooks_button.clicked.connect(lambda _=False: self._flip_hooks())
        grid.addWidget(self.hooks_label, 1, 0)
        grid.addWidget(self.hooks_button, 1, 1)
        self.login = QCheckBox("Start Clawd at login")
        self.login.setChecked(autostart_enabled())
        self.login.toggled.connect(lambda on: (set_autostart(on), self.pet.settings_changed("login", not on, on)))
        grid.addWidget(self.login, 2, 0, 1, 2)
        root.addWidget(box)
        self._show_hooks()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.close)
        root.addWidget(buttons)

    def _resize(self, scale):
        old = self.pet.scale
        self.pet.set_scale(scale)
        self.pet.settings_changed("scale", old, scale)

    def _check(self, key, label):
        cb = QCheckBox(label)
        cb.setChecked(self.pet.prefs[key])
        cb.toggled.connect(lambda on: self.pet.set_pref(key, on))
        self.checks[key] = cb
        return cb

    def _scene(self, key, on):
        off = [k for k in self.pet.prefs["scenes_off"] if k != key]
        self.pet.set_pref("scenes_off", off if on else off + [key])

    def _reminder(self, grid, row, flag, every, before, after):
        cb = self._check(flag, before)
        spin = QSpinBox()
        spin.setRange(10, 240)
        spin.setSingleStep(5)
        spin.setValue(self.pet.prefs[every])
        spin.setEnabled(cb.isChecked())
        spin.valueChanged.connect(lambda v: self.pet.set_pref(every, v))
        cb.toggled.connect(spin.setEnabled)
        grid.addWidget(cb, row, 0)
        grid.addWidget(spin, row, 1)
        grid.addWidget(QLabel(after), row, 2)
        self.spins[every] = spin

    def _show_hooks(self):
        have, want = hooks_installed()
        if have == want:
            text, button = "Hooks installed: he follows what Claude Code is doing.", "Remove"
        elif have:
            text, button = f"Hooks partly installed ({have} of {want} events).", "Repair"
        else:
            text, button = "Hooks not installed: he can't see what Claude Code is doing.", "Install"
        self.hooks_label.setText(text)
        self.hooks_button.setText(button)

    def _flip_hooks(self):
        have, want = hooks_installed()
        run_hook_installer(remove=have == want)
        self._show_hooks()


def tray_icon(sprites):
    img = sprites.poses["idle"]
    s = 2
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    w, h = img.width() * s, img.height() * s
    p.drawImage(QRect((64 - w) // 2, (64 - h) // 2, w, h), img)
    p.end()
    return QIcon(pm)


def main():
    if already_running():
        print("Clawd is already running.")
        return
    app = QApplication(sys.argv)
    app.setApplicationName("clawd-pet")
    app.setQuitOnLastWindowClosed(False)
    sprites = load_sprites()
    settings = QSettings("clawd-pet", "clawd-pet")
    pet = ClawdPet(sprites, settings)
    pet.show()
    pet.listen()
    feed = start_kwin_feed(pet.cursor_moved, pet.windows_changed)
    pet._poll_cursor = feed is None
    if feed is not None:
        app.aboutToQuit.connect(stop_kwin_feed)
        catcher = DropCatcher(pet)               # KWin won't hand drags to him directly
        if catcher.typed:
            pet.catcher = catcher
    if os.path.exists(PLASMA_DESKTOP_RC):
        pet.watch_wallpaper()

    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(tray_icon(sprites), app)
        tray.setToolTip("Clawd")
        menu = QMenu()
        show_hide = menu.addAction("Hide Clawd")
        menu.addSeparator()
        pet.fill_menu(menu)

        def flip():
            pet.setVisible(not pet.isVisible())
            show_hide.setText("Hide Clawd" if pet.isVisible() else "Show Clawd")

        show_hide.triggered.connect(lambda _=False: flip())
        tray.activated.connect(
            lambda reason: flip() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        tray.setContextMenu(menu)
        tray.show()
        pet.tray = tray

    app.aboutToQuit.connect(pet.save)
    # quit cleanly (and remember where he was) on Ctrl+C or kill
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
