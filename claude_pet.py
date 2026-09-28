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

import json
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import webbrowser

# A desktop pet is a window that moves itself, which Wayland doesn't allow,
# so run through XWayland unless QT_QPA_PLATFORM is already set. Programs we
# launch get the original environment back (see launch_env).
_FORCED_XCB = False
if (sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY")
        and "QT_QPA_PLATFORM" not in os.environ):
    os.environ["QT_QPA_PLATFORM"] = "xcb"
    _FORCED_XCB = True

from PyQt6.QtCore import (QElapsedTimer, QObject, QPoint, QRect, QSettings, Qt, QTimer,
                          pyqtClassInfo, pyqtSlot)
from PyQt6.QtGui import QActionGroup, QBitmap, QCursor, QIcon, QImage, QPainter, QPixmap, QRegion
from PyQt6.QtNetwork import QLocalServer
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

HERE = os.path.dirname(os.path.abspath(__file__))
SPRITES = os.path.join(HERE, "sprites", "clawd.json")

SCALES = {"Small": 3, "Medium": 4, "Large": 6, "Huge": 8}   # screen px per sprite pixel
DEFAULT_SCALE = 4
TICK_MS = 16

# Speeds in sprite pixels per second, so they grow with the pet. The tempos
# speed up the official walk cycle so his legs keep up with the pace.
WALK_SPEED, WALK_TEMPO = 15, 0.7
TRIP_SPEED, TRIP_TEMPO = 24, 0.5
CLOUD_SPEED = 30
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
}
PLAYABLE = set(ACTIONS + BETWEEN_SCREENS + ["idle", "work", "attention", "celebrate"])

# Where the eyes sit in the idle pose (top-left of each 2x2 eye).
EYES = ((6, 2), (16, 2))

INK = (20, 20, 19)            # Anthropic's near-black, as in the official art
IVORY = (250, 249, 245)
BLUE = (106, 155, 204)        # Claude Code's own "professional blue"
PINK = (232, 91, 106)
RAIL_DARK = (77, 76, 72)      # the kart's greys
RAIL_LIGHT = (156, 154, 146)

# Little extras drawn on the same pixel grid as Clawd.
GLYPHS = {
    "z_small": (["##.", ".#.", ".##"], {"#": BLUE}),
    "z_big": (["####", "..#.", ".#..", "####"], {"#": BLUE}),
    "heart": ([".#.#.", "#####", "#####", ".###.", "..#.."], {"#": PINK}),
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
}
LADDER_W = 10
LADDER_PALETTE = {"d": RAIL_DARK, "l": RAIL_LIGHT}

CURSOR_NEAR = 70              # sprite pixels: how close the pointer must be for him to watch it

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
                      for k in ("idle", "blink", "look_l", "look_r", "happy")}
        left = climb_rows(idle)
        right = ["".join(reversed(r)) for r in left]
        self.anims["climb"] = Anim("climb", {
            "size": [self.iw, self.ih + 4], "home": [0, 4],
            "frames": [{"ms": 130, "rows": left}, {"ms": 130, "rows": right}]}, palette)
        self.glyphs = {k: grid_image(rows, pal) for k, (rows, pal) in GLYPHS.items()}


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


def open_claude_web():
    if sys.platform.startswith("linux") and shutil.which("xdg-open"):
        spawn(["xdg-open", "https://claude.ai"])
    else:
        webbrowser.open("https://claude.ai")


# ── Where the pointer is ────────────────────────────────────────────
# Under XWayland an X11 app only sees the pointer while it is over an X11
# window, so on KDE we ask the compositor itself: a tiny KWin script reports
# the pointer position to us over D-Bus.

def kwin_cursor_script():
    return f"""
let last = {{x: -1e6, y: -1e6}};
function send() {{
    const p = workspace.cursorPos;
    if (Math.abs(p.x - last.x) + Math.abs(p.y - last.y) < 4) return;
    last = {{x: p.x, y: p.y}};
    callDBus("{DBUS_SERVICE}", "/Pet", "{DBUS_SERVICE}", "Cursor", p.x + "," + p.y);
}}
workspace.cursorPosChanged.connect(send);
send();
"""


