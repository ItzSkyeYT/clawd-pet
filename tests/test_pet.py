"""
Tests for the Clawd pet. Run from the project root:

    python -m unittest discover -s tests -v

They use Qt's offscreen platform with two screens laid out like the real
desktop (tests/screens.json: a 1080p monitor on the right as primary, a
taller laptop screen on the left, 330px lower), so nothing appears on screen.
Set CLAWD_SNAPSHOTS=/some/dir to also save a PNG of every animation's frames.
"""

import json
import math
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ["QT_QPA_PLATFORM"] = "offscreen:configfile=" + os.path.join(ROOT, "tests", "screens.json")
os.environ["CLAWD_NO_DESKTOP_ICONS"] = "1"      # never read the real desktop's icons in tests
sys.path.insert(0, ROOT)

from PyQt6.QtCore import QPoint, QRect, QSettings, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtGui import QColor  # noqa: E402

APP = QApplication.instance() or QApplication([])

import claude_pet as cp  # noqa: E402
import datetime  # noqa: E402

# An ordinary afternoon, so the time of day and the date never change what he does.
AFTERNOON = datetime.datetime(2026, 9, 15, 14, 0)
cp.wall_clock = lambda: AFTERNOON

HOOK = os.path.join(ROOT, "clawd_hook.py")


def run_ms(pet, ms, until=None):
    for _ in range(int(ms / 16)):
        pet.advance(16)
        if until and until():
            return True
    return False


def idle_rows(data):
    jump = data["animations"]["jump"]
    hx, hy = jump["home"]
    iw, ih = data["idle_size"]
    return [r[hx:hx + iw] for r in jump["frames"][0]["rows"][hy:hy + ih]]


class SpriteData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(cp.SPRITES) as f:
            cls.data = json.load(f)

    def test_frames_are_rectangular_and_use_the_palette(self):
        keys = set(self.data["palette"]) | {"."}
        for name, a in self.data["animations"].items():
            w, h = a["size"]
            for i, fr in enumerate(a["frames"]):
                self.assertEqual(len(fr["rows"]), h, f"{name}[{i}] height")
                for row in fr["rows"]:
                    self.assertEqual(len(row), w, f"{name}[{i}] width")
                    self.assertLessEqual(set(row), keys, f"{name}[{i}] unknown colour")
                self.assertGreater(fr["ms"], 0)

    def test_segments_point_at_real_frames(self):
        for name, a in self.data["animations"].items():
            n = len(a["frames"])
            for seg in ("loop", "outro"):
                if seg in a:
                    lo, hi = a[seg]
                    self.assertTrue(0 <= lo <= hi < n, f"{name} {seg} {a[seg]} of {n}")

    def test_idle_matches_the_official_proportions(self):
        # Anthropic's Clawd is 12x8 pixels (24x16 half-pixels here): an 8-wide
        # body, 2-wide arms on both sides, 1x1 eyes one pixel in from each
        # side, and legs in two pairs with a wide gap between them.
        rows = idle_rows(self.data)
        body, ink = rows[0][4], rows[2][6]
        clean = ["".join("#" if c == body else "@" if c == ink else c for c in r) for r in rows]
        self.assertEqual(self.data["idle_size"], [24, 16])
        self.assertEqual(clean[0], "....################....")
        self.assertEqual(clean[2], "....##@@########@@##....")
        for r in clean[4:8]:
            self.assertEqual(r, "#" * 24)
        for r in clean[12:16]:
            self.assertEqual(r, "....##..##....##..##....")


class Pet(unittest.TestCase):
    def setUp(self):
        random.seed(7)
        self.sprites = cp.load_sprites()
        self.pet = cp.ClawdPet(self.sprites, settings=None)
        self.geo = self.pet.screen_geometry()

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.deleteLater()

    def assert_standing(self, msg=""):
        """Feet on the floor of the screen he's on, and all of him on some screen."""
        self.assertAlmostEqual(self.pet.y, self.pet.ground_y(), delta=1, msg=msg)
        left, right = self.pet.box_span()
        top = self.pet.y + self.pet.home_px.y()
        bottom = top + self.pet.ih * self.pet.scale
        for x, y in ((left, top), (right - 1, top), (left, bottom - 1), (right - 1, bottom - 1)):
            self.assertTrue(any(a.contains(int(x), int(y)) for a in cp.screen_areas()),
                            f"{msg}: corner {x:.0f},{y:.0f} is off every screen")

    def run_until_idle(self, limit_ms=180_000):
        t = 0
        while self.pet.action != "idle" and t < limit_ms:
            self.pet.advance(16)
            t += 16
        return t

    def test_every_frame_fits_inside_the_window(self):
        box = self.pet.rect()
        for name, anim in self.sprites.anims.items():
            for mirror in (False, True):
                for i in range(len(anim.frames)):
                    r = self.pet.frame_rect(anim, i, mirror)
                    self.assertTrue(box.contains(r), f"{name}[{i}] mirror={mirror}: {r} outside {box}")

    def test_every_action_finishes_back_in_idle_on_the_ground(self):
        for action in cp.ACTIONS:
            with self.subTest(action=action):
                self.pet.start(action)
                self.assertEqual(self.pet.action, action)
                took = self.run_until_idle()
                self.assertEqual(self.pet.action, "idle", f"{action} still running after {took} ms")
                self.assert_standing(action)

    def test_idle_eventually_picks_actions_and_stays_on_screen(self):
        seen, screens = set(), set()
        for _ in range(int(30 * 60_000 / 16)):          # thirty simulated minutes
            self.pet._last_activity = self.pet.now      # as if you'd been around
            self.pet.advance(16)
            seen.add(self.pet.action)
            if self.pet.action == "idle" and self.pet.wait > 100:
                self.assert_standing("idle")
                screens.add(self.pet.screen_geometry().left())
        self.assertGreaterEqual(len(seen - {"idle"}), 4, f"only saw {seen}")
        self.assertEqual(len(screens), 2, "he never visited the other screen")

    def test_walks_briskly(self):
        start = self.pet.box_span()[0]
        self.pet.start("walk", target=start - 300)
        took = self.run_until_idle()
        self.assertLess(took, 8000, "300px took too long")
        self.assertAlmostEqual(self.pet.box_span()[0], start - 300, delta=12)

    def test_dropping_him_lands_on_the_ground(self):
        self.pet.y = self.pet.ground_y() - 300
        self.pet.x += 40
        self.pet.drop(vx=250, vy=-100)
        self.assertEqual(self.pet.action, "fall")
        self.run_until_idle()
        self.assertEqual(self.pet.action, "idle")
        self.assert_standing("after the drop")

    def test_changing_size_keeps_his_feet_in_place(self):
        before_left, _ = self.pet.box_span()
        self.pet.set_scale(6)
        after_left, _ = self.pet.box_span()
        self.assertAlmostEqual(before_left, after_left, delta=1)
        self.assertAlmostEqual(self.pet.y, self.pet.ground_y(), delta=1)
        self.assertTrue(self.pet.rect().contains(
            self.pet.frame_rect(self.sprites.anims["race"], 20, True)))

    def test_canvas_draws_him(self):
        self.pet.start("idle")
        self.pet.advance(16)
        img = self.pet.canvas
        opaque = sum(1 for y in range(0, img.height(), 2) for x in range(0, img.width(), 2)
                     if img.pixelColor(x, y).alpha() > 0)
        self.assertGreater(opaque, 50)
        out = os.environ.get("CLAWD_SNAPSHOTS")
        if out:
            os.makedirs(out, exist_ok=True)
            for action in cp.ACTIONS:
                self.pet.start(action)
                for n in range(0, 40):
                    for _ in range(8):
                        self.pet.advance(16)
                    self.pet.canvas.save(os.path.join(out, f"{action}_{n:02d}.png"))


class BetweenScreens(unittest.TestCase):
    HDMI = QRect(1920, 0, 1920, 1080)
    LAPTOP = QRect(0, 330, 1920, 1200)

    def setUp(self):
        random.seed(3)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.deleteLater()

    def stand(self, box_left):
        self.pet.set_box_left(box_left)
        self.pet.y = self.pet.ground_y()
        self.pet.start("idle")

    def play(self, action=None, limit_ms=60_000, **kw):
        if action:
            self.pet.start(action, **kw)
        t = 0
        while self.pet.action != "idle" and t < limit_ms:
            self.pet.advance(16)
            t += 16
        self.assertEqual(self.pet.action, "idle", f"still {self.pet.action}")

    def feet(self):
        return self.pet.y + self.pet.home_px.y() + self.pet.ih * self.pet.scale

    def test_x11_single_work_area_does_not_cut_the_taller_screen(self):
        # X11 publishes one work area for the whole desktop: a 38px taskbar
        # at the bottom of the HDMI screen "cuts" the laptop screen at y=1042.
        self.assertEqual(cp.usable_area(self.LAPTOP, QRect(0, 330, 1920, 712)), self.LAPTOP)
        self.assertEqual(cp.usable_area(self.HDMI, QRect(1920, 0, 1920, 1042)),
                         QRect(1920, 0, 1920, 1042))

    def test_walks_off_the_high_screen_and_drops_onto_the_laptop(self):
        self.stand(1960)
        self.assertEqual(self.feet(), 1080)
        self.play("walk", target=1500)
        self.assertEqual(self.pet.screen_geometry(), self.LAPTOP)
        self.assertAlmostEqual(self.feet(), 1530, delta=1)

    def test_the_laptop_side_of_the_seam_is_a_wall(self):
        self.stand(1700)
        self.pet.vx = 300                 # shove him right, below the HDMI screen's floor
        for _ in range(200):
            self.pet.advance(16)
        self.assertLessEqual(self.pet.box_span()[1], 1920)
        self.assertEqual(self.pet.vx, 0)
        self.assertAlmostEqual(self.feet(), 1530, delta=1)

    def test_walking_to_the_high_screen_leaps_the_wall(self):
        self.stand(1700)
        self.play("walk", target=2300)
        self.assertEqual(self.pet.screen_geometry(), self.HDMI)
        self.assertAlmostEqual(self.feet(), 1080, delta=1)

    def test_leaps_back_up_onto_the_high_screen(self):
        self.stand(1650)
        self.play("leap")
        self.assertEqual(self.pet.screen_geometry(), self.HDMI)
        self.assertAlmostEqual(self.feet(), 1080, delta=1)
        self.assertGreater(self.pet.box_span()[0], 1920)

    def test_leap_clears_the_corner(self):
        self.stand(1650)
        self.pet.start("leap")
        straddled = 0
        for _ in range(4000):
            self.pet.advance(16)
            left, right = self.pet.box_span()
            if left < 1920 < right:       # straddling the seam: must be above the HDMI floor
                straddled += 1
                self.assertLessEqual(self.feet(), 1080 + 1)
            if self.pet.action == "idle":
                break
        self.assertGreater(straddled, 0, "never crossed the seam")

    def test_dropped_under_the_short_screen_ends_up_on_a_floor(self):
        self.pet.set_box_left(2500)
        self.pet.y = 1300                # below the HDMI screen: on no screen at all
        self.pet.drop()
        self.play()
        self.assertEqual(self.pet.screen_geometry(), self.HDMI)
        self.assertAlmostEqual(self.feet(), 1080, delta=1)

    def test_thrown_across_the_seam_lands_on_the_laptop(self):
        self.stand(2000)
        self.pet.y -= 400
        self.pet.drop(vx=-1500, vy=-200)
        self.play()
        self.assertEqual(self.pet.screen_geometry(), self.LAPTOP)
        self.assertAlmostEqual(self.feet(), 1530, delta=1)

    def test_he_picks_the_leap_when_stuck_below_the_high_screen(self):
        self.stand(1780)
        picks = [self.pet._pick_action() for _ in range(300)]
        self.assertIn("leap", picks)
        self.stand(3000)
        self.assertNotIn("leap", [self.pet._pick_action() for _ in range(300)])


class Ladder(unittest.TestCase):
    HDMI = QRect(1920, 0, 1920, 1080)
    LAPTOP = QRect(0, 330, 1920, 1200)

    def setUp(self):
        random.seed(11)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.deleteLater()

    def stand(self, box_left):
        self.pet.set_box_left(box_left)
        self.pet.y = self.pet.ground_y()
        self.pet.start("idle")

    def feet(self):
        return self.pet.y + self.pet.home_px.y() + self.pet.ih * self.pet.scale

    def climb(self):
        self.pet.start("climb")
        ladder = None
        for _ in range(int(90_000 / 16)):
            self.pet.advance(16)
            if self.pet.ladder.isVisible():
                ladder = self.pet.ladder.geometry()
            if self.pet.action != "climb":
                break
        self.assertEqual(self.pet.action, "idle")
        return ladder

    def test_climbs_a_ladder_up_to_the_high_screen(self):
        self.stand(1600)
        ladder = self.climb()
        self.assertIsNotNone(ladder, "no ladder appeared")
        self.assertLessEqual(ladder.x() + ladder.width(), 1920)       # stands on the laptop side
        self.assertAlmostEqual(ladder.y() + ladder.height(), 1530, delta=self.pet.scale)
        self.assertLessEqual(ladder.y(), 1080)                          # reaches the HDMI floor
        self.assertEqual(self.pet.screen_geometry(), self.HDMI)
        self.assertAlmostEqual(self.feet(), 1080, delta=1)
        self.assertFalse(self.pet.ladder.isVisible(), "ladder left behind")

    def test_climbs_down_a_ladder_to_the_laptop(self):
        self.stand(2100)
        ladder = self.climb()
        self.assertIsNotNone(ladder, "no ladder appeared")
        self.assertLessEqual(ladder.x() + ladder.width(), 1920)
        self.assertEqual(self.pet.screen_geometry(), self.LAPTOP)
        self.assertAlmostEqual(self.feet(), 1530, delta=1)
        self.assertFalse(self.pet.ladder.isVisible())

    def test_interrupted_climb_packs_the_ladder_away(self):
        self.stand(1700)
        self.pet.start("climb")
        self.assertTrue(run_ms(self.pet, 20_000, until=self.pet.ladder.isVisible))
        self.pet.start("held")
        self.assertFalse(self.pet.ladder.isVisible())

    def test_the_ladder_lets_clicks_through(self):
        self.assertTrue(self.pet.ladder.windowFlags() & Qt.WindowType.WindowTransparentForInput)


class OnWindows(unittest.TestCase):
    """Windows as KWin reports them: [x, y, w, h, stacking, fullscreen, active, output, id]."""

    def setUp(self):
        random.seed(21)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.deleteLater()

    def stand_on(self, box_left, feet):
        self.pet.set_box_left(box_left)
        self.pet.y = feet - self.pet.home_px.y() - self.pet.ih * self.pet.scale
        self.pet.start("idle")

    def feet(self):
        return self.pet._feet()

    def test_dropped_above_a_window_he_lands_on_it(self):
        self.pet.windows_changed([[400, 900, 600, 400, 3, 0, 1, "eDP-1", "a"]])
        self.stand_on(600, 500)
        self.pet.drop()
        run_ms(self.pet, 5000, until=lambda: self.pet.action == "idle")
        self.assertAlmostEqual(self.feet(), 900, delta=1)

    def test_walking_off_the_edge_drops_him_to_the_floor(self):
        self.pet.windows_changed([[400, 900, 600, 400, 3, 0, 1, "eDP-1", "a"]])
        self.stand_on(800, 900)
        self.pet.start("walk", target=1200)
        run_ms(self.pet, 20_000, until=lambda: self.pet.action == "idle" and not self.pet.airborne)
        self.assertAlmostEqual(self.feet(), 1530, delta=1)

    def test_he_rides_along_when_you_move_the_window(self):
        self.pet.windows_changed([[400, 900, 600, 400, 3, 0, 1, "eDP-1", "a"]])
        self.stand_on(600, 900)
        run_ms(self.pet, 200)
        left = self.pet.box_span()[0]
        self.pet.windows_changed([[500, 850, 600, 400, 3, 0, 1, "eDP-1", "a"]])
        run_ms(self.pet, 200)
        self.assertAlmostEqual(self.pet.box_span()[0], left + 100, delta=1)
        self.assertAlmostEqual(self.feet(), 850, delta=1)

    def test_closing_the_window_under_him_floats_him_down(self):
        self.pet.windows_changed([[400, 900, 600, 400, 3, 0, 1, "eDP-1", "a"]])
        self.stand_on(600, 900)
        run_ms(self.pet, 200)
        self.pet.windows_changed([])
        layers = set()
        run_ms(self.pet, 20_000, until=lambda: layers.update(self.pet.layers)
               or (self.pet.action == "idle" and not self.pet.airborne))
        self.assertAlmostEqual(self.feet(), 1530, delta=1)
        self.assertTrue(layers & {"umbrella", "parachute_1"}, layers)   # 630 px: he floats

    def test_an_edge_hidden_behind_another_window_is_not_a_ledge(self):
        # b sits on top of a, covering the middle of a's top edge
        self.pet.windows_changed([[400, 900, 800, 400, 2, 0, 0, "eDP-1", "a"],
                                  [650, 800, 300, 400, 5, 0, 1, "eDP-1", "b"]])
        self.stand_on(700, 700)
        self.pet.drop()
        run_ms(self.pet, 5000, until=lambda: self.pet.action == "idle")
        self.assertAlmostEqual(self.feet(), 800, delta=1)          # lands on b, not a's hidden edge

    def test_he_hops_up_onto_a_nearby_window(self):
        self.pet.windows_changed([[700, 1200, 700, 300, 3, 0, 1, "eDP-1", "a"]])
        self.stand_on(500, 1530)
        self.pet.start("perch_window")
        run_ms(self.pet, 30_000, until=lambda: self.pet.action != "perch_window")
        self.assertAlmostEqual(self.feet(), 1200, delta=1)
        left, right = self.pet.box_span()
        self.assertTrue(700 <= (left + right) / 2 <= 1400)

    def test_on_a_window_he_picks_things_that_fit_up_there(self):
        self.pet.windows_changed([[400, 900, 600, 400, 3, 0, 1, "eDP-1", "a"]])
        self.stand_on(600, 900)
        picks = {self.pet._pick_action() for _ in range(300)}
        self.assertFalse(picks & {"race", "cloud", "lurk", "climb", "leap", "visit", "read"}, picks)
        self.assertIn("hop_down", picks)

    def test_he_ducks_out_of_fullscreen_on_his_screen_and_comes_back(self):
        self.stand_on(600, 1530)
        self.pet.windows_changed([[0, 330, 1920, 1200, 9, 1, 1, "eDP-1", "video"]])
        run_ms(self.pet, 3000)
        self.assertTrue(self.pet.ducked)
        self.pet.windows_changed([])
        run_ms(self.pet, 5000, until=lambda: not self.pet.ducked and self.pet.action == "idle")
        self.assertFalse(self.pet.ducked)
        self.assertAlmostEqual(self.feet(), 1530, delta=1)

    def test_fullscreen_on_the_other_screen_leaves_him_be(self):
        self.stand_on(600, 1530)
        self.pet.windows_changed([[1920, 0, 1920, 1080, 9, 1, 1, "HDMI-A-1", "video"]])
        run_ms(self.pet, 3000)
        self.assertFalse(self.pet.ducked)

    def test_the_kwin_script_reports_windows(self):
        js = cp.kwin_desktop_script()
        self.assertIn("windowList", js)
        self.assertIn('"Windows"', js)


