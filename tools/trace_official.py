"""
Trace Anthropic's official Clawd animations into pixel grids for the pet.

Every official animation is pixel art drawn on the same 2750x1850 canvas with
100px "pixels", and details (eyes, props, sparks) on a 50px half-grid. The
smaller 1189x800 exports are the same canvas scaled by 0.4324. This script:

  1. finds each file's half-grid (cell size + phase) from its colour edges,
  2. samples every cell at its centre, so rounding and dithering at the
     edges never matter,
  3. snaps colours to one shared palette,
  4. crops, dedupes held frames, and records where the idle Clawd stands,
  5. checks fidelity: how many pixels inside cells disagree with the cell.

Output: sprites/clawd.json (committed; the app reads it at startup).
Needs: Pillow, numpy, ffmpeg (for the webm).

Usage:  python tools/trace_official.py [--preview DIR]
"""

import json
import os
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image, ImageSequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
OUT = os.path.join(ROOT, "sprites", "clawd.json")

# name -> (file under assets/, description, playback segments)
# Segments index the deduped frame list: "loop" repeats for as long as the pet
# wants (walking, typing, driving), then "outro" plays; without them the
# animation plays once. The indices were read off each animation's sequence of
# repeated frames; they only change if Anthropic's files change.
SOURCES = {
    "walk":       ("official/Clawd-CrabWalking.gif", "turns 3/4 and crab-walks",
                   {"loop": [1, 8]}),
    "wave":       ("official/Clawd-Waving.gif", "crouches, turns, waves", {}),
    "lurk":       ("official/Clawd-Lurking.gif", "peeks in from the left edge", {}),
    "jump":       ("official/Clawd-Jumping.gif", "crouch, leap with arms up, land", {}),
    "jump_happy": ("official/Clawd-JumpingHappy.gif", "same leap with ^ ^ eyes", {}),
    "dance":      ("official/Clawd-Dancing.gif", "happy sway dance",
                   {"loop": [1, 19], "outro": [0, 0]}),
    "race":       ("official/Clawd-RacingCar.gif", "helmet on, drives a kart",
                   {"loop": [9, 24], "outro": [25, 39]}),
    "cloud":      ("official/Clawd-Cloud-once.gif", "rides a flying cloud",
                   {"loop": [9, 17], "outro": [38, 52]}),
    "laptop":     ("official/Clawd-Laptop.webm", "winks, pulls out a laptop, types",
                   {"loop": [12, 14], "outro": [27, 35]}),
    "sparkler":   ("clawd_sparkle.gif", "holds up a sparkler", {}),
}

CANVAS_W = 2750          # the design canvas all exports come from
HALF = 50.0              # half-grid cell on that canvas
WEBM_FPS = 12

# Palette keys, most common colour first. '.' is transparent.
KEYS = "#@%&*+=abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


# ── Loading ─────────────────────────────────────────────────────────

def load_gif(path):
    im = Image.open(path)
    frames, durations = [], []
    for fr in ImageSequence.Iterator(im):
        frames.append(np.array(fr.convert("RGBA")))
        durations.append(int(fr.info.get("duration", 83)) or 83)
    return frames, durations


def load_webm(path):
    with tempfile.TemporaryDirectory() as tmp:
        # libvpx-vp9 is needed to decode the alpha channel
        subprocess.run(["ffmpeg", "-v", "error", "-c:v", "libvpx-vp9", "-i", path,
                        os.path.join(tmp, "f_%04d.png")], check=True)
        names = sorted(os.listdir(tmp))
        frames = [np.array(Image.open(os.path.join(tmp, n)).convert("RGBA")) for n in names]
    return frames, [round(1000 / WEBM_FPS)] * len(frames)


def knock_out_background(frames):
    """The sparkler GIF has an opaque, dithered ivory background: make it clear."""
    out = []
    for a in frames:
        a = a.copy()
        rgb = a[..., :3].astype(int)
        ivory = (rgb[..., 0] > 240) & (rgb[..., 1] > 240) & (rgb[..., 2] > 240)
        # The ivory is dithered with pale-yellow dots, the same colour as the
        # pale sparks. A dot is background when most of its neighbours are ivory.
        pale = (rgb[..., 0] > 240) & (rgb[..., 1] > 240) & ~ivory
        p = np.pad(ivory, 1)
        ivory_neighbours = (p[:-2, 1:-1].astype(int) + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:])
        bg = ivory | (pale & (ivory_neighbours >= 3))
        a[..., 3] = np.where(bg, 0, 255)
        out.append(a)
    return out


# ── Grid detection ──────────────────────────────────────────────────

def edge_positions(frames):
    xs, ys = [], []
    for a in frames[::2]:
        op = a[..., 3] > 127
        rgb = a[..., :3].astype(np.int16)
        dx = (op[:, 1:] != op[:, :-1]) | (op[:, 1:] & op[:, :-1] &
                                          (np.abs(rgb[:, 1:] - rgb[:, :-1]).sum(-1) > 120))
        dy = (op[1:] != op[:-1]) | (op[1:] & op[:-1] &
                                    (np.abs(rgb[1:] - rgb[:-1]).sum(-1) > 120))
        xs.append(np.where(dx)[1] + 1)
        ys.append(np.where(dy)[0] + 1)
    return np.concatenate(xs).astype(float), np.concatenate(ys).astype(float)


