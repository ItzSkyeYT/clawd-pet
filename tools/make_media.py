#!/usr/bin/env python3
"""Draws the README's pictures: a GIF of each of Clawd's scenes on a mock
desktop, and a screenshot of his settings. Everything runs offscreen (Qt's
offscreen platform with the tests' two-screen layout), so nothing appears.

    python tools/make_media.py              # everything, into docs/media/
    python tools/make_media.py dance grab   # just these
    python tools/make_media.py --list       # what there is

Each scene runs the real behaviour with a fixed random seed, so a GIF only
changes when he does. Needs Pillow (pip install Pillow)."""

import collections
import contextlib
import datetime
import math
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["QT_QPA_PLATFORM"] = "offscreen:configfile=" + os.path.join(ROOT, "tests", "screens.json")
os.environ["CLAWD_NO_DESKTOP_ICONS"] = "1"
sys.path.insert(0, ROOT)

from PIL import Image  # noqa: E402
from PyQt6.QtCore import QLocale, QPoint, QRect, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygon  # noqa: E402
from PyQt6.QtWidgets import QApplication, QStyleFactory  # noqa: E402

QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedKingdom))
APP = QApplication.instance() or QApplication([])
import claude_pet as cp  # noqa: E402

OUT = os.path.join(ROOT, "docs", "media")
FRAME_MS = 40                      # a GIF frame every 40 ms (25 fps)
STEP_MS = 20                       # he's advanced in steps this small
AFTERNOON = datetime.datetime(2026, 9, 15, 14, 0)

# The mock desktop: flat colours, no anti-aliasing, like the pixel art
WALL = QColor("#e8e3d8")           # wallpaper
WALL_NEW = QColor("#c9dbe3")       # the new one, for the fright
VOID = QColor("#1c1b19")           # no screen there
PANEL = QColor("#34322e")          # a taskbar under each screen
PANEL_H = 16
WIN_BODY, WIN_EDGE, WIN_BAR = QColor("#faf9f5"), QColor("#a8a194"), QColor("#e6e1d6")
WIN_TEXT = QColor("#dcd6ca")
FOLDER, FOLDER_BACK, PAGE, LABEL = QColor("#e9b44c"), QColor("#c99335"), QColor("#ffffff"), QColor("#3d3a33")

LAPTOP_FLOOR, HDMI_FLOOR = 1530, 1080
SIZES = {"S": (260, 200), "M": (400, 280), "W": (400, 170)}   # three, or two, across GitHub's README
MAX_W = 860                        # wider, and GitHub would shrink it (and blur the pixels)

SPRITES = cp.load_sprites()
SCENES = {}                        # name -> (section, caption, function)


def scene(name, section, caption):
    def register(fn):
        SCENES[name] = (section, caption, fn)
        return fn
    return register


