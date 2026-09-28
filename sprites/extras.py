"""Extras for Clawd: seasonal hats, particles and props, on the same cell grid as
sprites/clawd.json (one cell = half an official Clawd pixel; his idle pose is
24x16 cells, head at columns 4-19, eyes on rows 2-3).

EXTRAS maps a name to:
  "frames":  list of frames; each frame is a list of equal-length strings of
             palette keys, "." = transparent. For "confetti" and "droplet" each
             frame is a separate piece (sizes differ), not an animation step.
  "palette": single-character key -> "#rrggbb".
  "anchor":  hats only. [x, y] is the hat cell that sits on the top-centre of his
             head: hat cell (x, y) goes on idle cell (12, 0), the cell just right
             of his centre line. So draw the hat at (12 - x, 0 - y) relative to
             the idle pose's top-left.

Style follows the official art: flat colours, no outlines (except the speech
bubbles, which match the "!" bubble), light from the upper right, so shade sits
on the left and bottom and highlights on the upper right.
"""

EXTRAS = {
    # Santa hat (21x13, 2 frames). Christmas-red cap, fur trim over his top two rows
    # (one cell wider than his head each side), tip flopping to the left (clear of the
    # Z's and the "!" bubble on his right) into a pom-pom. Frame 2 drops the pom-pom
    # a cell: alternate the two for a gentle bob.
    "santa_hat": {
        "anchor": [12, 11],
        "palette": {"R": "#c8363e", "r": "#962634", "h": "#e0555a", "W": "#faf9f5", "w": "#d1cfc5"},
        "frames": [
            [
                ".........RRRR........",
                ".......RRRRRhhR......",
                "...RRRRRRRRRRhhR.....",
                "...RRR..rRRRRRhhR....",
                ".WWrr...rrRRRRRRhR...",
                "WWWW...rrRRRRRRRRR...",
                "wWWW...rrRRRRRRRRRR..",
                ".wW...rrRRRRRRRRRRR..",
                "....rrrRRRRRRRRRRRRR.",
                "....WWWWWWWWWWWWWWWW.",
                "...wWWWWWWWWWWWWWWWWW",
                "...wwWWWWWWWWWWWWWWWW",
                "....wwwwwwwwwwwwwwww.",
            ],
            [
                ".........RRRR........",
                ".......RRRRRhhR......",
                "...RRRRRRRRRRhhR.....",
                "...RRR..rRRRRRhhR....",
                "..rrr...rrRRRRRRhR...",
                ".WWr...rrRRRRRRRRR...",
                "WWWW...rrRRRRRRRRRR..",
                "wWWW..rrRRRRRRRRRRR..",
                ".wW.rrrRRRRRRRRRRRRR.",
                "....WWWWWWWWWWWWWWWW.",
                "...wWWWWWWWWWWWWWWWWW",
                "...wwWWWWWWWWWWWWWWWW",
                "....wwwwwwwwwwwwwwww.",
            ],
        ],
    },
    # Halloween jack-o'-lantern cap (14x10). Sits on his top two rows, clear of his
    # eyes; ribbed pumpkin orange, curled stem with a leaf, glowing carved face.
    "pumpkin_hat": {
        "anchor": [7, 8],
        "palette": {"O": "#ea7f22", "o": "#b65419", "P": "#f59c3c", "Y": "#fbd85a", "S": "#6f6b36",
                    "s": "#4f4a26", "L": "#7e9a42", "l": "#5f7a30"},
        "frames": [
            [
                ".......sS.LL..",
                "......sSLLl...",
                "...ooOsSOoP...",
                ".oooOOOOOOoPP.",
                "oooOYOOOOYOoPP",
                "oooYYYOOYYYoPP",
                "oooOOOOOOOOoPP",
                "oooYYOYYOYYoPP",
                "oooOYYYYYYOoPP",
                ".oooOOOOOOoPP.",
            ],
        ],
    },
    # Halloween particle (9x5, 2 frames): wings up, then wings down; the body rises a
    # cell on the downstroke. Dark purple with a lighter leading edge and gold eyes.
    "bat": {
        "palette": {"B": "#2e2438", "b": "#54436a", "E": "#eec875"},
        "frames": [
            [
                "b.......b",
                "Bb.b.b.bB",
                "BBBEBEBBB",
                ".BBBBBBB.",
                "...B.B...",
            ],
            [
                "...b.b...",
                "..bEBEb..",
                "bbBBBBBbb",
                "BBB.B.BBB",
                "B.......B",
            ],
        ],
    },
    # New Year party hat (10x12). Blue and gold striped cone leaning jauntily to the
    # right, ivory pom-pom on the tip; its base rests on his top row.
    "party_hat": {
        "anchor": [4, 11],
        "palette": {"B": "#6a9bcc", "b": "#52799f", "C": "#9dbfe0", "G": "#eec875", "g": "#c9a24f",
                    "H": "#f7e3b0", "W": "#faf9f5", "w": "#d1cfc5"},
        "frames": [
            [
                "......WW..",
                ".....WWWW.",
                ".....wWWW.",
                "......bC..",
                ".....bBH..",
                ".....bGH..",
                "....bGGHB.",
                "...bGGGCB.",
                "..bGGGBCB.",
                "..gGGBBBHG",
                ".gGGBBBGHG",
                "gGGBBBGGHB",
            ],
        ],
    },
    # New Year confetti: five separate pieces (1x2, 2x1, 2x2); frames are pieces here.
    "confetti": {
        "palette": {"B": "#6a9bcc", "G": "#eec875", "O": "#d87756", "P": "#e85b6a", "L": "#c5d3e0"},
        "frames": [["B", "B"], ["GG"], ["OO", "OO"], ["PP"], ["L", "L"]],
    },
    # Nightcap for late-night naps (23x13, 2 frames). Blue-striped sleeping cap with a
    # dark cuff over his top two rows; the long tail droops to the left (clear of the
    # Z's rising on his right) to an ivory pom-pom. Frame 2 sways it a cell lower.
    "nightcap": {
        "anchor": [14, 10],
        "palette": {"B": "#6a9bcc", "b": "#52799f", "L": "#c5d3e0", "l": "#a3b6ca", "W": "#faf9f5",
                    "w": "#d1cfc5", "M": "#8fb6dd", "K": "#dfe8f1"},
        "frames": [
            [
                "..........bBBBB........",
                ".......bbBBBBBBMB......",
                ".....bbbBBBBBBBBMB.....",
                "....lll...llLLLLLKL....",
                "...lll...llLLLLLLLKL...",
                "..bbb....bbBBBBBBBMB...",
                "..bb....bbBBBBBBBBBMB..",
                ".ll.....llLLLLLLLLLKL..",
                ".WW....llLLLLLLLLLLLKL.",
                "WWWW.bBBBBBBBBBBBBBBBBB",
                "wWWW.bBBBBBBBBBBBBBBBBB",
                ".ww...bbbbbbbbbbbbbbbb.",
                ".......................",
            ],
            [
                "..........bBBBB........",
                ".......bbBBBBBBMB......",
                ".....bbbBBBBBBBBMB.....",
                "....lll...llLLLLLKL....",
                "...lll...llLLLLLLLKL...",
                "..bbb....bbBBBBBBBMB...",
                "..bb....bbBBBBBBBBBMB..",
                ".ll.....llLLLLLLLLLKL..",
                ".ll....llLLLLLLLLLLLKL.",
                ".WW..bBBBBBBBBBBBBBBBBB",
                "WWWW.bBBBBBBBBBBBBBBBBB",
                "wWWW..bbbbbbbbbbbbbbbb.",
                ".ww....................",
            ],
        ],
    },
    # The nightcap while he stretches (same size, anchor and palette as "nightcap"): the
    # stretch bounces the tail up, so the pom-pom clears his raised left arm (the regular
    # nightcap's pom-pom hangs right where that arm goes).
    "nightcap_stretch": {
        "anchor": [14, 10],
        "palette": {"B": "#6a9bcc", "b": "#52799f", "L": "#c5d3e0", "l": "#a3b6ca", "W": "#faf9f5",
                    "w": "#d1cfc5", "M": "#8fb6dd", "K": "#dfe8f1"},
        "frames": [
            [
                "..........bBBBB........",
                ".......bbBBBBBBMB......",
                ".....bbbBBBBBBBBMB.....",
                "....lll...llLLLLLKL....",
                "...lll...llLLLLLLLKL...",
                "..WW.....bbBBBBBBBMB...",
                ".WWWW...bbBBBBBBBBBMB..",
                ".wWWW...llLLLLLLLLLKL..",
                "..ww...llLLLLLLLLLLLKL.",
                ".....bBBBBBBBBBBBBBBBBB",
                ".....bBBBBBBBBBBBBBBBBB",
                "......bbbbbbbbbbbbbbbb.",
                ".......................",
            ],
        ],
    },
    # Morning coffee mug (9x9). Cream ceramic, grey shadow on the left, ivory highlight
    # on the right, coffee at the rim, handle on the right and an orange Claude-style
    # starburst. The cup is columns 0-6 (the handle is 7-8).
    "mug": {
        "palette": {"W": "#faf9f5", "M": "#f2e3d7", "w": "#d1cfc5", "C": "#5c3b2b", "O": "#d87756"},
        "frames": [
            [
                ".wMMMWW..",
                "wCCCCCCW.",
                "wMMMMMWW.",
                "wOMOMOWMW",
                "wMOOOMW.W",
                "wOOOOOW.W",
                "wMOOOMWMW",
                "wOMOMOW..",
                ".wwwwww..",
            ],
        ],
    },
    # Steam for the mug (6x5, 3 frames that loop): two wisps rising. Draw it on the
    # rows just above the mug, centred over the cup (mug x + 0..6).
    "steam": {
        "palette": {"W": "#faf9f5", "w": "#d1cfc5"},
        "frames": [
            [
                "w..w..",
                "w...w.",
                ".W...W",
                "..W..W",
                "..W...",
            ],
            [
                ".w...w",
                "..w..w",
                "..W.W.",
                ".W.W..",
                "W.....",
            ],
            [
                "..w.w.",
                ".w.w..",
                "W..W..",
                "W...W.",
                ".W....",
            ],
        ],
    },
    # "Take a break" speech bubble (13x14): a steaming cup on a saucer, in the same
    # style as the "!" bubble (ink outline, ivory inside, tail at the bottom centre).
    "break_bubble": {
        "palette": {"#": "#141413", "+": "#faf9f5", "@": "#141413", "C": "#5c3b2b", "g": "#9c9a92"},
        "frames": [
            [
                ".###########.",
                "#+++++++++++#",
                "#++++g++g+++#",
                "#+++g++g++++#",
                "#++++g++g+++#",
                "#++@CCCC@+++#",
                "#++@@@@@@@@+#",
                "#++@@@@@@+@+#",
                "#++@@@@@@@@+#",
                "#+@@@@@@@@++#",
                "#+++++++++++#",
                ".#####+#####.",
                ".....#+#.....",
                "......#......",
            ],
        ],
    },
    # Hydration reminder: a water bottle he holds up and waves (7x12). Blue cap, clear
    # neck, light-blue water with a darker surface line, label band, white highlight.
    "water_bottle": {
        "palette": {"C": "#3f6694", "c": "#6a9bcc", "N": "#e4ebf2", "n": "#c8d3de", "L": "#c5d3e0",
                    "D": "#9db8d6", "H": "#faf9f5", "B": "#6a9bcc", "b": "#52799f", "s": "#a3b6ca"},
        "frames": [
            [
                "..CCc..",
                "..CCc..",
                "..nNH..",
                ".nNNNH.",
                "nNNNNHN",
                "sDDDDHD",
                "sLLLLHL",
                "bBBBBBB",
                "bBBBBBB",
                "sLLLLHL",
                "sLLLLHL",
                ".ssLLL.",
            ],
        ],
    },
    # The same bottle tipped up to drink (11x11): cap at the lower left, base at the
    # upper right, water pooled toward the mouth under a level water line, air at the
    # base. "cap" is the cell at the tip of the cap (the mouth of the bottle): put it
    # on idle cell (12, 5). It hides his right eye and leaves the left one clear.
    "water_bottle_tilt": {
        "cap": [0, 10],
        "palette": {"C": "#3f6694", "c": "#6a9bcc", "N": "#e4ebf2", "n": "#c8d3de", "L": "#c5d3e0",
                    "D": "#9db8d6", "H": "#faf9f5", "B": "#6a9bcc", "b": "#52799f", "s": "#a3b6ca"},
        "frames": [
            [
                "......NHn..",
                ".....NHNNn.",
                "....BBDDDDD",
                "...LBBBLLLL",
                "..LHLBBBLLs",
                "..HLLLBBbs.",
                "..LLLLLbb..",
                "..LLLLLs...",
                ".HLLLLs....",
                "ccLLLs.....",
                "CC.........",
            ],
        ],
    },
    # "Drink some water" speech bubble (13x14): a bold water drop with a highlight,
    # in the same style as the "!" bubble.
    "water_bubble": {
        "palette": {"#": "#141413", "+": "#faf9f5", "B": "#6a9bcc", "b": "#4f7db0", "H": "#faf9f5"},
        "frames": [
            [
                ".###########.",
                "#+++++++++++#",
                "#+++++B+++++#",
                "#++++BBB++++#",
                "#++++BBB++++#",
                "#+++BBBHB+++#",
                "#++BBBBBHB++#",
                "#++bBBBBBB++#",
                "#+++bBBBB+++#",
                "#++++bbb++++#",
                "#+++++++++++#",
                ".#####+#####.",
                ".....#+#.....",
                "......#......",
            ],
        ],
    },
    # Splash droplets flying off the bottle: three separate pieces (1x1, 1x2, 2x2).
    "droplet": {
        "palette": {"B": "#6a9bcc", "H": "#faf9f5"},
        "frames": [["B"], ["H", "B"], ["BH", "BB"]],
    },
    # Stretch pose (50x24, 2 frames), clawd.json palette: arms raised in a V from the
    # shoulders, outside every hat; on tiptoe (legs a row longer); eyes squeezed shut > <.
    # Frame 2 reaches a cell further. "home": frame cell of the idle pose's top-left
    # (feet on the idle feet line). "head": head top row and the cell right of its centre
    # line; a hat's anchor cell goes there. No hat touches the arms (use nightcap_stretch
    # for the nightcap), so draw the frame, then the hat.
    "stretch": {
        "home": [13, 8],
        "head": [25, 7],
        "palette": {"#": "#d87756", "@": "#141413"},
        "frames": [
            [
                "..................................................",
                ".######....................................######.",
                "..######..................................######..",
                "...######................................######...",
                "....######..............................######....",
                ".....######............................######.....",
                "......######..........................######......",
                ".......######....################....######.......",
                "........######...################...######........",
                ".........######..##@##########@##..######.........",
                "..........######.###@########@###.######..........",
                "...........########@##########@########...........",
                "..............######################..............",
                "...............####################...............",
                "................##################................",
                ".................################.................",
                ".................################.................",
                ".................################.................",
                ".................################.................",
                ".................##..##....##..##.................",
                ".................##..##....##..##.................",
                ".................##..##....##..##.................",
                ".................##..##....##..##.................",
                ".................##..##....##..##.................",
            ],
            [
                "######......................................######",
                ".######....................................######.",
                "..######..................................######..",
                "...######................................######...",
                "....######..............................######....",
                ".....######............................######.....",
                "......######..........................######......",
                ".......######....################....######.......",
                "........######...################...######........",
                ".........######..##@##########@##..######.........",
                "..........######.###@########@###.######..........",
                "...........########@##########@########...........",
                "..............######################..............",
                "...............####################...............",
                "................##################................",
                ".................################.................",
                ".................################.................",
                ".................################.................",
                ".................################.................",
                ".................##..##....##..##.................",
                ".................##..##....##..##.................",
                ".................##..##....##..##.................",
                ".................##..##....##..##.................",
                ".................##..##....##..##.................",
            ],
        ],
    },
    # Hanging from the mouse pointer (36x27, 6 frames): 0 straight, 1 lean_1 (swung about
    # 14 degrees to his right), 2 lean_2 (about 27 degrees), 3 kick_a (his left legs up),
    # 4 kick_b (his right legs up), 5 one_hand (right fist on the grip, left hand slipping
    # off, body swung a little left). Mirror 1-2 for the swing to the left. "grip": top
    # row of the fists, left of the two middle columns; the fists are the only cells on
    # that row and nothing is above it, in every frame. "eyes": per frame, the
    # top-left cell of each 2x2 eye, for redrawing expressions.
    "dangle": {
        "grip": [17, 0],
        "eyes": [
            [[12, 11], [22, 11]],   # straight
            [[15, 12], [24, 9]],   # lean_1
            [[17, 12], [26, 8]],   # lean_2
            [[12, 11], [22, 11]],   # kick_a
            [[12, 11], [22, 11]],   # kick_b
            [[10, 11], [20, 10]],   # one_hand
        ],
        "palette": {"#": "#d87756", "@": "#141413", "*": "#bf694d"},
        "frames": [
            # 0: straight
            [
                "................####................",
                "...............######...............",
                "..............########..............",
                ".............####..####.............",
                ".............####..####.............",
                ".............####..####.............",
                "............####....####............",
                "............####....####............",
                "...........****......****...........",
                "..........################..........",
                "..........################..........",
                "..........##@@########@@##..........",
                "..........##@@########@@##..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........##..##....##..##..........",
                "..........##..##....##..##..........",
                "..........##..##....##..##..........",
                "..........##..##....##..##..........",
                "..........##..##....##..##..........",
                "....................................",
            ],
            # 1: lean_1
            [
                "................####................",
                "...............######...............",
                "..............########..............",
                "..............####.#####............",
                "..............####..#####...........",
                "..............####..#####...........",
                "..............####...####*..........",
                ".............####.....***###........",
                ".............####....#######........",
                ".............****#######@@##........",
                ".............###########@@##........",
                ".............################.......",
                "............###@@############.......",
                ".............##@@############.......",
                ".............################.......",
                ".............#################......",
                ".............#################......",
                "..............################......",
                "..............################......",
                "..............############...##.....",
                "..............########...##..##.....",
                "...............###.##....##..##.....",
                "...............##..##....##..##.....",
                "...............##..##....##...##....",
                "...............##...##....##........",
                "................##..##..............",
                "................##..................",
            ],
            # 2: lean_2
            [
                "................####................",
                "...............######...............",
                "..............########..............",
                "..............####.######...........",
                "..............####...######.........",
                "..............####....###**##.......",
                "...............####....**####.......",
                "...............####....#######......",
                "...............####..#####@@##......",
                "...............##**#######@@###.....",
                "...............**##############.....",
                "...............#################....",
                "...............##@@#############....",
                "...............##@@##############...",
                "...............##################...",
                "................#################...",
                "................###############.##..",
                ".................############....##.",
                ".................##########.##...##.",
                "..................#######....##...##",
                "..................#######....##...##",
                "...................##...##....##....",
                "...................##...##....##....",
                "....................##...##.........",
                "....................##...##.........",
                ".....................##.............",
                ".....................##.............",
            ],
            # 3: kick_a
            [
                "................####................",
                "...............######...............",
                "..............########..............",
                ".............####..####.............",
                ".............####..####.............",
                ".............####..####.............",
                "............####....####............",
                "............####....####............",
                "...........****......****...........",
                "..........################..........",
                "..........################..........",
                "..........##@@########@@##..........",
                "..........##@@########@@##..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........##..##....##..##..........",
                "..........##..##....##..##..........",
                "....................##..##..........",
                "....................##..##..........",
                "....................##..##..........",
                "....................................",
            ],
            # 4: kick_b
            [
                "................####................",
                "...............######...............",
                "..............########..............",
                ".............####..####.............",
                ".............####..####.............",
                ".............####..####.............",
                "............####....####............",
                "............####....####............",
                "...........****......****...........",
                "..........################..........",
                "..........################..........",
                "..........##@@########@@##..........",
                "..........##@@########@@##..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........################..........",
                "..........##..##....##..##..........",
                "..........##..##....##..##..........",
                "..........##..##....................",
                "..........##..##....................",
                "..........##..##....................",
                "....................................",
            ],
            # 5: one_hand
            [
                "................####................",
                "................####................",
                "................####................",
                "................####................",
                "...........####.####................",
                "...........####.####................",
                "..........####..####................",
                "..........####..****................",
                ".........****...########............",
                "........################............",
                "........############@@##............",
                "........##@@########@@##............",
                "........##@@############............",
                "........################............",
                "........################............",
                "........################............",
                "........################............",
                "........################............",
                "........################............",
                "........################............",
                "........########..##..##............",
                "........##..##....##..##............",
                "........##..##....##..##............",
                "........##..##....##..##............",
                "........##..##....##..##............",
                "........##..##......................",
                "....................................",
            ],
        ],
    },
}
