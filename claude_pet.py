"""
Clawd Desktop Pet: the Claude Code mascot, living on your desktop.

Every animation is traced from Anthropic's official Clawd art
(tools/trace_official.py turns their GIFs into sprites/clawd.json), so he
moves the way he does in the Claude apps: crab-walking, jumping, waving,
typing on a laptop, riding a cloud, racing a kart, peeking in from the edge
of the screen.

Left-click: open Claude Code        Right-click: menu
Drag: pick him up and throw him     (he falls back to the bottom of the screen)
"""

import json
import os
import random
import shutil
import signal
import subprocess
import sys
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

from PyQt6.QtCore import QElapsedTimer, QPoint, QRect, QSettings, Qt, QTimer
from PyQt6.QtGui import QActionGroup, QBitmap, QIcon, QImage, QPainter, QPixmap, QRegion
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

HERE = os.path.dirname(os.path.abspath(__file__))
SPRITES = os.path.join(HERE, "sprites", "clawd.json")

SCALES = {"Small": 3, "Medium": 4, "Large": 6, "Huge": 8}   # screen px per sprite pixel
DEFAULT_SCALE = 4
TICK_MS = 16

# Speeds in sprite pixels per second, so they grow with the pet.
WALK_SPEED = 6
CLOUD_SPEED = 30
RACE_SPEED = 45
GRAVITY = 700

# What he does on his own, and how often.
WEIGHTS = {
    "walk": 30, "wave": 8, "jump": 5, "jump_happy": 5, "dance": 5, "laptop": 8,
    "sparkler": 4, "cloud": 5, "race": 4, "lurk": 7, "sleep": 3,
}
ACTIONS = list(WEIGHTS)
LABELS = {
    "walk": "Walk", "wave": "Wave", "jump": "Jump", "jump_happy": "Happy jump",
    "dance": "Dance", "laptop": "Laptop", "sparkler": "Sparkler",
    "cloud": "Ride the cloud", "race": "Go karting", "lurk": "Peek from the edge",
    "sleep": "Nap",
}

# Where the eyes sit in the idle pose (top-left of each 2x2 eye).
EYES = ((6, 2), (16, 2))

# Sleeping Z's, drawn on the same pixel grid as Clawd.
Z_SMALL = ["##.", ".#.", ".##"]
Z_BIG = ["####", "..#.", ".#..", "####"]
Z_RGB = (106, 155, 204)        # Claude Code's own "professional blue"


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


class Anim:
    """One traced animation: frames, their timing, and where the idle Clawd
    stands inside them, so every animation lines up with every other."""

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
        self.z = [grid_image(g, {"#": Z_RGB}) for g in (Z_SMALL, Z_BIG)]


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


def open_claude_code():
    """Open Claude Code in a terminal. Returns False if no terminal was found."""
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


