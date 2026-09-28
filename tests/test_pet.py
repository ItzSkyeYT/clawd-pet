"""
Tests for the Clawd pet. Run from the project root:

    python -m unittest discover -s tests -v

They use Qt's offscreen platform, so nothing appears on screen. Set
CLAWD_SNAPSHOTS=/some/dir to also save a PNG of every animation's frames.
"""

import json
import os
import random
import sys
import unittest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

import claude_pet as cp  # noqa: E402


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
                self.assertAlmostEqual(self.pet.y, self.pet.ground_y(), delta=1)
                left, right = self.pet.box_span()
                self.assertGreaterEqual(left, self.geo.left() - 1, action)
                self.assertLessEqual(right, self.geo.left() + self.geo.width() + 1, action)

    def test_idle_eventually_picks_actions_and_stays_on_screen(self):
        seen = set()
        for _ in range(int(20 * 60_000 / 16)):          # twenty simulated minutes
            self.pet.advance(16)
            seen.add(self.pet.action)
            if self.pet.action in ("idle",):
                left, right = self.pet.box_span()
                self.assertGreaterEqual(left, self.geo.left() - 1)
                self.assertLessEqual(right, self.geo.left() + self.geo.width() + 1)
        self.assertGreaterEqual(len(seen - {"idle"}), 4, f"only saw {seen}")

    def test_dropping_him_lands_on_the_ground(self):
        self.pet.y = self.pet.ground_y() - 300
        self.pet.x += 40
        self.pet.drop(vx=250, vy=-100)
        self.assertEqual(self.pet.action, "fall")
        self.run_until_idle()
        self.assertEqual(self.pet.action, "idle")
        self.assertAlmostEqual(self.pet.y, self.pet.ground_y(), delta=1)

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


class Launcher(unittest.TestCase):
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