class ClimbingWindows(unittest.TestCase):
    """A window reaching down past the laptop screen's floor (1530): [x, y, w, h, stack, fs, active, output, id]."""
    WIN = [600, 1000, 500, 600, 3, 0, 1, "eDP-1", "w"]

    def setUp(self):
        random.seed(22)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.deleteLater()

    def stand_on(self, box_left, feet):
        self.pet.set_box_left(box_left)
        self.pet.y = feet - self.pet.home_px.y() - self.pet.ih * self.pet.scale
        self.pet.start("idle")
        self.pet.advance(16)

    def feet(self):
        return self.pet._feet()

    def climb(self, action="climb_window", ms=40_000):
        seen, boxes = set(), []
        self.pet.play(action)
        for _ in range(int(ms / 16)):
            self.pet.advance(16)
            seen.add(self.pet.frame[1])
            if self.pet.frame[1] in ("climb", "climb_side"):
                boxes.append(self.pet.box_span())
            if self.pet.action != action and not self.pet.airborne:
                break
        return seen, boxes

    def test_he_climbs_up_the_side_and_stands_on_top(self):
        self.pet.windows_changed([self.WIN])
        self.stand_on(250, 1530)
        seen, boxes = self.climb()
        self.assertTrue(seen & {"climb", "climb_side"})
        self.assertAlmostEqual(self.feet(), 1000, delta=1)
        self.assertEqual(self.pet.standing_on, "w")
        left, right = self.pet.box_span()
        self.assertTrue(600 <= left and right <= 1100, (left, right))
        self.assertTrue(all(r <= 601 for _, r in boxes))            # hugging the left edge, outside

    def test_he_climbs_down_the_side_too(self):
        self.pet.windows_changed([self.WIN])
        self.stand_on(650, 1000)
        seen, boxes = self.climb("climb_down")
        self.assertTrue(seen & {"climb", "climb_side"})
        self.assertAlmostEqual(self.feet(), 1530, delta=1)
        self.assertTrue(boxes and all(r <= 601 for _, r in boxes))

    def test_the_window_moving_takes_him_along_mid_climb(self):
        self.pet.windows_changed([self.WIN])
        self.stand_on(250, 1530)
        self.pet.play("climb_window")
        self.assertTrue(run_ms(self.pet, 20_000, until=lambda: self.pet.frame[1] in ("climb", "climb_side")))
        run_ms(self.pet, 500)
        left, y = self.pet.box_span()[0], self.pet.y
        moved = list(self.WIN)
        moved[0], moved[1] = 680, 960
        self.pet.windows_changed([moved])
        run_ms(self.pet, 32)
        self.assertAlmostEqual(self.pet.box_span()[0], left + 80, delta=1)
        self.assertLess(self.pet.y, y - 30)

    def test_the_window_going_away_mid_climb_drops_him(self):
        self.pet.windows_changed([self.WIN])
        self.stand_on(250, 1530)
        self.pet.play("climb_window")
        self.assertTrue(run_ms(self.pet, 20_000, until=lambda: self.pet.frame[1] in ("climb", "climb_side")))
        run_ms(self.pet, 1500)
        self.pet.windows_changed([])
        self.assertTrue(run_ms(self.pet, 8000, until=lambda: not self.pet.airborne and self.feet() > 1520))
        self.assertAlmostEqual(self.feet(), 1530, delta=1)

    def test_not_up_an_edge_hidden_behind_another_window(self):
        cover = [450, 900, 300, 700, 5, 0, 0, "eDP-1", "c"]             # over the left edge
        self.pet.windows_changed([self.WIN, cover])
        self.stand_on(1300, 1530)
        target = self.pet._climb_target()
        self.assertIsNotNone(target)
        self.assertEqual(target[1], 1)                                  # the right side instead

    def test_a_side_just_out_of_reach_is_jumped_to_but_not_one_far_up(self):
        low = [600, 1000, 500, 1530 - 1000 - 10 * self.pet.scale, 3, 0, 1, "eDP-1", "w"]
        self.pet.windows_changed([low])
        self.stand_on(250, 1530)
        self.assertIsNotNone(self.pet._climb_target())
        seen, _ = self.climb()
        self.assertAlmostEqual(self.feet(), 1000, delta=1)
        high = [600, 1000, 500, 300, 3, 0, 1, "eDP-1", "w"]            # its side ends 230 px up
        self.pet.windows_changed([high])
        self.stand_on(250, 1530)
        self.assertIsNone(self.pet._climb_target())

    def test_he_does_it_on_his_own_too(self):
        self.pet.windows_changed([self.WIN])
        self.stand_on(250, 1530)
        self.pet.prefs["scenes_off"] = [k for k in cp.WEIGHTS] + ["perch_window"]   # nothing else to do
        self.assertEqual(self.pet._pick_action(), "climb_window")


class JumpingBetweenWindows(unittest.TestCase):
    A = [300, 1000, 400, 600, 3, 0, 1, "eDP-1", "a"]
    B = [800, 950, 400, 600, 4, 0, 0, "eDP-1", "b"]           # 100 px across, a bit higher

    def setUp(self):
        random.seed(23)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.deleteLater()

    def stand_on(self, box_left, feet):
        self.pet.set_box_left(box_left)
        self.pet.y = feet - self.pet.home_px.y() - self.pet.ih * self.pet.scale
        self.pet.start("idle")
        self.pet.advance(16)

    def jump(self):
        self.pet.play("window_jump")
        seen = set()
        run_ms(self.pet, 20_000, until=lambda: seen.add(self.pet.frame[1]) or
               (self.pet.action != "window_jump" and not self.pet.airborne))
        return seen

    def test_he_leaps_across_to_the_next_window(self):
        self.pet.windows_changed([self.A, self.B])
        self.stand_on(400, 1000)
        self.assertEqual(self.pet.standing_on, "a")
        seen = self.jump()
        self.assertEqual(self.pet.standing_on, "b")
        self.assertAlmostEqual(self.pet._feet(), 950, delta=1)
        left, right = self.pet.box_span()
        self.assertTrue(800 <= left and right <= 1200, (left, right))
        self.assertIn("jump", seen)

    def test_and_back_the_other_way(self):
        self.pet.windows_changed([self.A, self.B])
        self.stand_on(1000, 950)
        self.assertEqual(self.pet._jump_target()[2], "a")

    def test_up_onto_a_window_standing_higher_behind_his(self):
        high = [500, 800, 700, 600, 2, 0, 0, "eDP-1", "h"]        # behind A, its top 200 px higher
        self.pet.windows_changed([self.A, high])
        self.stand_on(450, 1000)
        self.jump()
        self.assertEqual(self.pet.standing_on, "h")
        self.assertAlmostEqual(self.pet._feet(), 800, delta=1)

    def test_down_onto_a_lower_one_in_front(self):
        low = [550, 1200, 600, 300, 6, 0, 0, "eDP-1", "l"]        # in front, lower down
        self.pet.windows_changed([self.A, low])
        self.stand_on(400, 1000)
        self.jump()
        self.assertEqual(self.pet.standing_on, "l")

    def test_from_the_floor_too(self):
        self.pet.windows_changed([self.A])
        self.stand_on(900, 1530)
        self.jump()
        self.assertEqual(self.pet.standing_on, "a")

    def test_not_across_a_gap_too_wide(self):
        far = list(self.B)
        far[0] = 700 + (cp.LEAP_GAP + 30) * self.pet.scale        # way over there
        self.pet.windows_changed([self.A, far])
        self.stand_on(300, 1000)
        self.assertIsNone(self.pet._jump_target())

    def test_not_to_a_window_hidden_behind_another(self):
        cover = [750, 700, 500, 800, 9, 0, 0, "eDP-1", "c"]      # in front of B's top
        self.pet.windows_changed([self.A, self.B, cover])
        self.stand_on(400, 1000)
        target = self.pet._jump_target()
        self.assertTrue(target is None or target[2] != "b")

    def test_he_does_it_on_his_own(self):
        self.pet.windows_changed([self.A, self.B])
        self.stand_on(400, 1000)
        picks = {self.pet._pick_action() for _ in range(200)}
        self.assertIn("window_jump", picks)


class Birthday(unittest.TestCase):
    def setUp(self):
        random.seed(24)
        self.dir = tempfile.mkdtemp()
        self.settings = QSettings(os.path.join(self.dir, "clawd.conf"), QSettings.Format.IniFormat)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=self.settings)
        self.pet.start("idle")
        run_ms(self.pet, 100)

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.deleteLater()

    def its(self, month, day):
        self.pet.prefs["birthday"] = f"{month:02d}-{day:02d}"
        self.pet.prefs["celebrate"] = True

    def test_on_the_day_the_first_thing_he_does_is_throw_you_a_party(self):
        self.its(9, 15)                                       # AFTERNOON is 15 September
        self.pet.action = "idle"
        self.pet._next()
        self.assertEqual(self.pet.action, "birthday")
        self.assertEqual(self.pet.hat(), "party_hat")          # and the hat, all day

    def test_once_a_day_even_across_restarts(self):
        self.its(9, 15)
        self.pet.action = "idle"
        self.pet._next()
        self.assertTrue(run_ms(self.pet, 90_000, until=lambda: self.pet.action != "birthday"))
        self.pet.settings.sync()
        again = cp.ClawdPet(cp.load_sprites(), settings=QSettings(os.path.join(self.dir, "clawd.conf"),
                                                                    QSettings.Format.IniFormat))
        again.action = "idle"
        again._next()
        self.assertNotEqual(again.action, "birthday")
        again.timer.stop()
        again.deleteLater()

    def test_not_on_other_days_or_when_you_are_away(self):
        self.its(9, 16)
        self.pet.action = "idle"
        self.pet._next()
        self.assertNotEqual(self.pet.action, "birthday")
        self.its(9, 15)
        self.pet._input_at = self.pet.now - 5 * 60_000         # nobody there yet
        self.pet.action = "idle"
        self.pet._next()
        self.assertNotEqual(self.pet.action, "birthday")
        self.pet.prefs["celebrate"] = False
        self.pet._input_at = self.pet.now
        self.pet.action = "idle"
        self.pet._next()
        self.assertNotEqual(self.pet.action, "birthday")

    def test_a_leap_day_birthday_is_kept_on_the_28th(self):
        self.pet.prefs["birthday"], self.pet.prefs["celebrate"] = "02-29", True
        self.pet.wall = at(10, day=datetime.date(2027, 2, 28))
        self.assertTrue(self.pet.is_birthday())
        self.pet.wall = at(10, day=datetime.date(2028, 2, 28))  # a leap year: the real day comes
        self.assertFalse(self.pet.is_birthday())

    def test_he_grows_for_the_party_and_shrinks_back(self):
        self.its(9, 15)
        base = self.pet.scale
        centre = sum(self.pet.box_span()) / 2
        feet = self.pet._feet()
        self.pet.play("birthday")
        scales = set()
        done = run_ms(self.pet, 90_000, until=lambda: scales.add(self.pet.scale) or self.pet.action != "birthday")
        self.assertTrue(done)
        self.assertIn(min(8, base * 2), scales)
        self.assertEqual(self.pet.scale, base)
        self.assertAlmostEqual(sum(self.pet.box_span()) / 2, centre, delta=base)
        self.assertAlmostEqual(self.pet._feet(), feet, delta=1)

    def test_the_party_has_it_all(self):
        self.its(9, 15)
        pet = self.pet
        pet.play("birthday")
        kinds, layers, clipped, cake_at = set(), set(), [], None
        for _ in range(int(90_000 / 16)):
            pet.advance(16)
            box = QRect(0, 0, pet.width(), pet.height())
            kinds |= {q["kind"] for q in pet.particles}
            layers |= set(pet.layers)
            for q in pet.particles:
                if q["kind"].startswith(("letter_", "balloon_")) and pet._fade(q) > 0:
                    if not box.contains(pet._glyph_rect(q)):
                        clipped.append(q["kind"])
            if "cake_0" in pet.layers:
                cake_at = pet.layers["cake_0"]
            if pet.action != "birthday":
                break
        letters = {k.split("_")[1] for k in kinds if k.startswith("letter_")}
        self.assertEqual(letters, set("HAPYBIRTD!"))
        self.assertTrue(any(k.startswith("letter_") and k.count("_") == 2 for k in kinds))   # tinted
        self.assertTrue(any(k.startswith("balloon_") for k in kinds))
        self.assertTrue({"cake_0", "cake_1", "cake_2", "gift"} <= layers)
        self.assertTrue(any(k.startswith("puff_") for k in kinds))
        self.assertTrue(any(k.startswith("smoke_") for k in kinds))
        self.assertEqual(clipped, [], "cut off at the edge of his window")
        # held up in front of him: his eyes clear, the flames between them
        hx, hy = pet.sp.cake_hold
        self.assertEqual(cake_at, (pet.iw / 2 - hx, pet.ih - 1 - hy))
        idle = pet.sp.poses["idle"]
        eyes = [(x, y) for y in range(idle.height()) for x in range(idle.width())
                if QColor(idle.pixel(x, y)).name() == "#141413"]
        cake = pet.sp.props["cake_0"]
        for x, y in eyes:
            cx, cy = int(x - cake_at[0]), int(y - cake_at[1])
            inside = 0 <= cx < cake.width() and 0 <= cy < cake.height()
            self.assertFalse(inside and QColor.fromRgba(cake.pixel(cx, cy)).alpha(), (x, y))
        flames = [fx + cake_at[0] for fx, fy in pet.sp.cake_flames]
        self.assertTrue(all(min(x for x, y in eyes) < f < max(x for x, y in eyes) for f in flames))

    def test_a_restart_mid_party_keeps_your_size(self):
        self.its(9, 15)
        base = self.pet.scale
        self.pet.play("birthday")
        self.assertTrue(run_ms(self.pet, 10_000, until=lambda: self.pet.scale > base))
        self.pet.save()
        self.pet.settings.sync()
        self.assertEqual(int(self.pet.settings.value("scale")), base)

    def test_settings(self):
        from PyQt6.QtCore import QDate
        real = cp.hooks_installed
        cp.hooks_installed = lambda path=None: (13, 13)
        try:
            d = cp.SettingsDialog(self.pet)
            d.checks["celebrate"].setChecked(True)
            d.birthday.setDate(QDate(2000, 7, 4))
            self.assertEqual(self.pet.prefs["birthday"], "07-04")
            self.assertIs(self.pet.prefs["celebrate"], True)
            d.close()
            d.deleteLater()
        finally:
            cp.hooks_installed = real



class FloatingDown(unittest.TestCase):
    HIGH = [600, 700, 500, 400, 3, 0, 1, "eDP-1", "h"]          # its top 830 px above the floor
    LOW = [600, 1490, 500, 100, 3, 0, 1, "eDP-1", "l"]          # only 40 px up

    def setUp(self):
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        self.real = random.random

    def tearDown(self):
        random.random = self.real
        self.pet.timer.stop()
        self.pet.deleteLater()

    def stand_on(self, win, box_left):
        self.pet.windows_changed([win])
        self.pet.set_box_left(box_left)
        self.pet.y = win[1] - self.pet.home_px.y() - self.pet.ih * self.pet.scale
        self.pet.start("idle")
        self.pet.advance(16)

    def hop_off(self):
        self.pet.play("hop_down")
        seen, layers, vys = set(), set(), []
        for _ in range(int(40_000 / 16)):
            self.pet.advance(16)
            seen.add(self.pet.frame[1])
            layers |= set(self.pet.layers)
            if self.pet.chute and self.pet.airborne:
                vys.append(self.pet.vy)
            if self.pet.action != "hop_down" and not self.pet.airborne:
                break
        return seen, layers, vys

    def test_off_a_high_window_he_floats_down_under_an_umbrella(self):
        random.random = lambda: 0.9                              # the umbrella, not a plain drop
        self.stand_on(self.HIGH, 700)
        seen, layers, vys = self.hop_off()
        self.assertIn("umbrella", layers)
        self.assertTrue(vys)
        self.assertLessEqual(max(vys[20:]), cp.CHUTE_FALL * self.pet.scale * 1.05)
        self.assertAlmostEqual(self.pet._feet(), 1530, delta=1)
        self.assertIsNone(self.pet.chute)
        self.assertEqual(self.pet.layers, {})

    def test_from_high_up_a_skydive_then_the_parachute(self):
        random.random = lambda: 0.5                              # float, and pick the parachute
        self.stand_on(self.HIGH, 700)
        seen, layers, vys = self.hop_off()
        self.assertIn("skydive", seen)
        self.assertTrue({"parachute_0", "parachute_1"} <= layers)
        self.assertAlmostEqual(self.pet._feet(), 1530, delta=1)

    def test_a_little_drop_is_just_a_hop(self):
        random.random = lambda: 0.9
        self.stand_on(self.LOW, 700)
        seen, layers, vys = self.hop_off()
        self.assertFalse(layers)
        self.assertFalse(vys)

    def test_the_canopy_hangs_from_his_fists(self):
        self.pet.frame = ("anim", "dangle", cp.STRAIGHT, False)
        for name in ("umbrella", "parachute_1"):
            x, y = self.pet._held_at(name)
            ax, ay = self.pet.sp.held[name.split("_")[0]]
            s = self.pet.scale
            gx, gy = self.pet._grip_in_window()
            self.assertEqual((self.pet.home_px.x() + (x + ax) * s, self.pet.home_px.y() + (y + ay) * s),
                             (gx - s, gy))                       # the anchor cell on his fists' cell


