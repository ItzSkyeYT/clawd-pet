"""
Collect Anthropic's official Clawd animations into assets/official/.

Sources:
  - claude.ai static images that the Claude desktop/web app itself loads
  - the Claude desktop app bundle installed on this machine (Clawd-Laptop)

These files are Anthropic's art: fine as reference for a pet on your own
desktop, but keep them out of public repos (assets/ is git-ignored).

Usage:  python tools/fetch_official.py [--force]
"""

import glob
import os
import shutil
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "official")

BASE = "https://claude.ai"
REMOTE = {
    "Clawd-CrabWalking.gif": "/images/clawd/core/Clawd-CrabWalking.gif",
    "Clawd-Waving.gif": "/images/clawd/core/Clawd-Waving.gif",
    "Clawd-Lurking.gif": "/images/clawd/core/Clawd-Lurking.gif",
    "Clawd-Jumping.gif": "/images/clawd/core/Clawd-Jumping.gif",
    "Clawd-JumpingHappy.gif": "/images/clawd/core/Clawd-JumpingHappy.gif",
    "Clawd-JumpingHappy-large.gif": "/images/home-page-assets/Clawd-JumpingHappy.gif",
    "Clawd-Dancing.gif": "/images/spotlights/claude-code-celebration/Clawd-Dancing.gif",
    "Clawd-RacingCar.gif": "/images/clawd/persona/Clawd-RacingCar.gif",
    "Clawd-Cloud-once.gif": "/images/clawd/persona/Clawd-Cloud-once.gif",
    "Clawd-Cloud-still.png": "/images/clawd/persona/Clawd-Cloud-still.png",
    "clawd.svg": "/images/clawd.svg",
    "clawd-guest-pass.svg": "/images/clawd-guest-pass.svg",
}

DESKTOP_WEB = "/usr/lib/claude-desktop/resources/ion-dist"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")

# The web app answers unknown paths with its HTML shell and a 200, so the
# content type is the only reliable "does this file exist" signal.
EXPECTED = {".gif": "image/gif", ".png": "image/png", ".svg": "image/svg+xml"}


def fetch(name, path, force):
    dest = os.path.join(OUT, name)
    if os.path.exists(dest) and not force:
        return "kept", os.path.getsize(dest)
    req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        ctype = r.headers.get("Content-Type", "").split(";")[0].strip()
        want = EXPECTED[os.path.splitext(name)[1]]
        if ctype != want:
            raise RuntimeError(f"expected {want}, got {ctype!r}")
        data = r.read()
    with open(dest, "wb") as f:
        f.write(data)
    return "downloaded", len(data)


def copy_local(force):
    """Clawd-Laptop ships inside the desktop app: a webm render and a Lottie."""
    found = []
    webm = os.path.join(DESKTOP_WEB, "images", "install-hub", "clawd-laptop.webm")
    if os.path.exists(webm):
        found.append((webm, "Clawd-Laptop.webm"))
    # The Lottie's filename is a build hash, so find it by its layer name.
    for fn in glob.glob(os.path.join(DESKTOP_WEB, "assets", "**", "*.json"), recursive=True):
        with open(fn, "rb") as f:
            if b'"nm":"Clawd-Laptop"' in f.read(4096):
                found.append((fn, "Clawd-Laptop.lottie.json"))
                break
    out = []
    for src, name in found:
        dest = os.path.join(OUT, name)
        if force or not os.path.exists(dest):
            shutil.copyfile(src, dest)
            out.append((name, "copied", os.path.getsize(dest)))
        else:
            out.append((name, "kept", os.path.getsize(dest)))
    return out


def main():
    force = "--force" in sys.argv
    os.makedirs(OUT, exist_ok=True)
    total = 0
    failed = []
    for name, path in REMOTE.items():
        try:
            status, size = fetch(name, path, force)
            total += size
            print(f"{status:>10}  {size/1024:8.1f} KB  {name}")
        except Exception as e:  # keep going; report at the end
            failed.append(name)
            print(f"{'FAILED':>10}  {'':>11}  {name}: {e}")
    if os.path.isdir(DESKTOP_WEB):
        for name, status, size in copy_local(force):
            total += size
            print(f"{status:>10}  {size/1024:8.1f} KB  {name}")
    else:
        print("Claude desktop app not found; skipped Clawd-Laptop")
    print(f"total {total/1024/1024:.2f} MB in {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
