# ps_buttons.py <gamefonts_pc.iwi in> <glyphs.json> <out.iwi> [preview.png] [--icons <folder>]
# ps_buttons.py --hud-dpad <icons folder> <template .iwi> <out .iwi>   (the HUD's d-pad picture)
#
# --icons: a folder of Vita button pictures (ButtonIcon-PSvita-<name>.png, RGBA; 2026-11-23 the user's download) used
# where it has one: the face buttons, START / SELECT, L / R, the d-pad, and the rear touch pad (its half lit) - the
# front screen's edges stay drawn here. Without it every glyph is drawn here.
#
# The fonts' gamepad button glyphs (the Xbox 360 pad's, letters 1..31 in every font's glyph table, all in the one atlas
# image gamefonts_pc) painted over with the PS Vita's buttons, drawn here (no image from anywhere else): the face
# buttons as dark round buttons with the four colored symbols, L / R, START / SELECT, the d-pad, and the touch zones the
# Vita port uses for the pad's other buttons (vita_input.cpp: LB / RB the front screen's left / right edge, LS / RS the
# rear panel's left / right half). Only the 4x4 DXT5 blocks under a button glyph are encoded again; the letters keep
# their blocks byte for byte. glyphs.json: build/vita/buttons/font_glyphs.py's output (the glyph tables of
# en_code_post_gfx.ff). 2026-11-23.
import json
import math
import struct
import sys
import zlib

import os  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import iwi_repack as R  # noqa: E402  (the DXT block decoder)

SS = 4  # supersampling per axis

# ---------------------------------------------------------------- shapes: inside tests in the icon's unit box (0..1)
def circle(cx, cy, r):
    return lambda x, y: (x - cx) ** 2 + (y - cy) ** 2 <= r * r

def ring(cx, cy, r0, r1):
    return lambda x, y: r0 * r0 <= (x - cx) ** 2 + (y - cy) ** 2 <= r1 * r1

def rrect(x0, y0, x1, y1, r):
    def f(x, y):
        if x < x0 or x > x1 or y < y0 or y > y1:
            return False
        dx = max(x0 + r - x, 0, x - (x1 - r))
        dy = max(y0 + r - y, 0, y - (y1 - r))
        return dx * dx + dy * dy <= r * r
    return f

def seg(ax, ay, bx, by, w):
    def f(x, y):
        vx, vy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((x - ax) * vx + (y - ay) * vy) / (vx * vx + vy * vy)))
        px, py = ax + t * vx - x, ay + t * vy - y
        return px * px + py * py <= w * w / 4
    return f