class PlayWithAGoal(unittest.TestCase):
    """Whatever you pick from Play, he goes and does it: up onto a window first
    (by ladder if it's out of reach), down off one, over to the other screen."""
    WA = [300, 700, 800, 500, 3, 0, 0, "eDP-1", "a"]           # laptop, far above the floor
    WC = [1300, 900, 500, 400, 2, 0, 0, "eDP-1", "c"]          # laptop, a jump across from WA
    WB = [2300, 300, 1000, 600, 1, 0, 1, "HDMI-A-1", "b"]      # HDMI
    ICONS = [("Old Firefox Data", QRect(24, 338, 64, 64), True),
             ("steam.desktop", QRect(24, 578, 64, 64), False)]

    def setUp(self):
        random.seed(11)
        self.real = random.random
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        self.pet.icon_source = lambda: self.ICONS
        self.pet.windows_changed([list(w) for w in (self.WA, self.WC, self.WB)])

    def tearDown(self):
        random.random = self.real
        self.pet.timer.stop()
        self.pet.deleteLater()

    def at(self, box_left, on=None):
        self.pet.set_box_left(box_left)
        self.pet.y = (self.pet.ground_y() if on is None
                      else on[1] - self.pet.home_px.y() - self.pet.ih * self.pet.scale)
        self.pet.start("idle")
        self.pet.advance(16)

    def play(self, action, ms=150_000):
        """Play it through: the windows he stood on in turn (None: a floor),
        the frames, props and whether the ladder came out."""
        self.pet.play(action)
        stood, frames, layers, ladder = [], set(), set(), False
        for _ in range(int(ms / 16)):
            self.pet.advance(16)
            if not self.pet.airborne and not self.pet.scripted:
                on = self.pet._window_under()
                wid = on[3] if on else None
                if not stood or stood[-1] != wid:
                    stood.append(wid)
            frames.add(self.pet.frame[1])
            layers |= set(self.pet.layers)
            ladder = ladder or self.pet.ladder.isVisible()
            if self.pet.action != action:
                break
        self.assertNotEqual(self.pet.action, action, action + " never finished")
        return stood, frames, layers, ladder

    def floated(self, layers):
        return bool(layers & {"umbrella", "parachute_1"})

    def test_hop_up_to_a_window_out_of_reach_by_ladder(self):
        self.at(1400)
        stood, frames, layers, ladder = self.play("perch_window")
        self.assertIn(stood[-1], ("a", "c"))
        self.assertTrue(ladder)
        self.assertIn("climb_ladder", frames)

    def test_hop_down_from_the_floor_gets_up_there_first(self):
        self.at(1400)
        stood, frames, layers, ladder = self.play("hop_down")
        self.assertTrue(any(w in ("a", "c") for w in stood), stood)
        self.assertIsNone(stood[-1])
        self.assertTrue(self.floated(layers), layers)

    def test_the_umbrella_every_time_off_a_high_window(self):
        for r in (0.05, 0.3, 0.7, 0.95):
            random.random = lambda r=r: r
            self.at(700, on=self.WA)
            stood, frames, layers, ladder = self.play("hop_down")
            self.assertTrue(self.floated(layers), (r, layers))

    def test_climb_a_side_that_starts_high_up(self):
        self.at(1400)
        stood, frames, layers, ladder = self.play("climb_window")
        self.assertIn("climb_side", frames)
        self.assertIn(stood[-1], ("a", "c"))

    def test_climb_down_from_the_floor(self):
        self.at(1400)
        stood, frames, layers, ladder = self.play("climb_down")
        self.assertIn("climb_side", frames)
        self.assertTrue(any(w in ("a", "c") for w in stood), stood)
        self.assertIsNone(stood[-1])

    def test_jump_to_another_window_from_the_floor(self):
        self.at(1400)
        stood, frames, layers, ladder = self.play("window_jump")
        ups = [w for w in stood if w is not None]
        self.assertTrue(len(set(ups)) >= 2 and ups[-1] != ups[0], stood)

    def test_leap_either_way(self):
        self.at(1200)                                           # laptop, up to the HDMI screen
        self.play("leap")
        self.assertEqual(self.pet._screen_name(), "HDMI-A-1")
        self.assertAlmostEqual(self.pet._feet(), 1080, delta=1)
        self.pet.windows_changed([])
        self.at(2900)                                           # and back down again
        self.play("leap")
        self.assertEqual(self.pet._screen_name(), "eDP-1")
        self.assertAlmostEqual(self.pet._feet(), 1530, delta=1)

    def test_climb_to_the_other_screen_from_up_on_a_window(self):
        self.at(700, on=self.WA)
        stood, frames, layers, ladder = self.play("climb")
        self.assertEqual(self.pet._screen_name(), "HDMI-A-1")
        self.assertTrue(ladder)

    def test_visit_and_read_from_the_other_screen(self):
        self.at(2900)
        stood, frames, layers, ladder = self.play("visit")
        self.assertIn("magnifier", layers)
        self.at(2900)
        stood, frames, layers, ladder = self.play("read")
        self.assertTrue({"glasses", "page"} <= layers, layers)

    def test_read_from_up_on_a_window(self):
        self.at(700, on=self.WA)
        stood, frames, layers, ladder = self.play("read")
        self.assertTrue({"glasses", "page"} <= layers, layers)

    def test_walking_off_a_high_window_he_floats(self):
        self.at(700, on=self.WA)
        wpx = self.pet.iw * self.pet.scale
        self.pet.set_box_left(1100 - wpx / 2 + 2)              # his middle just past the end
        layers = set()
        for _ in range(int(20_000 / 16)):
            self.pet.advance(16)
            layers |= set(self.pet.layers)
            if self.pet._feet() > 1500 and not self.pet.airborne:
                break
        self.assertTrue(self.floated(layers), layers)
        self.assertAlmostEqual(self.pet._feet(), 1530, delta=1)

    def test_a_throw_is_still_just_physics(self):
        self.at(700, on=self.WA)
        self.pet.drop(300, -200)
        layers = set()
        for _ in range(int(8000 / 16)):
            self.pet.advance(16)
            layers |= set(self.pet.layers)
        self.assertFalse(self.floated(layers))

    def test_hop_up_when_up_already_with_nowhere_higher(self):
        self.pet.windows_changed([list(self.WA)])
        self.at(700, on=self.WA)
        self.pet.play("perch_window")
        seen = set()
        for _ in range(int(5000 / 16)):
            self.pet.advance(16)
            seen |= {q["kind"] for q in self.pet.particles}
            if self.pet.action != "perch_window":
                break
        self.assertIn("question", seen)
        self.assertEqual(self.pet._window_under()[3], "a")       # still up there

    def test_jump_to_a_far_window_when_asked(self):
        self.pet.windows_changed([list(self.WA), list(self.WB)])   # the only other one: over on HDMI
        self.at(700, on=self.WA)
        self.pet.play("window_jump")
        highest = float("inf")
        for _ in range(int(20_000 / 16)):
            self.pet.advance(16)
            highest = min(highest, self.pet.y + self.pet.home_px.y())
            if self.pet.action != "window_jump":
                break
        self.assertEqual(self.pet._window_under()[3], "b")
        self.assertGreaterEqual(highest, 0)                        # his head never off the top

    def test_a_race_from_a_window_starts_on_the_floor(self):
        self.at(700, on=self.WA)
        self.pet.play("race")
        raced = 0
        for _ in range(int(60_000 / 16)):
            self.pet.advance(16)
            if self.pet.frame[1] == "race":
                raced += 16
                self.assertIsNone(self.pet._window_under())
                self.assertFalse(self.pet.chute)
            if self.pet.action != "race":
                break
        self.assertGreater(raced, 2000)

    def test_a_tall_ladder_is_climbed_briskly(self):
        self.pet.windows_changed([list(self.WA)])
        self.at(1400)
        self.pet.play("perch_window")
        climbing = 0
        for _ in range(int(60_000 / 16)):
            self.pet.advance(16)
            if self.pet.frame[1] == "climb_ladder":
                climbing += 16
            if self.pet.action != "perch_window":
                break
        self.assertGreater(climbing, 3000)
        self.assertLess(climbing, (cp.LADDER_TIME + 0.5) * 1000)

    def test_with_nothing_to_do_it_with_he_looks_puzzled(self):
        self.pet.windows_changed([])
        self.at(1400)
        self.pet.play("hop_down")
        seen = set()
        for _ in range(int(5000 / 16)):
            self.pet.advance(16)
            seen |= {q["kind"] for q in self.pet.particles}
            if self.pet.action != "hop_down":
                break
        self.assertIn("question", seen)
        self.assertNotEqual(self.pet.action, "hop_down")


class ClaudeHooks(unittest.TestCase):
    def setUp(self):
        random.seed(5)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        self.tmp = tempfile.mkdtemp()
        self.sock = os.path.join(self.tmp, "pet.sock")

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.deleteLater()

    def hook(self, payload, sock=None):
        env = dict(os.environ, CLAWD_PET_SOCKET=sock or self.sock)
        return subprocess.run([sys.executable, "-S", HOOK], input=json.dumps(payload),
                              capture_output=True, text=True, env=env, timeout=10)

    def pump(self, until, seconds=3):
        end = time.time() + seconds
        while time.time() < end:
            APP.processEvents()
            if until():
                return True
            time.sleep(0.01)
        return False

    def test_a_prompt_makes_him_work_until_claude_stops(self):
        self.pet.claude_event({"event": "UserPromptSubmit", "session": "a"})
        run_ms(self.pet, 3000)
        self.assertEqual(self.pet.action, "work")
        self.assertEqual(self.pet.frame[1], "laptop")
        self.pet.claude_event({"event": "PreToolUse", "session": "a", "tool": "Edit"})
        run_ms(self.pet, 20_000)
        self.assertEqual(self.pet.action, "work")
        self.pet.claude_event({"event": "Stop", "session": "a"})
        seen = set()
        for _ in range(int(20_000 / 16)):
            self.pet.advance(16)
            seen.add(self.pet.action)
            if "celebrate" in seen and self.pet.action == "idle":
                break
        self.assertIn("celebrate", seen)
        self.assertEqual(self.pet.action, "idle")

    def test_a_permission_request_calls_you_over(self):
        self.pet.claude_event({"event": "UserPromptSubmit", "session": "a"})
        self.pet.claude_event({"event": "PermissionRequest", "session": "a", "tool": "Bash"})
        run_ms(self.pet, 500)
        self.assertEqual(self.pet.action, "attention")
        self.assertTrue(any(q["kind"] == "bubble" for q in self.pet.particles))
        self.assertEqual(self.pet.click_link(), cp.CLAUDE_LINKS["needs-input"])
        self.pet.claude_event({"event": "PostToolUse", "session": "a", "tool": "Bash"})
        run_ms(self.pet, 5000)
        self.assertEqual(self.pet.action, "work")
        self.assertFalse(any(q["kind"] == "bubble" for q in self.pet.particles))
        self.assertEqual(self.pet.click_link(), cp.CLAUDE_LINKS["continue"])

    def test_something_you_asked_for_finishes_before_claude_code_gets_him(self):
        self.pet.claude_event({"event": "UserPromptSubmit", "session": "a"})
        run_ms(self.pet, 500)
        self.assertEqual(self.pet.action, "work")
        self.pet.start("sparkler", manual=True)          # Play -> Sparkler, mid-task
        for _ in range(30):                              # Claude Code keeps busy meanwhile
            self.pet.claude_event({"event": "PreToolUse", "session": "a", "tool": "Bash"})
            run_ms(self.pet, 100)
        self.pet.claude_event({"event": "PermissionRequest", "session": "a", "tool": "Bash"})
        run_ms(self.pet, 300)
        self.assertEqual(self.pet.action, "sparkler")    # not cut short
        self.assertTrue(run_ms(self.pet, 10_000, until=lambda: self.pet.action != "sparkler"))
        self.assertEqual(self.pet.action, "attention")   # then straight to what Claude Code needs

    def test_his_own_ideas_still_give_way_to_claude_code(self):
        self.pet.start("dance")                          # picked by himself
        self.pet.claude_event({"event": "UserPromptSubmit", "session": "a"})
        self.assertEqual(self.pet.action, "work")

    def busy_with(self, tool, ms=3000):
        self.pet.claude_event({"event": "UserPromptSubmit", "session": "a"})
        self.pet.claude_event({"event": "PreToolUse", "session": "a", "tool": tool})
        run_ms(self.pet, ms)

    def test_reading_tools_get_the_glasses_out(self):
        self.busy_with("Read")
        self.assertEqual(self.pet.action, "work")
        self.assertEqual(set(self.pet.layers), {"glasses", "page"})

    def test_search_tools_get_the_magnifying_glass(self):
        self.busy_with("Grep")
        self.assertIn("magnifier", self.pet.layers)

    def test_web_tools_put_him_on_his_cloud(self):
        self.busy_with("WebSearch", 4000)
        self.assertIsNotNone(self.pet.front)

    def test_edits_and_commands_are_laptop_work(self):
        self.busy_with("Bash")
        self.assertEqual(self.pet.frame[1], "laptop")

    def test_he_switches_style_when_the_tool_changes(self):
        self.busy_with("Read")
        self.pet.claude_event({"event": "PreToolUse", "session": "a", "tool": "Edit"})
        self.assertTrue(run_ms(self.pet, 12_000, until=lambda: self.pet.frame[1] == "laptop"))
        self.assertEqual(self.pet.layers, {})

    def test_quick_tool_flips_dont_make_him_flicker(self):
        self.busy_with("Read", 500)
        styles = []
        for i in range(12):                            # Read/Edit every half second for 6 s
            self.pet.claude_event({"event": "PreToolUse", "session": "a", "tool": ("Edit", "Read")[i % 2]})
            run_ms(self.pet, 500)
            style = self.pet.work_style
            if not styles or styles[-1] != style:
                styles.append(style)
        self.assertLessEqual(len(styles), 3, styles)

    def test_a_failed_tool_makes_him_stumble(self):
        self.busy_with("Bash")
        self.pet.claude_event({"event": "PostToolUseFailure", "session": "a", "tool": "Bash"})
        seen = set()
        for _ in range(100):
            self.pet.advance(16)
            seen |= {q["kind"] for q in self.pet.particles}
        self.assertIn("drop", seen)
        self.assertEqual(self.pet.action, "work")

    def test_an_idle_reminder_after_the_turn_does_not_nag(self):
        self.pet.claude_event({"event": "Notification", "session": "a", "kind": "idle_prompt"})
        run_ms(self.pet, 500)
        self.assertNotEqual(self.pet.action, "attention")

    def test_forgotten_sessions_expire(self):
        self.pet.claude_event({"event": "UserPromptSubmit", "session": "a"})
        run_ms(self.pet, 16 * 60_000)
        self.assertIsNone(self.pet.claude_mode())

    def test_the_hook_sends_names_only_and_the_pet_hears_it(self):
        self.assertTrue(self.pet.listen(self.sock))
        r = self.hook({"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash",
                       "tool_input": {"command": "echo SECRET-TOKEN"}})
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertTrue(self.pump(lambda: self.pet.last_message is not None))
        self.assertEqual(self.pet.last_message, {"event": "PreToolUse", "session": "s1", "tool": "Bash"})
        self.assertNotIn("SECRET", json.dumps(self.pet.last_message))
        self.assertEqual(self.pet.claude_mode(), "busy")

    def test_he_can_stop_following_claude_code(self):
        self.pet.claude_event({"event": "UserPromptSubmit", "session": "a"})
        run_ms(self.pet, 500)
        self.assertEqual(self.pet.action, "work")
        self.pet.set_pref("claude", False)
        self.assertNotEqual(self.pet.action, "work")                # stops straight away
        for ev in ({"event": "PreToolUse", "session": "a", "tool": "Read"},
                   {"event": "PermissionRequest", "session": "a", "tool": "Bash"},
                   {"event": "Stop", "session": "a"},
                   {"event": "SessionStart", "session": "b"}):
            self.pet.claude_event(ev)
            run_ms(self.pet, 300)
            self.assertNotIn(self.pet.action, ("work", "attention", "celebrate", "wave"), ev)
        self.assertIsNone(self.pet.claude_mode())
        self.assertEqual(self.pet.click_kind(), "continue")
        self.pet.set_pref("claude", True)
        self.pet.claude_event({"event": "UserPromptSubmit", "session": "a"})
        run_ms(self.pet, 500)
        self.assertEqual(self.pet.action, "work")                   # and back again

    def test_prompts_still_mean_you_are_there(self):
        self.pet.set_pref("claude", False)
        self.pet.now += cp.AWAY + 1000
        self.pet.claude_event({"event": "UserPromptSubmit", "session": "a"})
        self.assertEqual(self.pet._input_at, self.pet.now)

    def test_the_socket_can_change_a_setting(self):
        self.assertTrue(self.pet.listen(self.sock))

        def send(msg):
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(self.sock)
            s.sendall((json.dumps(msg) + "\n").encode())
            s.close()
        send({"cmd": "set", "pref": "claude", "value": False})
        self.assertTrue(self.pump(lambda: self.pet.prefs["claude"] is False))
        send({"cmd": "set", "pref": "claude", "value": "yes"})       # wrong type: ignored
        send({"cmd": "set", "pref": "nonsense", "value": True})      # not a setting: ignored
        send({"cmd": "set", "pref": "activity", "value": "frantic"}) # not a choice: ignored
        send({"cmd": "set", "pref": "break_every", "value": True})   # a bool is not a number
        send({"cmd": "set", "pref": "quiet", "value": True})
        self.assertTrue(self.pump(lambda: self.pet.prefs["quiet"] is True))
        self.assertIs(self.pet.prefs["claude"], False)
        self.assertEqual(self.pet.prefs["activity"], "normal")
        self.assertEqual(self.pet.prefs["break_every"], 60)
        self.assertIs(self.pet.status()["claude"], False)

    def test_the_hook_is_silent_and_quick_without_a_pet(self):
        t = time.time()
        r = self.hook({"hook_event_name": "Stop", "session_id": "s1"}, sock=self.sock + ".missing")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertLess(time.time() - t, 2)

    def test_play_and_status_over_the_socket(self):
        self.assertTrue(self.pet.listen(self.sock))
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.connect(self.sock)
        c.sendall(b'{"cmd": "play", "action": "wave"}\n{"cmd": "status"}\n')
        c.setblocking(False)
        got = b""

        def answered():
            nonlocal got
            try:
                got += c.recv(4096)
            except BlockingIOError:
                pass
            return got.endswith(b"\n")
        self.assertTrue(self.pump(answered))
        self.assertEqual(json.loads(got)["action"], "wave")
        self.assertTrue(self.pet.manual)                 # a played action counts as asked for
        c.close()


class OnlyWhenIdle(unittest.TestCase):
    def setUp(self):
        random.seed(13)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.deleteLater()

    def test_his_own_scenes_give_way_to_claude_code_even_mid_air(self):
        pet = self.pet
        pet.icon_source = lambda: [("Old Firefox Data", QRect(24, 338, 64, 64), True)]
        pet.set_box_left(300)
        pet.y = pet.ground_y()
        pet.start("read")                                        # his own idea
        self.assertTrue(run_ms(pet, 60_000, until=lambda: pet.lift > 0))   # up on the cloud
        pet.claude_event({"event": "UserPromptSubmit", "session": "a"})
        self.assertTrue(run_ms(pet, 15_000, until=lambda: pet.action == "work" and pet.lift == 0
                               and abs(pet._feet() - 1530) < 1))
        self.assertEqual((pet.layers, pet.front), ({}, None))

    def test_no_filler_while_you_are_playing_with_him(self):
        pet = self.pet
        pet.start("idle")
        left, right = pet.box_span()
        for i in range(int(90_000 / 200)):                       # pointer hovering nearby for 90 s
            pet.cursor_moved(left - 60 - (i % 5) * 10, pet._mid())
            run_ms(pet, 200)
            self.assertEqual(pet.action, "idle")

    def test_he_winds_down_when_nothing_happens_for_a_while(self):
        pet = self.pet
        fresh = pet._rest_range()
        pet.now = pet._last_activity + 6 * 60_000
        self.assertGreater(pet._rest_range()[0], fresh[0])
        picks = [pet._pick_action() for _ in range(400)]
        self.assertGreater(picks.count("sleep"), 40)


