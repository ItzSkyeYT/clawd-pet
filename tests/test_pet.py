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
sys.path.insert(0, ROOT)

from PyQt6.QtCore import QRect, Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

import claude_pet as cp  # noqa: E402

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
        c.close()


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

    def test_kwin_script_reports_to_our_service(self):
        js = cp.kwin_cursor_script()
        self.assertIn("cursorPosChanged", js)
        self.assertIn(cp.DBUS_SERVICE, js)


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
