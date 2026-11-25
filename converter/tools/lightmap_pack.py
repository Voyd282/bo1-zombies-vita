"""lightmap_pack.py - the world's lightmaps for the Vita (zone_repack.py --shrink-lightmaps).

A map's lightmaps are inline images of its zone (*lightmap<n>_primary / _secondary / _secondaryb), loaded into the
zone's temporary block and copied into video memory - one level each, so the Vita's texture size cap does not touch
them. Rust's are 2048x2048 DXT1 + 1024x2048 R5G6B5 + 1024x1024 G16R16 a page, five pages: 37.5 MB where Kino has 7.5
(2026-10-20); with them the Vita's video memory ran out, 294 textures came out white, and a white lightmap decodes
to purple (light = brightness * (4R, 2(1-R-B), 4B)).

  - the primary lightmaps become a 4x4 black DXT1: no Vita program reads them (the world draw binds them to sampler
    12 for passes that ask, vita_gxm_draw.cpp binds 13 and 14 only; the PC's lit shaders read 13 and 14 too);
  - when the secondary and secondary B lightmaps together exceed LIGHTMAP_BUDGET, each is halved (2x2 box filter of
    each channel: R5G6B5's 5/6/5 bits, G16R16's two 16-bit values). A secondary holds two colors, one in its top half
    and one in its bottom half: halving keeps them apart (the halves' border is an even row). The shaders read them
    with normalized coordinates, at any size.
The image struct's width, height and card memory sizes and the load def's resourceSize follow the new data. Needs
maps recorded with the image marks (ZM_MARK_IMAGE / ZM_MARK_IMAGE_DATA).
"""
import array
import bisect
import struct

LIGHTMAP_BUDGET = 8 * 1024 * 1024  # bytes of secondary + secondary B lightmaps a map keeps at full size (Kino: 6 MB)

ZM_READ, ZM_MARK_BEGIN, ZM_MARK_END = 1, 6, 7
MARK_IMAGE, MARK_IMAGE_DATA = 7, 8
IMAGE_SIZE = 52       # GfxImage: cardMemory +12 (2 ints), width +20, height +22, name +44
D3DFMT_R5G6B5, D3DFMT_G16R16, D3DFMT_DXT1 = 23, 34, 0x31545844
INLINE = 0xFFFFFFFF


class Image:
    def __init__(self, a, name):
        self.a = a          # the GfxImage struct's stream offset
        self.name = name
        self.loaddef = None  # the load def header's stream offset
        self.reads = []      # its pixels' stream ranges


def collect(stream, events):
    """the zone's images with inline pixels, in load order"""
    reads = [([], []) for _ in range(7)]

    def resolve(encoded):
        blk, off = (encoded - 1) >> 29, (encoded - 1) & 0x1FFFFFFF
        if not 1 <= blk <= 6:
            return None
        starts, rd = reads[blk]
        i = bisect.bisect_right(starts, off) - 1
        if i < 0 or off >= starts[i] + rd[i][1]:
            return None
        return rd[i][0] + (off - starts[i])

    images, image, data_reads = [], None, None
    for kind, block, extra, a, b, c in events:
        if kind == ZM_READ:
            if 1 <= block <= 6 and c:
                reads[block][0].append(a)
                reads[block][1].append((b, c))
            if data_reads is not None and c:
                data_reads.append((b, c))
        elif kind == ZM_MARK_BEGIN and extra == MARK_IMAGE and a != INLINE:
            field = struct.unpack_from("<I", stream, a + 44)[0]
            at = a + IMAGE_SIZE if field == INLINE else resolve(field)
            name = stream[at:stream.index(b"\0", at)].decode("latin-1") if at is not None else None
            image = Image(a, name)
            images.append(image)
        elif kind == ZM_MARK_BEGIN and extra == MARK_IMAGE_DATA and a != INLINE and image is not None:
            image.loaddef = a
            data_reads = image.reads
        elif kind == ZM_MARK_END and extra == MARK_IMAGE_DATA:
            data_reads = None
            image = None
    return [i for i in images if i.loaddef is not None]