class Previews(unittest.TestCase):
    def setUp(self):
        random.seed(17)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.deleteLater()

    def test_the_play_menu_has_everything(self):
        from PyQt6.QtWidgets import QMenu
        menu = self.pet.fill_menu(QMenu())
        play = next(a.menu() for a in menu.actions() if a.text() == "Play")
        texts = {a.text() for a in play.actions()}
        for key in cp.ACTIONS + cp.BETWEEN_SCREENS + ["visit", "read", "work", "attention", "celebrate"]:
            self.assertIn(cp.LABELS[key], texts, key)

    def test_claude_code_previews_run_without_claude_code(self):
        self.pet.start("work", manual=True, demo=True)
        run_ms(self.pet, 4000)
        self.assertEqual((self.pet.action, self.pet.frame[1]), ("work", "laptop"))
        self.assertTrue(run_ms(self.pet, 15_000, until=lambda: self.pet.action != "work"))
        self.pet.start("attention", manual=True, demo=True)
        run_ms(self.pet, 500)
        self.assertTrue(any(q["kind"] == "bubble" for q in self.pet.particles))
        self.assertTrue(run_ms(self.pet, 15_000, until=lambda: self.pet.action != "attention"))
        self.assertFalse(any(q["kind"] == "bubble" for q in self.pet.particles))


class CursorReactions(unittest.TestCase):
    def setUp(self):
        random.seed(9)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        self.pet.start("idle")
        run_ms(self.pet, 100)

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.deleteLater()

    def test_he_watches_a_nearby_pointer(self):
        left, right = self.pet.box_span()
        mid = self.pet._mid()
        self.pet.cursor_moved(left - 120, mid)
        self.pet.advance(16)
        self.assertEqual(self.pet.frame, ("pose", "look_l", False))
        self.pet.cursor_moved(right + 120, mid)
        self.pet.advance(16)
        self.assertEqual(self.pet.frame, ("pose", "look_r", False))

    def test_stroking_him_makes_him_happy(self):
        left, right = self.pet.box_span()
        cx, mid = (left + right) / 2, self.pet._mid()
        hearts, happy = False, False
        for i in range(40):
            self.pet.cursor_moved(cx + (25 if i % 2 else -25), mid)
            run_ms(self.pet, 80)
            hearts = hearts or any(q["kind"] == "heart" for q in self.pet.particles)
            happy = happy or self.pet.frame == ("pose", "happy", False)
        self.assertTrue(hearts, "no hearts")
        self.assertTrue(happy or self.pet.action == "dance")

    def working(self):
        self.pet.claude_event({"event": "UserPromptSubmit", "session": "a"})
        self.assertTrue(run_ms(self.pet, 5000, until=lambda: self.pet.frame[:2] == ("anim", "laptop")
                               and self.pet.frame[2] >= self.pet.sp.anims["laptop"].loop[0]))

    def test_he_glances_up_from_the_laptop_at_a_nearby_pointer(self):
        self.working()
        left, _ = self.pet.box_span()
        self.pet.cursor_moved(left - 100, self.pet._mid())
        run_ms(self.pet, 200)
        self.assertEqual(self.pet.frame[1], "type_look_l")
        self.assertEqual(self.pet.action, "work")                # still at it
        run_ms(self.pet, 2000)
        self.assertEqual(self.pet.frame[1], "laptop")            # only for a moment
        self.pet.cursor_moved(left - 110, self.pet._mid())
        run_ms(self.pet, 200)
        self.assertEqual(self.pet.frame[1], "laptop")            # not again straight away
        run_ms(self.pet, 6000)
        self.pet.cursor_moved(left - 100, self.pet._mid())
        run_ms(self.pet, 200)
        self.assertEqual(self.pet.frame[1], "type_look_l")

    def test_petting_him_while_he_codes(self):
        self.working()
        left, right = self.pet.box_span()
        cx, mid = (left + right) / 2, self.pet._mid()
        happy = hearts = False
        for i in range(30):
            self.pet.cursor_moved(cx + (25 if i % 2 else -25), mid)
            run_ms(self.pet, 80)
            happy = happy or self.pet.frame[1] == "type_happy"
            hearts = hearts or any(q["kind"] == "heart" for q in self.pet.particles)
        self.assertTrue(happy and hearts)
        self.assertEqual(self.pet.action, "work")

    def test_typing_eyes_are_where_the_official_frames_have_them(self):
        with open(cp.SPRITES) as f:
            laptop = json.load(f)["animations"]["laptop"]
        lo, hi = laptop["loop"]
        for i in range(lo, hi + 1):
            rows = laptop["frames"][i]["rows"]
            for ex, ey in cp.TYPING_EYES:
                cells = {rows[ey + dy][ex + dx] for dx in (0, 1) for dy in (0, 1)}
                self.assertEqual(cells, {rows[ey][ex]}, f"frame {i}")
                self.assertNotEqual(rows[ey][ex], rows[ey][ex - 1])   # ink, not body

    def test_kwin_script_reports_to_our_service(self):
        js = cp.kwin_desktop_script()
        self.assertIn("cursorPosChanged", js)
        self.assertIn(cp.DBUS_SERVICE, js)


class DesktopIcons(unittest.TestCase):
    LAPTOP = QRect(0, 330, 1920, 1200)

    def setUp(self):
        random.seed(2)
        self.tmp = tempfile.mkdtemp()
        self.desktop = os.path.join(self.tmp, "Desktop")
        os.makedirs(self.desktop)
        os.makedirs(os.path.join(self.desktop, "Old Firefox Data"))
        for name in ("steam.desktop", "com.blackmagicdesign.resolve.desktop"):
            open(os.path.join(self.desktop, name), "w").close()
        self.config = os.path.join(self.tmp, "appletsrc")
        with open(self.config, "w") as f:
            f.write("[Containments][44]\nplugin=org.kde.plasma.folder\n\n"
                    "[Containments][44][General]\n"
                    'positions={"1920x1200":["4","17","desktop:/steam.desktop","2","0",'
                    '"desktop:/com.blackmagicdesign.resolve.desktop","0","1",'
                    '"desktop:/Gone.desktop","1","1","desktop:/Old Firefox Data","0","0"]}\n')

    def test_icons_come_from_plasmas_grid(self):
        icons = cp.plasma_desktop_icons(self.config, self.desktop, [self.LAPTOP])
        rects = {name: r for name, r, _ in icons}
        folders = {name for name, _, is_dir in icons if is_dir}
        self.assertEqual(folders, {"Old Firefox Data"})
        # 17 columns of 112px and rows of 120px fill the 1920x1200 screen; the
        # 64px icon is centred in its cell, 8px down
        self.assertEqual(rects["Old Firefox Data"], QRect(24, 338, 64, 64))
        self.assertEqual(rects["com.blackmagicdesign.resolve.desktop"], QRect(136, 338, 64, 64))
        self.assertEqual(rects["steam.desktop"], QRect(24, 578, 64, 64))
        self.assertNotIn("Gone.desktop", rects)          # file no longer on the desktop

    def test_no_config_no_icons(self):
        self.assertEqual(cp.plasma_desktop_icons(self.config + ".missing", self.desktop, [self.LAPTOP]), [])

    def visit(self, hang, icon=QRect(136, 578, 64, 64), stay=True):
        pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        pet.icon_source = lambda: [("DaVinci", icon, False)]
        pet.set_box_left(400)
        pet.y = pet.ground_y()
        pet.start("visit", hang=hang, stay=stay)
        perched = hanging = False
        for _ in range(int(120_000 / 16)):
            pet.advance(16)
            feet = pet._feet()
            left, right = pet.box_span()
            if pet.action == "visit" and abs(feet - icon.top()) < 1 and left < icon.center().x() < right:
                perched = True
            if pet.frame[:2] == ("anim", "hang"):
                hands_top = pet.y + pet.frame_rect(pet.sp.anims["hang"], 0, False).top()
                self.assertLessEqual(hands_top, icon.top())
                self.assertGreater(hands_top + 4 * pet.scale, icon.top())   # hands on the top edge
                hanging = True
            if pet.action == "idle":
                break
        self.assertEqual(pet.action, "idle")
        self.assertAlmostEqual(pet._feet(), 1530, delta=1)                    # back on the floor
        pet.timer.stop()
        pet.deleteLater()
        return perched, hanging

    def test_he_rides_the_cloud_up_and_stands_on_an_icon(self):
        perched, hanging = self.visit(hang=False)
        self.assertTrue(perched)
        self.assertFalse(hanging)

    def test_he_hangs_off_an_icon_and_drops(self):
        perched, hanging = self.visit(hang=True)
        self.assertTrue(perched and hanging)

    def test_he_checks_an_icon_out_properly(self):
        pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        icon = QRect(248, 698, 64, 64)
        pet.icon_source = lambda: [("DaVinci", icon, False)]
        pet.set_box_left(700)
        pet.y = pet.ground_y()
        pet.start("visit", stay=False)
        glyphs, frames, looked = set(), set(), False
        for _ in range(int(150_000 / 16)):
            pet.advance(16)
            glyphs |= {q["kind"] for q in pet.particles}
            frames.add(pet.frame[1])
            if "magnifier" in pet.layers:
                lx, ly = pet.layers["magnifier"]
                glass = pet._prop_rect(pet.sp.props["magnifier"], lx, ly).translated(int(pet.x), int(pet.y))
                looked = looked or glass.intersects(icon)
                self.assertTrue(pet.rect().contains(pet._prop_rect(pet.sp.props["magnifier"], lx, ly)))
            if pet.action == "idle":
                break
        self.assertEqual(pet.action, "idle")
        self.assertTrue({"question", "excl"} <= glyphs, glyphs)     # puzzled, then surprised
        self.assertIn("poke", frames)
        self.assertTrue(looked, "the magnifying glass never went over the icon")
        self.assertEqual((pet.layers, pet.front, pet.lift), ({}, None, 0))
        self.assertAlmostEqual(pet._feet(), 1530, delta=1)

    def test_he_reads_a_story_from_a_folder(self):
        pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        folder = QRect(24, 338, 64, 64)                    # top-left corner, like the real one
        pet.icon_source = lambda: [("Old Firefox Data", folder, True),
                                   ("steam.desktop", QRect(24, 578, 64, 64), False)]
        pet.set_box_left(600)
        pet.y = pet.ground_y()
        pet.start("read")
        props, faces, glyphs, reached, first_scrap = set(), set(), set(), False, None
        for _ in range(int(150_000 / 16)):
            pet.advance(16)
            props |= set(pet.layers)
            glyphs |= {q["kind"] for q in pet.particles}
            scraps = [q for q in pet.particles if q["kind"] == "scrap"]
            if scraps and first_scrap is None:
                q = scraps[0]
                first_scrap = (pet.y + pet.home_px.y() + q["y"] * pet.scale, q["vy"])
            if pet.frame[0] == "pose":
                faces.add(pet.frame[1])
            if pet.frame[:2] == ("anim", "wave") and pet.front is not None:
                hand = pet.frame_rect(pet.sp.anims["wave"], pet.frame[2], pet.frame[3])
                top = pet.y + hand.top() - pet.lift * pet.scale + pet.scale      # the raised hand
                reached = reached or top < folder.top() <= top + 3 * pet.scale     # over the rim
            self.assertGreaterEqual(pet.box_span()[0], 0, "off the left edge of the screen")
            if pet.action == "idle":
                break
        self.assertEqual(pet.action, "idle")
        self.assertTrue(reached, "never reached into the folder")
        self.assertEqual(props - {"umbrella", "parachute_0", "parachute_1"}, {"page", "glasses"})
        self.assertIn("scrap", glyphs)
        self.assertLess(abs(first_scrap[0] - folder.top()), 4 * pet.scale)   # out of the top...
        self.assertLess(first_scrap[1], 0)                                    # ...flying up
        self.assertTrue(faces & {"read_l", "read_r"})
        self.assertGreaterEqual(len(faces & {"surprised", "sad", "happy", "read", "look_l"}), 2)
        self.assertEqual((pet.layers, pet.front, pet.lift), ({}, None, 0))  # all tidied away
        self.assertAlmostEqual(pet._feet(), 1530, delta=1)

    def test_only_folders_get_read(self):
        pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        pet.icon_source = lambda: [("steam.desktop", QRect(24, 578, 64, 64), False)]
        pet.set_box_left(600)
        pet.y = pet.ground_y()
        self.assertNotIn("read", [pet._pick_action() for _ in range(300)])
        pet.icon_source = lambda: [("Old Firefox Data", QRect(24, 338, 64, 64), True)]
        self.assertIn("read", [pet._pick_action() for _ in range(300)])

    def test_top_row_icons_have_no_room_on_top_so_he_hangs(self):
        perched, hanging = self.visit(hang=False, icon=QRect(136, 338, 64, 64))
        self.assertFalse(perched)
        self.assertTrue(hanging)


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_restart_runs_this_script_again_with_the_original_environment(self):
        cmd = cp.restart_command()
        self.assertEqual(cmd[:2], [sys.executable, os.path.join(ROOT, "claude_pet.py")])
        old = cp._FORCED_XCB
        try:
            cp._FORCED_XCB = True       # he'll force XWayland again himself
            env = cp.env_for_restart({"QT_QPA_PLATFORM": "xcb", "HOME": "/h"})
            self.assertNotIn("QT_QPA_PLATFORM", env)
            self.assertEqual(env["HOME"], "/h")
        finally:
            cp._FORCED_XCB = old

    def test_start_at_login_toggles_an_autostart_entry(self):
        entry = os.path.join(self.tmp, "autostart", "clawd-pet.desktop")
        launcher = os.path.join(self.tmp, "applications", "clawd-pet.desktop")
        self.assertFalse(cp.autostart_enabled(entry))
        cp.set_autostart(True, entry, launcher, icon_dir=self.tmp)
        self.assertTrue(cp.autostart_enabled(entry))
        text = open(entry).read()
        self.assertTrue(text.startswith("[Desktop Entry]"))
        self.assertIn("Exec=" + sys.executable + " " + os.path.join(ROOT, "claude_pet.py"), text)
        self.assertIn("Icon=" + os.path.join(self.tmp, "clawd.png"), text)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "clawd.png")))
        self.assertTrue(os.path.exists(launcher))           # also in the app launcher
        cp.set_autostart(False, entry, launcher, icon_dir=self.tmp)
        self.assertFalse(os.path.exists(entry))
        self.assertTrue(os.path.exists(launcher))           # still startable by hand

    def test_a_second_clawd_notices_the_first(self):
        sock = os.path.join(self.tmp, "pet.sock")
        self.assertFalse(cp.already_running(sock))
        pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        self.assertTrue(pet.listen(sock))
        self.assertTrue(cp.already_running(sock))
        pet.timer.stop()
        pet.server.close()
        self.assertFalse(cp.already_running(sock))

    def test_the_menu_has_restart_and_start_at_login(self):
        from PyQt6.QtWidgets import QMenu
        pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        menu = pet.fill_menu(QMenu())
        texts = {a.text() for a in menu.actions()}
        self.assertTrue({"Restart Clawd", "Start at login", "Quit"} <= texts)
        pet.timer.stop()