def poly(pts):
    def f(x, y):
        inside = False
        j = len(pts) - 1
        for i in range(len(pts)):
            xi, yi = pts[i]
            xj, yj = pts[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
        return inside
    return f

def outline_poly(pts, w):
    segs = [seg(pts[i][0], pts[i][1], pts[(i + 1) % len(pts)][0], pts[(i + 1) % len(pts)][1], w)
            for i in range(len(pts))]
    return lambda x, y: any(s(x, y) for s in segs)

def union(*fs):
    return lambda x, y: any(f(x, y) for f in fs)

def minus(a, b):
    return lambda x, y: a(x, y) and not b(x, y)

# ---------------------------------------------------------------- colors (r, g, b, a)
BODY = (44, 44, 48, 255)
BODY_HI = (78, 78, 84, 255)
RIM = (150, 150, 156, 255)
GREEN = (64, 214, 170, 255)
RED = (255, 92, 92, 255)
BLUE = (124, 170, 238, 255)
PINK = (236, 138, 216, 255)
LIGHT = (214, 214, 218, 255)
DARK = (40, 40, 44, 255)
HILITE = (98, 186, 255, 255)
WHITE = (250, 250, 250, 255)

def face_button(symbol):
    """a Vita face button: a dark round button with a light rim and a soft top light, the colored symbol"""
    layers = [(circle(0.5, 0.5, 0.47), RIM), (circle(0.5, 0.5, 0.43), BODY),
              (lambda x, y: (x - 0.5) ** 2 + (y - 0.36) ** 2 <= 0.30 ** 2 and (x - 0.5) ** 2 + (y - 0.5) ** 2 <= 0.43 ** 2,
               BODY_HI)]
    return layers + symbol

def sym_cross():
    return [(union(seg(0.31, 0.31, 0.69, 0.69, 0.1), seg(0.69, 0.31, 0.31, 0.69, 0.1)), BLUE)]

def sym_circle():
    return [(ring(0.5, 0.5, 0.15, 0.245), RED)]

def sym_square():
    return [(outline_poly([(0.31, 0.31), (0.69, 0.31), (0.69, 0.69), (0.31, 0.69)], 0.085), PINK)]

def sym_triangle():
    return [(outline_poly([(0.5, 0.26), (0.73, 0.68), (0.27, 0.68)], 0.085), GREEN)]

def letter_L(x0, y0, h, w):
    return union(seg(x0, y0, x0, y0 + h, w), seg(x0, y0 + h, x0 + h * 0.55, y0 + h, w))

def letter_R(x0, y0, h, w):
    m = y0 + h * 0.52
    return union(seg(x0, y0, x0, y0 + h, w), seg(x0, y0, x0 + h * 0.32, y0, w),
                 seg(x0 + h * 0.32, m, x0, m, w),
                 lambda x, y: abs(math.hypot(x - (x0 + h * 0.32), y - (y0 + h * 0.26)) - h * 0.26) <= w / 2
                 and x >= x0 + h * 0.32,
                 seg(x0 + h * 0.22, m, x0 + h * 0.58, y0 + h, w))

def shoulder(left):
    """the Vita's L / R: a light shoulder button (round on the outer top corner) with the dark letter"""
    if left:
        body = union(rrect(0.08, 0.2, 0.92, 0.84, 0.12), circle(0.32, 0.42, 0.24))
        mark = letter_L(0.38, 0.32, 0.4, 0.1)
    else:
        body = union(rrect(0.08, 0.2, 0.92, 0.84, 0.12), circle(0.68, 0.42, 0.24))
        mark = letter_R(0.38, 0.32, 0.4, 0.1)
    return [(body, RIM), (lambda x, y, b=body: b(x, y), LIGHT), (mark, DARK)]

def pill(mark):
    """START / SELECT: the Vita's small oval buttons"""
    return [(rrect(0.04, 0.22, 0.96, 0.78, 0.27), RIM), (rrect(0.09, 0.28, 0.91, 0.72, 0.21), BODY)] + mark

def dpad(lit):
    """the Vita's d-pad: four arrow buttons around a center; lit: the directions shown bright"""
    arms = {'up': poly([(0.5, 0.06), (0.66, 0.2), (0.66, 0.4), (0.34, 0.4), (0.34, 0.2)]),
            'down': poly([(0.5, 0.94), (0.66, 0.8), (0.66, 0.6), (0.34, 0.6), (0.34, 0.8)]),
            'left': poly([(0.06, 0.5), (0.2, 0.34), (0.4, 0.34), (0.4, 0.66), (0.2, 0.66)]),
            'right': poly([(0.94, 0.5), (0.8, 0.34), (0.6, 0.34), (0.6, 0.66), (0.8, 0.66)])}
    layers = [(circle(0.5, 0.5, 0.49), (30, 30, 34, 170))]
    for name, f in arms.items():
        layers.append((f, WHITE if name in lit else (120, 120, 126, 255)))
    return layers

def front_touch(right):
    """the front screen's edge (LB / RB: the grenades): the Vita's screen with the edge's fifth lit"""
    frame = rrect(0.03, 0.14, 0.97, 0.86, 0.1)
    screen = rrect(0.1, 0.22, 0.9, 0.78, 0.04)
    edge = rrect(0.72, 0.22, 0.9, 0.78, 0.04) if right else rrect(0.1, 0.22, 0.28, 0.78, 0.04)
    finger = circle(0.81 if right else 0.19, 0.5, 0.06)
    return [(frame, RIM), (screen, DARK), (edge, HILITE), (finger, WHITE)]

def rear_touch(right):
    """the rear panel's half (LS / RS: sprint, melee): the panel's dotted pad with the half lit"""
    frame = rrect(0.04, 0.08, 0.96, 0.92, 0.22)
    pad = rrect(0.1, 0.18, 0.9, 0.82, 0.16)
    half = (lambda x, y: pad(x, y) and x >= 0.5) if right else (lambda x, y: pad(x, y) and x < 0.5)
    dots = lambda x, y: pad(x, y) and ((x * 18) % 1 - 0.5) ** 2 + ((y * 11) % 1 - 0.5) ** 2 < 0.06
    finger = circle(0.7 if right else 0.3, 0.5, 0.09)
    return [(frame, RIM), (pad, (70, 70, 76, 255)), (half, HILITE), (dots, (255, 255, 255, 90)), (finger, WHITE)]

def triangle_right():
    return [(poly([(0.4, 0.36), (0.64, 0.5), (0.4, 0.64)]), WHITE)]

def bars():
    return [(rrect(0.33, 0.4, 0.67, 0.46, 0.02), WHITE), (rrect(0.33, 0.54, 0.67, 0.6, 0.02), WHITE)]

ICONS = {
    1: face_button(sym_cross()), 2: face_button(sym_circle()), 3: face_button(sym_square()),
    4: face_button(sym_triangle()),
    5: front_touch(False), 6: front_touch(True),
    14: pill(triangle_right()), 15: pill(bars()),
    16: rear_touch(False), 17: rear_touch(True),
    18: shoulder(True), 19: shoulder(False),
    20: dpad({'up'}), 21: dpad({'down'}), 22: dpad({'left'}), 23: dpad({'right'}),
    24: dpad({'up', 'down'}), 25: dpad({'left', 'right'}),
}

# ---------------------------------------------------------------- pictures (--icons)
def read_png(path):
    """an 8-bit RGBA or RGB PNG without interlacing as (w, h, [[r, g, b, a], ...])"""
    d = open(path, 'rb').read()
    o, idat, w = 8, b'', 0
    while o < len(d):
        n, t = struct.unpack('>I4s', d[o:o + 8])
        if t == b'IHDR':
            w, h, depth, ctype, _, _, inter = struct.unpack('>IIBBBBB', d[o + 8:o + 21])
            if depth != 8 or ctype not in (2, 6) or inter:
                raise SystemExit('%s: only 8-bit RGB / RGBA PNGs without interlacing' % path)
        elif t == b'IDAT':
            idat += d[o + 8:o + 8 + n]
        o += 12 + n
    bpp = 4 if ctype == 6 else 3
    raw = zlib.decompress(idat)
    stride = w * bpp
    out, prev = [], bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]
        line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        for x in range(w):
            px = line[x * bpp:x * bpp + bpp]
            out.append([px[0], px[1], px[2], px[3] if bpp == 4 else 255])
        prev = line
    return w, h, out