def fit_axis(pos, h0):
    """Best cell size near h0 and its phase, by circular concentration."""
    best = (-1, h0, 0.0)
    for h in np.linspace(h0 * 0.995, h0 * 1.005, 81):
        ang = 2 * np.pi * (pos % h) / h
        c, s = np.cos(ang).mean(), np.sin(ang).mean()
        r = np.hypot(c, s)
        if r > best[0]:
            best = (r, h, (np.arctan2(s, c) % (2 * np.pi)) / (2 * np.pi) * h)
    return best


def detect_grid(frames):
    height, width = frames[0].shape[:2]
    xs, ys = edge_positions(frames)
    rx, hx, px = fit_axis(xs, HALF * width / CANVAS_W)
    ry, hy, py = fit_axis(ys, HALF * height / 1850)
    return dict(hx=hx, hy=hy, px=px, py=py, rx=rx, ry=ry)


# ── Sampling ────────────────────────────────────────────────────────

def cell_centres(phase, h, size, win):
    """Centres of every cell whose centre lies on the image.

    The exports crop the canvas mid-cell (Clawd's feet lose their last pixel
    row at the bottom edge), so partial edge cells must be kept.
    """
    k0 = int(np.floor(-phase / h - 0.5)) + 1
    k1 = int(np.floor((size - 1 - phase) / h - 0.5))
    return (phase + (np.arange(k0, k1 + 1) + 0.5) * h).round().astype(int)


def cell_means(a, g):
    """Mean colour and opacity of the central window of every cell."""
    height, width = a.shape[:2]
    win_x = max(1, int(g["hx"] * 0.25))
    win_y = max(1, int(g["hy"] * 0.25))
    cx = cell_centres(g["px"], g["hx"], width, win_x)
    cy = cell_centres(g["py"], g["hy"], height, win_y)
    ncols, nrows = len(cx), len(cy)
    rgb = np.zeros((nrows, ncols, 3))
    opq = np.zeros((nrows, ncols))
    for j, y in enumerate(cy):
        for i, x in enumerate(cx):
            w = a[max(0, y - win_y):y + win_y + 1, max(0, x - win_x):x + win_x + 1].reshape(-1, 4)
            on = w[:, 3] > 127
            opq[j, i] = on.mean()
            if on.any():
                rgb[j, i] = w[on, :3].mean(0)
    return rgb, opq, cx, cy


def cluster(colors, thresh=28.0):
    """Greedy colour clustering; returns centres sorted by population."""
    centres, counts = [], []
    for c in colors:
        if centres:
            d = np.linalg.norm(np.array(centres) - c, axis=1)
            k = int(d.argmin())
            if d[k] < thresh:
                n = counts[k]
                centres[k] = (np.array(centres[k]) * n + c) / (n + 1)
                counts[k] += 1
                continue
        centres.append(np.array(c, float))
        counts.append(1)
    order = np.argsort(counts)[::-1]
    return [centres[k] for k in order], [counts[k] for k in order]


# ── Main ────────────────────────────────────────────────────────────