@pyqtClassInfo("D-Bus Interface", DBUS_SERVICE)
class CursorFeed(QObject):
    def __init__(self, callback):
        super().__init__()
        self._callback = callback

    @pyqtSlot(str)
    def Cursor(self, pos):                       # noqa: N802 (D-Bus method name)
        try:
            x, y = pos.split(",")
            self._callback(int(float(x)), int(float(y)))
        except ValueError:
            pass


def start_kwin_cursor_feed(callback):
    """Start the KWin cursor feed; returns it, or None if this isn't KDE Wayland."""
    if not (os.environ.get("WAYLAND_DISPLAY") and "KDE" in os.environ.get("XDG_CURRENT_DESKTOP", "")):
        return None
    try:
        from PyQt6.QtDBus import QDBusConnection, QDBusInterface
    except ImportError:
        return None
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected() or not bus.registerService(DBUS_SERVICE):
        return None
    feed = CursorFeed(callback)
    if not bus.registerObject("/Pet", feed, QDBusConnection.RegisterOption.ExportAllSlots):
        return None
    path = os.path.join(runtime_dir(), "clawd-pet-cursor.js")
    with open(path, "w") as f:
        f.write(kwin_cursor_script())
    kwin = QDBusInterface("org.kde.KWin", "/Scripting", "org.kde.kwin.Scripting", bus)
    if not kwin.isValid():
        return None
    kwin.call("unloadScript", KWIN_SCRIPT)
    args = kwin.call("loadScript", path, KWIN_SCRIPT).arguments()
    if not args or not isinstance(args[0], int) or args[0] < 0:
        return None
    QDBusInterface("org.kde.KWin", f"/Scripting/Script{args[0]}", "org.kde.kwin.Script", bus).call("run")
    return feed


def stop_kwin_cursor_feed():
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
    if QApplication.platformName() != "xcb" or not shutil.which("xprop"):
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