def crop(pic):
    """the picture cut to its visible part"""
    w, h, px = pic
    xs = [i % w for i, p in enumerate(px) if p[3] > 8]
    ys = [i // w for i, p in enumerate(px) if p[3] > 8]
    x0, x1, y0, y1 = min(xs), max(xs) + 1, min(ys), max(ys) + 1
    return x1 - x0, y1 - y0, [px[y * w + x] for y in range(y0, y1) for x in range(x0, x1)]

def fit(pic, w, h, margin=0.04):
    """the picture in a w x h glyph, its aspect kept, centered: SS x SS samples a texel (bilinear), premultiplied"""
    pw, ph, px = pic
    scale = min(w * (1 - 2 * margin) / pw, h * (1 - 2 * margin) / ph)
    ox, oy = (w - pw * scale) / 2, (h - ph * scale) / 2
    def sample(u, v):
        if u < -0.5 or v < -0.5 or u > pw - 0.5 or v > ph - 0.5:
            return (0.0, 0.0, 0.0, 0.0)
        x0, y0 = int(math.floor(u)), int(math.floor(v))
        fx, fy = u - x0, v - y0
        acc = [0.0, 0.0, 0.0, 0.0]
        for yy, wy in ((y0, 1 - fy), (y0 + 1, fy)):
            for xx, wx in ((x0, 1 - fx), (x0 + 1, fx)):
                if 0 <= xx < pw and 0 <= yy < ph and wx * wy > 0:
                    p = px[yy * pw + xx]
                    a = p[3] / 255 * wx * wy
                    acc[0] += p[0] * a
                    acc[1] += p[1] * a
                    acc[2] += p[2] * a
                    acc[3] += a
        return acc
    out = []
    for y in range(h):
        for x in range(w):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(SS):
                for sx in range(SS):
                    u = (x + (sx + 0.5) / SS - ox) / scale - 0.5
                    v = (y + (sy + 0.5) / SS - oy) / scale - 0.5
                    c = sample(u, v)
                    for k in range(4):
                        acc[k] += c[k]
            a = acc[3] / (SS * SS)
            if a > 0.002:
                out.append([min(255, int(acc[k] / acc[3] + 0.5)) for k in range(3)] + [int(a * 255 + 0.5)])
            else:
                out.append([80, 80, 84, 0])
    return out

def brighter(a, b):
    """per texel the brighter of two pictures (the d-pad's two lit directions)"""
    w, h, pa = a
    _, _, pb = b
    def lum(p):
        return (p[0] + p[1] + p[2]) * p[3]
    return w, h, [p if lum(p) >= lum(q) else q for p, q in zip(pa, pb)]

def lit_half(texels, w, h, right):
    """the rear touch pad's half lit: its texels there toward the lit color"""
    out = []
    for i, t in enumerate(texels):
        x = i % w
        if t[3] and ((x >= w / 2) if right else (x < w / 2)):
            out.append([int(t[k] * 0.4 + HILITE[k] * 0.6) for k in range(3)] + [max(t[3], 200)])
        else:
            out.append(t)
    return out

PICTURES = {1: 'Cross', 2: 'Circle', 3: 'Square', 4: 'Triangle', 14: 'Start', 15: 'Select', 18: 'Bumper_Left',
            19: 'Bumper_Right', 20: 'Dpad_Up', 21: 'Dpad_Down', 22: 'Dpad_Left', 23: 'Dpad_Right',
            24: ('Dpad_Up', 'Dpad_Down'), 25: ('Dpad_Left', 'Dpad_Right'), 16: 'Touch_Pad', 17: 'Touch_Pad'}

def picture_icon(folder, letter, w, h):
    """the glyph from the folder's picture, or None (drawn then)"""
    import os
    name = PICTURES.get(letter)
    if not folder or not name:
        return None
    def load(n):
        path = os.path.join(folder, 'ButtonIcon-PSvita-%s.png' % n)
        return read_png(path) if os.path.exists(path) else None
    if isinstance(name, tuple):
        a, b = load(name[0]), load(name[1])
        if not a or not b:
            return None
        pic = brighter(a, b)
    else:
        pic = load(name)
        if not pic:
            return None
    texels = fit(crop(pic), w, h)
    if letter in (16, 17):
        texels = lit_half(texels, w, h, letter == 17)
    if letter in (18, 19):  # (the picture's thin L / R vanish at a glyph's size: a bold dark letter over it)
        mark = letter_L(0.44, 0.4, 0.24, 0.09) if letter == 18 else letter_R(0.46, 0.4, 0.24, 0.09)
        over = render([(mark, (36, 36, 40, 255))], w, h)
        texels = [over_texel(o, t) for o, t in zip(over, texels)]
    return texels

def over_texel(top, below):
    """top over below (straight alpha)"""
    a = top[3] / 255
    if a <= 0:
        return below
    ob = below[3] / 255 * (1 - a)
    out = a + ob
    return [int((top[k] * a + below[k] * ob) / out + 0.5) for k in range(3)] + [int(out * 255 + 0.5)]

def render(layers, w, h):
    """the icon at w x h: each texel the layers' colors over each other, SS x SS samples; the unit box keeps its aspect
    (centered in the glyph's rect)"""
    out = []
    s = min(w, h)
    ox, oy = (w - s) / 2, (h - s) / 2
    wide = w > h * 1.3  # (LS / RS: 54 x 33 - the rear panel drawn wide)
    for y in range(h):
        for x in range(w):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(SS):
                for sx in range(SS):
                    px = x + (sx + 0.5) / SS
                    py = y + (sy + 0.5) / SS
                    if wide:
                        u, v = px / w, py / h
                    else:
                        u, v = (px - ox) / s, (py - oy) / s
                    c = (0.0, 0.0, 0.0, 0.0)
                    for f, col in layers:
                        if f(u, v):
                            a = col[3] / 255
                            c = (col[0] * a + c[0] * (1 - a), col[1] * a + c[1] * (1 - a), col[2] * a + c[2] * (1 - a),
                                 255 * a + c[3] * (1 - a))
                    # (premultiplied accumulation)
                    acc[0] += c[0] * c[3] / 255
                    acc[1] += c[1] * c[3] / 255
                    acc[2] += c[2] * c[3] / 255
                    acc[3] += c[3]
            n = SS * SS
            a = acc[3] / n
            if a > 0.5:
                rgb = [min(255, int(acc[k] / n * 255 / a + 0.5)) for k in range(3)]
            else:
                rgb = list(RIM[:3])  # (the transparent texels: the rim's color, no dark fringe when filtered)
            out.append(rgb + [int(a + 0.5)])
    return out

# ---------------------------------------------------------------- DXT5
def rgb565(c):
    return ((c[0] * 31 + 127) // 255) << 11 | ((c[1] * 63 + 127) // 255) << 5 | ((c[2] * 31 + 127) // 255)

def color_block(texels):
    """the best color end points among the block's own colors (alpha-weighted error), DXT5's four-color mode"""
    cands = []
    for t in texels:
        c = rgb565(t)
        if c not in cands:
            cands.append(c)
    best = None
    pairs = [(a, b) for i, a in enumerate(cands) for b in cands[i:]] or [(0, 0)]
    for c0, c1 in pairs:
        if c0 < c1:
            c0, c1 = c1, c0
        p0, p1 = R._dxt_rgb(c0), R._dxt_rgb(c1)
        pal = (p0, p1, tuple((2 * a + b) // 3 for a, b in zip(p0, p1)), tuple((a + 2 * b) // 3 for a, b in zip(p0, p1)))
        err = 0
        bits = 0
        for i, t in enumerate(texels):
            k = min(range(4), key=lambda j: sum((t[ch] - pal[j][ch]) ** 2 for ch in range(3)))
            err += sum((t[ch] - pal[k][ch]) ** 2 for ch in range(3)) * (t[3] + 8)
            bits |= k << (2 * i)
        if best is None or err < best[0]:
            best = (err, struct.pack('<HHI', c0, c1, bits))
    return best[1]

def dxt5_block(texels):
    alphas = [t[3] for t in texels]
    a0, a1 = max(alphas), min(alphas)
    if a0 == a1:
        alpha = bytes((a0, a1)) + bytes(6)
    else:
        vals = [a0, a1] + [((7 - k) * a0 + k * a1) // 7 for k in range(1, 7)]
        idx = 0
        for i, a in enumerate(alphas):
            idx |= min(range(8), key=lambda k: abs(a - vals[k])) << (3 * i)
        alpha = bytes((a0, a1)) + idx.to_bytes(6, 'little')
    return alpha + color_block(texels)

def png(path, w, h, rgba):
    raw = b''.join(b'\0' + bytes(rgba[y * w * 4:(y + 1) * w * 4]) for y in range(h))
    def chunk(t, b):
        return struct.pack('>I', len(b)) + t + b + struct.pack('>I', zlib.crc32(t + b) & 0xffffffff)
    open(path, 'wb').write(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) +
                           chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))

def hud_dpad(icons, template):
    """the HUD's d-pad picture (ui_button_xenon_dpad_64x64 / hud_dpad_xenon: one DXT5 level, no mips - the Xbox pad's
    round d-pad, user 2026-11-25) as the folder's ButtonIcon-PSvita-Dpad.png, the template's header kept"""
    import os
    w, h = struct.unpack_from('<HH', template, 6)
    if template[:3] != b'IWi' or template[4] != 13 or len(template) != 48 + w * h:
        raise SystemExit('the HUD d-pad: expected one DXT5 level without mips')
    texels = fit(crop(read_png(os.path.join(icons, 'ButtonIcon-PSvita-Dpad.png'))), w, h, margin=0.03)
    out = bytearray(template[:48])
    for by in range(0, h, 4):
        for bx in range(0, w, 4):
            out += dxt5_block([texels[(by + k // 4) * w + bx + k % 4] for k in range(16)])
    return bytes(out)

def main():
    # --hud-dpad <icons folder> <template .iwi> <out .iwi>: the HUD's d-pad picture (hud_dpad)
    if len(sys.argv) == 5 and sys.argv[1] == '--hud-dpad':
        open(sys.argv[4], 'wb').write(hud_dpad(sys.argv[2], open(sys.argv[3], 'rb').read()))
        return
    icons = None
    if '--icons' in sys.argv:
        i = sys.argv.index('--icons')
        icons = sys.argv[i + 1]
        del sys.argv[i:i + 2]
    data = bytearray(open(sys.argv[1], 'rb').read())
    if data[:3] != b'IWi' or data[4] != 13:
        raise SystemExit('not a DXT5 iwi')
    w, h = struct.unpack_from('<HH', data, 6)
    if len(data) != 48 + w * h:
        raise SystemExit('expected one DXT5 level without mips (%d bytes)' % len(data))
    fonts = json.load(open(sys.argv[2]))
    rects = {}
    for f in fonts:
        for g in f['buttons']:
            if g['letter'] in ICONS and g['w'] >= 16:  # (the console font's 8-pixel boxes are not buttons)
                r = (round(g['s0'] * w), round(g['t0'] * h), round(g['s1'] * w), round(g['t1'] * h))
                rects[r] = g['letter']
    # the texels: decoded per block where a rect touches it
    def block_at(bx, by):
        o = 48 + (by * (w // 4) + bx) * 16
        return o, R._dxt_texels(13, bytes(data[o:o + 16]))
    blocks = {}
    for (x0, y0, x1, y1), letter in sorted(rects.items()):
        icon = picture_icon(icons, letter, x1 - x0, y1 - y0) or render(ICONS[letter], x1 - x0, y1 - y0)
        for y in range(y0, y1):
            for x in range(x0, x1):
                key = (x // 4, y // 4)
                if key not in blocks:
                    blocks[key] = block_at(*key)[1]
                blocks[key][(y % 4) * 4 + x % 4] = icon[(y - y0) * (x1 - x0) + (x - x0)]
    for (bx, by), texels in blocks.items():
        o = 48 + (by * (w // 4) + bx) * 16
        data[o:o + 16] = dxt5_block(texels)
    open(sys.argv[3], 'wb').write(bytes(data))
    print('%d button glyphs, %d blocks encoded again' % (len(rects), len(blocks)))
    if len(sys.argv) > 4:  # a preview: the new glyphs, twice their size, on grey
        items = sorted(rects.items(), key=lambda kv: (kv[1], kv[0]))
        SW = 1100
        x = y = rowh = 0
        place = []
        for r, letter in items:
            cw, ch = (r[2] - r[0]) * 2, (r[3] - r[1]) * 2
            if x + cw + 6 > SW:
                x, y, rowh = 0, y + rowh + 6, 0
            place.append((r, x, y))
            x += cw + 6
            rowh = max(rowh, ch)
        SH = y + rowh + 6
        sheet = bytearray(b'\x60\x60\x60\xff' * SW * SH)
        for r, sx, sy in place:
            for yy in range((r[3] - r[1]) * 2):
                for xx in range((r[2] - r[0]) * 2):
                    X, Y = r[0] + xx // 2, r[1] + yy // 2
                    _, t = block_at(X // 4, Y // 4)
                    c = t[(Y % 4) * 4 + X % 4]
                    a = c[3] / 255
                    q = ((sy + yy) * SW + sx + xx) * 4
                    sheet[q:q + 3] = bytes(int(c[k] * a + 0x60 * (1 - a)) for k in range(3))
        png(sys.argv[4], SW, SH, sheet)

if __name__ == '__main__':
    main()