class Stage:
    """One pet on the mock desktop, recorded frame by frame. Each scene runs
    twice with the same seed: once to see where he goes (`crop` is None), then
    to draw just that part of the desktop."""

    crop = None
    crops = {}

    def __init__(self, windows=(), icons=(), clock=AFTERNOON, seed=1, frame_ms=FRAME_MS):
        random.seed(seed)
        cp.wall_clock = lambda: clock
        pet = self.pet = cp.ClawdPet(SPRITES, settings=None)
        pet.show()
        pet.timer.stop()
        pet.launch_claude_code = lambda *a, **k: None
        pet.icon_source = lambda: list(icons)
        pet.windows_changed([list(w) for w in windows])
        self.icons = list(icons)
        self.frame_ms = frame_ms            # longer scenes: fewer frames, same speed
        self.t = 0
        self.frames = []                    # QImages of the crop (drawing pass only)
        self.boxes = []                     # what was drawn, per frame, to crop to
        self.feet = []                      # where his feet were, per frame
        self.wall = WALL
        self.show_cursor = False
        self.extra = None                   # draws more on top: fn(painter)
        self.keep = []                      # rects the crop must include (a window being climbed...)

    def stand(self, box_left, on=None):
        pet = self.pet
        pet.set_box_left(box_left)
        pet.y = pet.ground_y() if on is None else on[1] - pet.home_px.y() - pet.ih * pet.scale
        pet.start("idle")
        pet.advance(16)

    def run(self, ms, until=None, each=None):
        """Advance `ms`, a frame every FRAME_MS; `each(t)` runs every step
        (move the pointer...), `until()` ends it early."""
        end = self.t + ms
        while self.t < end:
            for _ in range(self.frame_ms // STEP_MS):
                self.pet.advance(STEP_MS)
                self.t += STEP_MS
                if each:
                    each(self.t)
            self.snap()
            if until and until():
                break

    def settle(self, action, ms=60_000, tail=600):
        """Run until he's done with `action` (and a little after)."""
        self.run(ms, until=lambda: self.pet.action != action)
        self.run(tail)

    def snap(self):
        pet = self.pet
        mask = getattr(pet, "_mask", None)
        body = mask.boundingRect() if mask is not None and not mask.isEmpty() else pet.rect()
        box = body.translated(int(pet.x), int(pet.y))
        for prop in (pet.ladder, pet.rope):
            if prop.isVisible():
                box = box.united(prop.geometry())
        if self.show_cursor and pet.cursor is not None:
            box = box.united(QRect(int(pet.cursor[0]) - 4, int(pet.cursor[1]) - 4, 24, 30))
        self.boxes.append(box)
        if not (pet.airborne or pet.scripted or pet._dangling):
            self.feet.append(pet._feet())               # standing on something
        if Stage.crop is not None:
            self.frames.append(self.draw())

    def draw(self):
        """The part of the desktop the GIF shows."""
        view = Stage.crop
        img = QImage(view.width(), view.height(), QImage.Format.Format_RGB32)
        img.fill(VOID)
        p = QPainter(img)
        p.translate(-view.x(), -view.y())
        for a in cp.screen_areas():
            p.fillRect(a, self.wall)
            p.fillRect(QRect(a.left(), a.top() + a.height(), a.width(), PANEL_H), PANEL)
        for w in sorted(self.pet.window_list, key=lambda w: w["stack"]):
            draw_window(p, QRect(w["x"], w["y"], w["w"], w["h"]))
        for name, rect, is_dir in self.icons:
            draw_icon(p, name, rect, is_dir)
        for prop in (self.pet.ladder, self.pet.rope):   # his own overlays, as Prop paints them
            if prop.isVisible() and prop.image is not None:
                g = prop.geometry()
                shown = int(g.height() * prop.reveal)
                top = 0 if prop.from_top else g.height() - shown
                p.drawPixmap(g.x(), g.y() + top, prop.image, 0, top, g.width(), shown)
        p.save()
        p.translate(int(self.pet.x), int(self.pet.y))
        p.setClipRect(self.pet.rect())                   # his window's edges, as on screen
        self.pet.paint(p)
        p.restore()
        if self.extra:
            self.extra(p)
        if self.show_cursor and self.pet.cursor is not None:
            draw_pointer(p, int(self.pet.cursor[0]), int(self.pet.cursor[1]))
        p.end()
        return img

    def save(self, name, size=None, pad=24, max_h=None):
        """Measuring: crop to everything that moved (plus `keep`), at a
        standard size ("S", "M", "W") when it fits; no taller than `max_h`
        (the top's cut off: he comes in over it). Drawing: write the GIF."""
        if Stage.crop is not None:
            write_gif(os.path.join(OUT, name + ".gif"), self.frames, self.frame_ms)
            return
        core = self.boxes[0]
        for b in self.boxes[1:] + self.keep:
            core = core.united(b)
        bottom = core.bottom() + pad
        lowest = max(self.feet, default=0)                  # the lowest floor he stood on
        for floor in (LAPTOP_FLOOR, HDMI_FLOOR):            # down to its taskbar (any confetti
            if abs(lowest - floor) < 2 or floor - 2 * pad <= core.bottom() <= floor + PANEL_H:
                bottom = floor + PANEL_H - 1               # falling past it is cut off)
                core.setBottom(min(core.bottom(), bottom))
                break
        if max_h is not None and bottom - core.top() + pad > max_h:
            core.setTop(bottom - max_h + pad)
        if size:
            w, h = SIZES[size]
            if core.width() + 16 <= w and bottom - core.top() + 8 <= h:
                Stage.crops[name] = QRect(core.center().x() - w // 2, bottom - h + 1, w, h)
                return
            print(f"  {name}: {core.width()}x{bottom - core.top()} doesn't fit {size}")
        box = QRect(core.left() - pad, core.top() - pad, core.width() + 2 * pad, bottom - core.top() + pad + 1)
        if box.width() > MAX_W:
            box = QRect(core.center().x() - MAX_W // 2, box.top(), MAX_W, box.height())
        Stage.crops[name] = box


def draw_window(p, r):
    p.fillRect(r, WIN_BODY)
    p.fillRect(QRect(r.x(), r.y(), r.width(), 22), WIN_BAR)
    p.setPen(QPen(WIN_EDGE, 1))
    p.drawRect(r.adjusted(0, 0, -1, -1))
    p.drawLine(r.x(), r.y() + 22, r.x() + r.width() - 1, r.y() + 22)
    for k in range(3):                                   # the buttons, top right
        p.fillRect(QRect(r.x() + r.width() - 18 - 16 * k, r.y() + 7, 8, 8), WIN_EDGE)
    y = r.y() + 38                                       # some text
    for k in range(max(0, (r.height() - 50) // 18)):
        width = int((r.width() - 40) * (0.45 + 0.5 * ((k * 37) % 11) / 11))
        p.fillRect(QRect(r.x() + 20, y, width, 6), WIN_TEXT)
        y += 18


def draw_icon(p, name, r, is_dir):
    cx = r.center().x()
    if is_dir:
        p.fillRect(QRect(cx - 22, r.y() + 10, 18, 8), FOLDER_BACK)
        p.fillRect(QRect(cx - 22, r.y() + 16, 44, 30), FOLDER_BACK)
        p.fillRect(QRect(cx - 22, r.y() + 20, 44, 26), FOLDER)
    else:
        p.fillRect(QRect(cx - 15, r.y() + 6, 30, 40), PAGE)
        p.setPen(QPen(WIN_EDGE, 1))
        p.drawRect(QRect(cx - 15, r.y() + 6, 29, 39))
        for k in range(4):
            p.fillRect(QRect(cx - 9, r.y() + 16 + 6 * k, 18, 2), WIN_TEXT)
    f = QFont()
    f.setPixelSize(11)
    f.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
    p.setFont(f)
    p.setPen(LABEL)
    p.drawText(QRect(r.x() - 20, r.y() + 50, r.width() + 40, 16), Qt.AlignmentFlag.AlignHCenter, name)


def draw_pointer(p, x, y):
    """An arrow pointer, tip at (x, y), about the size of Breeze's at 24 px."""
    pts = [(0, 0), (0, 17), (4, 13), (7, 20), (10, 19), (7, 12), (12, 12)]
    poly = QPolygon([QPoint(x + dx, y + dy) for dx, dy in pts])
    p.setPen(QPen(QColor("#ffffff"), 1))
    p.setBrush(QColor("#232629"))
    p.drawPolygon(poly)
    p.setBrush(Qt.BrushStyle.NoBrush)


def write_gif(path, frames, frame_ms=FRAME_MS):
    """QImages to a looping GIF on one shared palette: every colour exactly
    when there are no more than 256 (the flat mock desktop sees to that),
    else the most common ones exactly and the rare in-between shades of a
    fading hat or prop approximated."""
    ims = []
    for q in frames:
        q = q.convertToFormat(QImage.Format.Format_RGB888)
        ptr = q.constBits()
        ptr.setsize(q.sizeInBytes())
        ims.append(Image.frombuffer("RGB", (q.width(), q.height()), bytes(ptr), "raw", "RGB", q.bytesPerLine(), 1))
    counts = collections.Counter()
    for im in ims:
        for n, c in im.getcolors(im.width * im.height):
            counts[c] += n
    if len(counts) <= 256:
        colours = list(counts)
    else:
        colours = [c for c, _ in counts.most_common(200)]
        rest = [c for c in counts if c not in set(colours)]
        strip = Image.new("RGB", (len(rest), 1))
        strip.putdata(rest)
        extra = strip.quantize(56, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).getpalette()[:56 * 3]
        colours += [tuple(extra[k:k + 3]) for k in range(0, len(extra), 3)]
    flat = [v for c in colours for v in c]
    pal = Image.new("P", (1, 1))
    pal.putpalette(flat + [0] * (768 - len(flat)))
    ps = [im.quantize(palette=pal, dither=Image.Dither.NONE) for im in ims]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ps[0].save(path, save_all=True, append_images=ps[1:], duration=frame_ms, loop=0, optimize=False)


@contextlib.contextmanager
def ride_distance(dist):
    """A ride (cloud, kart) of this length, not a random one, to fit the page."""
    real = random.uniform
    random.uniform = lambda a, b: dist if (a, b) == (250, 900) else real(a, b)
    try:
        yield
    finally:
        random.uniform = real


def nudge_random(value):
    """Pin random.random() (to pick the umbrella or the parachute); returns the undo."""
    real = random.random
    random.random = lambda: value
    return lambda: setattr(random, "random", real)


def laptop_floor_x(cx):
    return cx


# ── On his own ────────────────────────────────────────────────────

def simple(name, action, caption, at=900, tail=700, lead=400, size="S", **kw):
    @scene(name, "own", caption)
    def _():
        st = Stage(**kw)
        st.stand(at)
        st.run(lead)
        st.pet.play(action)
        st.settle(action, tail=tail)
        st.save(name, size)


@scene("idle", "own", "Idle: blinks and looks about")
def _idle():
    st = Stage(seed=3)
    st.stand(900)
    st.pet.start("idle")
    st.run(400)
    for pose, ms in (("blink", 110), ("idle", 900), ("look_l", 1100), ("idle", 500),
                     ("look_r", 1100), ("idle", 600), ("blink", 110), ("idle", 150), ("blink", 110), ("idle", 800)):
        st.pet.pose(pose)
        st.run(ms)
    st.save("idle", "S")


@scene("walk", "own", "Walk")
def _walk():
    st = Stage()
    st.stand(700)
    st.pet.start("walk", target=880)
    st.settle("walk", tail=300)
    st.pet.start("walk", target=700)
    st.settle("walk", tail=300)
    st.save("walk", "W")


simple("wave", "wave", "Wave")
simple("jump", "jump", "Jump")
simple("jump_happy", "jump_happy", "Happy jump")
simple("dance", "dance", "Dance")
simple("laptop", "laptop", "Laptop")
simple("sparkler", "sparkler", "Sparkler")


@scene("sleep", "own", "Nap")
def _sleep():
    st = Stage()
    st.stand(900)
    st.run(400)
    st.pet.play("sleep")
    st.run(9000)
    st.save("sleep", "S")


def ride(name, action, caption):
    @scene(name, "own", caption)
    def _():
        st = Stage()
        st.stand(600)
        st.run(300)
        with ride_distance(420):
            st.pet.play(action)
            st.settle(action)
        st.save(name)


ride("cloud", "cloud", "Ride the cloud")
ride("race", "race", "Go karting")
simple("yawn", "yawn", "Yawn and stretch")


@scene("lurk", "own", "Peek in from the edge of the screen")
def _lurk():
    st = Stage(seed=2)
    st.stand(160)
    st.run(300)
    st.pet.play("lurk")
    st.settle("lurk")
    st.save("lurk", "W")


# ── Claude Code ───────────────────────────────────────────────────

def working(name, tool, caption):
    @scene(name, "claude", caption)
    def _():
        st = Stage()
        st.stand(900)
        st.pet.claude_event({"event": "UserPromptSubmit", "session": "s"})
        st.pet.claude_event({"event": "PreToolUse", "session": "s", "tool": tool})
        st.run(6500)
        st.save(name, "S")


working("work", "Edit", "Typing while Claude Code writes")
working("work_read", "Read", "Reading glasses while it reads files")
working("work_search", "Grep", "The magnifying glass while it searches")
working("work_web", "WebFetch", "Up on his cloud while it browses the web")


@scene("attention", "claude", "Calling you over when it needs a permission")
def _attention():
    st = Stage()
    st.stand(900)
    st.pet.claude_event({"event": "UserPromptSubmit", "session": "s"})
    st.run(800)
    st.pet.claude_event({"event": "PermissionRequest", "session": "s", "tool": "Bash"})
    st.run(5000)
    st.save("attention", "S")


@scene("celebrate", "claude", "Celebrating when it's done")
def _celebrate():
    st = Stage()
    st.stand(900)
    st.pet.claude_event({"event": "UserPromptSubmit", "session": "s"})
    st.run(600)
    st.pet.sessions["s"]["since"] -= 10 * 60_000            # a good long job
    st.pet.claude_event({"event": "Stop", "session": "s"})
    st.run(4000, until=lambda: st.pet.action == "celebrate")
    st.settle("celebrate", tail=500)
    st.save("celebrate", "S")


# ── Around the desktop ────────────────────────────────────────────

LOW = [800, 1250, 200, 280, 2, 0, 0, "eDP-1", "low"]         # within a hop of the floor
TALL = [800, 1130, 200, 400, 2, 0, 0, "eDP-1", "tall"]       # its side comes down to the floor
HIGH = [800, 1150, 200, 200, 2, 0, 0, "eDP-1", "high"]       # well above the floor
TOWER = [800, 880, 200, 300, 2, 0, 0, "eDP-1", "tower"]      # out of reach
LEFT = [640, 1100, 190, 430, 2, 0, 0, "eDP-1", "left"]       # two with a gap between
RIGHT = [920, 1220, 190, 310, 3, 0, 0, "eDP-1", "right"]


def on_screens(name, action, caption, at, tail=600):
    @scene(name, "desktop", caption)
    def _():
        st = Stage()
        st.stand(at)
        st.run(300)
        st.pet.play(action)
        st.settle(action, tail=tail)
        st.save(name)


on_screens("ladder", "climb", "A ladder up (or down) to the next screen", 1720)
on_screens("leap", "leap", "Or a leap", 1800)


def with_windows(name, action, caption, windows, at, on=None, pin=None, seed=1, keep=True, size=None):
    @scene(name, "desktop", caption)
    def _():
        st = Stage(windows=windows, seed=seed)
        st.stand(at, on=on)
        if keep:
            st.keep += [QRect(w[0], w[1], w[2], min(w[3], 120)) for w in windows]
        st.run(400)
        undo = nudge_random(pin) if pin is not None else None
        st.pet.play(action)
        st.settle(action, ms=90_000)
        if undo:
            undo()
        st.save(name, size)


with_windows("perch", "perch_window", "Hop up onto a window", [LOW], 1060)
with_windows("climb_window", "walk_up_window", "Walk straight up a window's side", [TALL], 1060)
with_windows("climb_rope", "rope_up_window", "Or throw up a rope and climb it", [TALL], 1060)
with_windows("window_jump", "window_jump", "Jump from window to window", [LEFT, RIGHT], 680, on=LEFT, keep=False)
with_windows("umbrella", "hop_down", "Float down off a window under an umbrella", [HIGH], 820, on=HIGH, pin=0.9,
             keep=False)
with_windows("parachute", "hop_down", "From high up: skydive, then the parachute", [HIGH], 820, on=HIGH, pin=0.3,
             keep=False)
with_windows("climb_down", "walk_down_window", "Walk down a window's side, head first", [TALL], 820, on=TALL)
with_windows("rappel", "rappel_down_window", "Or rappel down it", [TALL], 820, on=TALL)
with_windows("ladder_window", "perch_window", "A ladder up to a window out of reach", [TOWER], 1060)


@scene("ride_window", "desktop", "He rides a window you move")
def _ride():
    win = list(LOW)
    st = Stage(windows=[win])
    st.stand(win[0] + 100, on=win)
    st.keep.append(QRect(win[0] - 70, win[1], win[2] + 140, 60))
    st.show_cursor = True
    start = win[0]

    def drag(t):
        x = start + int(70 * math.sin((t - 600) / 700)) if t > 600 else start
        if x != win[0]:
            win[0] = x
            st.pet.windows_changed([list(win)])
        st.pet.cursor = (win[0] + 40, win[1] + 10, st.pet.now)
    st.run(6800, each=drag)
    st.save("ride_window", "M")


ICONS = [("Projects", QRect(160, 1170, 64, 64), True), ("notes.txt", QRect(300, 1170, 64, 64), False)]


@scene("visit", "desktop", "Checking out a desktop icon")
def _visit():
    st = Stage(icons=ICONS[1:], seed=5, frame_ms=60)
    st.stand(440)
    st.keep += [r.adjusted(-20, 0, 20, 20) for _, r, _ in ICONS[1:]]
    st.pet.play("visit")
    st.settle("visit", ms=90_000)
    st.save("visit")


@scene("read", "desktop", "Reading a story from a desktop folder")
def _read():
    st = Stage(icons=ICONS[:1], seed=4, frame_ms=60)
    st.stand(330)
    st.keep += [r.adjusted(-20, 0, 20, 20) for _, r, _ in ICONS[:1]]
    st.pet.play("read")
    st.settle("read", ms=120_000)
    st.save("read")


# ── With you ──────────────────────────────────────────────────────

def mid(st):
    left, right = st.pet.box_span()
    return (left + right) / 2


@scene("watch", "you", "He watches the pointer")
def _watch():
    st = Stage()
    st.pet.prefs["grab"] = False                          # just looking, this time
    st.stand(900)
    st.show_cursor = True
    cx = mid(st)

    def move(t):
        a = t / 1400
        st.pet.cursor_moved(int(cx + 130 * math.sin(a)), int(LAPTOP_FLOOR - 150 - 30 * math.cos(a * 0.7)))
    st.run(7000, each=move)
    st.save("watch", "M")


@scene("petting", "you", "Pet him: hearts, then a dance")
def _petting():
    st = Stage()
    st.stand(900)
    st.show_cursor = True
    cx, y = mid(st), st.pet._mid()

    def pet(t):
        if t < 5200:
            st.pet.cursor_moved(int(cx + 26 * math.sin(t / 95)), int(y - 6))
        else:
            st.pet.cursor_moved(int(cx + 170), int(y - 90))
    st.run(9500, each=pet)
    st.save("petting", "M")


@scene("grab", "you", "He grabs the pointer and swings from it")
def _grab():
    st = Stage(seed=6)
    st.stand(900)
    st.show_cursor = True
    pet = st.pet
    x, y = int(mid(st)), int(pet.y + pet.home_px.y() - 40)
    pet.cursor_moved(x, y)
    pet.play("grab")
    st.run(3000, until=lambda: pet._dangling)
    t0 = st.t

    def sway(t):
        u = (t - t0) / 1000
        pet.cursor_moved(x + int(60 * math.sin(u * 2.2)), y - 20 + int(12 * math.sin(u * 4.4)))
    st.run(5500, each=sway)
    pet.cursor_moved(x, y - 20)
    st.run(1200)
    pet._release = True                                    # and off (shake it, and he lets go)
    st.run(1800)
    st.save("grab", "M")


@scene("spin", "you", "Whirl the pointer and he spins right round")
def _spin():
    st = Stage(seed=6)
    st.stand(900)
    st.show_cursor = True
    pet = st.pet
    x, y = int(mid(st)), int(pet.y + pet.home_px.y() - 40)
    pet.cursor_moved(x, y)
    pet.play("grab")
    st.run(3000, until=lambda: pet._dangling)
    st.run(300)
    t0 = st.t

    def whirl(t):
        a = 2 * math.pi * 1.6 * (t - t0) / 1000
        pet.cursor_moved(int(x + 70 * math.cos(a)), int(y + 70 * math.sin(a)))
    st.run(4000, each=whirl)
    pet.cursor_moved(x, y)                                 # stop: he swings down, dizzy
    st.run(2600)
    pet._release = True
    st.run(2200)
    st.save("spin", "M")


@scene("throw", "you", "Pick him up and throw him")
def _throw():
    st = Stage()
    st.stand(700)
    st.show_cursor = True
    pet = st.pet
    grip = (int(mid(st)), int(pet._mid()))
    pet.cursor_moved(*grip)
    st.run(500)
    pet.dragging = True
    pet.start("held")
    base = (pet.x, pet.y)

    def lift(t):
        u = min(1.0, (t - 500) / 900)
        pet.x, pet.y = base[0] + 60 * u, base[1] - 240 * u * (2 - u)
        pet.cursor = (grip[0] + int(60 * u), grip[1] - int(240 * u * (2 - u)), pet.now)
    st.run(1000, each=lift)
    pet.dragging = False
    pet.drop(240, -260)
    st.run(4200, each=lambda t: setattr(pet, "cursor", (grip[0] + 60, grip[1] - 240, pet.now)))
    st.save("throw", "M")


@scene("drop_folder", "you", "Drop a folder on him for a Claude Code session there")
def _drop():
    st = Stage()
    st.stand(900)
    st.show_cursor = True
    pet = st.pet
    cx, cy = mid(st), pet._mid()
    carry = {"on": True}
    st.extra = lambda p: carry["on"] and draw_icon(p, "", QRect(int(pet.cursor[0]) - 10, int(pet.cursor[1]) + 6, 64, 64), True)

    def move(t):
        u = min(1.0, t / 2200)
        pet.cursor_moved(int(cx + 260 - 260 * u), int(cy - 150 + 130 * u))
        if u >= 1.0 and carry["on"]:
            pet._drag_seen = pet.now
            if not pet._drag_over:
                pet._drag_over = True
                pet._before_drag = pet.frame
                pet._emit("excl", 11, -8, vy=-2, life=1000)
    st.run(3800, each=move)
    carry["on"] = False                                    # dropped: off he goes
    pet._drag_over = False
    pet.start("jump_happy", manual=True)
    for k in range(3):
        pet._emit("spark", 6 + 6 * k, -4, vy=-5, life=700)
    st.run(2200, each=lambda t: pet.cursor_moved(int(cx), int(cy - 20)))
    st.save("drop_folder", "M")


@scene("arrive", "you", "Start him and he drops in by parachute")
def _arrive():
    st = Stage()
    st.stand(900)
    undo = nudge_random(0.3)                        # the parachute (now and then it's the umbrella)
    st.pet.arrive()
    while st.pet._feet() < LAPTOP_FLOOR - 560:      # the top of the dive is out of the picture
        st.pet.advance(STEP_MS)
        st.t += STEP_MS
    st.settle("entrance", ms=20_000, tail=700)
    undo()
    st.save("arrive", max_h=440)


@scene("goodbye", "you", "Quit him and he goes the way that emoji does")
def _goodbye():
    st = Stage()
    st.stand(900)
    st.run(900)
    gone = []
    real, cp.QApplication.quit = cp.QApplication.quit, lambda: gone.append(1)
    try:
        st.pet.leave()
        st.run(8000, until=lambda: gone)
    finally:
        cp.QApplication.quit = real
    st.run(700)                             # ...and he's gone
    st.save("goodbye", "S")


# ── Time and seasons ──────────────────────────────────────────────

@scene("nightcap", "time", "His nightcap all night, and naps")
def _night():
    st = Stage(clock=datetime.datetime(2026, 9, 15, 23, 30), seed=2)
    st.stand(900)
    st.run(1500)
    st.pet.play("sleep")
    st.run(6500)
    st.save("nightcap", "S")


@scene("morning", "time", "Good morning: a stretch and a coffee")
def _morning():
    st = Stage(clock=datetime.datetime(2026, 9, 15, 8, 30))
    st.stand(900)
    st.pet.play("morning")
    st.run(12_000)
    st.save("morning", "S")


@scene("santa", "time", "A Santa hat in December")
def _santa():
    st = Stage(clock=datetime.datetime(2026, 12, 18, 15, 0))
    st.stand(900)
    st.pet.settle_hat()
    st.pet.play("dance")
    st.settle("dance")
    st.save("santa", "S")


@scene("halloween", "time", "A pumpkin and bats for Halloween")
def _halloween():
    st = Stage(clock=datetime.datetime(2026, 10, 29, 16, 0), seed=3)
    st.stand(900)
    st.pet.settle_hat()
    st.run(300)
    st.pet._bats()
    st.run(4000)
    st.pet._bats()
    st.run(3500)
    st.save("halloween", "M")


@scene("new_year", "time", "New Year: party hat, confetti and a dance")
def _new_year():
    st = Stage(clock=datetime.datetime(2027, 1, 1, 0, 0, 20))
    st.stand(900)
    st.pet.settle_hat()
    st.pet.start("new_year")
    st.settle("new_year")
    st.save("new_year", "M")


@scene("birthday", "time", "Your birthday: a party, a cake, balloons")
def _birthday():
    st = Stage(seed=24, frame_ms=60)
    st.pet.prefs["birthday"], st.pet.prefs["celebrate"] = "09-15", True
    st.stand(880)
    st.pet.play("birthday")
    st.settle("birthday", ms=120_000)
    st.save("birthday")


@scene("hats", "time", "Hats change with a little flourish")
def _hats():
    st = Stage()
    st.stand(900)
    for hat in ("santa_hat", "party_hat", "pumpkin_hat", "nightcap", "auto"):
        st.pet.prefs["hat"] = hat
        st.run(1500)
    st.save("hats", "S")


# ── Reminders ─────────────────────────────────────────────────────

def remind(name, kind, caption, after):
    @scene(name, "reminders", caption)
    def _():
        st = Stage()
        st.pet.windows_changed([[0, 330, 1920, 1200, 1, 0, 1, "eDP-1", "you"]])   # you're on the laptop
        st.pet.windows_changed([])
        st.stand(900)
        st.pet.play("remind_" + kind)
        st.show_cursor = True
        pet = st.pet
        rest = (int(mid(st)) + 260, int(pet._mid()) - 160)
        pet.cursor = (*rest, pet.now)
        st.run(6500)
        button = next(q for q in pet.particles if q["kind"].startswith("done_button"))
        target = pet._glyph_rect(button).center() + QPoint(int(pet.x), int(pet.y))
        start = pet.cursor[:2]

        def go(t, t0=st.t):
            u = min(1.0, (t - t0) / 900)
            pet.cursor = (int(start[0] + (target.x() - start[0]) * u), int(start[1] + (target.y() - start[1]) * u), pet.now)
        st.run(1000, each=go)
        pet._button_down = True                            # press...
        st.run(160)
        pet._button_down = False
        pet.confirm_reminder()                             # ...Done
        st.run(after)
        st.save(name, "M")


remind("water", "water", "Water: he brings you a bottle until you press Done", 5000)
remind("break", "break", "A break: then a coffee with you", 6000)


# ── The rest ──────────────────────────────────────────────────────

@scene("startled", "other", "A new wallpaper gives him a fright")
def _startled():
    st = Stage(seed=2)
    st.stand(900)
    st.run(1200)
    st.wall = WALL_NEW
    st.pet.wallpaper_changed()
    st.settle("startled", tail=800)
    st.save("startled", "S")


@scene("settings_scene", "other", "Open his settings and he reads along")
def _settings():
    st = Stage()
    st.stand(900)
    pet = st.pet
    pet._settings_news = []
    pet._settings_open = lambda: st.t < 10_000
    pet.start("settings", manual=True)

    def news(t):
        if t == 4200:
            pet.prefs["activity"] = "lively"
            pet.settings_changed("activity", "normal", "lively")
        if t == 7600:
            pet.prefs["hat"] = "party_hat"
            pet.settings_changed("hat", "auto", "party_hat")
    st.run(13_000, each=news)
    st.save("settings_scene", "S")


def settings_png():
    """The settings dialog, in the Breeze style if it's there."""
    if "Breeze" in QStyleFactory.keys():
        APP.setStyle("Breeze")
    real = cp.hooks_installed
    cp.hooks_installed = lambda path=None: (13, 13)
    try:
        pet = cp.ClawdPet(SPRITES, settings=None)
        pet.timer.stop()
        d = cp.SettingsDialog(pet)
        d.login.setChecked(True)
        d.adjustSize()
        pm = d.grab()
    finally:
        cp.hooks_installed = real
        APP.setStyle("Fusion")
    title = 30
    img = QImage(pm.width() + 2, pm.height() + title + 2, QImage.Format.Format_ARGB32)
    img.fill(QColor("#bdb7aa"))
    p = QPainter(img)
    p.fillRect(QRect(1, 1, pm.width(), title), QColor("#dedad2"))
    f = QFont()
    f.setPixelSize(13)
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#2b2a27"))
    p.drawText(QRect(1, 1, pm.width(), title), Qt.AlignmentFlag.AlignCenter, "Clawd settings")
    p.drawPixmap(1, title + 1, pm)
    p.end()
    os.makedirs(OUT, exist_ok=True)
    img.save(os.path.join(OUT, "settings.png"))


def main(argv):
    if "--list" in argv:
        for name, (section, caption, _) in SCENES.items():
            print(f"{name:16s} {section:10s} {caption}")
        return
    want = [a for a in argv if not a.startswith("-")] or list(SCENES) + ["settings"]
    for name in want:
        if name == "settings":
            settings_png()
            print("settings.png")
            continue
        section, caption, fn = SCENES[name]
        Stage.crop = None
        fn()                                   # where does he go?
        Stage.crop = Stage.crops[name]
        fn()                                   # draw that
        Stage.crop = None
        size = os.path.getsize(os.path.join(OUT, name + ".gif"))
        c = Stage.crops[name]
        print(f"{name}.gif  {c.width()}x{c.height()}  {size // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1:])