def open_claude_web():
    if sys.platform.startswith("linux") and shutil.which("xdg-open"):
        spawn(["xdg-open", "https://claude.ai"])
    else:
        webbrowser.open("https://claude.ai")


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
        self.setWindowTitle("Clawd")

        scale = DEFAULT_SCALE
        if settings is not None:
            scale = int(settings.value("scale", DEFAULT_SCALE))
        self.scale = scale if scale in SCALES.values() else DEFAULT_SCALE
        self._pixmaps, self._masks = {}, {}
        self._layout()

        self.x = self.y = 0.0          # window top-left, as floats for smooth motion
        self.vx = self.vy = 0.0        # px/s
        self.airborne = False
        self.dragging = False
        self.offscreen = False         # lurking: allowed past the screen edge
        self.scripted = False          # leaping: the behaviour moves him, not physics
        self.pad = (0, 0)              # how far the current prop sticks out, in pixels
        self.particles = []            # sleeping Z's: [x, y, age_ms, glyph]
        self.frame = ("pose", "idle", False)
        self.action = None
        self.script = None
        self.wait = 0.0
        self._press = None
        self._trail = []
        self._shown = None
        self._mask = None

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
        left, right, top, bottom = 0, self.iw, 14, self.ih   # 14: room for Z's
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

    def _leap_target(self):
        """(side, area) when he's near a seam and the next screen's floor is
        higher than his, so walking there would hit a wall."""
        geo = self.screen_geometry()
        room_l, room_r = self._room()
        feet = self._feet()
        for side, room in ((-1, room_l), (1, room_r)):
            if room > 300:
                continue
            x = geo.left() - 1 if side < 0 else geo.left() + geo.width()
            for a in screen_areas():
                floor = a.top() + a.height()
                if (a != geo and a.left() <= x < a.left() + a.width()
                        and geo.top() + self.ih * self.scale < floor < feet - 8):
                    return side, a
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
        bottom = self.y + self.home_px.y() + self.ih * self.scale
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
        screen = QApplication.screenAt(QPoint(int(self.box_span()[0]), int(self.y + self.height() / 2)))
        if screen is not None:
            self.settings.setValue("screen", screen.name())

    # ── Driving behaviours ────────────────────────────────────────

    def start(self, action, **kw):
        self.vx = 0.0 if not self.airborne else self.vx
        self.pad = (0, 0)
        self.offscreen = False
        self.scripted = False
        if action != "sleep":
            self.particles.clear()
        self.action = action
        self.script = getattr(self, "_act_" + action)(**kw)
        self.wait = 0.0

    def _next(self):
        if self.action == "idle":
            self.start(self._pick_action())
        else:
            self.start("idle")

    def _pick_action(self):
        weights = dict(WEIGHTS)
        if self._lurk_side() is None:
            weights["lurk"] = 0
        if self._leap_target() is not None:
            weights["leap"] = 25         # otherwise he'd pile up on the lower screen
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

    # ── What to show ──────────────────────────────────────────────

    def show_frame(self, name, idx, mirror=False):
        self.frame = ("anim", name, idx, mirror)

    def pose(self, name):
        self.frame = ("pose", name, False)

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

    def _refresh(self):
        """Repaint (and re-shape the window) only when something visible changed."""
        s = self.scale
        zs = tuple((int(p[0]), int(p[1]), int(p[2] // 500), p[3]) for p in self.particles)
        state = (self.frame, zs)
        if state == self._shown:
            return
        mask = self._masks.get(self.frame)
        if mask is None:
            bitmap = QBitmap.fromImage(self._pixmap(self.frame).toImage().createAlphaMask())
            mask = QRegion(bitmap).translated(self._frame_pos(self.frame))
            self._masks[self.frame] = mask
        for x, y, _, glyph in self.particles:
            z = self.sp.z[glyph]
            mask = mask.united(QRect(self.home_px.x() + int(x) * s, self.home_px.y() + int(y) * s,
                                     z.width() * s, z.height() * s))
        if mask.isEmpty():
            mask = QRegion(0, 0, 1, 1)           # an empty mask would mean "no mask"
        # The window is shaped to Clawd (plus his Z's): the empty space around
        # him lets clicks through to whatever is behind. The shape clips
        # painting too, which is why the Z's have to be part of it.
        if mask != self._mask:
            self.setMask(mask)
            self._mask = mask
        self._shown = state
        self.update()

    def paint(self, p):
        p.drawPixmap(self._frame_pos(self.frame), self._pixmap(self.frame))
        s = self.scale
        for x, y, age, glyph in self.particles:
            p.setOpacity(max(0.0, 1.0 - (age // 500) * 500 / 3000))
            z = self.sp.z[glyph]
            p.drawImage(QRect(self.home_px.x() + int(x) * s, self.home_px.y() + int(y) * s,
                              z.width() * s, z.height() * s), z)
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

    def _age_particles(self, dt):
        for p in self.particles:
            p[2] += dt
            p[1] -= dt / 1000 * 2.0          # rise two pixels a second
            p[0] += dt / 1000 * 0.7
        self.particles = [p for p in self.particles if p[2] < 3000]

    # ── Behaviours ────────────────────────────────────────────────

    def _play(self, name, frames, mirror=False):
        a = self.sp.anims[name]
        for i in frames:
            self.show_frame(name, i, mirror)
            yield a.ms[i]

    def _loop(self, name, mirror=False):
        """The animation's loop segment, forever (the caller decides when to stop)."""
        a = self.sp.anims[name]
        lo, hi = a.loop
        while True:
            for i in range(lo, hi + 1):
                self.show_frame(name, i, mirror)
                yield a.ms[i]

    def _mirror_for(self, name):
        """Face whichever way keeps the animation on screen."""
        room_l, room_r = self._room()
        if self._pads(name, False)[1] <= room_r:
            return False
        return self._pads(name, True)[0] <= room_l

    def _act_idle(self):
        self.pose("idle")
        yield 0
        left, right = self.box_span()
        geo = self.screen_geometry()
        if left < geo.left() or right > geo.left() + geo.width():
            # interrupted while peeking from off-screen: walk back into view
            yield from self._walk_to(min(max(left, geo.left() + 20),
                                         geo.left() + geo.width() - self.iw * self.scale - 20))
            self.pose("idle")
        end, t = random.uniform(5000, 14000), 0.0
        while t < end:
            hold = random.uniform(1500, 4000)
            yield hold
            t += hold
            r = random.random()
            if r < 0.6:
                for _ in range(2 if random.random() < 0.2 else 1):
                    self.pose("blink")
                    yield 110
                    self.pose("idle")
                    yield 150
            elif r < 0.9:
                self.pose(random.choice(("look_l", "look_r")))
                look = random.uniform(500, 1400)
                yield look
                t += look
                self.pose("idle")
            else:
                self.pose("happy")
                yield 900
                t += 900
                self.pose("idle")

    def _walk_to(self, target, speed=WALK_SPEED):
        """Crab-walk until the idle pose's left edge reaches `target`."""
        direction = 1 if target > self.box_span()[0] else -1
        self.vx = direction * speed * self.scale
        steps = self._loop("walk", mirror=direction < 0)
        while self.vx and (target - self.box_span()[0]) * direction > 0:
            yield next(steps)
        self.vx = 0.0
        if self.offscreen:
            self.set_box_left(target)

    def _act_walk(self, target=None):
        if target is None:
            if random.random() < 0.25 and len(screen_areas()) > 1:
                # a trip: anywhere on the desktop, across screens if need be
                a = random.choice(screen_areas())
                target = a.left() + random.uniform(0, a.width() - self.iw * self.scale)
            else:
                room_l, room_r = self._reach()
                direction = 1 if random.uniform(0, room_l + room_r) < room_r else -1
                room = room_r if direction > 0 else room_l
                dist = min(room, random.uniform(60, 420))
                if dist < 8:
                    return
                target = self.box_span()[0] + direction * dist
        for _ in range(4):                 # a few seams at most
            yield from self._walk_to(target)
            left = self.box_span()[0]
            if abs(left - target) < 4:
                return
            # Stopped short: a wall. Leap if a higher screen is what's in the way.
            leap = self._leap_target()
            if leap is None or leap[0] != (1 if target > left else -1):
                return
            yield from self._act_leap()

    def _act_leap(self):
        """Jump up onto the higher screen next to this one."""
        target = self._leap_target()
        if target is None:
            return
        side, area = target
        s = self.scale
        width = self.iw * s
        geo = self.screen_geometry()
        seam = geo.left() if side < 0 else geo.left() + geo.width()
        # run up to just short of the seam
        yield from self._walk_to(seam + 3 * s if side < 0 else seam - width - 3 * s)
        self.pose("idle")
        yield 200
        land = random.uniform(10, 40) * s
        x0, y0 = self.x, self.y
        x1 = (seam + land if side > 0 else seam - width - land) - self.home_px.x()
        y1 = area.top() + area.height() - self.home_px.y() - self.ih * s
        # A real ballistic arc under GRAVITY, peaking a little above the new floor.
        g = GRAVITY * s
        apex = min(y0, y1) - 10 * s
        t_up = (2 * (y0 - apex) / g) ** 0.5
        t_down = (2 * (y1 - apex) / g) ** 0.5
        total = t_up + t_down
        # No sideways drift until his feet are above the floor he's aiming
        # for, so he never clips the corner where the screens meet.
        t_clear = t_up - t_down if y1 < y0 else 0.0
        self.show_frame("jump", 1)           # crouch
        yield 120
        self.show_frame("jump", 2)           # arms up
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
        self.scripted = False
        yield from self._play("jump", range(8, len(self.sp.anims["jump"].frames)))

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

    def _act_laptop(self):
        mirror = self._mirror_for("laptop")
        self.pad = self._pads("laptop", mirror)
        a = self.sp.anims["laptop"]
        yield from self._play("laptop", range(0, a.loop[0]), mirror)
        steps, typed, end = self._loop("laptop", mirror), 0.0, random.uniform(3000, 9000)
        while typed < end:
            ms = next(steps)
            typed += ms
            yield ms
        c, d = a.outro
        yield from self._play("laptop", range(c, d + 1), mirror)

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
            self.particles.append([self.iw - 6 + random.random() * 2, -1.0, 0.0, n % 2])
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
        if self._press is None:
            return
        pos = e.globalPosition().toPoint()
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
            self.start("jump_happy")
            if not waking:                         # a click on a sleeping Clawd just wakes him
                self.launch_claude_code()
        self._press = None
        e.accept()

    def launch_claude_code(self):
        if not open_claude_code() and getattr(self, "tray", None) is not None:
            self.tray.showMessage("Clawd", "No terminal found to run Claude Code in.")

    # ── Menus ─────────────────────────────────────────────────────

    def fill_menu(self, m):
        m.addAction("Open Claude Code").triggered.connect(lambda _=False: self.launch_claude_code())
        m.addAction("Open claude.ai").triggered.connect(lambda _=False: open_claude_web())
        m.addSeparator()
        play = m.addMenu("Play")
        for key in ACTIONS:
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