def main():
    preview = None
    if "--preview" in sys.argv:
        preview = sys.argv[sys.argv.index("--preview") + 1]
        os.makedirs(preview, exist_ok=True)

    traced = {}
    all_colors = []
    for name, (rel, desc, segments) in SOURCES.items():
        path = os.path.join(ASSETS, rel)
        if not os.path.exists(path):
            print(f"skip {name}: {rel} missing (run tools/fetch_official.py)")
            continue
        frames, durations = load_webm(path) if path.endswith(".webm") else load_gif(path)
        if name == "sparkler":
            frames = knock_out_background(frames)
        g = detect_grid(frames)
        cells = [cell_means(a, g) for a in frames]
        for rgb, opq, _, _ in cells:
            all_colors.append(rgb[opq >= 0.5])
        traced[name] = dict(desc=desc, frames=frames, durations=durations, grid=g, cells=cells,
                            segments=segments)
        print(f"{name:10s} {len(frames):3d} frames  cell {g['hx']:.2f}x{g['hy']:.2f}px "
              f"phase ({g['px']:.1f},{g['py']:.1f})  fit ({g['rx']:.3f},{g['ry']:.3f})")

    # One palette for everything, so all animations share colours exactly.
    colors = np.concatenate(all_colors)
    rng = np.random.default_rng(0)
    sample = colors[rng.permutation(len(colors))[:60000]]
    centres, counts = cluster(sample)
    # The sparkler GIF uses pure black where the rest use Anthropic's
    # near-black #141413; they are the same ink, so fold dark clusters together.
    dark = [i for i, c in enumerate(centres) if max(c) < 40]
    for i in dark[1:]:
        counts[dark[0]] += counts[i]
        counts[i] = 0
    keep = [c for c, n in zip(centres, counts) if n >= 3]
    palette = np.array([np.round(c) for c in keep])
    print(f"palette: {len(palette)} colours")
    for k, c in zip(KEYS, palette):
        print(f"   {k}  #{int(c[0]):02x}{int(c[1]):02x}{int(c[2]):02x}")

    out = {"palette": {k: "#%02x%02x%02x" % tuple(int(v) for v in c)
                       for k, c in zip(KEYS, palette)},
           "animations": {}}

    grids = {}
    for name, t in traced.items():
        seq = []
        for rgb, opq, _, _ in t["cells"]:
            d = np.linalg.norm(rgb[..., None, :] - palette[None, None], axis=-1)
            idx = d.argmin(-1) + 1
            idx[opq < 0.5] = 0
            seq.append(idx)
        grids[name] = np.array(seq)

    # The idle pose is Jumping's first frame; find it in every animation so
    # they can all be drawn with Clawd's feet in the same spot.
    j0 = grids["jump"][0]
    ys, xs = np.nonzero(j0)
    idle = j0[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    ih, iw = idle.shape

    def find_idle(frame):
        H, W = frame.shape
        for y in range(H - ih + 1):
            for x in range(W - iw + 1):
                if np.array_equal(frame[y:y + ih, x:x + iw], idle):
                    return x, y
        return None

    fidelity = {}
    for name, t in traced.items():
        G = grids[name]
        occ = np.nonzero(G.any(axis=0))
        y0, y1, x0, x1 = occ[0].min(), occ[0].max() + 1, occ[1].min(), occ[1].max() + 1
        home = None
        for f in G:
            home = find_idle(f)
            if home:
                break
        if home is None:
            # Lurking never shows the full idle pose: put its ground line on
            # the idle ground line and let its left edge be the screen edge.
            ground = max(np.nonzero(f.any(axis=1))[0].max() for f in G if f.any())
            home = (x0, ground + 1 - ih)
        C = G[:, y0:y1, x0:x1]
        frames = []
        for f, ms in zip(C, t["durations"]):
            rows = ["".join("." if v == 0 else KEYS[v - 1] for v in r) for r in f]
            if frames and frames[-1]["rows"] == rows:
                frames[-1]["ms"] += ms       # merge held frames
            else:
                frames.append({"ms": ms, "rows": rows})
        out["animations"][name] = {
            "desc": t["desc"],
            "size": [int(x1 - x0), int(y1 - y0)],
            "home": [int(home[0] - x0), int(home[1] - y0)],
            "frames": frames,
            **t["segments"],
        }
        fidelity[name] = measure_fidelity(t, G, palette)
        print(f"{name:10s} -> {len(frames):2d} unique frames, {x1-x0}x{y1-y0} cells, "
              f"home {out['animations'][name]['home']}, "
              f"cells disagreeing with source: {fidelity[name]:.2%}")

    out["idle_size"] = [int(iw), int(ih)]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"wrote {OUT} ({os.path.getsize(OUT)/1024:.0f} KB)")

    if preview:
        write_previews(out, preview)
    bad = {k: v for k, v in fidelity.items() if v > 0.02}
    if bad:
        print("FIDELITY WARNING:", bad)
        return 1
    return 0


def measure_fidelity(t, G, palette):
    """Share of cells whose inner pixels mostly disagree with the traced value."""
    g = t["grid"]
    _, _, cx, cy = t["cells"][0]
    wrong = total = 0
    for a, f in list(zip(t["frames"], G))[::3]:
        ys, xs = np.nonzero(f)
        for y, x in zip(ys, xs):
            # inner 60% of the cell, well clear of rounded edges
            hx, hy = g["hx"] * 0.3, g["hy"] * 0.3
            win = a[int(cy[y] - hy):int(cy[y] + hy), int(cx[x] - hx):int(cx[x] + hx)].reshape(-1, 4)
            on = win[:, 3] > 127
            if on.mean() < 0.5:
                wrong += 1
            else:
                d = np.linalg.norm(win[on, :3][:, None].astype(float) - palette[None], axis=-1)
                if (d.argmin(-1) + 1 == f[y, x]).mean() < 0.5:
                    wrong += 1
            total += 1
    return wrong / max(total, 1)


def write_previews(data, folder):
    pal = {k: tuple(int(v[i:i + 2], 16) for i in (1, 3, 5)) for k, v in data["palette"].items()}
    for name, anim in data["animations"].items():
        w, h = anim["size"]
        s = 4
        cols = min(8, len(anim["frames"]))
        rows = (len(anim["frames"]) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * (w * s + 4), rows * (h * s + 4)), (60, 60, 60))
        for n, fr in enumerate(anim["frames"]):
            tile = Image.new("RGB", (w, h), (60, 60, 60))
            px = tile.load()
            for y, row in enumerate(fr["rows"]):
                for x, ch in enumerate(row):
                    if ch != ".":
                        px[x, y] = pal[ch]
            hx, hy = anim["home"]
            tile = tile.resize((w * s, h * s), Image.NEAREST)
            sheet.paste(tile, ((n % cols) * (w * s + 4), (n // cols) * (h * s + 4)))
        sheet.save(os.path.join(folder, f"traced_{name}.png"))


if __name__ == "__main__":
    sys.exit(main())
