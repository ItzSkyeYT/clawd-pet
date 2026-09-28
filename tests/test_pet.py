"""
Tests for the Clawd pet. Run from the project root:

    python -m unittest discover -s tests -v

They use Qt's offscreen platform with two screens laid out like the real
desktop (tests/screens.json: a 1080p monitor on the right as primary, a
taller laptop screen on the left, 330px lower), so nothing appears on screen.
Set CLAWD_SNAPSHOTS=/some/dir to also save a PNG of every animation's frames.
"""

import json
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

from PyQt6.QtCore import QRect, QSettings, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

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

    def test_closing_the_window_under_him_drops_him(self):
        self.pet.windows_changed([[400, 900, 600, 400, 3, 0, 1, "eDP-1", "a"]])
        self.stand_on(600, 900)
        run_ms(self.pet, 200)
        self.pet.windows_changed([])
        run_ms(self.pet, 5000, until=lambda: self.pet.action == "idle" and not self.pet.airborne)
        self.assertAlmostEqual(self.feet(), 1530, delta=1)

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
        self.assertEqual(props, {"page", "glasses"})
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
        self.assertEqual(self.pet._rest_range(), (cp.REST[0] / 2, cp.REST[1] / 2))

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

    def test_a_nightcap_for_sleeping_at_night(self):
        self.pet.wall = at(23, 30)
        self.pet.start("sleep")
        self.assertEqual(self.pet.hat(), "nightcap")
        self.pet.wall = at(14)
        self.assertIsNone(self.pet.hat())

    def test_the_hat_sits_on_his_head_in_every_frame(self):
        self.pet.prefs["hat"] = "santa_hat"
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

    def test_water_time_interrupts_what_he_is_doing(self):
        self.pet.claude_event({"event": "UserPromptSubmit", "session": "r"})
        run_ms(self.pet, 500)
        self.assertEqual(self.pet.action, "work")
        self.water_due()
        run_ms(self.pet, 1100)
        self.assertEqual(self.pet.action, "remind_water")
        self.pet.claude_event({"event": "PreToolUse", "session": "r", "tool": "Read"})
        run_ms(self.pet, 500)
        self.assertEqual(self.pet.action, "remind_water")          # Claude Code can wait
        self.pet.claude_event({"event": "PermissionRequest", "session": "r", "tool": "Bash"})
        run_ms(self.pet, 100)
        self.assertEqual(self.pet.action, "attention")             # ...but not a permission

    def test_he_hops_waving_the_bottle_and_holds_it_out(self):
        self.water_due()
        run_ms(self.pet, 1100)
        lifts, bubble, droplets = set(), False, False
        for _ in range(int(5000 / 16)):
            self.pet.advance(16)
            if "water_bottle" in self.pet.layers and self.pet.frame[1] == "cheer":
                self.assertEqual(self.pet.layers["water_bottle"][1], cp.BOTTLE_UP[1] - self.pet.lift)
                lifts.add(self.pet.lift)
            bubble = bubble or any(q["kind"] == "water_bubble" for q in self.pet.particles)
            droplets = droplets or any(q["kind"].startswith("droplet") for q in self.pet.particles)
        self.assertIn(5, lifts)
        self.assertTrue(bubble and droplets)

    def test_he_runs_over_to_you_first(self):
        geo = self.pet.screen_geometry()
        self.pet.set_box_left(geo.left() + 100)
        far = geo.left() + geo.width() - 200
        self.pet.cursor_moved(far, geo.top() + 300)
        self.water_due()
        run_ms(self.pet, 1100)
        self.assertEqual(self.pet.action, "remind_water")
        self.assertTrue(run_ms(self.pet, 30_000, until=lambda: self.pet.frame[1] == "cheer"))
        left, right = self.pet.box_span()
        self.assertLess(abs((left + right) / 2 - far), 300)

    def test_a_click_says_seen_it_and_he_drinks(self):
        self.water_due()
        run_ms(self.pet, 1100)
        click(self.pet)
        self.assertEqual(self.pet.action, "drink")
        self.assertEqual(self.launched, [])                         # no Claude Code this time
        self.assertEqual(self.pet._water_at, self.pet.now)
        self.assertTrue(run_ms(self.pet, 8000, until=lambda: self.pet.action != "drink"))
        self.assertEqual(self.pet.lift, 0)
        click(self.pet)
        self.assertEqual(self.launched, ["continue"])               # back to normal

    def test_one_reminder_at_a_time(self):
        self.water_due()
        self.pet._streak_at = self.pet.now - 61 * 60_000            # a break is due too
        run_ms(self.pet, 1100)
        self.assertEqual(self.pet.action, "remind_water")
        click(self.pet)
        self.assertTrue(run_ms(self.pet, 8000, until=lambda: self.pet.action != "drink"))
        run_ms(self.pet, 2000)
        self.assertNotEqual(self.pet.action, "remind_break")        # not straight after
        self.pet.now += 5 * 60_000
        self.pet._input_at = self.pet.now
        self.assertEqual(self.pet._due_reminder(), "remind_break")

    def test_ignored_it_comes_back_later(self):
        self.water_due()
        run_ms(self.pet, 1100)
        self.assertTrue(run_ms(self.pet, cp.REMIND_FOR["water"] + 6000,
                               until=lambda: self.pet.action != "remind_water"))
        self.assertIsNone(self.pet._due_reminder())
        self.pet.now = self.pet._snooze["water"] + 1
        self.pet._input_at = self.pet.now
        self.assertEqual(self.pet._due_reminder(), "remind_water")
        for _ in range(2):                                          # third time unanswered: count again
            self.pet._ignored("water")
        self.assertIsNone(self.pet._due_reminder())
        self.assertEqual(self.pet._water_at, self.pet.now)

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
        run_ms(self.pet, 1100)
        self.assertEqual(self.pet.action, "remind_break")
        self.assertTrue(any(q["kind"] == "break_bubble" for q in self.pet.particles))
        click(self.pet)
        self.assertEqual(self.pet.action, "coffee")                 # he takes one with you
        self.assertEqual(self.pet._streak_at, self.pet.now)

    def test_coming_back_from_a_break_restarts_the_count(self):
        self.pet._streak_at = self.pet._water_at = 0.0
        self.pet.now += cp.AWAY + 60_000
        self.pet.cursor_moved(500, 500)
        self.assertEqual(self.pet._streak_at, self.pet.now)
        self.assertEqual(self.pet._water_at, self.pet.now)

    def test_the_menu_previews_run_by_themselves(self):
        self.pet.play("remind_water")
        self.assertTrue(self.pet.manual)
        self.assertTrue(run_ms(self.pet, 50_000, until=lambda: self.pet.action != "remind_water"))


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