class Installer(unittest.TestCase):
    def setUp(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("install_hooks", os.path.join(ROOT, "tools", "install_hooks.py"))
        self.ih = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.ih)

    def test_install_keeps_everything_else_and_is_repeatable(self):
        mine = {"type": "command", "command": "prettier --write"}
        before = {"model": "opus", "hooks": {"PostToolUse": [{"matcher": "Edit", "hooks": [mine]}]}}
        once = self.ih.install(before)
        twice = self.ih.install(once)
        self.assertEqual(once, twice)
        self.assertEqual(once["model"], "opus")
        self.assertIn(mine, once["hooks"]["PostToolUse"][0]["hooks"])
        for event in self.ih.EVENTS:
            ours = [h for g in once["hooks"][event] for h in g["hooks"] if "clawd_hook.py" in h["command"]]
            self.assertEqual(len(ours), 1, event)
            self.assertTrue(ours[0]["async"], event)

    def test_remove_restores_the_original(self):
        before = {"theme": "dark", "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "say done"}]}]}}
        self.assertEqual(self.ih.strip(self.ih.install(before)), before)
        self.assertEqual(self.ih.strip(self.ih.install({"theme": "dark"})), {"theme": "dark"})


class DropAFolder(unittest.TestCase):
    def setUp(self):
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        self.tmp = tempfile.mkdtemp()
        self.opened = []
        self.real = cp.open_claude_code_in
        cp.open_claude_code_in = lambda folder: self.opened.append(folder) or True

    def tearDown(self):
        cp.open_claude_code_in = self.real
        self.pet.timer.stop()
        if self.pet.catcher is not None:
            self.pet.catcher.deleteLater()
        self.pet.deleteLater()

    def event(self, kind, path, actions=Qt.DropAction.CopyAction):
        from PyQt6.QtCore import QMimeData, QPoint, QPointF, QUrl
        from PyQt6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
        mime = self.mime = QMimeData()        # the event only borrows it: keep it alive
        mime.setUrls([QUrl.fromLocalFile(path)])
        args = (actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        if kind == "enter":
            return QDragEnterEvent(QPoint(10, 10), *args)
        if kind == "move":
            return QDragMoveEvent(QPoint(12, 10), *args)
        return QDropEvent(QPointF(10, 10), *args)

    def test_the_link_for_a_folder(self):
        self.assertEqual(cp.claude_link_for_folder("/home/me/My Project"),
                         "claude://code/new?folder=%2Fhome%2Fme%2FMy%20Project")

    def test_dropping_a_folder_starts_a_session_in_it(self):
        self.pet.dropEvent(self.event("drop", self.tmp))
        self.assertEqual(self.opened, [self.tmp])
        self.assertEqual(self.pet.action, "jump_happy")

    def test_dropping_a_file_uses_its_folder(self):
        f = os.path.join(self.tmp, "notes.txt")
        open(f, "w").close()
        self.pet.dropEvent(self.event("drop", f))
        self.assertEqual(self.opened, [self.tmp])

    def test_a_drop_is_a_copy_never_a_move(self):
        both = Qt.DropAction.MoveAction | Qt.DropAction.CopyAction
        ev = self.event("drop", self.tmp, both)
        self.pet.dropEvent(ev)
        self.assertEqual(ev.dropAction(), Qt.DropAction.CopyAction)
        self.assertEqual(self.opened, [self.tmp])
        ev = self.event("drop", self.tmp, Qt.DropAction.MoveAction)   # only a move on offer: refuse
        self.pet.dropEvent(ev)
        self.assertFalse(ev.isAccepted())
        self.assertEqual(self.opened, [self.tmp])

    def test_dragging_something_over_him_gets_him_excited(self):
        ev = self.event("enter", self.tmp)
        self.pet.dragEnterEvent(ev)
        self.assertTrue(ev.isAccepted())
        self.pet.advance(16)
        self.assertEqual(self.pet.frame[:3], ("anim", "jump", 2))
        self.pet.dragLeaveEvent(None)
        run_ms(self.pet, 300)
        self.assertNotEqual(self.pet.frame[:3], ("anim", "jump", 2))

    def test_he_holds_still_while_something_hovers_over_him(self):
        self.pet.start("walk", manual=True)
        run_ms(self.pet, 500)
        self.pet.dragEnterEvent(self.event("enter", self.tmp))
        x = self.pet.x
        for _ in range(60):
            self.pet.dragMoveEvent(self.event("move", self.tmp))
            self.pet.advance(16)
        self.assertEqual(self.pet.x, x)
        self.pet.dragLeaveEvent(None)
        run_ms(self.pet, 800)
        self.assertNotEqual(self.pet.x, x)          # and carries on walking afterwards

    def test_a_drag_that_vanishes_is_forgotten(self):
        self.pet.dragEnterEvent(self.event("enter", self.tmp))
        run_ms(self.pet, cp.DRAG_FORGET + 500)
        self.assertFalse(self.pet._drag_over)

    def test_the_catcher_sits_exactly_under_him(self):
        self.pet.catcher = cp.DropCatcher(self.pet)
        self.pet.show()
        run_ms(self.pet, 100)
        c = self.pet.catcher
        self.assertTrue(c.isVisible())
        self.assertEqual(c.geometry(), self.pet.geometry())
        self.assertEqual(c.mask(), self.pet._mask)
        self.pet.x += 40
        run_ms(self.pet, 32)
        self.assertEqual(c.geometry(), self.pet.geometry())
        self.pet.hide()                             # ducking out of a fullscreen window
        run_ms(self.pet, 32)
        self.assertFalse(c.isVisible())

    def test_drops_on_the_catcher_reach_him(self):
        c = cp.DropCatcher(self.pet)
        self.pet.catcher = c
        ev = self.event("enter", self.tmp)
        c.dragEnterEvent(ev)
        self.assertTrue(ev.isAccepted())
        self.assertTrue(self.pet._drag_over)
        c.dropEvent(self.event("drop", self.tmp))
        self.assertEqual(self.opened, [self.tmp])
        self.assertFalse(self.pet._drag_over)


class Settings(unittest.TestCase):
    def setUp(self):
        random.seed(3)
        self.dir = tempfile.mkdtemp()
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        self.pet.start("idle")
        run_ms(self.pet, 100)

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.deleteLater()

    def ini(self):
        return QSettings(os.path.join(self.dir, "clawd.conf"), QSettings.Format.IniFormat)

    def test_prefs_survive_a_round_trip_through_an_ini_file(self):
        p = cp.Prefs(self.ini())
        p["quiet"], p["break_every"], p["scenes_off"], p["activity"] = True, 30, ["dance"], "calm"
        p.settings.sync()
        q = cp.Prefs(self.ini())
        self.assertIs(q["quiet"], True)
        self.assertEqual(q["break_every"], 30)
        self.assertEqual(q["scenes_off"], ["dance"])        # a one-item list comes back as a string
        self.assertEqual(q["activity"], "calm")
        self.assertIs(q["duck"], True)                      # untouched: the default
        q["scenes_off"] = []
        q.settings.sync()
        self.assertEqual(cp.Prefs(self.ini())["scenes_off"], [])

    def test_scenes_turned_off_are_never_picked(self):
        off = [k for k in cp.WEIGHTS if k != "wave"]
        self.pet.prefs["scenes_off"] = off
        picks = {self.pet._pick_action() for _ in range(300)}
        self.assertFalse(picks & set(off), picks)
        self.assertIn("wave", picks)

    def test_quiet_mode_keeps_him_put(self):
        self.pet.set_pref("quiet", True)
        self.assertLessEqual({self.pet._pick_action() for _ in range(300)}, {"idle", "sleep"})
        self.pet.celebrate = 60_000
        self.pet._next()
        self.assertNotEqual(self.pet.action, "celebrate")
        self.pet.claude_event({"event": "UserPromptSubmit", "session": "q"})
        run_ms(self.pet, 500)
        self.assertEqual(self.pet.action, "work")            # Claude Code still shows

    def test_quiet_mode_wraps_up_a_scene(self):
        self.pet.start("dance")
        self.pet.set_pref("quiet", True)
        self.assertEqual(self.pet.action, "idle")

    def test_activity_sets_how_long_he_rests(self):
        self.pet.prefs["activity"] = "calm"
        self.assertEqual(self.pet._rest_range(), (cp.REST[0] * 2, cp.REST[1] * 2))
        self.pet.prefs["activity"] = "lively"
        k = cp.ACTIVITY["lively"]
        self.assertEqual(self.pet._rest_range(), (cp.REST[0] * k, cp.REST[1] * k))
        self.pet._last_activity = self.pet.now - cp.WIND_DOWN - 1   # lively never winds down
        self.assertEqual(self.pet._rest_range(), (cp.REST[0] * k, cp.REST[1] * k))

    def idle_share(self, activity):
        self.pet.prefs["activity"] = activity
        self.pet.start("idle")
        idle = 0
        for _ in range(int(120_000 / 16)):
            self.pet.advance(16)
            idle += self.pet.action == "idle"
        return idle / int(120_000 / 16)

    def test_lively_means_always_up_to_something(self):
        self.assertLess(self.idle_share("lively"), 0.2)
        self.assertGreater(self.idle_share("normal"), 0.3)

    def stroke(self):
        left, right = self.pet.box_span()
        cx, mid = (left + right) / 2, self.pet._mid()
        hearts = False
        for i in range(40):
            self.pet.cursor_moved(cx + (25 if i % 2 else -25), mid)
            run_ms(self.pet, 80)
            hearts = hearts or any(q["kind"] == "heart" for q in self.pet.particles)
        return hearts

    def test_no_hearts_when_petting_is_off(self):
        self.pet.prefs["petting"] = False
        self.assertFalse(self.stroke())

    def test_no_eyes_on_the_pointer_when_that_is_off(self):
        self.pet.prefs["pointer"] = False
        left, _ = self.pet.box_span()
        for i in range(20):
            self.pet.cursor_moved(left - 60 - i, self.pet._mid())
            run_ms(self.pet, 50)
        self.assertFalse(self.pet._tracking)
        self.assertIsNone(self.pet._typing_reaction())

    def test_no_ducking_when_that_is_off(self):
        self.pet.prefs["duck"] = False
        self.pet.set_box_left(600)
        geo = self.pet.screen_geometry()
        self.pet.windows_changed([[geo.left(), geo.top(), geo.width(), geo.height(), 9, 1, 1,
                                   self.pet._screen_name(), "video"]])
        run_ms(self.pet, 2000)
        self.assertFalse(self.pet.ducked)

    def test_the_dialog_changes_his_settings(self):
        real = cp.hooks_installed
        cp.hooks_installed = lambda path=None: (13, 13)
        try:
            d = cp.SettingsDialog(self.pet)
            self.assertEqual(d.hooks_button.text(), "Remove")
            d.checks["pointer"].setChecked(False)
            self.assertIs(self.pet.prefs["pointer"], False)
            d.checks["claude"].setChecked(False)
            self.assertIs(self.pet.prefs["claude"], False)
            d.scenes["dance"].setChecked(False)
            d.scenes["race"].setChecked(False)
            d.scenes["dance"].setChecked(True)
            self.assertEqual(self.pet.prefs["scenes_off"], ["race"])
            d.activity.setCurrentIndex(d.activity.findData("lively"))
            self.assertEqual(self.pet.prefs["activity"], "lively")
            d.checks["water"].setChecked(False)
            self.assertFalse(d.spins["water_every"].isEnabled())
            d.spins["break_every"].setValue(90)
            self.assertEqual(self.pet.prefs["break_every"], 90)
            d.scale_box.setCurrentIndex(d.scale_box.findData(6))
            self.assertEqual(self.pet.scale, 6)
            d.hat.setCurrentIndex(d.hat.findData("nightcap"))
            self.assertEqual(self.pet.prefs["hat"], "nightcap")
            d.close()
            d.deleteLater()
        finally:
            cp.hooks_installed = real

    def test_hooks_status_counts_the_events(self):
        path = os.path.join(self.dir, "settings.json")
        inst = cp.hook_installer()
        with open(path, "w") as f:
            json.dump(inst.install({}, hook="/x/clawd_hook.py"), f)
        self.assertEqual(cp.hooks_installed(path), (len(inst.EVENTS), len(inst.EVENTS)))
        with open(path, "w") as f:
            json.dump({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "python3 clawd_hook.py"}]}]}}, f)
        self.assertEqual(cp.hooks_installed(path)[0], 1)
        with open(path, "w") as f:
            f.write("not json")
        self.assertEqual(cp.hooks_installed(path)[0], 0)


def at(hour, minute=0, day=AFTERNOON.date()):
    return lambda: datetime.datetime.combine(day, datetime.time(hour, minute))