def halve(data, width, height, fmt):
    """a one-level R5G6B5 or G16R16 image at half its width and height (2x2 box filter per channel)"""
    w2, h2 = width // 2, height // 2
    if fmt == D3DFMT_R5G6B5:
        src = array.array("H")
        src.frombytes(data[:width * height * 2])
        out = array.array("H", bytes(w2 * h2 * 2))
        for y in range(h2):
            r0, r1 = 2 * y * width, (2 * y + 1) * width
            for x in range(w2):
                a, b, c, d = src[r0 + 2 * x], src[r0 + 2 * x + 1], src[r1 + 2 * x], src[r1 + 2 * x + 1]
                r = ((a >> 11) + (b >> 11) + (c >> 11) + (d >> 11) + 2) >> 2
                g = (((a >> 5) & 63) + ((b >> 5) & 63) + ((c >> 5) & 63) + ((d >> 5) & 63) + 2) >> 2
                bl = ((a & 31) + (b & 31) + (c & 31) + (d & 31) + 2) >> 2
                out[y * w2 + x] = (r << 11) | (g << 5) | bl
        return out.tobytes()
    src = array.array("H")  # G16R16: two 16-bit values a texel
    src.frombytes(data[:width * height * 4])
    out = array.array("H", bytes(w2 * h2 * 4))
    for y in range(h2):
        r0, r1 = 2 * y * width * 2, (2 * y + 1) * width * 2
        for x in range(w2):
            for k in range(2):
                i0, i1 = r0 + 4 * x + k, r1 + 4 * x + k
                out[(y * w2 + x) * 2 + k] = (src[i0] + src[i0 + 2] + src[i1] + src[i1 + 2] + 2) >> 2
    return out.tobytes()


class Plan:
    def __init__(self):
        self.patches = {}   # stream offset -> bytes (image structs, load def headers)
        self.replace = {}   # load def stream offset -> [new pixels]
        self.primaries = 0
        self.halved = 0
        self.bytes = [0, 0]  # the lightmaps' bytes before, after


def plan(stream, events, drop_primary=True):
    """drop_primary=False keeps the primary lightmaps (a PC test of the halving alone: the PC's sunlit techniques read
    them for the sun's visibility, the Vita's programs do not)"""
    p = Plan()
    lightmaps = []
    for image in collect(stream, events):
        if not image.name or not image.name.startswith("*lightmap"):
            continue
        levels, flags = stream[image.loaddef], stream[image.loaddef + 1]
        fmt, size = struct.unpack_from("<iI", stream, image.loaddef + 4)
        width, height = struct.unpack_from("<HH", stream, image.a + 20)
        if levels != 1 or sum(n for _, n in image.reads) != size:
            continue
        lightmaps.append((image, fmt & 0xFFFFFFFF, size, width, height))
    secondary_bytes = sum(size for image, fmt, size, w, h in lightmaps if fmt in (D3DFMT_R5G6B5, D3DFMT_G16R16))
    halve_all = secondary_bytes > LIGHTMAP_BUDGET
    for image, fmt, size, width, height in lightmaps:
        p.bytes[0] += size
        new = None
        if drop_primary and image.name.endswith("_primary") and fmt == D3DFMT_DXT1:
            new, nw, nh = bytes(8), 4, 4  # one black DXT1 block
            p.primaries += 1
        elif halve_all and fmt in (D3DFMT_R5G6B5, D3DFMT_G16R16) and width >= 8 and height >= 8 \
                and size == width * height * (2 if fmt == D3DFMT_R5G6B5 else 4):
            data = b"".join(stream[o:o + n] for o, n in image.reads)
            new, nw, nh = halve(data, width, height, fmt), width // 2, height // 2
            p.halved += 1
        if new is None:
            p.bytes[1] += size
            continue
        p.bytes[1] += len(new)
        p.patches[image.a + 12] = struct.pack("<II", len(new), len(new))  # cardMemory
        p.patches[image.a + 20] = struct.pack("<HH", nw, nh)
        p.patches[image.loaddef + 8] = struct.pack("<I", len(new))        # resourceSize
        p.replace[image.loaddef] = [new]
    return p