def screen_areas():
    work = x11_work_area()
    areas = []
    for sc in QApplication.screens():
        geo = sc.geometry()
        avail = geo.intersected(work) if work is not None else sc.availableGeometry()
        areas.append(usable_area(geo, avail))
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
        self.setWindowTitle("Clawd")

        scale = DEFAULT_SCALE
        if settings is not None:
            scale = int(settings.value("scale", DEFAULT_SCALE))
        self.scale = scale if scale in SCALES.values() else DEFAULT_SCALE
        self._pixmaps, self._masks = {}, {}
        self._layout()

        self.now = 0.0                 # ms of simulated time, advanced by advance()
        self.x = self.y = 0.0          # window top-left, as floats for smooth motion
        self.vx = self.vy = 0.0        # px/s
        self.airborne = False
        self.dragging = False
        self.offscreen = False         # lurking: allowed past the screen edge
        self.scripted = False          # leaping or climbing: the behaviour moves him, not physics
        self.pad = (0, 0)              # how far the current prop sticks out, in pixels
        self.particles = []            # Z's, hearts, the "!" bubble
        self.frame = ("pose", "idle", False)
        self.action = None
        self.script = None
        self.wait = 0.0
        self._press = None
        self._trail = []
        self._shown = None
        self._mask = None
        self.ladder = Prop()

        self.sessions = {}             # Claude Code sessions: state, since, seen
        self.celebrate = None          # ms of work to celebrate once he's free
        self.greet = False
        self.last_message = None
        self.server = None

        self.cursor = None             # (x, y, when) of the pointer, when known
        self._poll_cursor = False
        self._polled = 0.0
        self._tracking = False
        self._pet_dir = 0
        self._pet_flips = []
        self._petting_until = -1.0
        self._was_petting = False
        self._pet_ms = 0.0
        self._last_heart = -1e9

        self._place_initially()
        self.start("idle")
        QApplication.instance().screenRemoved.connect(
            lambda _screen: QTimer.singleShot(300, self._rescue))

        self.clock = QElapsedTimer()
        self.clock.start()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_timer)
        self.timer.start(TICK_MS)

    # ── Geometry ──────────────────────────────────────────────────

    def _layout(self):
        """Size the window to fit every animation, mirrored or not."""
        left, right, top, bottom = 0, self.iw, 14, self.ih   # 14: room for Z's and hearts
        for a in self.sp.anims.values():
            hx, hy = a.home
            for lft in (hx, a.w - hx - self.iw):
                left = max(left, lft)
                right = max(right, a.w - lft)
            top = max(top, hy)
            bottom = max(bottom, a.h - hy)
        s = self.scale
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

    def ground_y(self):
        geo = self.screen_geometry()
        return geo.top() + geo.height() - self.home_px.y() - self.ih * self.scale

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
        """Space left and right of him on this screen."""
        geo = self.screen_geometry()
        left, right = self.box_span()
        return left - geo.left(), geo.left() + geo.width() - right

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
        self.settings.setValue("scale", self.scale)
        self.settings.setValue("left", self.box_span()[0])
        screen = QApplication.screenAt(QPoint(int(self.box_span()[0]), int(self._mid())))
        if screen is not None:
            self.settings.setValue("screen", screen.name())

    # ── Driving behaviours ────────────────────────────────────────

    def start(self, action, **kw):
        self.vx = 0.0 if not self.airborne else self.vx
        self.pad = (0, 0)
        self.offscreen = False
        self.scripted = False
        if self.ladder.isVisible():
            self.ladder.hide()                  # interrupted mid-climb: pack it away
        if action != "sleep":
            self.particles = [q for q in self.particles if q["kind"] == "heart"]
        self.action = action
        self.script = getattr(self, "_act_" + action)(**kw)
        self.wait = 0.0

    def _next(self):
        mode = self.claude_mode()
        if mode == "attention":
            self.start("attention")
        elif mode == "busy":
            self.start("work")
        elif self.celebrate is not None:
            self.start("celebrate")
        elif self.greet:
            self.greet = False
            self.start("wave")
        elif self.action == "idle":
            self.start(self._pick_action())
        else:
            self.start("idle")

    def _pick_action(self):
        weights = dict(WEIGHTS)
        if self._lurk_side() is None:
            weights["lurk"] = 0
        if self._seam("up") is not None:
            weights["climb"] = 18        # otherwise he'd pile up on the lower screen
            weights["leap"] = 6
        elif self._seam("down") is not None:
            weights["climb"] = 10
        return random.choices(list(weights), weights=list(weights.values()))[0]

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
        self.advance(min(self.clock.restart(), 100))

    def advance(self, dt):
        """Move the simulation on by dt milliseconds."""
        self.now += dt
        if not (self.dragging or self.scripted):
            self._physics(dt)
        self.wait -= dt
        for _ in range(100):                     # a script can take several steps at once
            if self.wait > 0:
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
        self._petting(dt)
        self._watch_cursor()
        self._age_particles(dt)
        if (int(self.x), int(self.y)) != (self.pos().x(), self.pos().y()):
            self.move(int(self.x), int(self.y))
        self._refresh()

    def _physics(self, dt):
        sec = dt / 1000
        s = self.scale
        if self.airborne:
            self.vy = min(self.vy + GRAVITY * s * sec, 250 * s)
            self._slide(self.vx * sec, bounce=True)
            self.y += self.vy * sec
            ceiling = self.screen_geometry().top() - self.home_px.y()
            if self.y < ceiling:
                self.y, self.vy = ceiling, abs(self.vy) * 0.3
            if self.y >= self.ground_y():
                self.y = self.ground_y()
                self.airborne = False
                self.vx = self.vy = 0.0
            return
        if self.vx:
            self._slide(self.vx * sec)
        if not self.offscreen:
            ground = self.ground_y()
            if self.y < ground - 1:
                self.drop(self.vx * 0.6)         # walked off the edge of a higher screen
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
                self.start(msg["action"])
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
                "ladder": self.ladder.isVisible(), "scale": self.scale}

    def claude_event(self, msg):
        self.last_message = msg
        ev, sid = msg.get("event", ""), msg.get("session") or "?"
        s = self.sessions.setdefault(sid, {"state": "idle", "since": self.now, "seen": self.now})
        s["seen"] = self.now
        if ev in BUSY_EVENTS:
            if s["state"] != "busy":
                s["since"] = self.now
            s["state"] = "busy"
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
        """Switch to what Claude Code needs now, unless he's in the middle of something physical."""
        if (self.dragging or self.airborne or self.scripted
                or self.action in ("held", "fall", "climb", "leap")):
            return                                  # _next() catches up when he's back on his feet
        mode = self.claude_mode()
        if mode == "attention" and self.action != "attention":
            self.start("attention")
        elif mode == "busy" and self.action not in ("work", "attention"):
            self.start("work")
        elif mode is None and self.greet and self.action == "idle":
            self.greet = False
            self.start("wave")

    def click_kind(self):
        return "needs-input" if self.claude_mode() == "attention" else "continue"

    def click_link(self):
        return CLAUDE_LINKS[self.click_kind()]

    # ── The pointer ───────────────────────────────────────────────

    def cursor_moved(self, x, y):
        prev = self.cursor
        self.cursor = (x, y, self.now)
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
        if over and len(self._pet_flips) >= 3:      # stroked back and forth: petting
            self._petting_until = self.now + 500

    def _petting(self, dt):
        petting = self.now < self._petting_until
        if petting:
            self._pet_ms += dt
            if self.now - self._last_heart > 320:
                self._last_heart = self.now
                self._emit("heart", self.iw / 2 - 2 + random.uniform(-6, 6), -3.0,
                           vx=random.uniform(-1, 1), vy=-4.0, life=1400)
            if self.action == "idle" and self.frame[0] == "pose":
                self.pose("happy")
            if self._pet_ms > 3500 and self.action == "idle":
                self._pet_ms = 0.0
                self.start("dance")                 # he loves it
        else:
            self._pet_ms = max(0.0, self._pet_ms - dt)
            if self._was_petting and self.action == "idle" and self.frame == ("pose", "happy", False):
                self.pose("idle")
        self._was_petting = petting

    def _watch_cursor(self):
        """While idle, follow a nearby, moving pointer with his eyes."""
        if self.action != "idle" or self.frame[0] != "pose" or self.now < self._petting_until:
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
        if self.cursor is not None and random.random() < 0.7:
            return "look_l" if self.cursor[0] < sum(self.box_span()) / 2 else "look_r"
        return random.choice(("look_l", "look_r"))

    # ── What to show ──────────────────────────────────────────────

    def show_frame(self, name, idx, mirror=False):
        self.frame = ("anim", name, idx, mirror)

    def pose(self, name):
        self.frame = ("pose", name, False)

    def _emit(self, kind, x, y, vx=0.0, vy=0.0, life=3000.0):
        self.particles.append({"kind": kind, "x": x, "y": y, "y0": y, "age": 0.0,
                               "life": life, "vx": vx, "vy": vy})

    def _bubble(self, on):
        self.particles = [q for q in self.particles if q["kind"] != "bubble"]
        if on:
            self._emit("bubble", self.iw - 8, -13.0, life=float("inf"))

    def _age_particles(self, dt):
        for q in self.particles:
            q["age"] += dt
            if q["kind"] == "bubble":
                q["y"] = q["y0"] - (q["age"] // 400) % 2      # a gentle bob
            else:
                q["x"] += q["vx"] * dt / 1000
                q["y"] += q["vy"] * dt / 1000
        self.particles = [q for q in self.particles if q["age"] < q["life"]]

    def _fade(self, q):
        if q["life"] == float("inf"):
            return 1.0
        return max(0.0, min(1.0, round((q["life"] - q["age"]) / 800 * 4) / 4))

    def _pixmap(self, key):
        pm = self._pixmaps.get(key)
        if pm is None:
            img = self.sp.anims[key[1]].frames[key[2]] if key[0] == "anim" else self.sp.poses[key[1]]
            if key[-1]:
                img = flipped(img)
            s = self.scale
            pm = QPixmap.fromImage(img.scaled(img.width() * s, img.height() * s,
                                              Qt.AspectRatioMode.IgnoreAspectRatio,
                                              Qt.TransformationMode.FastTransformation))
            self._pixmaps[key] = pm
        return pm

    def _frame_pos(self, key):
        if key[0] == "anim":
            return self.frame_rect(self.sp.anims[key[1]], key[2], key[3]).topLeft()
        return self.home_px

    def _glyph_rect(self, q):
        s = self.scale
        g = self.sp.glyphs[q["kind"]]
        return QRect(self.home_px.x() + int(q["x"]) * s, self.home_px.y() + int(q["y"]) * s,
                     g.width() * s, g.height() * s)

    def _refresh(self):
        """Repaint (and re-shape the window) only when something visible changed."""
        state = (self.frame, tuple((q["kind"], int(q["x"]), int(q["y"]), self._fade(q))
                                   for q in self.particles))
        if state == self._shown:
            return
        mask = self._masks.get(self.frame)
        if mask is None:
            bitmap = QBitmap.fromImage(self._pixmap(self.frame).toImage().createAlphaMask())
            mask = QRegion(bitmap).translated(self._frame_pos(self.frame))
            self._masks[self.frame] = mask
        for q in self.particles:
            mask = mask.united(self._glyph_rect(q))
        if mask.isEmpty():
            mask = QRegion(0, 0, 1, 1)           # an empty mask would mean "no mask"
        # The window is shaped to Clawd (plus his Z's, hearts and bubble): the
        # empty space around him lets clicks through to whatever is behind.
        # The shape clips painting too, which is why the extras are part of it.
        if mask != self._mask:
            self.setMask(mask)
            self._mask = mask
        self._shown = state
        self.update()

    def paint(self, p):
        p.drawPixmap(self._frame_pos(self.frame), self._pixmap(self.frame))
        for q in self.particles:
            p.setOpacity(self._fade(q))
            p.drawImage(self._glyph_rect(q), self.sp.glyphs[q["kind"]])
        p.setOpacity(1.0)

    def paintEvent(self, _event):
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
        yield from self._come_back()
        end, t = random.uniform(5000, 14000), 0.0
        while t < end:
            hold = random.uniform(1500, 4000)
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

    def _walk_to(self, target, speed=WALK_SPEED, tempo=WALK_TEMPO):
        """Crab-walk until the idle pose's left edge reaches `target`."""
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
        while (target - self.y) * direction > 0.5:
            self.show_frame("climb", i % 2)
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

    def _act_laptop(self, until_idle=False):
        mirror = self._mirror_for("laptop")
        self.pad = self._pads("laptop", mirror)
        a = self.sp.anims["laptop"]
        yield from self._play("laptop", range(0, a.loop[0]), mirror)
        steps, typed, end = self._loop("laptop", mirror), 0.0, random.uniform(3000, 9000)
        while (self.claude_mode() == "busy") if until_idle else typed < end:
            ms = next(steps)
            typed += ms
            yield ms
        c, d = a.outro
        yield from self._play("laptop", range(c, d + 1), mirror)

    def _act_work(self):
        """Typing on the laptop for as long as Claude Code is busy."""
        yield from self._come_back()
        yield from self._act_laptop(until_idle=True)

    def _act_attention(self):
        """Claude Code needs you: wave with a "!" until it's answered."""
        yield from self._come_back()
        self._bubble(True)
        while self.claude_mode() == "attention":
            yield from self._once("wave")
            self.pose("idle")
            t = 0
            while t < 2500 and self.claude_mode() == "attention":
                yield 100
                t += 100
        self._bubble(False)

    def _act_celebrate(self):
        took, self.celebrate = self.celebrate or 0, None
        yield from self._once("sparkler" if took > 90_000 else "jump_happy")

    def _act_dance(self):
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
        yield from self._once("jump_happy")

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

    def _act_sleep(self):
        self.pose("blink")
        end, t, n = random.uniform(20000, 60000), 0.0, 0
        while t < end:
            n += 1
            self._emit("z_big" if n % 2 else "z_small", self.iw - 6 + random.random() * 2, -1.0,
                       vx=0.7, vy=-2.0, life=3000)
            yield 1300
            t += 1300
        self.pose("idle")
        yield 400

    def _act_held(self):
        self.show_frame("jump", 2)               # arms up, eyes wide
        while True:
            yield 1000

    def _act_fall(self):
        self.show_frame("jump", 2)
        while self.airborne:
            yield TICK_MS
        # Clawd-Jumping's landing: squash, wobble, back to standing
        yield from self._play("jump", range(8, len(self.sp.anims["jump"].frames)))

    # ── Mouse ─────────────────────────────────────────────────────

    def drop(self, vx=0.0, vy=0.0):
        self.vx, self.vy = vx, vy
        self.airborne = True
        self.start("fall")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.globalPosition().toPoint()
            self._grab = self._press - QPoint(int(self.x), int(self.y))
            self._trail = [(self.clock.elapsed(), self._press)]
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
            self._trail = (self._trail + [(self.clock.elapsed(), pos)])[-6:]
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self._press is None:
            return
        if self.dragging:
            self.dragging = False
            (t0, p0), (t1, p1) = self._trail[0], self._trail[-1]
            dt = max(t1 - t0, 1) / 1000
            limit = 400 * self.scale
            vx = max(-limit, min(limit, (p1.x() - p0.x()) / dt))
            vy = max(-limit, min(limit, (p1.y() - p0.y()) / dt))
            self.drop(vx, vy)
        elif not self.airborne:
            waking = self.action == "sleep"
            kind = self.click_kind()
            if self.action not in ("work", "attention"):
                self.start("jump_happy")
            if not waking:                         # a click on a sleeping Clawd just wakes him
                self.launch_claude_code(kind)
        self._press = None
        e.accept()

    def launch_claude_code(self, kind="continue"):
        if not open_claude_code(kind) and getattr(self, "tray", None) is not None:
            self.tray.showMessage("Clawd", "No terminal found to run Claude Code in.")

    def launch_terminal(self):
        if not open_in_terminal() and getattr(self, "tray", None) is not None:
            self.tray.showMessage("Clawd", "No terminal found to run Claude Code in.")

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
        for key in ACTIONS + BETWEEN_SCREENS:
            play.addAction(LABELS[key]).triggered.connect(lambda _=False, k=key: self.start(k))
        size = m.addMenu("Size")
        group = QActionGroup(size)
        for label, s in SCALES.items():
            act = size.addAction(label)
            act.setCheckable(True)
            act.setChecked(s == self.scale)
            group.addAction(act)
            act.triggered.connect(lambda _=False, s=s: self.set_scale(s))
        m.addSeparator()
        m.addAction("Quit").triggered.connect(lambda _=False: QApplication.quit())
        return m

    def contextMenuEvent(self, e):
        self._menu = self.fill_menu(QMenu(self))
        self._menu.exec(e.globalPos())


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
    app = QApplication(sys.argv)
    app.setApplicationName("clawd-pet")
    app.setQuitOnLastWindowClosed(False)
    sprites = load_sprites()
    settings = QSettings("clawd-pet", "clawd-pet")
    pet = ClawdPet(sprites, settings)
    pet.show()
    pet.listen()
    feed = start_kwin_cursor_feed(pet.cursor_moved)
    pet._poll_cursor = feed is None
    if feed is not None:
        app.aboutToQuit.connect(stop_kwin_cursor_feed)

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