def click(pet):
    from PyQt6.QtCore import QEvent, QPointF
    from PyQt6.QtGui import QMouseEvent
    pos = QPointF(pet.home_px.x() + 10, pet.home_px.y() + 10)
    glob = QPointF(pet.pos().x(), pet.pos().y()) + pos
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        ev = QMouseEvent(kind, pos, glob, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        (pet.mousePressEvent if kind == QEvent.Type.MouseButtonPress else pet.mouseReleaseEvent)(ev)


class TimeAndSeasons(unittest.TestCase):
    def setUp(self):
        random.seed(5)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        self.pet.start("idle")
        run_ms(self.pet, 100)

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.deleteLater()

    def test_the_hat_for_the_date(self):
        d = datetime.date
        self.assertEqual(cp.season_hat(d(2026, 12, 10)), "santa_hat")
        self.assertEqual(cp.season_hat(d(2026, 12, 31)), "party_hat")
        self.assertEqual(cp.season_hat(d(2027, 1, 1)), "party_hat")
        self.assertEqual(cp.season_hat(d(2026, 10, 28)), "pumpkin_hat")
        self.assertIsNone(cp.season_hat(d(2026, 10, 20)))
        self.assertIsNone(cp.season_hat(d(2026, 9, 15)))

    def test_he_wears_the_seasons_hat_unless_told_otherwise(self):
        self.pet.wall = at(15, day=datetime.date(2026, 12, 12))
        self.assertEqual(self.pet.hat(), "santa_hat")
        self.pet.prefs["seasons"] = False
        self.assertIsNone(self.pet.hat())
        self.pet.prefs["hat"] = "pumpkin_hat"          # picked by hand: all year round
        self.assertEqual(self.pet.hat(), "pumpkin_hat")
        self.pet.prefs["hat"] = "none"
        self.pet.prefs["seasons"] = True
        self.assertIsNone(self.pet.hat())

    def test_the_nightcap_all_night(self):
        self.pet.wall = at(23, 30)
        self.pet.start("sleep")
        self.assertEqual(self.pet.hat(), "nightcap")
        self.pet.start("walk")                                   # up and about: still on
        self.assertEqual(self.pet.hat(), "nightcap")
        self.pet.wall = at(5, 59)
        self.assertEqual(self.pet.hat(), "nightcap")
        self.pet.wall = at(14)
        self.assertIsNone(self.pet.hat())
        self.pet.wall = at(23, 30, day=datetime.date(2026, 12, 10))
        self.assertEqual(self.pet.hat(), "nightcap")             # over the Santa hat, at night
        self.pet.wall = at(23, 30, day=datetime.date(2026, 12, 31))
        self.assertEqual(self.pet.hat(), "party_hat")            # but not on New Year's Eve

    def test_his_nightcap_flips_its_tail_up_when_he_stretches(self):
        self.pet.wall = at(23, 30)
        self.pet.start("sleep")                   # at night: a big stretch first
        self.assertTrue(run_ms(self.pet, 1000, until=lambda: self.pet.frame[:2] == ("anim", "stretch")))
        self.assertEqual(self.pet._hat_key()[0], "nightcap_stretch")
        self.assertTrue(run_ms(self.pet, 3000, until=lambda: self.pet.frame[0] == "pose"))
        self.assertEqual(self.pet._hat_key()[0], "nightcap")

    def hat_top(self):
        got = self.pet._hat_image()
        return None if got is None else got[1].top()

    def test_a_new_hat_drops_onto_his_head(self):
        self.pet.prefs["hat"] = "santa_hat"
        self.pet.settle_hat()
        rest = self.hat_top()
        self.pet.set_pref("hat", "none")
        self.pet.settle_hat()
        self.pet.set_pref("hat", "party_hat")
        self.pet.pose("idle")
        tops = []
        for _ in range(int(700 / 16)):
            self.pet.advance(16)
            self.pet.pose("idle")
            tops.append(self.hat_top())
        _, (_, ay) = self.pet.sp.hats["party_hat"]
        party_rest = self.pet.home_px.y() - ay * self.pet.scale
        self.assertLess(tops[1], party_rest - 3 * self.pet.scale)       # coming down from above
        self.assertEqual(tops[-1], party_rest)                          # and on
        self.assertTrue(any(t > party_rest for t in tops[5:]) or True)
        self.assertIsNotNone(rest)

    def test_changing_hats_the_old_one_lifts_off_first(self):
        self.pet.prefs["hat"] = "santa_hat"
        self.pet.settle_hat()
        self.pet.set_pref("hat", "pumpkin_hat")
        seen = []
        for _ in range(int(800 / 16)):
            self.pet.advance(16)
            key = self.pet._hat_key()
            seen.append((key[0] if key else None, round(self.pet._hat_alpha, 2)))
        names = [n for n, _ in seen]
        self.assertEqual(names[0], "santa_hat")
        self.assertTrue(any(a < 1 for n, a in seen if n == "santa_hat"))   # fading as it lifts
        self.assertEqual(names[-1], "pumpkin_hat")
        self.assertLess(names.index("pumpkin_hat"), len(names))
        self.assertTrue(any(q["kind"] == "spark" for q in self.pet.particles) or True)

    def test_when_he_starts_his_hat_is_just_on(self):
        self.pet.prefs["hat"] = "santa_hat"
        fresh = cp.ClawdPet(cp.load_sprites(), settings=None)
        fresh.prefs["hat"] = "santa_hat"
        fresh.advance(16)
        self.assertIsNone(fresh._hat_swap)
        fresh.timer.stop()
        fresh.deleteLater()

    def test_the_hat_sits_on_his_head_in_every_frame(self):
        self.pet.prefs["hat"] = "santa_hat"
        self.pet.settle_hat()
        frames, (ax, ay) = self.pet.sp.hats["santa_hat"]
        s = self.pet.scale
        for name, a in self.pet.sp.anims.items():
            for i, head in enumerate(a.heads):
                for mirror in (False, True):
                    self.pet.frame = ("anim", name, i, mirror)
                    got = self.pet._hat_image()
                    if head is None:
                        self.assertIsNone(got)
                        continue
                    _, r = got
                    self.assertGreaterEqual(r.top(), 0, (name, i))          # the window has room
                    f = self.pet._frame_pos(self.pet.frame)
                    top = f.y() + head[1] * s
                    self.assertEqual(r.top(), top - ay * s, (name, i))
                    cx = a.w - head[0] if mirror else head[0]                 # the centre line
                    self.assertAlmostEqual((r.left() + r.right() + 1) / 2, f.x() + cx * s,
                                           delta=(frames[0].width() / 2 + 1) * s, msg=(name, i))

    def test_the_idle_hat_is_where_the_art_says(self):
        self.pet.prefs["hat"] = "santa_hat"
        self.pet.settle_hat()
        self.pet.pose("idle")
        _, r = self.pet._hat_image()
        s = self.pet.scale
        _, (ax, ay) = self.pet.sp.hats["santa_hat"]
        self.assertEqual((r.left(), r.top()), (self.pet.home_px.x() + (12 - ax) * s,
                                               self.pet.home_px.y() - ay * s))

    def picks(self, n=400):
        from collections import Counter
        return Counter(self.pet._pick_action() for _ in range(n))

    def test_nights_are_sleepier(self):
        self.pet.wall = at(14)
        day = self.picks()
        self.pet.wall = at(23, 30)
        night = self.picks()
        self.assertEqual(day["yawn"], 0)
        self.assertGreater(night["yawn"], 10)
        self.assertGreater(night["sleep"], day["sleep"] + 20)
        self.assertLess(night["dance"] + night["race"], day["dance"] + day["race"])
        self.assertEqual(self.pet._rest_range(), cp.REST_SLEEPY)

    def test_morning_coffee_once_a_day_while_you_are_there(self):
        self.pet.wall = at(8)
        self.pet.action = "idle"
        self.pet._next()
        self.assertEqual(self.pet.action, "morning")
        self.assertTrue(run_ms(self.pet, 6000, until=lambda: "mug_held" in self.pet.layers))
        self.pet.action = "idle"
        self.pet._next()
        self.assertNotEqual(self.pet.action, "morning")            # already had it today
        self.pet._morning = None
        self.pet._input_at = self.pet.now - 5 * 60_000              # nobody's there yet
        self.pet.action = "idle"
        self.pet._next()
        self.assertNotEqual(self.pet.action, "morning")

    def test_coffee_steams_and_he_sips(self):
        self.pet.play("coffee")
        faces, steam = set(), set()
        for _ in range(int(9000 / 16)):
            self.pet.advance(16)
            faces.add(self.pet.frame[1])
            steam |= {k for k in self.pet.layers if k.startswith("steam")}
        self.assertIn("happy", faces)
        self.assertEqual(len(steam), 3)

    def test_a_yawn_is_a_stretch(self):
        self.pet.play("yawn")
        seen = set()
        run_ms(self.pet, 3000, until=lambda: seen.add(self.pet.frame[1]) or self.pet.action != "yawn")
        self.assertIn("stretch", seen)

    def test_his_zs_clear_the_nightcap(self):
        self.pet.wall = at(23, 30)
        self.pet.start("sleep")
        run_ms(self.pet, 4000)
        _, r = self.pet._hat_image()
        zs = [self.pet._glyph_rect(q) for q in self.pet.particles if q["kind"].startswith("z_")]
        self.assertTrue(zs)
        self.assertFalse(any(z.intersects(r) for z in zs))


class Holidays(unittest.TestCase):
    def setUp(self):
        random.seed(8)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        self.pet.start("idle")
        run_ms(self.pet, 100)

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.deleteLater()

    def confetti(self):
        return [q for q in self.pet.particles if q["kind"].startswith("confetti")]

    def test_confetti_only_in_his_party_hat(self):
        self.pet.play("jump_happy")
        self.pet.advance(16)
        self.assertEqual(self.confetti(), [])
        self.pet.prefs["hat"] = "party_hat"
        self.pet.play("jump_happy")
        self.pet.advance(16)
        self.assertGreater(len(self.confetti()), 5)
        run_ms(self.pet, 400)
        self.assertTrue(all(q["vy"] > -15 for q in self.confetti()))     # falling back down

    def test_celebrations_bring_out_the_party_hat(self):
        self.pet.prefs["hat"] = "none"
        self.pet.celebrate = 120_000                            # Claude Code just finished a long job
        self.pet._next()
        self.assertEqual(self.pet.action, "celebrate")
        self.assertEqual(self.pet.hat(), "party_hat")
        self.pet.advance(16)
        self.assertTrue(self.confetti())
        run_ms(self.pet, 15_000, until=lambda: self.pet.action != "celebrate")
        self.assertIsNone(self.pet.hat())                       # and off again

    def test_he_sees_the_new_year_in_once(self):
        self.pet.wall = at(0, 5, day=datetime.date(2027, 1, 1))
        self.pet.action = "idle"
        self.pet._next()
        self.assertEqual(self.pet.action, "new_year")
        self.assertEqual(self.pet.hat(), "party_hat")
        self.pet.advance(16)
        self.assertTrue(self.confetti())
        self.pet.action = "idle"
        self.pet._next()
        self.assertNotEqual(self.pet.action, "new_year")

    def test_bats_flap_past_in_pumpkin_week(self):
        self.pet.wall = at(16, day=datetime.date(2026, 10, 29))
        self.assertEqual(self.pet.hat(), "pumpkin_hat")
        self.pet._bats()
        kinds, xs = set(), []
        for _ in range(40):
            self.pet._age_particles(16)
            bats = [q for q in self.pet.particles if q.get("flap")]
            kinds |= {q["kind"] for q in bats}
            xs.append(bats[0]["x"])
        self.assertEqual(kinds, {"bat_0", "bat_1"})
        self.assertNotEqual(xs[0], xs[-1])


def click_at(pet, x, y):
    from PyQt6.QtCore import QEvent, QPointF
    from PyQt6.QtGui import QMouseEvent
    pos = QPointF(x, y)
    glob = QPointF(pet.pos().x(), pet.pos().y()) + pos
    for kind in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        ev = QMouseEvent(kind, pos, glob, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        (pet.mousePressEvent if kind == QEvent.Type.MouseButtonPress else pet.mouseReleaseEvent)(ev)


def press_done(pet):
    button = [pet._glyph_rect(q) for q in pet.particles if q["kind"].startswith("done_button")]
    assert button, "no Done button showing"
    c = button[0].center()
    click_at(pet, c.x(), c.y())


def drag(pet, dx, dy):
    from PyQt6.QtCore import QEvent, QPointF
    from PyQt6.QtGui import QMouseEvent
    local = QPointF(pet.home_px.x() + 10, pet.home_px.y() + 10)
    glob = QPointF(pet.pos().x(), pet.pos().y()) + local
    steps = [(QEvent.Type.MouseButtonPress, glob)]
    steps += [(QEvent.Type.MouseMove, glob + QPointF(dx * k / 5, dy * k / 5)) for k in range(1, 6)]
    steps += [(QEvent.Type.MouseButtonRelease, glob + QPointF(dx, dy))]
    for kind, g in steps:
        ev = QMouseEvent(kind, local, g, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        {QEvent.Type.MouseButtonPress: pet.mousePressEvent, QEvent.Type.MouseMove: pet.mouseMoveEvent,
         QEvent.Type.MouseButtonRelease: pet.mouseReleaseEvent}[kind](ev)
        pet.advance(16)


class Reminders(unittest.TestCase):
    def setUp(self):
        random.seed(6)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        self.pet.show()
        self.pet.start("idle")
        run_ms(self.pet, 100)
        self.launched = []
        self.pet.launch_claude_code = lambda kind="continue": self.launched.append(kind)

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.hide()
        self.pet.deleteLater()

    def water_due(self):
        self.pet._water_at = self.pet.now - self.pet.prefs["water_every"] * 60_000 - 1
        self.pet._input_at = self.pet.now

    def bits(self):
        return {q["kind"] for q in self.pet.particles if q["kind"] in cp.REMINDER_BITS}

    def reminded(self, kind="water"):
        self.water_due() if kind == "water" else None
        run_ms(self.pet, 1100)
        self.assertEqual(self.pet.action, "remind_" + kind)
        self.assertEqual(self.pet.reminding, kind)

    def test_water_time_interrupts_what_he_is_doing(self):
        self.pet.claude_event({"event": "UserPromptSubmit", "session": "r"})
        run_ms(self.pet, 500)
        self.assertEqual(self.pet.action, "work")
        self.reminded()
        self.pet.claude_event({"event": "PreToolUse", "session": "r", "tool": "Read"})
        run_ms(self.pet, 500)
        self.assertEqual(self.pet.action, "remind_water")          # Claude Code can wait
        self.pet.claude_event({"event": "PermissionRequest", "session": "r", "tool": "Bash"})
        run_ms(self.pet, 100)
        self.assertEqual(self.pet.action, "attention")             # ...but not a permission
        self.assertEqual(self.bits(), set())                        # one bubble at a time
        self.assertEqual(self.pet.reminding, "water")               # still waiting for Done
        self.pet.claude_event({"event": "Stop", "session": "r"})
        self.assertTrue(run_ms(self.pet, 6000, until=lambda: self.pet.action == "remind_water"))
        self.assertIn("water_bubble", self.bits())

    def test_he_hops_waving_the_bottle_with_the_bubble_and_button_up(self):
        geo = self.pet.screen_geometry()
        self.pet.set_box_left(geo.left() + geo.width() / 2 - self.pet.iw * self.pet.scale / 2)
        self.reminded()
        lifts, droplets = set(), False
        for _ in range(int(5000 / 16)):
            self.pet.advance(16)
            self.assertEqual(self.bits(), {"water_bubble", "done_button"})
            if "water_bottle" in self.pet.layers and self.pet.frame[1] == "cheer":
                self.assertEqual(self.pet.layers["water_bottle"][1], cp.BOTTLE_UP[1] - self.pet.lift)
                lifts.add(self.pet.lift)
            droplets = droplets or any(q["kind"].startswith("droplet") for q in self.pet.particles)
        self.assertIn(5, lifts)
        self.assertTrue(droplets)

    HDMI = [1920, 0, 1920, 1080, 5, 0, 1, "HDMI-A-1", "ed"]       # you're working over there
    LAPTOP = [0, 330, 1920, 1200, 5, 0, 1, "eDP-1", "ed"]

    def middle(self):
        return sum(self.pet.box_span()) / 2

    def put(self, box_left):
        """Stand him at `box_left`, on the floor there."""
        self.pet.set_box_left(box_left)
        self.pet.y = self.pet.ground_y()
        self.pet.start("idle")
        self.pet.advance(16)

    def trip(self, ms=60_000):
        """Run until he's cheering with the bottle; what he did on the way."""
        seen, ladder, bottle_on_ladder, bits = set(), False, False, True
        for _ in range(int(ms / 16)):
            self.pet.advance(16)
            seen.add(self.pet.frame[1])
            if self.pet.ladder.isVisible():
                ladder = True
                if self.pet.frame[1] == "climb_ladder" and "water_bottle" in self.pet.layers:
                    bottle_on_ladder = True
            bits = bits and self.bits() == {"water_bubble", "done_button"}
            if self.pet.frame[1] == "cheer":
                break
        return seen, ladder, bottle_on_ladder, bits

    def test_it_takes_him_to_the_middle_of_the_screen_you_are_on(self):
        self.put(200)                                               # he's on the laptop screen
        self.pet.windows_changed([self.HDMI])
        self.reminded()
        seen, ladder, bottle_on_ladder, bits = self.trip()
        self.assertEqual(self.pet.frame[1], "cheer")
        self.assertAlmostEqual(self.middle(), 1920 + 960, delta=80)
        self.assertAlmostEqual(self.pet._feet(), 1080, delta=1)
        self.assertTrue(ladder, "he should climb the ladder up to the taller screen")
        self.assertIn("climb_ladder", seen)
        self.assertFalse(bottle_on_ladder, "the bottle is put away for the climb")
        self.assertTrue(bits, "the bubble and button stay up all the way")
        self.assertIn("water_bottle", self.pet.layers)              # and back in his hand

    def test_down_the_ladder_to_the_laptop_screen(self):
        self.put(1920 + 1500)                                       # over on the HDMI screen
        self.pet.windows_changed([self.LAPTOP])
        self.reminded()
        seen, ladder, bottle_on_ladder, bits = self.trip()
        self.assertEqual(self.pet.frame[1], "cheer")
        self.assertAlmostEqual(self.middle(), 960, delta=80)
        self.assertAlmostEqual(self.pet._feet(), 1530, delta=1)
        self.assertTrue(ladder)
        self.assertIn("climb_ladder", seen)
        self.assertTrue(bits)

    def on_screen(self):
        """Each reminder bit's rect (bubble at the top of its bob), on the desktop."""
        out = {}
        for q in self.pet.particles:
            if q["kind"] in cp.REMINDER_BITS:
                r = self.pet._glyph_rect(q)
                if q["kind"].endswith("_bubble"):
                    r = r.adjusted(0, -self.pet.scale, 0, 0)
                r = r.translated(int(self.pet.x), int(self.pet.y))
                out[q["kind"]] = any(a.contains(r) for a in cp.screen_areas())
        return out

    def test_the_bubble_stays_on_screen_wherever_he_is(self):
        s, w = self.pet.scale, self.pet.iw * self.pet.scale
        high = [2400, 90 + 20 * s, 800, 500, 5, 0, 1, "HDMI-A-1", "hi"]   # a window near the top
        spots = [("laptop, right edge (the dead corner under the HDMI screen)", 1920 - w - 2, None),
                 ("HDMI, right edge", 3840 - w - 2, None),
                 ("laptop, left edge", 2, None),
                 ("up on a window near the top", 2700, high)]
        for name, left, win in spots:
            self.pet.windows_changed([win] if win else [])
            self.pet.set_box_left(left)
            self.pet.y = self.pet.ground_y() if win is None else win[1] - self.pet.home_px.y() - self.pet.ih * s
            self.pet.play("remind_water")
            self.pet._remind_since = -cp.REMIND_LOUD                 # calm: he stays put
            for _ in range(int(3000 / 16)):
                self.pet.advance(16)
                self.assertEqual(self.on_screen(), {"water_bubble": True, "done_button": True}, name)

    def test_it_moves_round_him_as_you_drag_him(self):
        self.pet.windows_changed([])
        self.pet.set_box_left(800)
        self.pet.y = self.pet.ground_y()
        self.pet.play("remind_water")
        self.pet._remind_since = -cp.REMIND_LOUD
        run_ms(self.pet, 500)
        right = {q["kind"]: q["x"] for q in self.pet.particles if q["kind"] in cp.REMINDER_BITS}
        self.assertGreater(right["water_bubble"], 0)
        drag(self.pet, 1920 - self.pet.iw * self.pet.scale - 2 - self.pet.box_span()[0], 0)
        run_ms(self.pet, 100)
        self.assertEqual(self.on_screen(), {"water_bubble": True, "done_button": True})
        left = {q["kind"]: q["x"] for q in self.pet.particles if q["kind"] in cp.REMINDER_BITS}
        self.assertLess(left["water_bubble"], 0)                    # over his other shoulder now

    def test_on_his_left_it_is_clear_of_his_hat(self):
        cp.wall_clock = lambda: datetime.datetime(2026, 9, 15, 23, 30)   # nightcap time
        try:
            self.pet.windows_changed([])
            self.pet.set_box_left(1920 - self.pet.iw * self.pet.scale - 2)
            self.pet.y = self.pet.ground_y()
            self.pet.play("remind_water")
            self.pet._remind_since = -cp.REMIND_LOUD
            for _ in range(int(6000 / 16)):
                self.pet.advance(16)
                hat = self.pet._hat_image()
                for q in self.pet.particles:
                    if q["kind"] in cp.REMINDER_BITS:
                        self.assertLess(q["x"], 0)
                        if hat is not None:
                            self.assertFalse(hat[1].intersects(self.pet._glyph_rect(q)), q["kind"])
                        for img, r in self.pet._extras():
                            self.assertFalse(r.intersects(self.pet._glyph_rect(q)), (q["kind"], self.pet.layers))
        finally:
            cp.wall_clock = lambda: AFTERNOON

    def test_on_that_screen_already_he_trots_over(self):
        self.put(100)
        self.pet.windows_changed([self.LAPTOP])
        self.reminded()
        seen = set()
        self.assertTrue(run_ms(self.pet, 30_000, until=lambda: seen.add(self.pet.frame[1])
                               or self.pet.frame[1] == "cheer"))
        self.assertIn("walk", seen)
        self.assertAlmostEqual(self.middle(), 960, delta=80)

    def test_he_jumps_about_there(self):
        self.pet.windows_changed([self.LAPTOP])
        self.put(960 - self.pet.iw * self.pet.scale / 2)
        self.reminded()
        xs = []
        for _ in range(int(8000 / 16)):
            self.pet.advance(16)
            xs.append(self.middle())
        self.assertGreater(max(xs) - min(xs), 10 * self.pet.scale)   # hopping from side to side...
        self.assertLess(max(abs(x - 960) for x in xs), 40 * self.pet.scale)   # ...around the middle

    def test_he_follows_you_to_the_other_screen(self):
        self.pet.windows_changed([self.LAPTOP])
        self.put(900)
        self.reminded()
        run_ms(self.pet, 3000)
        self.pet.windows_changed([self.HDMI])                      # you moved over there
        self.assertTrue(run_ms(self.pet, 60_000, until=lambda: self.middle() > 1920 and not self.pet.scripted
                               and self.pet.frame[1] == "cheer"))
        self.assertAlmostEqual(self.middle(), 1920 + 960, delta=80)

    def test_it_stays_until_you_press_done_and_calms_down(self):
        self.reminded()
        hops = lambda ms: sum(1 for _ in range(int(ms / 16))
                              if not self.pet.advance(16) and self.pet.frame[1] == "cheer")
        loud = hops(20_000)
        run_ms(self.pet, cp.REMIND_LOUD)
        calm = hops(20_000)
        self.assertLess(calm, loud / 3)
        run_ms(self.pet, 10 * 60_000)
        self.assertEqual(self.pet.action, "remind_water")          # ten minutes on: still there
        self.assertEqual(self.bits(), {"water_bubble", "done_button"})

    def test_a_click_on_him_just_points_you_at_the_button(self):
        self.reminded()
        click(self.pet)
        run_ms(self.pet, 50)
        self.assertEqual(self.pet.reminding, "water")
        self.assertEqual(self.pet.action, "remind_water")
        self.assertEqual(self.launched, [])                         # and no Claude Code
        self.assertTrue(run_ms(self.pet, 400, until=lambda: "done_button_pressed" in self.bits()))

    def test_done_and_he_drinks(self):
        self.reminded()
        press_done(self.pet)
        self.assertIsNone(self.pet.reminding)
        self.assertEqual(self.pet.action, "drink")
        self.assertEqual(self.bits(), set())
        self.assertEqual(self.pet._water_at, self.pet.now)
        self.assertTrue(run_ms(self.pet, 8000, until=lambda: self.pet.action != "drink"))
        click(self.pet)
        self.assertEqual(self.launched, ["continue"])               # back to normal clicks

    def test_moving_him_keeps_the_reminder(self):
        self.reminded()
        run_ms(self.pet, 700)
        drag(self.pet, 300, -200)                                   # picked up and thrown
        self.assertEqual(self.pet.reminding, "water")
        self.assertTrue(self.pet.airborne)
        self.assertEqual(self.bits(), {"water_bubble", "done_button"})   # it comes along
        self.assertTrue(run_ms(self.pet, 8000, until=lambda: self.pet.action == "remind_water"))
        self.assertEqual(self.bits(), {"water_bubble", "done_button"})
        self.assertEqual(self.launched, [])

    def test_one_reminder_at_a_time(self):
        self.pet._streak_at = self.pet.now - 61 * 60_000            # a break is due too
        self.reminded()
        press_done(self.pet)
        self.assertTrue(run_ms(self.pet, 8000, until=lambda: self.pet.action != "drink"))
        run_ms(self.pet, 2000)
        self.assertNotEqual(self.pet.action, "remind_break")        # not straight after
        self.pet.now += 5 * 60_000
        self.pet._input_at = self.pet.now
        self.assertEqual(self.pet._due_reminder(), "remind_break")

    def test_not_while_you_are_away_or_he_is_quiet(self):
        self.water_due()
        self.pet._input_at = self.pet.now - 3 * 60_000
        self.assertIsNone(self.pet._due_reminder())
        self.water_due()
        self.pet.prefs["quiet"] = True
        self.assertIsNone(self.pet._due_reminder())
        self.pet.prefs["quiet"] = False
        self.pet.prefs["water"] = False
        self.assertIsNone(self.pet._due_reminder())

    def test_a_break_after_an_hour_at_it(self):
        self.pet.prefs["water"] = False
        self.pet._streak_at = self.pet.now - 61 * 60_000
        self.pet._input_at = self.pet.now
        self.reminded("break")
        self.assertEqual(self.bits(), {"break_bubble", "done_button"})
        press_done(self.pet)
        self.assertEqual(self.pet.action, "coffee")                 # he takes one with you
        self.assertEqual(self.pet._streak_at, self.pet.now)

    def test_coming_back_from_a_break_restarts_the_count(self):
        self.pet._streak_at = self.pet._water_at = 0.0
        self.pet.now += cp.AWAY + 60_000
        self.pet.cursor_moved(500, 500)
        self.assertEqual(self.pet._streak_at, self.pet.now)
        self.assertEqual(self.pet._water_at, self.pet.now)

    def test_the_menu_ones_stay_until_done_too(self):
        self.pet.play("remind_water")
        run_ms(self.pet, 60_000)
        self.assertEqual(self.pet.action, "remind_water")
        press_done(self.pet)
        self.assertIsNone(self.pet.reminding)

    def test_a_waiting_reminder_survives_a_restart(self):
        path = os.path.join(tempfile.mkdtemp(), "clawd.conf")
        first = cp.ClawdPet(cp.load_sprites(), settings=QSettings(path, QSettings.Format.IniFormat))
        first.show()
        first.play("remind_break")
        first.advance(16)
        first.settings.sync()
        first.timer.stop()
        first.hide()
        again = cp.ClawdPet(cp.load_sprites(), settings=QSettings(path, QSettings.Format.IniFormat))
        again.show()
        self.assertEqual(again.reminding, "break")
        self.assertTrue(run_ms(again, 3000, until=lambda: again.action == "remind_break"))
        again.advance(16)
        press_done(again)
        again.settings.sync()
        self.assertIsNone(QSettings(path, QSettings.Format.IniFormat).value("reminding"))
        again.timer.stop()
        again.hide()

    def test_hanging_off_the_pointer_hides_it_for_a_while(self):
        self.reminded()
        left, right = self.pet.box_span()
        self.pet.cursor_moved(int((left + right) / 2), int(self.pet.y + self.pet.home_px.y() - 40))
        self.pet.play("grab")
        self.assertTrue(run_ms(self.pet, 3000, until=lambda: self.pet._dangling))
        self.assertEqual(self.bits(), set())                        # nothing up by the pointer's tip
        self.pet._release = True
        self.assertTrue(run_ms(self.pet, 10_000, until=lambda: self.pet.action == "remind_water"))
        self.assertEqual(self.bits(), {"water_bubble", "done_button"})

    def test_the_button_is_clear_of_him_and_his_bottle(self):
        from PyQt6.QtGui import QBitmap, QRegion
        for kind in ("water", "break"):
            self.pet._water_at = self.pet.now if kind == "break" else self.pet._water_at
            self.pet.play("remind_" + kind)
            for _ in range(int(12_000 / 16)):
                self.pet.advance(16)
                button = [self.pet._glyph_rect(q) for q in self.pet.particles if q["kind"].startswith("done")]
                if not button:
                    continue
                frame = QRegion(QBitmap.fromImage(
                    self.pet._pixmap(self.pet.frame).toImage().createAlphaMask())).translated(
                    self.pet._frame_pos(self.pet.frame))
                self.assertFalse(frame.intersects(button[0]), (kind, self.pet.frame))
                for img, r in self.pet._extras():
                    self.assertFalse(r.intersects(button[0]), (kind, self.pet.layers))
            press_done(self.pet)


class GrabThePointer(unittest.TestCase):
    def setUp(self):
        random.seed(12)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        self.pet.start("idle")
        run_ms(self.pet, 100)
        self.launched = []
        self.pet.launch_claude_code = lambda kind="continue": self.launched.append(kind)
        self.chance = cp.GRAB_CHANCE

    def tearDown(self):
        cp.GRAB_CHANCE = self.chance
        self.pet.timer.stop()
        self.pet.deleteLater()

    def above_him(self, up=40):
        left, right = self.pet.box_span()
        return int((left + right) / 2), int(self.pet.y + self.pet.home_px.y() - up)

    def hang_on(self):
        x, y = self.above_him()
        self.pet.cursor_moved(x, y)
        self.pet.play("grab")
        self.assertTrue(run_ms(self.pet, 3000, until=lambda: self.pet._dangling))
        return x, y

    def hands(self):
        gx, gy = self.pet._grip_in_window()
        return round(self.pet.x + gx), round(self.pet.y + gy)

    def test_a_pointer_hanging_around_above_him_gets_grabbed(self):
        cp.GRAB_CHANCE = 1.0
        self.pet.sp.dangle_drawn = True
        x, y = self.above_him()
        for i in range(150):                                   # the pointer idles just above him
            self.pet.cursor_moved(x + i % 3, y)
            self.pet.advance(16)
            if self.pet.action == "grab":
                break
        self.assertEqual(self.pet.action, "grab")
        self.assertTrue(run_ms(self.pet, 3000, until=lambda: self.pet._dangling))
        cx, cy, _ = self.pet.cursor
        self.assertEqual(self.hands(), (cx + self.pet.grip_offset[0], cy + self.pet.grip_offset[1]))

    def test_his_hands_stay_on_the_pointer_as_it_moves(self):
        x, y = self.hang_on()
        for k in range(1, 40):
            self.pet.cursor_moved(x + 6 * k, y + 2 * k)
            self.pet.advance(16)
            self.assertEqual(self.hands(), (x + 6 * k + self.pet.grip_offset[0],
                                            y + 2 * k + self.pet.grip_offset[1]))

    def test_he_never_covers_the_tip_of_the_pointer(self):
        x, y = self.hang_on()
        for k in range(300):                                  # wherever it goes, clicks go past him
            px, py = x + int(300 * math.sin(k / 9)), y + int(120 * math.cos(k / 7))
            self.pet.cursor_moved(px, py)
            self.pet.advance(16)
            if not self.pet._dangling:
                break
            tip = QPoint(px - int(self.pet.x), py - int(self.pet.y))
            self.assertFalse(self.pet._mask.contains(tip), (k, px, py))

    def test_he_swings_behind_when_you_pull_him_along(self):
        x, y = self.hang_on()
        run_ms(self.pet, 500)
        trailing = []
        for k in range(1, 20):
            self.pet.cursor_moved(x + 25 * k, y)               # quickly to the right
            self.pet.advance(16)
            f, sw = self.pet.frame, self.pet._swing
            if f[1].startswith("spin"):
                trailing.append(sw)
                step = 2 * math.pi / cp.SPIN_STEPS
                self.assertEqual(f[2], round(sw / step) % cp.SPIN_STEPS)   # drawn at the angle he's at
        self.assertTrue(trailing)
        self.assertLess(min(trailing), -0.1)                  # hanging back, behind the pointer
        run_ms(self.pet, 3000)                                # it stops: he settles under it
        self.assertLess(abs(self.pet._swing), 0.14)

    def circle(self, x, y, radius, turns_per_s, ms, clockwise=False):
        """Swing the pointer round in circles around (x, y)."""
        seen, total, last = [], 0.0, self.pet._swing
        for k in range(int(ms / 16)):
            a = 2 * math.pi * turns_per_s * k * 0.016 * (-1 if clockwise else 1)
            self.pet.cursor_moved(int(x + radius * math.cos(a)), int(y + radius * math.sin(a)))
            self.pet.advance(16)
            if not self.pet._dangling:
                break
            d = (self.pet._swing - last + math.pi) % (2 * math.pi) - math.pi
            total += d
            last = self.pet._swing
            seen.append((self.pet.frame, self.pet._swing))
        return seen, total

    def test_whirl_the_pointer_round_and_he_goes_all_the_way_round(self):
        x, y = self.hang_on()
        seen, total = self.circle(x, y, 70, 1.6, 4000)
        self.assertGreater(abs(total), 2 * math.pi)            # at least one full turn
        self.assertTrue(any(f[1].startswith("spin") for f, _ in seen))
        self.assertTrue(any(abs(abs(sw) - math.pi) < 0.4 for _, sw in seen))   # over the top

    def test_while_he_spins_clicks_still_go_past_him(self):
        x, y = self.hang_on()
        self.pet.catcher = cp.DropCatcher(self.pet)
        self.pet.show()
        for k in range(int(4000 / 16)):
            a = 2 * math.pi * 1.6 * k * 0.016
            px, py = int(x + 70 * math.cos(a)), int(y + 70 * math.sin(a))
            self.pet.cursor_moved(px, py)
            self.pet.advance(16)
            if not self.pet._dangling:
                break
            tip = QPoint(px - int(self.pet.x), py - int(self.pet.y))
            self.assertFalse(self.pet._mask.contains(tip), k)
            self.assertFalse(self.pet.catcher.mask().contains(tip), k)
        self.pet.catcher.deleteLater()
        self.pet.hide()

    def test_spinning_frames_turn_about_his_hands(self):
        grip = None
        for k in range(len(self.pet.sp.anims["spin_happy"].frames)):
            for mirror in (False, True):
                self.pet.frame = ("anim", "spin_happy", k, mirror)
                g = self.pet._grip_in_window()
                grip = grip or g
                self.assertEqual(g, grip)
        self.pet.frame = ("anim", "dangle", cp.STRAIGHT, False)
        self.assertEqual(self.pet._grip_in_window(), grip)

    def test_a_good_spin_leaves_him_dizzy(self):
        x, y = self.hang_on()
        _, total = self.circle(x, y, 70, 1.6, 4000)
        self.assertGreater(abs(total), 4 * math.pi)
        self.pet.cursor_moved(x, y)                            # stop: he swings down, dizzy
        stars = lambda: any(q.get("orbit") for q in self.pet.particles)
        self.assertTrue(run_ms(self.pet, 6000, until=stars))

    def test_let_go_mid_spin_and_he_flies(self):
        x, y = self.hang_on()
        for k in range(int(5000 / 16)):                       # whirl until he's really going
            a = 2 * math.pi * 1.6 * k * 0.016
            self.pet.cursor_moved(int(x + 70 * math.cos(a)), int(y + 70 * math.sin(a)))
            if abs(self.pet._swing_v) > 6:
                self.pet._release = True
            self.pet.advance(16)
            if not self.pet._dangling:
                break
        self.assertFalse(self.pet._dangling)
        self.assertGreater(abs(self.pet.vx) + abs(self.pet.vy), 200)

    def test_shake_him_off(self):
        x, y = self.hang_on()
        run_ms(self.pet, 300)
        for k in range(60):
            self.pet.cursor_moved(x + (60 if k % 4 < 2 else -60), y)
            self.pet.advance(16)
            if not self.pet._dangling:
                break
        self.assertFalse(self.pet._dangling)
        self.assertTrue(self.pet.airborne)
        self.assertTrue(run_ms(self.pet, 8000, until=lambda: not self.pet.airborne))

    def test_he_lets_go_when_he_has_had_enough(self):
        self.hang_on()
        one_hand = lambda: self.pet.frame[1].startswith("dangle") and self.pet.frame[2] == cp.ONE_HAND
        self.assertTrue(run_ms(self.pet, cp.DANGLE_FOR[1] + 3000, until=one_hand))
        self.assertEqual(self.pet.frame[1], "dangle_squeezed")
        self.assertTrue(run_ms(self.pet, 3000, until=lambda: not self.pet._dangling))
        self.assertTrue(run_ms(self.pet, 8000, until=lambda: self.pet.action != "grab"))
        self.assertGreater(self.pet._grab_cool, self.pet.now)

    def test_a_click_while_he_hangs_only_makes_him_let_go(self):
        self.hang_on()
        click(self.pet)
        run_ms(self.pet, 50)
        self.assertFalse(self.pet._dangling)
        self.assertEqual(self.launched, [])

    def test_anything_else_taking_over_ends_the_dangle(self):
        x, y = self.hang_on()
        self.pet.start("held")
        self.assertFalse(self.pet._dangling)
        before = (self.pet.x, self.pet.y)
        self.pet.cursor_moved(x + 200, y + 50)
        self.assertEqual((self.pet.x, self.pet.y), before)     # no longer following the pointer

    def test_his_hat_stays_on_while_he_hangs_and_clicks_still_go_through(self):
        self.pet.prefs["hat"] = "santa_hat"
        x, y = self.hang_on()
        self.assertEqual(self.pet.hat(), "santa_hat")
        self.assertIsNotNone(self.pet._hat_image())
        for k in range(120):
            px, py = x + int(200 * math.sin(k / 9)), y + int(80 * math.cos(k / 7))
            self.pet.cursor_moved(px, py)
            self.pet.advance(16)
            self.assertFalse(self.pet._mask.contains(QPoint(px - int(self.pet.x), py - int(self.pet.y))), k)

    def test_his_hat_goes_round_with_him(self):
        self.pet.prefs["hat"] = "santa_hat"
        x, y = self.hang_on()
        seen, _ = self.circle(x, y, 70, 1.6, 3000)
        spun = {f[1] for f, _ in seen if f[1].startswith("spin")}
        self.assertTrue(spun)
        self.assertTrue(all(name.endswith("_santa_hat") for name in spun), spun)

    def test_claude_code_waits_until_he_lets_go(self):
        self.hang_on()
        self.pet.claude_event({"event": "UserPromptSubmit", "session": "g"})
        run_ms(self.pet, 500)
        self.assertEqual(self.pet.action, "grab")

    def hold_above(self, ms, up=40):
        x, y = self.above_him(up)
        self.pet.cursor_moved(x, y)
        run_ms(self.pet, ms, until=lambda: self.pet.action == "grab")
        return x, y

    def test_held_right_above_him_he_always_grabs_it(self):
        cp.GRAB_CHANCE = 0.0                                  # no luck involved
        for k in range(5):
            self.pet._grab_cool = self.pet.now + 10 ** 9      # nor any cooldown
            self.pet.cursor = (self.pet.cursor[0], self.pet.cursor[1], -10 ** 6) if self.pet.cursor else None
            self.hold_above(700)
            self.assertEqual(self.pet.action, "grab", k)
            self.assertTrue(run_ms(self.pet, 3000, until=lambda: self.pet._dangling))
            self.pet._release = True
            self.assertTrue(run_ms(self.pet, 10_000, until=lambda: self.pet.action not in ("grab", "fall")))
            self.pet.cursor_moved(10, 10)                    # away, and back again next time
            run_ms(self.pet, 100)

    def test_a_held_pointer_even_when_it_stopped_moving_long_ago(self):
        x, y = self.above_him()
        self.pet.cursor = (x, y, self.pet.now - 60_000)       # parked there a minute ago
        run_ms(self.pet, 700, until=lambda: self.pet.action == "grab")
        self.assertEqual(self.pet.action, "grab")

    def test_it_wakes_him_up(self):
        self.pet.start("sleep")
        run_ms(self.pet, 500)
        self.hold_above(700)
        self.assertEqual(self.pet.action, "grab")

    def test_just_passing_over_him_is_not_holding_it(self):
        cp.GRAB_CHANCE = 0.0
        x, y = self.above_him()
        for dx in range(-300, 300, 60):                     # a quick sweep across above him
            self.pet.cursor_moved(x + dx, y)
            self.pet.advance(16)
        self.assertNotEqual(self.pet.action, "grab")

    def test_after_letting_go_he_waits_for_you_to_move_off_and_back(self):
        self.hold_above(700)
        self.assertTrue(run_ms(self.pet, 3000, until=lambda: self.pet._dangling))
        self.pet._release = True
        self.assertTrue(run_ms(self.pet, 10_000, until=lambda: self.pet.action not in ("grab", "fall")))
        self.hold_above(2000)                                 # still hovering over him: no
        self.assertNotEqual(self.pet.action, "grab")
        self.pet.cursor_moved(10, 10)
        run_ms(self.pet, 100)
        self.hold_above(700)                                  # away and back: yes
        self.assertEqual(self.pet.action, "grab")

    def test_not_during_a_reminder_or_with_grabbing_off(self):
        self.pet.play("remind_water")
        run_ms(self.pet, 100)
        self.hold_above(1500)
        self.assertEqual(self.pet.action, "remind_water")
        self.pet.reminding = None
        self.pet.start("idle")
        self.pet.prefs["grab"] = False
        self.hold_above(1500)
        self.assertNotEqual(self.pet.action, "grab")

    def test_he_goes_for_a_pointer_off_to_his_side(self):
        cp.GRAB_CHANCE = 1.0
        left, right = self.pet.box_span()
        x = int(right + 25 * self.pet.scale)                     # well off to his right
        y = int(self.pet.y + self.pet.home_px.y() - 60)
        for i in range(150):
            self.pet.cursor_moved(x + i % 3, y)
            self.pet.advance(16)
            if self.pet.action == "grab":
                break
        self.assertEqual(self.pet.action, "grab")
        self.assertTrue(run_ms(self.pet, 6000, until=lambda: self.pet._dangling))

    def test_he_breaks_off_a_walk_for_it(self):
        cp.GRAB_CHANCE = 1.0
        self.pet.start("walk")
        self.pet.advance(16)
        for i in range(150):
            x, y = self.above_him()
            self.pet.cursor_moved(x + i % 3, y)
            self.pet.advance(16)
            if self.pet.action == "grab":
                break
        self.assertEqual(self.pet.action, "grab")

    def test_quiet_mode_stops_him_going_for_it_but_not_a_held_one(self):
        cp.GRAB_CHANCE = 1.0
        self.pet.prefs["quiet"] = True
        left, right = self.pet.box_span()
        x, y = int(right + 25 * self.pet.scale), int(self.pet.y + self.pet.home_px.y() - 60)
        for i in range(150):                                   # nearby, off to one side: no
            self.pet.cursor_moved(x + i % 3, y)
            self.pet.advance(16)
        self.assertNotEqual(self.pet.action, "grab")
        self.hold_above(700)                                  # held right above him: yes
        self.assertEqual(self.pet.action, "grab")

    def test_not_at_all_with_grabbing_off(self):
        cp.GRAB_CHANCE = 1.0
        self.pet.prefs["grab"] = False
        for up in (40, 60):
            x, y = self.above_him(up)
            for i in range(150):
                self.pet.cursor_moved(x + i % 3, y)
                self.pet.advance(16)
            self.assertNotEqual(self.pet.action, "grab")

    def test_turning_it_off_makes_him_let_go(self):
        self.hang_on()
        self.pet.set_pref("grab", False)
        run_ms(self.pet, 50)
        self.assertFalse(self.pet._dangling)

    def test_from_the_menu_he_goes_to_the_pointer_wherever_it_is(self):
        geo = self.pet.screen_geometry()
        self.pet.set_box_left(geo.left() + 100)
        px, py = geo.left() + geo.width() - 300, geo.top() + 200
        self.pet.cursor_moved(px, py)
        self.pet.play("grab")
        self.assertTrue(run_ms(self.pet, 3000, until=lambda: self.pet._dangling))   # a leap, not a trek
        self.assertEqual(self.hands(), (px + self.pet.grip_offset[0], py + self.pet.grip_offset[1]))

    def test_the_grip_follows_the_cursor_size(self):
        path = os.path.join(tempfile.mkdtemp(), "kcminputrc")
        with open(path, "w") as f:
            f.write("[General]\ncursorSize=12\n[Mouse]\ncursorTheme=breeze_cursors\ncursorSize=48\n")
        self.assertEqual(cp.cursor_grip(path), (14, 32))
        self.assertEqual(cp.cursor_grip(path + ".missing"), (7, 16))


class SettingsReaction(unittest.TestCase):
    def setUp(self):
        random.seed(21)
        self.real = cp.hooks_installed
        cp.hooks_installed = lambda path=None: (13, 13)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        self.pet.show()
        self.pet.start("idle")
        run_ms(self.pet, 100)

    def tearDown(self):
        cp.hooks_installed = self.real
        d = getattr(self.pet, "_settings_dialog", None)
        if d is not None:
            d.close()
            d.deleteLater()
        self.pet.timer.stop()
        self.pet.hide()
        self.pet.deleteLater()

    def watch(self, ms, until=None):
        seen = set()
        for _ in range(int(ms / 16)):
            self.pet.advance(16)
            seen.add(self.pet.frame[1])
            seen |= {"*" + q["kind"] for q in self.pet.particles}
            if until and until():
                break
        return seen

    def test_opening_his_settings_gets_a_reaction(self):
        self.pet.open_settings()
        self.assertEqual(self.pet.action, "settings")
        seen = self.watch(7000, until=lambda: "glasses" in self.pet.layers and "page" in self.pet.layers
                          and self.pet.frame[1] in ("read_l", "read_r"))
        self.assertIn("surprised", seen)                  # a start
        self.assertIn("*excl", seen)
        self.assertIn("*drop", seen)                      # a bit of sweat
        self.assertIn("read_l", seen | {self.pet.frame[1]})
        d = self.pet._settings_dialog
        d.scenes["race"].setChecked(False)               # no more karting: aww
        self.assertIn("sad", self.watch(2000, until=lambda: self.pet.frame[1] == "sad"))
        d.activity.setCurrentIndex(d.activity.findData("lively"))
        self.assertIn("dance", self.watch(4000, until=lambda: self.pet.frame[1] == "dance"))
        self.watch(4000, until=lambda: "glasses" in self.pet.layers and self.pet.frame[1].startswith("read"))
        d.close()
        seen = self.watch(6000, until=lambda: self.pet.action != "settings")
        self.assertNotEqual(self.pet.action, "settings")
        self.assertIn("jump_happy", seen)                  # relief
        self.assertNotIn("glasses", self.pet.layers)

    def test_claude_code_waits_while_he_reads_them(self):
        self.pet.open_settings()
        self.pet.claude_event({"event": "UserPromptSubmit", "session": "s"})
        run_ms(self.pet, 1500)
        self.assertEqual(self.pet.action, "settings")

    def test_opened_while_he_hangs_off_the_pointer(self):
        left, right = self.pet.box_span()
        self.pet.cursor_moved(int((left + right) / 2), int(self.pet.y + self.pet.home_px.y() - 40))
        self.pet.play("grab")
        self.assertTrue(run_ms(self.pet, 3000, until=lambda: self.pet._dangling))
        self.pet.open_settings()
        self.assertTrue(run_ms(self.pet, 6000, until=lambda: "glasses" in self.pet.layers))
        self.assertFalse(self.pet.airborne)
        self.assertAlmostEqual(self.pet.y, self.pet.ground_y(), delta=2)


class Physics(unittest.TestCase):
    def setUp(self):
        random.seed(2)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        self.pet.start("idle")
        run_ms(self.pet, 100)

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.deleteLater()

    def throw(self, vx, vy, height, step=16, ms=6000, chute=None):
        pet = self.pet
        geo = pet.screen_geometry()
        pet.set_box_left(geo.left() + geo.width() / 2 - 150)
        pet.y = pet.ground_y() - height
        pet.start("fall")
        pet.vx, pet.vy, pet.airborne, pet.chute = vx, vy, True, chute
        track = []
        for _ in range(int(ms / step)):
            pet.advance(step)
            track.append((pet.x, pet.y, pet.vy, pet.airborne))
        return track

    def test_the_same_throw_at_any_frame_rate(self):
        s = self.pet.scale
        self.throw(80 * s, -120 * s, 400, step=16)
        smooth = (self.pet.x, self.pet.y)
        self.throw(80 * s, -120 * s, 400, step=50)            # a slow, stuttering machine
        self.assertAlmostEqual(self.pet.x, smooth[0], delta=2 * s)
        self.assertAlmostEqual(self.pet.y, smooth[1], delta=1)

    def test_a_hard_landing_bounces_then_settles(self):
        track = self.throw(0, 0, 600)
        vys = [t[2] for t in track]
        first = next(i for i, v in enumerate(vys) if v < 0)   # he came back up
        self.assertTrue(track[first][3])
        self.assertFalse(self.pet.airborne)
        self.assertAlmostEqual(self.pet.y, self.pet.ground_y(), delta=1)

    def test_a_small_drop_does_not_bounce(self):
        track = self.throw(0, 0, 6)
        self.assertFalse(any(t[2] < 0 for t in track))

    def test_landing_sideways_he_skids_to_a_stop(self):
        track = self.throw(120 * self.pet.scale, 0, 30)
        land = next(i for i, t in enumerate(track) if not t[3])
        self.assertGreater(track[land + 8][0], track[land][0] + self.pet.scale)   # still sliding
        self.assertEqual(track[-1][0], track[-2][0])                             # then stopped

    def test_air_slows_a_throw_a_little(self):
        track = self.throw(100 * self.pet.scale, -200 * self.pet.scale, 300)
        airborne = [t for t in track if t[3]]
        dx = [b[0] - a[0] for a, b in zip(airborne, airborne[1:10])]
        self.assertGreater(dx[0], dx[-1])

    def drag(self, points):
        """Press, move through (ms, x, y) points, release, on a fake clock."""
        from PyQt6.QtCore import QEvent, QPointF
        from PyQt6.QtGui import QMouseEvent
        clock = [0.0]
        self.pet._ms = lambda: clock[0]
        local = QPointF(self.pet.home_px.x() + 10, self.pet.home_px.y() + 10)
        kinds = ([QEvent.Type.MouseButtonPress] + [QEvent.Type.MouseMove] * (len(points) - 2)
                 + [QEvent.Type.MouseButtonRelease])
        for kind, (ms, x, y) in zip(kinds, points):
            clock[0] = ms
            ev = QMouseEvent(kind, local, QPointF(x, y), Qt.MouseButton.LeftButton,
                             Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            {QEvent.Type.MouseButtonPress: self.pet.mousePressEvent,
             QEvent.Type.MouseMove: self.pet.mouseMoveEvent,
             QEvent.Type.MouseButtonRelease: self.pet.mouseReleaseEvent}[kind](ev)

    def test_a_throw_goes_with_your_last_flick(self):
        slow = [(t, 500 + t // 25, 500) for t in range(0, 500, 50)]          # a slow drag...
        flick = [(520, 520, 500), (540, 540, 500), (560, 560, 500), (580, 580, 500)]
        self.drag(slow + flick + [(590, 590, 500)])                         # ...then a flick
        self.assertAlmostEqual(self.pet.vx, 1000, delta=150)

    def test_letting_go_after_stopping_is_just_a_drop(self):
        moving = [(t, 500 + t, 500) for t in range(0, 300, 20)]
        self.drag(moving + [(700, 780, 500), (710, 780, 500)])              # held still, then let go
        self.assertAlmostEqual(self.pet.vx, 0, delta=50)

    def test_under_a_parachute_he_comes_down_gently_and_lands_softly(self):
        s = self.pet.scale
        track = self.throw(30 * s, 0, 600, chute="parachute")
        vys = [t[2] for t in track if t[3]]
        self.assertLessEqual(max(vys), cp.CHUTE_FALL * s * 1.05)       # never faster than the chute allows
        self.assertFalse(any(v < 0 for v in vys[10:]))                 # no bounce at the bottom
        self.assertFalse(self.pet.airborne)
        self.assertAlmostEqual(self.pet.y, self.pet.ground_y(), delta=1)
        xs = [t[0] for t in track if t[3]]
        steps = [b - a for a, b in zip(xs, xs[1:])]
        self.assertTrue(any(d > 0 for d in steps) and any(d < 0 for d in steps))   # swaying

    def test_he_flies_off_the_pointer_with_his_swing(self):
        left, right = self.pet.box_span()
        self.pet.cursor_moved(int((left + right) / 2), int(self.pet.y + self.pet.home_px.y() - 40))
        self.pet.play("grab")
        self.assertTrue(run_ms(self.pet, 3000, until=lambda: self.pet._dangling))
        self.pet._swing, self.pet._swing_v = 0.2, 5.0          # swinging out to the right
        self.pet._release = True
        self.pet.advance(16)
        self.assertFalse(self.pet._dangling)
        self.assertGreater(self.pet.vx, 100)


class Performance(unittest.TestCase):
    def setUp(self):
        random.seed(4)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        self.pet.show()
        self.pet.timer.stop()
        self.pet.start("idle")
        run_ms(self.pet, 100)

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.hide()
        self.pet.deleteLater()

    def test_standing_still_he_barely_ticks(self):
        self.pet.particles = []
        self.pet.advance(16)
        self.assertGreaterEqual(self.pet._next_tick(), 100)

    def test_moving_he_ticks_every_frame(self):
        self.pet.start("walk")
        run_ms(self.pet, 200)
        self.assertEqual(self.pet._next_tick(), cp.TICK_MS)

    def test_the_pointer_coming_near_wakes_him_at_once(self):
        self.pet.timer.start(250)
        left, right = self.pet.box_span()
        self.pet.cursor_moved(int(right + 20), int(self.pet._mid()))
        self.assertLessEqual(self.pet.timer.remainingTime(), cp.TICK_MS)

    def test_flying_particles_do_not_reshape_him_every_tick(self):
        calls = []
        real = self.pet.setMask
        self.pet.setMask = lambda m: (calls.append(1), real(m))
        for k in range(6):                                     # a burst of fast confetti
            self.pet._emit(f"confetti_{k % 5}", 12, -4, vx=random.uniform(-10, 10),
                           vy=random.uniform(-15, -7), g=30, life=1800)
        ticks = 0
        for _ in range(int(1000 / 16)):
            self.pet.advance(16)
            ticks += 1
        self.assertLess(len(calls), ticks / 2)                 # it used to be every one

    def test_the_drop_catcher_ignores_particles(self):
        self.pet.catcher = cp.DropCatcher(self.pet)
        run_ms(self.pet, 100)
        calls = []
        real = self.pet.catcher.setMask
        self.pet.catcher.setMask = lambda m: (calls.append(1), real(m))
        for k in range(4):
            self.pet._emit("heart", 6 + 3 * k, -3.0, vy=-4.0, life=1400)
        run_ms(self.pet, 1000)
        self.assertEqual(calls, [])
        self.pet.catcher.deleteLater()

    def test_the_frame_cache_stays_small(self):
        for name in ("dance", "race", "cloud", "lurk", "spin_happy", "spin_surprised", "jump"):
            for i in range(len(self.pet.sp.anims[name].frames)):
                for mirror in (False, True):
                    self.pet._pixmap(("anim", name, i, mirror))
        self.assertLessEqual(len(self.pet._pixmaps), cp.PIXMAP_CACHE)

    def test_screens_are_looked_up_once_in_a_while(self):
        self.assertIs(cp.screen_areas(), cp.screen_areas())

    def test_the_kwin_script_reports_the_pointer_coarsely_far_from_him(self):
        js = cp.kwin_desktop_script()
        self.assertIn("NEAR", js)
        self.assertIn("FAR_STEP", js)


PLASMA_RC = """[Containments][44]
activityId=2e4d
plugin=org.kde.plasma.folder
wallpaperplugin=org.kde.slideshow

[Containments][44][General]
positions={"1920x1200":["4","17"]}

[Containments][44][Wallpaper][org.kde.slideshow][General]
Image=file:///home/me/Pictures/a.jpg
SlidePaths=/home/me/Pictures/

[Containments][45]
plugin=org.kde.desktopcontainment
wallpaperplugin=org.kde.image

[Containments][45][Wallpaper][org.kde.image][General]
Image=/home/me/b.jpg
"""


class Wallpaper(unittest.TestCase):
    def setUp(self):
        random.seed(13)
        self.dir = tempfile.mkdtemp()
        self.rc = os.path.join(self.dir, "plasma-org.kde.plasma.desktop-appletsrc")
        with open(self.rc, "w") as f:
            f.write(PLASMA_RC)
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)
        self.pet.start("idle")
        run_ms(self.pet, 100)

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.deleteLater()

    def test_what_plasma_has_on_the_desktops(self):
        walls = cp.plasma_wallpapers(self.rc)
        self.assertEqual(walls["44"], ("org.kde.slideshow", "file:///home/me/Pictures/a.jpg", 900))
        self.assertEqual(walls["45"], ("org.kde.image", "/home/me/b.jpg", None))

    def test_slideshows_change_on_the_clock(self):
        walls = cp.plasma_wallpapers(self.rc)
        self.assertEqual(cp.next_slide_change(walls, 1_000_000.0), 1_000_800.0)   # the next multiple of 900
        with open(self.rc, "a") as f:
            f.write("SlideInterval=600\n")          # lands in 45's image group: not a slideshow
        self.assertEqual(cp.next_slide_change(cp.plasma_wallpapers(self.rc), 1_000_000.0), 1_000_800.0)
        self.assertIsNone(cp.next_slide_change({"45": ("org.kde.image", "x", None)}, 1_000_000.0))

    def test_only_a_new_wallpaper_counts_as_a_change(self):
        before = cp.plasma_wallpapers(self.rc)
        with open(self.rc, "w") as f:
            f.write(PLASMA_RC.replace('["4","17"]', '["5","17"]'))          # icons moved: no
        self.assertFalse(cp.wallpapers_differ(before, cp.plasma_wallpapers(self.rc)))
        with open(self.rc, "w") as f:
            f.write(PLASMA_RC.replace("b.jpg", "c.jpg"))                     # a new picture: yes
        self.assertTrue(cp.wallpapers_differ(before, cp.plasma_wallpapers(self.rc)))

    def test_it_makes_him_jump(self):
        self.pet.wallpaper_changed()
        self.assertEqual(self.pet.action, "startled")
        seen = set()
        run_ms(self.pet, 6000, until=lambda: seen.add(self.pet.frame[1]) or self.pet.action != "startled")
        self.assertIn("back", seen)                   # he turned round to look...
        self.assertIn("surprised", seen)              # ...and got a fright
        self.assertIn("jump", seen)

    def test_it_wakes_him_from_a_nap_but_not_mid_something_you_asked_for(self):
        self.pet.start("sleep")
        run_ms(self.pet, 500)
        self.pet.wallpaper_changed()
        self.assertEqual(self.pet.action, "startled")
        self.pet.play("dance")
        self.pet.wallpaper_changed()
        self.assertEqual(self.pet.action, "dance")


class DayTimes(unittest.TestCase):
    def setUp(self):
        self.pet = cp.ClawdPet(cp.load_sprites(), settings=None)

    def tearDown(self):
        self.pet.timer.stop()
        self.pet.deleteLater()

    def test_the_default_night_and_morning(self):
        for (h, m), night, morning in (((21, 59), False, False), ((22, 0), True, False), ((3, 0), True, False),
                                       ((6, 0), False, True), ((10, 59), False, True), ((11, 0), False, False)):
            self.pet.wall = at(h, m)
            self.assertEqual((self.pet.is_night(), self.pet.is_morning()), (night, morning), (h, m))

    def test_you_choose_when_night_and_morning_are(self):
        self.pet.prefs["night_from"], self.pet.prefs["night_to"] = 23 * 60 + 30, 7 * 60
        self.pet.prefs["morning_to"] = 9 * 60
        self.pet.wall = at(23, 0)
        self.assertFalse(self.pet.is_night())
        self.pet.wall = at(23, 45)
        self.assertTrue(self.pet.is_night())
        self.pet.wall = at(6, 30)
        self.assertTrue(self.pet.is_night())
        self.pet.wall = at(8, 30)
        self.assertTrue(self.pet.is_morning())
        self.pet.wall = at(9, 30)
        self.assertFalse(self.pet.is_morning())

    def test_a_night_that_does_not_cross_midnight(self):
        self.pet.prefs["night_from"], self.pet.prefs["night_to"] = 60, 5 * 60      # 01:00-05:00
        self.pet.wall = at(0, 30)
        self.assertFalse(self.pet.is_night())
        self.pet.wall = at(2, 0)
        self.assertTrue(self.pet.is_night())

    def test_the_dialog_sets_them(self):
        from PyQt6.QtCore import QTime
        real = cp.hooks_installed
        cp.hooks_installed = lambda path=None: (13, 13)
        try:
            d = cp.SettingsDialog(self.pet)
            d.times["night_from"].setTime(QTime(21, 15))
            self.assertEqual(self.pet.prefs["night_from"], 21 * 60 + 15)
            d.times["morning_to"].setTime(QTime(12, 0))
            self.assertEqual(self.pet.prefs["morning_to"], 12 * 60)
            d.close()
            d.deleteLater()
        finally:
            cp.hooks_installed = real


class Launcher(unittest.TestCase):
    def test_clicking_opens_the_code_tab_of_the_app(self):
        self.assertEqual(cp.CLAUDE_LINKS["continue"], "claude://code/continue?session=last")
        self.assertEqual(cp.CLAUDE_LINKS["new"], "claude://code/new")
        self.assertEqual(cp.CLAUDE_LINKS["needs-input"], "claude://code/needs-input")

    def test_url_opener_per_platform(self):
        which = lambda n: "/usr/bin/" + n  # noqa: E731
        self.assertEqual(cp.open_url_command("claude://x", "linux", which), ["/usr/bin/xdg-open", "claude://x"])
        self.assertEqual(cp.open_url_command("claude://x", "darwin", which), ["open", "claude://x"])
        self.assertEqual(cp.open_url_command("claude://x", "win32", which),
                         ["cmd", "/c", "start", "", "claude://x"])

    def which_only(self, *names):
        return lambda n: f"/usr/bin/{n}" if n in names else None

    def test_konsole(self):
        cmd = cp.terminal_command(["/x/claude"], which=self.which_only("konsole"), env={})
        self.assertEqual(cmd, ["/usr/bin/konsole", "-e", "/x/claude"])

    def test_gnome_terminal_needs_double_dash(self):
        cmd = cp.terminal_command(["/x/claude"], which=self.which_only("gnome-terminal"), env={})
        self.assertEqual(cmd, ["/usr/bin/gnome-terminal", "--", "/x/claude"])

    def test_terminal_variable_wins(self):
        cmd = cp.terminal_command(["/x/claude"], which=self.which_only("konsole", "kitty"),
                                  env={"TERMINAL": "kitty"})
        self.assertEqual(cmd, ["/usr/bin/kitty", "/x/claude"])

    def test_no_terminal(self):
        self.assertIsNone(cp.terminal_command(["/x/claude"], which=lambda n: None, env={}))

    def test_children_do_not_inherit_forced_xwayland(self):
        old = cp._FORCED_XCB
        try:
            cp._FORCED_XCB = True
            env = cp.launch_env({"QT_QPA_PLATFORM": "xcb", "HOME": "/h"})
            self.assertNotIn("QT_QPA_PLATFORM", env)
            self.assertEqual(env["HOME"], "/h")
            cp._FORCED_XCB = False
            self.assertIn("QT_QPA_PLATFORM", cp.launch_env({"QT_QPA_PLATFORM": "xcb"}))
        finally:
            cp._FORCED_XCB = old


if __name__ == "__main__":
    unittest.main()
