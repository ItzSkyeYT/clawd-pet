"""Birthday party art for Clawd, on the same cell grid as sprites/clawd.json and
sprites/extras.py (one cell = half an official Clawd pixel; his idle pose is 24x16
cells, head at columns 4-19, eyes 2x2 at (6, 2) and (16, 2)).

BIRTHDAY maps a name to:
  "frames":  list of frames; each frame is a list of equal-length strings of
             palette keys, "." = transparent. For "balloon" each frame is a
             different balloon, not an animation step. For "letters" it is a
             dict instead: character -> rows (one glyph each).
  "palette": single-character key -> "#rrggbb".
  plus the placement fields described above each entry.

Style follows extras.py: flat colours, no outlines, light from the upper right
(shade on the left and bottom, highlights on the upper right), Anthropic's
palette. Everything is checked on light (#dcdcdc) and dark (#282828) desktops at
4 and 8 px per cell. Plain data only: read with ast.literal_eval.
"""

BIRTHDAY = {
    # Birthday cake (20x16, 3 frames) he holds in front of him: strawberry-pink sponge
    # with ivory frosting dripping down the front, sprinkles on top, ivory pearls
    # round the base, a sky-blue plate, and three striped candles (blue, pink, blue)
    # that stand between his eyes. 0 and 1: lit, alternate them to flicker (the middle
    # flame leans against the outer two). 2: blown out, a burnt wick on each candle.
    # "hold": the cake's bottom-centre cell (right of its centre line). Put it on idle
    # cell (12, 15): the cake then covers idle x 2-21, y 0-15, his arm tips poke out
    # on both sides, his eyes stay clear and the flames burn between them.
    # "flames": the top cell of each flame in frame 0. "wicks": each wick in frame 2;
    # smoke rises from the cell above a wick (see "smoke").
    "cake": {
        "flames": [[7, 0], [9, 0], [12, 0]],
        "wicks": [[7, 2], [9, 2], [12, 2]],
        "hold": [10, 15],
        "palette": {"Y": "#eec875", "y": "#fdf3cf", "@": "#141413", "S": "#6a9bcc", "W": "#faf9f5",
                    "V": "#efe7dc", "w": "#dccfbf", "P": "#ec7f8c", "p": "#cf5f6e", "Q": "#f6a9b2",
                    "C": "#9dbfe0", "c": "#c5d3e0", "s": "#52799f", "L": "#788c5d", "R": "#e85b6a"},
        "frames": [
            # 0: lit
            [
                ".......Y.Y..Y.......",
                "......YY.YY.YY......",
                "......Yy.yY.yY......",
                "......SS.RR.SS......",
                "......WW.WW.WW......",
                "......SS.RR.SS......",
                "...WSWWWWWWWWWLWW...",
                ".VRWWLWWSWWRWWWWLWW.",
                ".wwVVVVVVVVVVVVVVWW.",
                ".wwVPPVVVPPPVVPPVWW.",
                ".pwPPPVVPPPPVPPPPWQ.",
                ".ppPPPPVPPPPPPPPPPQ.",
                ".ppPPPPPPPPPPPPPPPQ.",
                ".ppWPPWPPWPPWPPWPPQ.",
                "SCCCCCCCCCCCCCCCCCCc",
                ".ssSSSSSSSSSSSSSSSS.",
            ],
            # 1: lit, flickered
            [
                "......Y...Y..Y......",
                "......YY.YY.YY......",
                "......yY.Yy.Yy......",
                "......SS.RR.SS......",
                "......WW.WW.WW......",
                "......SS.RR.SS......",
                "...WSWWWWWWWWWLWW...",
                ".VRWWLWWSWWRWWWWLWW.",
                ".wwVVVVVVVVVVVVVVWW.",
                ".wwVPPVVVPPPVVPPVWW.",
                ".pwPPPVVPPPPVPPPPWQ.",
                ".ppPPPPVPPPPPPPPPPQ.",
                ".ppPPPPPPPPPPPPPPPQ.",
                ".ppWPPWPPWPPWPPWPPQ.",
                "SCCCCCCCCCCCCCCCCCCc",
                ".ssSSSSSSSSSSSSSSSS.",
            ],
            # 2: blown out
            [
                "....................",
                "....................",
                ".......@.@..@.......",
                "......SS.RR.SS......",
                "......WW.WW.WW......",
                "......SS.RR.SS......",
                "...WSWWWWWWWWWLWW...",
                ".VRWWLWWSWWRWWWWLWW.",
                ".wwVVVVVVVVVVVVVVWW.",
                ".wwVPPVVVPPPVVPPVWW.",
                ".pwPPPVVPPPPVPPPPWQ.",
                ".ppPPPPVPPPPPPPPPPQ.",
                ".ppPPPPPPPPPPPPPPPQ.",
                ".ppWPPWPPWPPWPPWPPQ.",
                "SCCCCCCCCCCCCCCCCCCc",
                ".ssSSSSSSSSSSSSSSSS.",
            ],
        ],
    },
    # Smoke off a blown-out candle (3x5, 3 frames that loop): a thin wisp rising and
    # swaying, dark grey at the bottom fading to light grey at the top (on a light
    # desktop the top melts away, on a dark one it shows as a fading trail). "base":
    # the cell that goes just above a wick, i.e. draw the frame at
    # (wick_x - 1, wick_y - 5) relative to the cake. Give all three wicks the same
    # frame at the same time: the wisps then rise side by side without crossing.
    "smoke": {
        "base": [1, 4],
        "palette": {"g": "#9c9a92", "m": "#b5b3aa", "w": "#d1cfc5"},
        "frames": [
            [
                ".w.",
                "m..",
                "m..",
                ".g.",
                "..g",
            ],
            [
                "w..",
                ".m.",
                "..m",
                "..g",
                ".g.",
            ],
            [
                "..w",
                "..m",
                ".m.",
                "g..",
                "g..",
            ],
        ],
    },
    # Party balloons (9x23, 3 frames = 3 balloons: clay, sky blue, gold). Egg-shaped,
    # shaded on the lower left with a shine at the upper right, a pinched knot, and a
    # thin grey string (10 cells) wiggling a little differently on each. Keys: body
    # (upper case) and shade (lower case) per colour, then a highlight and a knot key
    # each; W is the shine, | the string.
    # "string_end": the string's last cell, the same on all three (tie them together
    # there, or to his hand).
    "balloon": {
        "string_end": [4, 22],
        "palette": {"C": "#d97757", "c": "#b25a3d", "D": "#eb9e82", "K": "#9a4c33",
                    "B": "#6a9bcc", "b": "#4f7aa6", "E": "#9dbfe0", "N": "#44698f",
                    "G": "#eec875", "g": "#c9a24f", "H": "#f7e3b0", "J": "#b08a3e",
                    "W": "#faf9f5", "|": "#9c9a92"},
        "frames": [
            # clay
            [
                "..CCCCC..",
                ".CCCCDDC.",
                "CCCCCDWDC",
                "cCCCCCWDC",
                "cCCCCCCDC",
                "cCCCCCCCC",
                "ccCCCCCCC",
                ".ccCCCCC.",
                ".cccCCCc.",
                "..ccccc..",
                "...ccc...",
                "....K....",
                "...KKK...",
                "....|....",
                "....|....",
                "...|.....",
                "...|.....",
                "....|....",
                ".....|...",
                ".....|...",
                "....|....",
                "....|....",
                "....|....",
            ],
            # sky blue
            [
                "..BBBBB..",
                ".BBBBEEB.",
                "BBBBBEWEB",
                "bBBBBBWEB",
                "bBBBBBBEB",
                "bBBBBBBBB",
                "bbBBBBBBB",
                ".bbBBBBB.",
                ".bbbBBBb.",
                "..bbbbb..",
                "...bbb...",
                "....N....",
                "...NNN...",
                "....|....",
                ".....|...",
                ".....|...",
                "....|....",
                "...|.....",
                "...|.....",
                "....|....",
                "....|....",
                "....|....",
                "....|....",
            ],
            # gold
            [
                "..GGGGG..",
                ".GGGGHHG.",
                "GGGGGHWHG",
                "gGGGGGWHG",
                "gGGGGGGHG",
                "gGGGGGGGG",
                "ggGGGGGGG",
                ".ggGGGGG.",
                ".gggGGGg.",
                "..ggggg..",
                "...ggg...",
                "....J....",
                "...JJJ...",
                "....|....",
                "....|....",
                ".....|...",
                ".....|...",
                "....|....",
                "...|.....",
                "...|.....",
                "...|.....",
                "....|....",
                "....|....",
            ],
        ],
    },
    # "HAPPY BIRTHDAY!" letters: a chunky block font, one glyph per character in
    # "frames" (a dict), all 9 rows tall: 7 rows of letter with 2-cell uprights, then
    # a 2-deep extrusion below (s the side, S the darkest edge) that never closes a
    # counter. Widths: 6, except I and ! (2). With 1 cell between letters "HAPPY" is
    # 34 cells wide and "BIRTHDAY!" 54. Clay by default ("palette"); "tints" are the
    # same keys in clay, gold, sky blue, pink and olive: cycle them along a line for a
    # rainbow banner. Leave 2 to 3 rows between the two lines if the letters bob.
    "letters": {
        "tints": [
            {"F": "#d97757", "s": "#9a4c33", "S": "#4a2418"},
            {"F": "#e8ba58", "s": "#9c762c", "S": "#45330f"},
            {"F": "#6a9bcc", "s": "#44698f", "S": "#1f344a"},
            {"F": "#e85b6a", "s": "#a83846", "S": "#551820"},
            {"F": "#788c5d", "s": "#56663f", "S": "#2a331e"},
        ],
        "palette": {"F": "#d97757", "s": "#9a4c33", "S": "#4a2418"},
        "frames": {
            "H": [
                "FF..FF",
                "FF..FF",
                "FF..FF",
                "FFFFFF",
                "FFssFF",
                "FFSSFF",
                "FF..FF",
                "ss..ss",
                "SS..SS",
            ],
            "A": [
                ".FFFF.",
                "FFssFF",
                "FF..FF",
                "FFFFFF",
                "FFssFF",
                "FFSSFF",
                "FF..FF",
                "ss..ss",
                "SS..SS",
            ],
            "P": [
                "FFFFF.",
                "FFssFF",
                "FF..FF",
                "FFFFFs",
                "FFsssS",
                "FFSSS.",
                "FF....",
                "ss....",
                "SS....",
            ],
            "Y": [
                "FF..FF",
                "FF..FF",
                "FF..FF",
                "sFFFFs",
                "SsFFsS",
                ".SFFS.",
                "..FF..",
                "..ss..",
                "..SS..",
            ],
            "B": [
                "FFFFF.",
                "FFssFF",
                "FF..FF",
                "FFFFFs",
                "FFssFF",
                "FF..FF",
                "FFFFFs",
                "sssssS",
                "SSSSS.",
            ],
            "I": [
                "FF",
                "FF",
                "FF",
                "FF",
                "FF",
                "FF",
                "FF",
                "ss",
                "SS",
            ],
            "R": [
                "FFFFF.",
                "FFssFF",
                "FF..FF",
                "FFFFFs",
                "FFsFF.",
                "FFSsFF",
                "FF.SFF",
                "ss..ss",
                "SS..SS",
            ],
            "T": [
                "FFFFFF",
                "ssFFss",
                "SSFFSS",
                "..FF..",
                "..FF..",
                "..FF..",
                "..FF..",
                "..ss..",
                "..SS..",
            ],
            "D": [
                "FFFFF.",
                "FFssFF",
                "FFSSFF",
                "FF..FF",
                "FF..FF",
                "FF..FF",
                "FFFFFs",
                "sssssS",
                "SSSSS.",
            ],
            "!": [
                "FF",
                "FF",
                "FF",
                "FF",
                "ss",
                "FF",
                "FF",
                "ss",
                "SS",
            ],
        },
    },
    # His breath blowing the candles out (5x4, 2 frames): 0 a small puff, 1 the puff
    # grown into a little cloud, breaking up. Ivory with a two-tone grey underside, so it
    # still shows on a light desktop.
    "puff": {
        "palette": {"W": "#faf9f5", "w": "#d1cfc5", "v": "#b3b0a6"},
        "frames": [
            [
                ".....",
                "..WW.",
                ".wWWW",
                "..vv.",
            ],
            [
                ".WW.W",
                "WWWWW",
                "wWWWw",
                ".vv.v",
            ],
        ],
    },
    # A present (11x10): sky-blue box with a lid, tied with a pink ribbon both ways,
    # a two-loop bow on top with its tails hanging over the lid. Its bottom row sits
    # on the floor.
    "gift": {
        "palette": {"B": "#6a9bcc", "b": "#52799f", "d": "#3f6694", "C": "#9dbfe0", "c": "#c5d3e0",
                    "G": "#e85b6a", "g": "#c0404f", "k": "#8f2f3b", "y": "#f5a3ab"},
        "frames": [
            [
                "..GGG.GGy..",
                ".g..GkG..G.",
                "..ggGkGGG..",
                "CCCgCyCGCCc",
                "bBBgBGBGBBC",
                ".bBBBGBBBC.",
                ".bBBBGBBBC.",
                ".gGGGkGGGy.",
                ".bBBBGBBBC.",
                ".dbbbgbbbb.",
            ],
        ],
    },
}
