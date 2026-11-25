"""zone_repack.py - rewrite a Black Ops fastfile (.ff) for the Vita: drop data it does not need.

    python -I tools/vita/zone_repack.py check  <zone.ff> <zone.zmap>
    python -I tools/vita/zone_repack.py repack <zone.ff> <zone.zmap> <out.ff> [--drop-shaders]
    python -I tools/vita/zone_repack.py all    <zone dir> <zmap dir> <out dir> [--drop-shaders]

all: every zone that has a map, found under <zone dir>/<Common|language>/, written to the same sub folder of
<out dir>. The usual run, from the repository root (tools/vita/stage-data.ps1 then takes the zones from there):
    python -I tools/vita/zone_repack.py all build/Release/zone build/Release/zonemap build/vita-zones --drop-shaders
Record the maps first with a PC run of the map:
    tools/headless.ps1 -Zombies -Commands "+set bo1_zonemap 1 +devmap zombie_theater" -AutoQuitMs 5000

The .zmap comes from the PC engine: run a map with 'bo1_zonemap' on the command line and every zone it loads is
recorded to build/Release/zonemap/<zone>.zmap (src/database/db_zonemap.h): how each byte of the decompressed stream
lands in the zone's memory blocks, every alignment and position reset, every encoded block offset the loader
resolves (aliases between assets), and markers around data a re-pack may drop.

repack copies the stream without the dropped data, replays the recorded block layout with the new sizes (alignment
padding is not in the stream, the loader recomputes it, so the replay does the same), re-encodes every block offset
for the new layout and writes the smaller block sizes into the XFile header. check does the same with nothing
dropped and verifies that the result is the original byte for byte (the self-test of the map and the replay).

--convert-wma: every loaded WMA sound (the Vita has no WMA decoder; 95% of Kino's loaded sounds) becomes 16 kHz
MS ADPCM, which the Vita's sound driver plays (platform/vita/vita_sound.cpp): ffmpeg (on the PATH, or --ffmpeg)
decodes the WMA and encodes the ADPCM in blocks of 256 bytes a channel (500 frames); the seek table goes, the
header's format, rate, frame count, block size and data size change. Converted sounds are cached by content
(--sound-cache, default build/vita-sndcache), so a second run and the sounds zones share cost nothing.
--shrink-adpcm: the loaded MS ADPCM sounds (the game's own: 44-48 kHz, 262-byte blocks) above 24 kHz are
resampled to about 24 kHz in our blocks, half their size. Every converted sound (WMA too) is decoded again and
compared with the same resampling without the encode - length, level, correlation, a loop's seam
(convert_sound / pcm_check); an MS ADPCM sound that does not match keeps its original data. A looping sound gets a
rate near the target that makes it a whole number of blocks (as the game's own loops have odd rates such as 48013
Hz): the Vita's driver plays whole blocks, so a padded last block would be a gap each time round.
--drop-shaders: the D3D9 vertex / pixel shader programs of every technique (the Vita renderer brings its own);
the load defs keep their size field at 0 and a null program, so the loader reads nothing for them. Zones made with
it are for the Vita (or a PC dedicated server) only: a PC client cannot create its shaders from them.
--drop-lod0 / --low-poly: the low-poly conversion (tools/vita/model_lowpoly.py has the rules): --drop-lod0 leaves
out every model's finest LOD where the model has a lower one and is not first-person (the LOD table moves up one,
LOD 0's surfaces stay in the surfs array, empty: zeroed counts and pointers, so the loader reads nothing for them);
--low-poly also simplifies the LOD a model then starts at when it has more triangles than a retail model of its
kind (model_lowpoly.BUDGETS): each such surface's data (skin weights, vertices, rigid lists and their collision
trees, triangles) is replaced by smaller data, read by the same loads. A model whose left-out or replaced data
something points at in a way the replay cannot follow stays as it is (found by a replay, planned again). Needs maps
recorded with the model marks (ZM_MARK_XMODEL / ZM_MARK_XSURFACE) and meshopt.dll (tools/vita/meshopt/build.bat).

Never writes next to its input: the input .ff files are the game's own.
"""
import argparse
import array
import bisect
import concurrent.futures
import hashlib
import math
import os
import shutil
import struct
import subprocess
# (no console window for each ffmpeg: under the converter's window, pythonw, every one would open its own)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# (threads for the ffmpeg conversions: the converter runs several zones at once and divides the CPU among them)
FFMPEG_THREADS = int(os.environ.get("BO1_TOOL_THREADS") or 0) or max(2, os.cpu_count() or 2)
import sys
import tempfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lightmap_pack  # noqa: E402
import model_collision  # noqa: E402
import model_lowpoly  # noqa: E402

HEADER = b"IWffu100"
VERSION = 473
REC = struct.Struct("<BBHIII")
ZM_READ, ZM_ALIGN, ZM_INC, ZM_RESET, ZM_OFFSET, ZM_MARK_BEGIN, ZM_MARK_END, ZM_ASSET = range(1, 9)
MARK_NAMES = {100: "unused anims' data", 1: "vertex shader programs", 2: "pixel shader programs", 3: "WMA seek tables", 4: "WMA sound data",
              6: "left-out LOD 0 model surfaces"}
MARK_SND_SEEK, MARK_SND_DATA = 3, 4
MARK_XMODEL, MARK_XSURFACE = 5, 6
MARK_IMAGE, MARK_IMAGE_DATA = 7, 8
# --world-verts-gpu: the world's vertex array (GfxWorldVertexData, mark 9) moved from the virtual block (4) to the
# physical one (6): from the zone pool to the zone's GPU memory (the Vita: video memory, where the world's vertex buffer
# reads it in place). The zone's version tells the loader (db_load.cpp Load_GfxWorldVertexData)
MARK_WORLD_VERTS = 9
BLOCK_VIRTUAL, BLOCK_PHYSICAL = 4, 6
ASSET_GFXWORLD = 0x11
ASSET_XMODEL = 0x5
ASSET_NAMES = ["xmodelpieces", "physpreset", "physconstraints", "destructibledef", "xanim", "xmodel", "material",
               "techniqueset", "image", "sound", "soundpatch", "clipmap", "clipmap_pvs", "comworld", "gameworld_sp",
               "gameworld_mp", "map_ents", "gfxworld", "lightdef", "ui_map", "font", "menulist", "menu", "localize",
               "weapon", "weapondef", "weaponvariant", "snddriverglobals", "fx", "impactfx", "aitype", "mptype",
               "mpbody", "mphead", "character", "xmodelalias", "rawfile", "stringtable", "packindex", "xglobals",
               "ddl", "glasses", "emblemset"]
VERSION_WORLD_GPU = VERSION | 0x10000
# --world-verts-compact: the world's vertices in 36 bytes instead of 44 (the engine's r_gfx.h GfxWorldVertexCompact;
# db_load.cpp Load_GfxWorldVertexData): position, color, texture and lightmap coordinates and normal exactly as they
# are; the tangent and binormal sign (normal mapping's - no Vita program reads them from the world) left out. Vita only
# (the PC's normal mapping needs them). The zone's version tells the loader
VERSION_WORLD_COMPACT = VERSION | 0x20000
SND_FORMAT_MSADPCM, SND_FORMAT_WMA = 6, 7
WMA_TARGET_RATE = 16000
# bytes a channel of an MS ADPCM block: ffmpeg's encoder takes powers of 2 only (the game's own ADPCM has 262); the
# Vita's driver reads it from block_size, which the game's loaded sounds otherwise fill with 67072 (a stream size)
ADPCM_BLOCK = 256
BLOCK_NAMES = ["temp", "runtime", "large_runtime", "physical_runtime", "virtual", "large", "physical"]


def read_ff(path):
    raw = open(path, "rb").read()
    if raw[:8] != HEADER or struct.unpack_from("<I", raw, 8)[0] not in (VERSION, VERSION_WORLD_GPU, VERSION_WORLD_COMPACT):
        sys.exit("%s: not an unsigned version %d fastfile" % (path, VERSION))
    d = zlib.decompressobj()
    stream = d.decompress(raw[12:]) + d.flush()
    return raw[:12], stream, d.unused_data


def read_zmap(path):
    raw = open(path, "rb").read()
    if raw[:8] != b"BO1ZMAP1":
        sys.exit("%s: not a zone map" % path)
    zone = raw[8:72].split(b"\0")[0].decode()
    body = raw[72:]
    return zone, [REC.unpack_from(body, i) for i in range(0, len(body), REC.size)]


def snd_header(stream, a):
    """the snd_asset fields a conversion needs: (frame_count, frame_rate, channel_count, format)"""
    frames, rate, channels = struct.unpack_from("<III", stream, a + 4)
    fmt = struct.unpack_from("<I", stream, a + 28)[0]
    return frames, rate, channels, fmt


# ---- loaded sounds: WMA -> MS ADPCM (the Vita has no WMA decoder), MS ADPCM -> MS ADPCM at a lower rate ----------

# the game's own MS ADPCM: 262 bytes a channel, 512 frames a block, the standard coefficients (a WAV's fmt extension)
GAME_ADPCM_BLOCK = 262
GAME_ADPCM_FRAMES = 512
ADPCM_COEFS = ((256, 0), (512, -256), (0, 0), (192, 64), (240, 0), (460, -208), (392, -232))
ADPCM_FRAMES = (ADPCM_BLOCK - 7) * 2 + 2  # 500: the frames of one of our blocks
ADPCM_TARGET_RATE = 24000                 # --shrink-adpcm: loaded MS ADPCM sounds above it are resampled to it
SND_FLAG_LOOPING = 1
SOUND_CHECK = "v2"                        # (in the cache key of checked conversions)
# --mono-3d: stereo loaded sounds that only 3D aliases play (the PC zone reader's snddump_usage.txt: name, 3D aliases,
# other aliases, channels) become mono - their first channel, all a 3D voice plays of them (snd.cpp SND_SetupVoice:
# Snd_Pan3d fills the speaker map for input 0 only; the second input's volumes stay 0). Half their data, no audible
# change. Names lower case
MONO_NAMES = frozenset()


def adpcm_wav(channels, rate, frames, data):
    """the game's MS ADPCM data as a WAV ffmpeg decodes"""
    align = GAME_ADPCM_BLOCK * channels
    ext = struct.pack("<HH", GAME_ADPCM_FRAMES, len(ADPCM_COEFS)) + b"".join(struct.pack("<hh", *c) for c in ADPCM_COEFS)
    fmt = struct.pack("<HHIIHHH", 2, channels, rate, rate * align // GAME_ADPCM_FRAMES, align, 4, len(ext)) + ext
    body = (b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"fact" + struct.pack("<I", 4)
            + struct.pack("<I", frames) + b"data" + struct.pack("<I", len(data)) + data)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def xwma(channels, rate, seek, data):
    """a WMA sound's data as an XWMA ffmpeg decodes (block align as iSND_CreateVoice describes it to XAudio2)"""
    block_align = 4096 if channels != 1 else 2230
    payload = data[:len(seek) // 4 * block_align]
    fmt = struct.pack("<HHIIHHH", 0x161, channels, rate, 6000 * channels, block_align, 16, 0)
    riff = (b"XWMA" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"dpds" + struct.pack("<I", len(seek)) + seek
            + b"data" + struct.pack("<I", len(payload)) + payload)
    return b"RIFF" + struct.pack("<I", len(riff)) + riff


def wav_chunks(wav):
    """(block align, fact frames, data) of a WAV ffmpeg wrote"""
    pos, frames, data, align = 12, None, None, None
    while pos + 8 <= len(wav):
        cid, size = wav[pos:pos + 4], struct.unpack_from("<I", wav, pos + 4)[0]
        body = wav[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            align = struct.unpack_from("<H", body, 12)[0]
        elif cid == b"fact":
            frames = struct.unpack_from("<I", body, 0)[0]
        elif cid == b"data":
            data = body
        pos += 8 + size + (size & 1)
    return align, frames, data


class FfmpegError(RuntimeError):
    pass


def run_ffmpeg(ffmpeg, args):
    r = subprocess.run([ffmpeg, "-v", "error", "-y"] + args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                       creationflags=NO_WINDOW)
    if r.returncode:
        raise FfmpegError("ffmpeg exit %d: %s" % (r.returncode, r.stderr.decode(errors="replace").strip()[:300]))


def pcm_check(ref, out, channels, looping):
    """None if the converted sound (out, s16) matches the reference (the same resampling without the ADPCM encode),
    else why not: its length, level and shape (correlation), and for a loop the seam"""
    a, b = array.array("h"), array.array("h")
    a.frombytes(ref[:len(ref) // 2 * 2])
    b.frombytes(out[:len(out) // 2 * 2])
    if looping and len(b) != len(a):
        return "loop length %d frames, expected %d" % (len(b) // channels, len(a) // channels)
    if len(b) < len(a) or len(b) > len(a) + ADPCM_FRAMES * channels:  # one-shots: the last block's padding only
        return "length %d frames, expected %d" % (len(b) // channels, len(a) // channels)
    b = b[:len(a)]
    n = len(a)
    if not n:
        return None
    sa = math.fsum(x * x for x in a)
    sb = math.fsum(x * x for x in b)
    if sa < n * 4.0:  # silent (rms under 2): the result must be quiet too
        return None if sb < n * 64.0 else "silence became sound"
    ratio = math.sqrt(sb / sa)
    corr = math.fsum(x * y for x, y in zip(a, b)) / math.sqrt(sa * sb) if sb else 0.0
    if not 0.7 <= ratio <= 1.3 or corr < 0.9:
        return "level %.2f, correlation %.3f" % (ratio, corr)
    if looping and n > 2 * channels:
        # the seam: the step from the last frame to the first, against the sound's own steps
        steps = sorted(abs(a[i + channels] - a[i]) for i in range(0, n - channels, max(channels, n // 20000 * channels)))
        typical = steps[int(len(steps) * 0.99)] if steps else 0
        seam = max(abs(b[c] - b[n - channels + c]) for c in range(channels))
        own = max(abs(a[c] - a[n - channels + c]) for c in range(channels))  # a loop that was not smooth to begin with
        if seam > max(2 * typical, 512, 2 * own):
            return "loop seam jumps %d (the sound's steps up to %d, its own seam %d)" % (seam, typical, own)
    return None


def convert_sound(job, ffmpeg, cache_dir):
    """job: (format, channels, rate, frames, looping, seek bytes, data bytes) -> (adpcm bytes, frames, rate, problem):
    MS ADPCM in our 256-byte blocks. A loop gets a rate near the target that makes it a whole number of blocks (the
    Vita's driver plays whole blocks: padding at a loop's end would be a gap each time round), resampled as three
    copies of itself of which the middle one is kept (no edge at the seam). Each result is decoded again and compared
    with the same resampling without the encode (pcm_check): problem is None when it matches. Cached by content."""
    fmt, channels, rate, frames, looping, seek, data, out_channels = job
    target = WMA_TARGET_RATE if fmt == SND_FORMAT_WMA else ADPCM_TARGET_RATE
    target = min(target, rate) if rate else target  # (a mono-only conversion of a sound already below the target)
    if fmt == SND_FORMAT_MSADPCM and not frames:
        frames = len(data) // (GAME_ADPCM_BLOCK * channels) * GAME_ADPCM_FRAMES
    key = hashlib.sha1(struct.pack("<IIIII", fmt, channels, rate, frames, looping) + SOUND_CHECK.encode()
                       + struct.pack("<I", target) + (b"mono" if out_channels != channels else b"") + seek + data
                       ).hexdigest()
    cached = os.path.join(cache_dir, key + ".snd")
    if os.path.isfile(cached):
        raw = open(cached, "rb").read()
        new_frames, new_rate, plen = struct.unpack_from("<III", raw, 0)
        problem = raw[12:12 + plen].decode() or None
        return raw[12 + plen:], new_frames, new_rate, problem
    work = tempfile.mkdtemp(prefix="bo1snd_")
    try:
        src, dst, ref, dec = (os.path.join(work, n) for n in ("in.wav", "out.wav", "ref.raw", "dec.raw"))
        with open(src, "wb") as f:
            f.write(xwma(channels, rate, seek, data) if fmt == SND_FORMAT_WMA else adpcm_wav(channels, rate, frames, data))
        if looping and frames:
            loop = max(1, round(frames * target / float(rate) / ADPCM_FRAMES)) * ADPCM_FRAMES
            new_rate = max(1, round(loop * rate / float(frames)))
            inp = ["-stream_loop", "2", "-i", src]
            chain = ["-af", "aresample=%d,atrim=start_sample=%d:end_sample=%d" % (new_rate, loop, 2 * loop)]
        else:
            new_rate = target
            inp = ["-i", src]
            chain = ["-af", "aresample=%d" % new_rate]
            if fmt == SND_FORMAT_MSADPCM and frames:  # the frames it has, not its last block's padding
                chain = ["-af", "atrim=end_sample=%d,aresample=%d" % (frames, new_rate)]
        if out_channels != channels:  # (the first channel: what a 3D voice plays of the stereo sound)
            chain = [chain[0], "pan=mono|c0=c0," + chain[1]]
        unreadable = None
        try:
            run_ffmpeg(ffmpeg, inp + chain + ["-ac", str(out_channels), "-c:a", "adpcm_ms", "-block_size",
                                              str(ADPCM_BLOCK * out_channels), dst])
        except FfmpegError as e:
            # a sound ffmpeg cannot read (a mod's odd WMA): silence of its length instead of failing the zone - the
            # Vita has no WMA decoder, so it would be silent there anyway
            unreadable = str(e)
            seconds = (frames / float(rate)) if frames and rate else 0.1
            if looping and frames:
                seconds *= 3  # (the loop's chain keeps the middle of three copies)
            inp = ["-f", "lavfi", "-t", "%.4f" % max(seconds, 0.02), "-i",
                   "anullsrc=r=%d:cl=%s" % (rate or target, "stereo" if channels == 2 else "mono")]
            chain = ["-af", "aresample=%d" % new_rate] if not (looping and frames) else chain
            run_ffmpeg(ffmpeg, inp + chain + ["-ac", str(out_channels), "-c:a", "adpcm_ms", "-block_size",
                                              str(ADPCM_BLOCK * out_channels), dst])
        run_ffmpeg(ffmpeg, inp + chain + ["-ac", str(out_channels), "-f", "s16le", "-c:a", "pcm_s16le", ref])
        wav = open(dst, "rb").read()
        align, new_frames, adpcm = wav_chunks(wav)
        if adpcm is None or align != ADPCM_BLOCK * out_channels:
            raise RuntimeError("ffmpeg made no MS ADPCM of %d-byte blocks (block align %s)" % (
                ADPCM_BLOCK * out_channels, align))
        if new_frames is None:
            new_frames = len(adpcm) // align * ADPCM_FRAMES
        run_ffmpeg(ffmpeg, ["-i", dst, "-f", "s16le", "-c:a", "pcm_s16le", dec])
        problem = pcm_check(open(ref, "rb").read(), open(dec, "rb").read(), out_channels, looping and frames)
        if looping and frames and not problem and len(adpcm) != loop // ADPCM_FRAMES * align:
            problem = "loop data is %d bytes, expected %d whole blocks" % (len(adpcm), loop // ADPCM_FRAMES)
        if unreadable:
            problem = "unreadable, replaced by silence (%s)" % unreadable
    finally:
        shutil.rmtree(work, ignore_errors=True)
    p = (problem or "").encode()
    with open(cached, "wb") as f:
        f.write(struct.pack("<III", new_frames, new_rate, len(p)) + p + adpcm)
    return adpcm, new_frames, new_rate, problem


ASSET_XANIM = 4
ZONE_ASSET_LIST = 36  # the XAssetList after the 36-byte header: string count, strings, asset count, assets


def model_names_at(stream, events):
    """{lower-case name: (stream offset of its inline name string, asset index)} of the zone's models"""
    out, index = {}, None
    for kind, block, extra, a, b, c in events:
        if kind == ZM_ASSET:
            index = a if extra == ASSET_XMODEL else None
        elif kind == ZM_MARK_BEGIN and extra == model_lowpoly.MARK_XMODEL and a != INLINE_PTR and index is not None:
            if struct.unpack_from("<I", stream, a)[0] == INLINE_PTR:
                at = a + model_lowpoly.XMODEL_SIZE
                name = stream[at:stream.index(bytes(1), at)].decode("latin-1").lower()
                out.setdefault(name, (at, index))
            index = None
    return out


def referenced_models(stream, events):
    """the zone's models another of its assets points at (an alias to the model's asset list slot or its struct):
    -> set of lower-case names"""
    count = struct.unpack_from("<i", stream, ZONE_ASSET_LIST + 8)[0]
    starts = [i for i, e in enumerate(events) if e[0] == ZM_ASSET]
    lists = [e for e in events[:starts[0]] if e[0] == ZM_READ and e[5] == 8 * count] if starts else []
    targets = {}
    if lists:  # the list's block offset: its INC
        i = events.index(lists[0])
        inc = next(e for e in events[i + 1:] if e[0] == ZM_INC)
        list_block, list_at = inc[1], inc[3]
    else:
        list_block = list_at = None
    names = model_names_at(stream, events)
    by_index = {index: name for name, (at, index) in names.items()}
    if list_at is not None:
        for index, name in by_index.items():
            for k in range(8):
                targets[(list_block, list_at + 8 * index + k)] = name
    # the struct's own block offset (a MARK_XMODEL's struct: the READ at that stream offset, placed by its INC)
    last, struct_at = None, {}
    marks = {a for kind, block, extra, a, b, c in events if kind == ZM_MARK_BEGIN and extra == model_lowpoly.MARK_XMODEL}
    for kind, block, extra, a, b, c in events:
        if kind == ZM_READ and c:
            last = b
        elif kind == ZM_INC and last is not None:
            if last in marks:
                struct_at[last] = (block, a)
            last = None
    for name, (at, index) in names.items():
        sa = struct_at.get(at - model_lowpoly.XMODEL_SIZE)
        if sa:
            for k in range(model_lowpoly.XMODEL_SIZE):
                targets[(sa[0], sa[1] + k)] = name
    hit = set()
    for kind, block, extra, a, b, c in events:
        if kind == ZM_OFFSET and b:
            t = ((b - 1) >> 29, (b - 1) & 0x1FFFFFFF)
            if t in targets:
                hit.add(targets[t])
    return hit


def plan_drop_assets(stream, events, dead_anims, keep=frozenset()):
    """anims (XAnimParts assets) emptied: the game finds anims by name (XAnimFindData_FastFile) and the converter's PC
    runs list the ones in use (bo1_livemodels: looked up, or a registered weapon's). Each other one keeps its struct
    and its name (weapon definitions share the name string: they point at it) and leaves out the rest - bone names,
    notifies, delta, frame data, indices: their counts and pointers 0, as an anim with no bones (it moves nothing).
    keep: asset indices left as they are (something points into their other data).
    -> ({index of the first event left out: asset index}, {offset: bytes}, count, bytes) or None"""
    starts = [i for i, e in enumerate(events) if e[0] == ZM_ASSET]
    if not starts:
        return None
    bounds = starts + [len(events)]
    drop_from, patches, count, nbytes = {}, {}, 0, 0
    for k, i in enumerate(starts):
        ev = events[i]
        if ev[2] != ASSET_XANIM or ev[3] in keep:
            continue
        reads = [e for e in events[i:bounds[k + 1]] if e[0] == ZM_READ and e[5]]
        if len(reads) < 3 or reads[0][5] != 104 or struct.unpack_from("<I", stream, reads[0][4])[0] != INLINE_PTR:
            continue
        raw = stream[reads[1][4]:reads[1][4] + reads[1][5]]
        if not raw.endswith(bytes(1)) or raw[:-1].decode("latin-1").lower() not in dead_anims:
            continue
        h = reads[0][4]
        # (from the event after the name's INC: the first left-out array's ALIGN comes before its READ, and the loader,
        # loading none of them, does not align either - 1 byte of difference broke every pointer after, 2026-11-24)
        name_at = next(j for j in range(i, bounds[k + 1]) if events[j][0] == ZM_READ and events[j][4] == reads[1][4])
        inc_at = next(j for j in range(name_at + 1, bounds[k + 1]) if events[j][0] == ZM_INC)
        drop_from[inc_at + 1] = ev[3]
        patches[h + 4] = bytes(10)          # data counts (numframes at +14 stays: the anim's length)
        patches[h + 17] = bytes(3)         # bDelta, bLeftHandGripIK, bStreamable
        patches[h + 20] = bytes(17)         # streamedFileSize, boneCount[10], notifyCount, assetType 0
        patches[h + 40] = bytes(8)          # randomDataShortCount, indexCount
        patches[h + 64] = bytes(40)         # names .. deltaPart
        count += 1
        nbytes += sum(e[5] for e in reads[2:])
    return (drop_from, patches, count, nbytes) if drop_from else None

INLINE_PTR = 0xFFFFFFFF
# --rename-models: models of a mod's zone that step aside for the game's own model of that name (the converter's
# "originals instead of the mod's copies": WDS's Black Ops III perk machines, 4.7 MB, give way to the game's) - their
# name's first letter becomes "~" (a mod zone's asset wins over a game zone's of the same name: renamed, it no longer
# does) and their geometry is left out. Only for models the game finds by name (setModel, precacheModel): one another
# asset of the zone points at (referenced_models) would draw nothing
RENAME_MODELS = frozenset()
VERIFY_POINTERS = False
DROP_ASSET = 100  # Replay's dropping state for a whole asset left out


class Replay:
    """the loader's block positions for the original stream and for the re-packed one"""

    def __init__(self, stream, events, drop_marks, convert=frozenset(), sound_results=None, models=None, images=None,
                 world_gpu=False, collision=None):
        self.stream = stream
        self.collision = collision      # model_collision.Plan: models' collision triangles replaced, or None
        self.read_replace = {}          # stream offset of a read -> (new bytes, [(orig rel start, end, new rel)])
        self.replace_pieces = None      # those pieces, for the INC that places the new bytes
        self.drop_from = {}             # plan_drop_assets: index of an emptied anim's first left-out event
        self.read_pos = {}              # stream offset of a read -> (block, orig block offset) its INC placed it at
        self.last_read = None
        self.dedup_seen = []            # surfaces that left out their copy (model_lowpoly.DEDUP)
        # --verify-pointers: every kept read's new place and every re-encoded pointer, checked after build
        self.placed = [[] for _ in range(7)]  # per block: (event index, new start, length, stream offset)
        self.checks = []                      # (event index, field stream offset, block, orig offset, new offset)
        self.ev_i = 0
        self.drop_ranges = [[] for _ in range(7)]  # per block: (orig start, end, asset index) of their data
        self.asset_unsafe = set()       # dropped assets something else points into (a name string shared)
        self.world_gpu = world_gpu      # --world-verts-gpu
        self.moving = False             # inside mark 9: the virtual block's room placed in the physical block
        self.moved = []                 # (orig virtual start, orig end, new physical start) of the moved data
        self.moved_bytes = 0
        self.events = events
        self.drop_marks = drop_marks
        self.models = models            # model_lowpoly.Plan: models' left-out and replaced surfaces, or None
        self.images = images            # lightmap_pack.Plan: lightmaps' new pixels, or None
        self.model_owner = None         # the model of the surface being left out or replaced
        self.replacing = None           # that surface's new data: bytes for each read still to come
        self.replace_size = None        # the size the next INC places (the new data's)
        self.model_ranges = [([], []) for _ in range(7)]  # per block: orig starts; (end, model, replaced)
        self.model_unsafe = set()       # models whose left-out / replaced data something points at
        self.convert = convert          # sound formats to convert (SND_FORMAT_WMA, SND_FORMAT_MSADPCM)
        self.sound_results = sound_results  # snd_asset stream offset -> convert_sound's result; None: collect jobs
        self.sound_jobs = {}            # snd_asset stream offset -> convert_sound's job
        self.sounds_kept = []           # (snd_asset offset, why): MS ADPCM sounds whose conversion did not match
        self.inserts = {}               # stream offset -> bytes put in place of the removed range starting there
        self.converted = {}             # format -> [sounds, bytes before, bytes after]
        self.removed = []      # stream ranges (start, end) left out
        self.patches = {}      # stream offset -> bytes written there
        self.intervals = [[] for _ in range(7)]  # per block: (orig start, orig end, new start), in event order
        self.starts = [[] for _ in range(7)]     # orig starts of the above (blocks 1..6 only grow: bisect)
        self.orig = [0] * 7
        self.new = [0] * 7
        self.orig_max = [0] * 7
        self.new_max = [0] * 7
        self.temp_new_at = {0: 0}  # temp block: new position for each original position it reached
        self.offsets = 0
        self.offsets_changed = 0
        self.dropped = {}      # mark kind -> [count, stream bytes]
        self.errors = []
        # --budget: each asset's share of the new blocks (the loader's DB_ZoneMap_Asset event comes before each asset
        # loads, and blocks 1..6 only grow: what they grew until the next asset is this one's)
        self.assets = []       # [type, index, first XModel mark's struct offset or None, [bytes in blocks 0..6]]
        self.asset_at = None   # self.new when the current asset began

    def model_range(self, block, off):
        """(start, model, replaced) of the left-out / replaced model data holding original offset off, or None"""
        starts, ranges = self.model_ranges[block]
        i = bisect.bisect_right(starts, off) - 1
        if i >= 0 and off < ranges[i][0]:
            return (starts[i],) + ranges[i][1:]
        return None

    def note_model_range(self, block, start, end, replaced):
        self.model_ranges[block][0].append(start)
        self.model_ranges[block][1].append((end, self.model_owner, replaced))

    def lookup(self, block, off):
        """new block offset of original block offset 'off' (inside data placed by an INC)"""
        ivs = self.intervals[block]
        if block == 0:  # the temp block is reused: the latest data there counts
            for start, end, new in reversed(ivs):
                if start <= off < end:
                    return new + (off - start)
            return None
        i = bisect.bisect_right(self.starts[block], off) - 1
        if i >= 0:
            start, end, new = ivs[i]
            if off < end:
                return new + (off - start)
        return None

    def run(self):
        dropping = 0
        drop_start = None
        sound = None  # a WMA sound's data being replaced: [snd_asset offset, data bytes, first stream offset, block]
        seek = None   # its seek table being dropped: [snd_asset offset, bytes]
        for self.ev_i, ev in enumerate(self.events):
            kind, block, extra, a, b, c = ev
            if kind == ZM_ASSET:
                if dropping == DROP_ASSET:
                    dropping = 0
                self.close_asset()
                self.assets.append([extra, a, None, None])
                self.asset_at = list(self.new)
                continue
            if kind == ZM_MARK_BEGIN and extra == MARK_XMODEL and self.assets and self.assets[-1][2] is None:
                self.assets[-1][2] = a
            if kind == ZM_READ and c:
                self.last_read = b
            if self.ev_i in self.drop_from and not dropping:  # (an emptied anim's data: to the next asset)
                if sound is not None or self.replacing is not None:
                    self.errors.append("an emptied anim inside other re-packed data")
                dropping = DROP_ASSET
                self.dropped.setdefault(DROP_ASSET, [0, 0])[0] += 1
            if kind == ZM_READ and c and b in self.read_replace:
                # the same read, other bytes (the INC that follows places them)
                new, self.replace_pieces = self.read_replace[b]
                self.removed.append((b, b + c))
                self.inserts[b] = new
                self.replace_size = len(new)
                continue
            if kind == ZM_READ and self.collision and c and b in self.collision.replace:
                # a model's simplified collision triangles: the same read, smaller (the INC that follows places them)
                if dropping or sound is not None or self.replacing is not None:
                    self.errors.append("collision triangles inside other re-packed data")
                self.model_owner, new = self.collision.replace[b]
                self.removed.append((b, b + c))
                self.inserts[b] = new
                self.replace_size = len(new)
                continue
            if kind == ZM_READ:
                if self.replacing is not None and c:
                    if not self.replacing:
                        self.errors.append("surface at stream %#x: more reads than its new data" % b)
                        continue
                    new = self.replacing.pop(0)
                    self.removed.append((b, b + c))
                    self.inserts[b] = new
                    self.replace_size = len(new)
                elif sound is not None and c:
                    self.removed.append((b, b + c))
                    sound[1] += self.stream[b:b + c]
                    if sound[2] is None:
                        sound[2] = b
                elif dropping and c:
                    self.removed.append((b, b + c))
                    self.dropped[dropping][1] += c
                    if seek is not None:
                        seek[1] += self.stream[b:b + c]
            elif kind == ZM_ALIGN:
                if self.orig[block] != a:
                    self.errors.append("align in block %d at %#x, expected %#x" % (block, a, self.orig[block]))
                self.orig[block] = b
                if self.moving and block == BLOCK_VIRTUAL:  # the moved array's alignment, in the physical block
                    self.new[BLOCK_PHYSICAL] = (self.new[BLOCK_PHYSICAL] + extra - 1) & ~(extra - 1)
                    self.note(BLOCK_VIRTUAL)
                    self.note(BLOCK_PHYSICAL)
                    continue
                if not dropping:
                    self.new[block] = (self.new[block] + extra - 1) & ~(extra - 1)
                self.note(block)
            elif kind == ZM_INC:
                if self.orig[block] != a:
                    self.errors.append("inc in block %d at %#x, expected %#x" % (block, a, self.orig[block]))
                inc_read = self.last_read
                if self.last_read is not None:
                    self.read_pos[self.last_read] = (block, a)
                    self.last_read = None
                if self.moving and block == BLOCK_VIRTUAL:  # the moved array's room, in the physical block
                    if dropping or sound is not None or self.replace_size is not None:
                        self.errors.append("the world's vertices inside other re-packed data")
                    if b:
                        self.moved.append((a, a + b, self.new[BLOCK_PHYSICAL]))
                    self.new[BLOCK_PHYSICAL] += b
                    self.moved_bytes += b
                    self.orig[block] = a + b
                    self.note(BLOCK_VIRTUAL)
                    self.note(BLOCK_PHYSICAL)
                    continue
                if sound is not None:
                    sound[3] = block  # the original data's room: the new data takes it (at MARK_END)
                elif self.replace_pieces is not None:  # (the asset list without the dropped entries)
                    for start, end, new in self.replace_pieces:
                        self.intervals[block].append((a + start, a + end, self.new[block] + new))
                        if block:
                            self.starts[block].append(a + start)
                    self.new[block] += self.replace_size
                    self.replace_size = self.replace_pieces = None
                elif self.replace_size is not None:
                    # a replaced surface array: only its start maps (pointers to the whole array: the surfaces
                    # sharing it, model_lowpoly's aliases); anything pointing inside it is caught at ZM_OFFSET.
                    # A lightmap's pixels (lightmap_pack): in the temp block, which nothing points into
                    if block in (2, 3) or (block == 0 and self.model_owner is not None):
                        self.errors.append("replaced data in block %d" % block)
                    self.intervals[block].append((a, a + 1, self.new[block]))
                    if block:
                        self.starts[block].append(a)
                        self.note_model_range(block, a, a + b, True)
                    self.new[block] += self.replace_size
                    self.replace_size = None
                elif dropping:
                    if block in (2, 3):
                        self.errors.append("dropped data in delayed block %d" % block)
                    if dropping == DROP_ASSET and b and block:  # (the temp block is reused: not kept track of)
                        self.drop_ranges[block].append((a, a + b, self.assets[-1][1]))
                    if dropping == MARK_XSURFACE and b and block:
                        self.note_model_range(block, a, a + b, False)
                else:
                    if b:
                        self.intervals[block].append((a, a + b, self.new[block]))
                        if block:
                            self.starts[block].append(a)
                        if inc_read is not None and inc_read not in self.inserts:
                            self.placed[block].append((self.ev_i, self.new[block], b, inc_read))
                    self.new[block] += b
                self.orig[block] = a + b
                self.note(block)
            elif kind == ZM_RESET:
                if self.orig[block] != a:
                    self.errors.append("reset in block %d at %#x, expected %#x" % (block, a, self.orig[block]))
                if b not in self.temp_new_at:
                    self.errors.append("reset of block %d to %#x, a position it never reached" % (block, b))
                self.orig[block] = b
                self.new[block] = self.temp_new_at.get(b, b)
            elif kind == ZM_OFFSET:
                if dropping:
                    continue
                self.offsets += 1
                if a == 0xFFFFFFFF:
                    self.errors.append("offset %#x in a field not read from the stream" % b)
                    continue
                if self.stream[a:a + 4] != struct.pack("<I", b):
                    self.errors.append("offset field at stream %#x holds %s, map says %#x" % (a, self.stream[a:a + 4].hex(), b))
                    continue
                tblock, toff = (b - 1) >> 29, (b - 1) & 0x1FFFFFFF
                hit = self.model_range(tblock, toff) if self.models else None
                if hit is not None:
                    # left-out or replaced model data: only an alias surface's pointer to a replaced array's start
                    # may lead there; anything else keeps that model as it is
                    start, owner, replaced = hit
                    if not (replaced and toff == start and a in self.models.alias_fields):
                        self.model_unsafe.add(owner)
                        continue
                moved = [m for m in self.moved if m[0] <= toff < m[1]] if tblock == BLOCK_VIRTUAL else None
                if moved:  # into the moved vertex array: its place in the physical block
                    tblock, new = BLOCK_PHYSICAL, moved[0][2] + (toff - moved[0][0])
                else:
                    new = self.lookup(tblock, toff)
                if new is None:
                    hits = [r[2] for r in self.drop_ranges[tblock] if r[0] <= toff < r[1]]
                    if hits:  # into a dropped asset: it stays (replayed again without it)
                        self.asset_unsafe.update(hits)
                        continue
                    self.errors.append("offset %#x (block %d %#x) points at no loaded data" % (b, tblock, toff))
                    continue
                enc = ((tblock << 29) | new) + 1
                self.checks.append((self.ev_i, a, tblock, toff, new))
                if enc != b:
                    self.offsets_changed += 1
                    self.patches[a] = struct.pack("<I", enc)
            elif kind == ZM_MARK_BEGIN and extra in (MARK_SND_SEEK, MARK_SND_DATA):
                if a == 0xFFFFFFFF or not self.convertible(a):
                    continue
                if dropping or sound is not None:
                    self.errors.append("nested sound marks")
                    continue
                if extra == MARK_SND_SEEK:  # dropped: seek_table_count (+40) and seek_table (+44) to 0
                    dropping = extra
                    self.dropped.setdefault(extra, [0, 0])[0] += 1
                    seek = [a, bytearray()]
                    self.patches[a + 40] = b"\0\0\0\0"
                    self.patches[a + 44] = b"\0\0\0\0"
                else:
                    sound = [a, bytearray(), None, None]
            elif kind == ZM_MARK_END and extra in (MARK_SND_SEEK, MARK_SND_DATA):
                if extra == MARK_SND_SEEK and seek is not None:
                    self.seek_bytes = getattr(self, "seek_bytes", {})
                    self.seek_bytes[seek[0]] = bytes(seek[1])
                    seek = None
                    dropping = 0
                elif extra == MARK_SND_DATA and sound is not None:
                    self.end_sound(sound)
                    sound = None
            elif kind == ZM_MARK_BEGIN and extra == MARK_WORLD_VERTS:
                if self.world_gpu:
                    if dropping or sound is not None or self.replacing is not None:
                        self.errors.append("the world's vertices inside other re-packed data")
                    self.moving = True
            elif kind == ZM_MARK_END and extra == MARK_WORLD_VERTS:
                self.moving = False
            elif kind == ZM_MARK_BEGIN and extra == MARK_XMODEL:
                if self.models and a in self.models.struct_patches:
                    self.patches.update(self.models.struct_patches[a])
            elif kind == ZM_MARK_BEGIN and extra == MARK_XSURFACE and self.models and a in self.models.dedup:
                # a copy of an earlier surface's data: left out, its pointers set to that one's (finish_dedup)
                self.model_owner = self.models.dedup[a][0]
                if dropping:
                    self.errors.append("nested drop marks")
                dropping = extra
                self.dropped.setdefault(extra, [0, 0])[0] += 1
                self.dedup_seen.append(a)
            elif kind == ZM_MARK_BEGIN and extra == MARK_XSURFACE:
                if self.models and a in self.models.replace:
                    self.model_owner, patches, data, _ = self.models.replace[a]
                    self.replacing = list(data)
                    self.patches.update(patches)
                elif self.models and a in self.models.empty:
                    self.model_owner = self.models.empty[a]
                    if dropping:
                        self.errors.append("nested drop marks")
                    dropping = extra
                    self.dropped.setdefault(extra, [0, 0])[0] += 1
                    # an empty surface: vertListCount +1, vertCount and triCount +4, triIndices +12,
                    # vertInfo (vertCount[4], vertsBlend, tensionData) +16, verts0 +32, vertList +40
                    self.patches[a + 1] = b"\0"
                    self.patches[a + 4] = bytes(4)
                    self.patches[a + 12] = bytes(4)
                    self.patches[a + 16] = bytes(16)
                    self.patches[a + 32] = bytes(4)
                    self.patches[a + 40] = bytes(4)
            elif kind == ZM_MARK_BEGIN and extra == MARK_IMAGE_DATA:
                if self.images and a in self.images.replace:
                    self.model_owner = None
                    self.replacing = list(self.images.replace[a])
            elif kind == ZM_MARK_END and extra == MARK_IMAGE_DATA:
                if self.replacing:
                    self.errors.append("a replaced image has %d arrays of new data left" % len(self.replacing))
                self.replacing = None
            elif kind == ZM_MARK_END and extra == MARK_XSURFACE:
                if dropping == extra:
                    dropping = 0
                if self.replacing:
                    self.errors.append("a replaced surface has %d arrays of new data left" % len(self.replacing))
                self.replacing = None
            elif kind == ZM_MARK_BEGIN:
                if extra in self.drop_marks:
                    if dropping:
                        self.errors.append("nested drop marks")
                    dropping = extra
                    self.dropped.setdefault(extra, [0, 0])[0] += 1
                    # the load def: program pointer (+0) and programSize (+4) to 0, so the loader reads nothing
                    self.patches[a] = b"\0\0\0\0"
                    self.patches[a + 4] = b"\0\0"
            elif kind == ZM_MARK_END:
                if dropping == extra:
                    dropping = 0
        self.close_asset()
        return self

    def close_asset(self):
        if self.assets and self.assets[-1][3] is None:
            self.assets[-1][3] = [max(0, self.new[k] - self.asset_at[k]) if k else 0 for k in range(7)]

    def budget(self, stream, events):
        """what the re-packed zone's memory holds, by asset type and model: {"pool": MB, "gpu": MB, "types":
        {type name: [pool MB, gpu MB, count]}, "models": [[name, pool MB, gpu MB]] (the biggest first)}. pool: blocks
        1, 2, 4, 5 (the Vita's zone pool), gpu: 3 and 6 (video memory)"""
        mb = 1048576.0
        names, extra = {}, {}
        if any(e[0] == ZM_MARK_BEGIN and e[2] == MARK_XMODEL for e in events):
            models, _ = model_lowpoly.collect(stream, events)
            names = {m.a: m.name for m in models}
            # [kind ("weapon" / "model"), LODs it has, MB one more left-out LOD would save (the LOD it starts at now,
            # the second: --drop-lods-models; 0 when it cannot)]
            for m in models:
                lods = model_lowpoly.lod_table(stream, m) if m.name else None
                weapon = bool(m.name) and model_lowpoly.is_weapon(m.name)
                save = 0.0
                if lods and len(lods) > 2 and not weapon:
                    count, first = lods[1]
                    save = sum(c for _, reads in m.surfaces[first:first + count] for _, c in reads) / mb
                extra[m.a] = ["weapon" if weapon else "model", len(lods or ()), save]
        types, models = {}, []
        for t, _, model_at, sizes in self.assets:
            if sizes is None:
                continue
            pool = (sizes[1] + sizes[2] + sizes[4] + sizes[5]) / mb
            gpu = (sizes[3] + sizes[6]) / mb
            name = ASSET_NAMES[t] if t < len(ASSET_NAMES) else str(t)
            row = types.setdefault(name, [0.0, 0.0, 0])
            row[0] += pool
            row[1] += gpu
            row[2] += 1
            if t == ASSET_XMODEL and model_at is not None:
                models.append([names.get(model_at) or "?", pool, gpu] + extra.get(model_at, ["", 0, 0.0]))
        models.sort(key=lambda r: -(r[1] + r[2]))
        return {"pool": sum(r[0] for r in types.values()), "gpu": sum(r[1] for r in types.values()),
                "types": types, "models": models}

    def out_channels(self, a):
        """the converted sound's channels: 1 for a stereo sound only 3D aliases play (MONO_NAMES, by the LoadedSound's
        name: inline right after the snd_asset, its pointer just before it)"""
        channels = snd_header(self.stream, a)[2]
        if channels != 2 or not MONO_NAMES or struct.unpack_from("<I", self.stream, a - 4)[0] != 0xFFFFFFFF:
            return channels
        end = self.stream.find(b"\0", a + 56)
        name = self.stream[a + 56:end].decode("latin-1").lower() if end > 0 else ""
        return 1 if name in MONO_NAMES else channels

    def convertible(self, a):
        """the loaded sound (snd_asset at stream offset a) is one to convert"""
        frames, rate, channels, fmt = snd_header(self.stream, a)
        if fmt not in self.convert or channels not in (1, 2):
            return False
        if fmt == SND_FORMAT_WMA:
            return True
        mono = self.out_channels(a) != channels
        # the game's own MS ADPCM: whole 262-byte blocks, no seek table, above the target rate; a loop whole blocks
        # (as the game's own loops are), a one-shot's frames ending in its last block
        seek_count, _, data_size = struct.unpack_from("<III", self.stream, a + 40)
        blocks, rest = divmod(data_size, GAME_ADPCM_BLOCK * channels)
        looping = struct.unpack_from("<I", self.stream, a + 36)[0] & SND_FLAG_LOOPING
        whole = blocks * GAME_ADPCM_FRAMES
        frames_ok = frames in (0, whole) if looping else (not frames or whole - GAME_ADPCM_FRAMES < frames <= whole)
        return (rate > ADPCM_TARGET_RATE * 1.05 or mono) and not seek_count and blocks and not rest and frames_ok

    def end_sound(self, sound):
        """a sound's data: collect the job, or put the converted data in its place (an MS ADPCM sound whose
        conversion did not match keeps its own)"""
        a, data, first, block = sound
        frames, rate, channels, fmt = snd_header(self.stream, a)
        looping = struct.unpack_from("<I", self.stream, a + 36)[0] & SND_FLAG_LOOPING
        seek = getattr(self, "seek_bytes", {}).get(a, b"")
        problem = None
        out_channels = self.out_channels(a)
        if self.sound_results is None:
            self.sound_jobs[a] = (fmt, channels, rate, frames, looping, seek, bytes(data), out_channels)
            adpcm, new_frames, new_rate = b"", 0, rate
        else:
            adpcm, new_frames, new_rate, problem = self.sound_results[a]
        if first is None or block is None:
            self.errors.append("sound at stream %#x: no data read" % a)
            return
        if problem and fmt == SND_FORMAT_MSADPCM:
            self.sounds_kept.append((a, problem))
            adpcm = bytes(data)
        self.inserts[first] = adpcm
        self.intervals[block].append((self.orig[block] - len(data), self.orig[block], self.new[block]))
        if block:
            self.starts[block].append(self.orig[block] - len(data))
        self.new[block] += len(adpcm)
        self.note(block)
        if problem and fmt == SND_FORMAT_MSADPCM:
            return
        # frame_count: a sound that had none (0: custom ones) keeps none - the engine reads 0 as "not given"
        self.patches[a + 4] = struct.pack("<I", new_frames if frames else 0)  # frame_count
        self.patches[a + 8] = struct.pack("<I", new_rate)            # frame_rate
        self.patches[a + 20] = struct.pack("<I", ADPCM_BLOCK * out_channels)  # block_size: the ADPCM blocks' bytes
        if out_channels != channels:  # (--mono-3d: one channel, the left speaker's flag as the game's mono sounds)
            self.patches[a + 12] = struct.pack("<I", out_channels)   # channel_count
            self.patches[a + 32] = struct.pack("<I", 1)              # channel_flags
            self.mono = getattr(self, "mono", 0) + 1
        self.patches[a + 28] = struct.pack("<I", SND_FORMAT_MSADPCM) # format
        self.patches[a + 48] = struct.pack("<I", len(adpcm))         # data_size
        c = self.converted.setdefault(fmt, [0, 0, 0])
        c[0] += 1
        c[1] += len(data)
        c[2] += len(adpcm)

    def note(self, block):
        if self.orig[block] > self.orig_max[block]:
            self.orig_max[block] = self.orig[block]
        if self.new[block] > self.new_max[block]:
            self.new_max[block] = self.new[block]
        if block == 0:
            self.temp_new_at[self.orig[0]] = self.new[0]

    def finish_dedup(self):
        """the left-out copies' pointer fields: the new place of the data they share (after run)"""
        for s in self.dedup_seen:
            for field, at in self.models.dedup[s][1].items():
                if at is None or at not in self.read_pos:
                    self.errors.append("shared surface data at stream %#x: no place known" % (at or 0))
                    continue
                block, orig = self.read_pos[at]
                new = self.lookup(block, orig)
                if new is None:
                    self.errors.append("shared surface data at stream %#x: not loaded" % at)
                    continue
                self.patches[s + field] = struct.pack("<I", ((block << 29) | new) + 1)

    def verify_pointers(self, new_stream):
        """-> [problems]: each re-encoded pointer whose new target holds other bytes than its original target did"""
        import bisect as _b
        # the original layout: block -> [(event index, start, end, stream offset)] (READ then INC)
        orig = [[] for _ in range(7)]
        last = None
        for i, (kind, block, extra, a, b, c) in enumerate(self.events):
            if kind == ZM_READ and c:
                last = b
            elif kind == ZM_INC:
                if last is not None and b:
                    orig[block].append((i, a, a + b, last))
                last = None
        removed = sorted(self.removed)
        starts = [r[0] for r in removed]
        cum, total = [], 0
        for r0, r1 in removed:
            total += (r1 - r0) - len(self.inserts.get(r0, b""))
            cum.append(total)

        def shift(x):
            k = _b.bisect_right(starts, x) - 1
            while k >= 0 and removed[k][1] > x:  # (inside a removed range: not kept)
                return None
            return x - (cum[k] if k >= 0 else 0)

        def index(entries, lo, length_of):
            """block>0 data never moves: sorted by start for bisect; block 0 (reused): scanned by event order"""
            ents = sorted(entries, key=lambda e: e[lo])
            return ents, [e[lo] for e in ents]

        def find(block, ents, keys, ev, off, lo, size):
            if block:
                k = _b.bisect_right(keys, off) - 1
                while k >= 0:
                    e = ents[k]
                    if off < e[lo] + size(e):
                        return e
                    k -= 1
                    if k >= 0 and keys[k] + 0x100000 < off:
                        break
                return None
            best = None
            for e in ents:
                if e[0] < ev and e[lo] <= off < e[lo] + size(e):
                    if best is None or e[0] > best[0]:
                        best = e
            return best

        self.verify_skipped = {}
        patched = set()
        for at, data in self.patches.items():
            patched.update(range(at, at + len(data)))
        problems = []
        oi = [index(x, 1, None) for x in orig]
        ni = [index(x, 1, None) for x in self.placed]
        for ev, field, block, toff, new in self.checks:
            o = find(block, oi[block][0], oi[block][1], ev, toff, 1, lambda e: e[2] - e[1])
            if o is None:
                self.verify_skipped["no original target"] = self.verify_skipped.get("no original target", 0) + 1
                continue
            so = o[3] + (toff - o[1])
            n = find(block, ni[block][0], ni[block][1], ev, new, 1, lambda e: e[2])
            if n is None:
                self.verify_skipped["new target replaced/inserted"] = self.verify_skipped.get(
                    "new target replaced/inserted", 0) + 1
                continue  # (replaced or inserted data: other bytes by design)
            ns = shift(n[3])
            if ns is None:
                self.verify_skipped["new target removed"] = self.verify_skipped.get("new target removed", 0) + 1
                continue
            ns += new - n[1]
            want = self.stream[so:so + 8]
            got = new_stream[ns:ns + 8]
            # (bytes the repack patches on purpose - other pointers, counts - are not compared)
            same = all(want[i] == got[i] for i in range(min(len(want), len(got))) if (so + i) not in patched)
            if not same:
                problems.append((ev, field, block, toff, new, want.hex(), got.hex()))
        return problems

    def build(self):
        """the re-packed stream (header fields updated)"""
        out = bytearray()
        pos = 0
        removed = sorted(self.removed)
        patches = sorted(self.patches.items())
        pi = 0
        for start, end in removed + [(len(self.stream), len(self.stream))]:
            chunk = bytearray(self.stream[pos:start])
            while pi < len(patches) and patches[pi][0] < start:
                off, data = patches[pi]
                if off >= pos:
                    chunk[off - pos:off - pos + len(data)] = data
                pi += 1
            out += chunk
            out += self.inserts.get(start, b"")  # a converted sound's data in place of its original
            pos = end
        size, external = struct.unpack_from("<II", self.stream, 0)
        blocks = list(struct.unpack_from("<7I", self.stream, 8))
        new_blocks = []
        for b in range(7):
            slack = blocks[b] - self.orig_max[b]  # what the zone asked for beyond what its data used
            new_blocks.append(self.new_max[b] + max(slack, 0))
        struct.pack_into("<II7I", out, 0, len(out) - 36, external, *new_blocks)
        return bytes(out), blocks, new_blocks


def world_vertex_read(events):
    """(stream offset, size) of the read that loads the world's vertex array, or None: the first read inside mark 9, or
    in a zone map recorded before the mark existed the gfxworld asset's biggest read of a multiple of 44 bytes"""
    inside, world, best = False, False, None
    for kind, block, extra, a, b, c in events:
        if kind == ZM_ASSET:
            world = extra == ASSET_GFXWORLD
        elif kind == ZM_MARK_BEGIN and extra == MARK_WORLD_VERTS:
            inside = True
        elif kind == ZM_MARK_END and extra == MARK_WORLD_VERTS:
            inside = False
        elif kind == ZM_READ and c and inside:
            return (b, c)
        elif kind == ZM_READ and c and world and c % 44 == 0 and (best is None or c > best[1]):
            best = (b, c)
    return best if best and best[1] >= 44 * 1000 else None


def compact_world_vertices(data):
    """the 44-byte vertices -> 36 bytes (VERSION_WORLD_COMPACT): (new bytes, 0, 0.0) - every value kept is exact"""
    out = bytearray()
    for i in range(0, len(data), 44):  # xyz 0..12, (sign 12..16), color 16..20, coordinates 20..36, normal 36..40
        out += data[i:i + 12] + data[i + 16:i + 40]
    return bytes(out), 0, 0.0


def report(zone, rp, blocks, new_blocks, stream_in, stream_out):
    MB = 1024.0 * 1024.0
    print("%s: %d events, %d block offsets (%d re-encoded)" % (zone, len(rp.events), rp.offsets, rp.offsets_changed))
    for kind, (count, nbytes) in sorted(rp.dropped.items()):
        print("  dropped %d %s, %.2f MB of stream" % (count, MARK_NAMES.get(kind, kind), nbytes / MB))
    for fmt, (n, before, after) in sorted(rp.converted.items()):
        print("  converted %d %s sounds to ~%d Hz MS ADPCM: %.2f -> %.2f MB" % (
            n, "WMA" if fmt == SND_FORMAT_WMA else "MS ADPCM", WMA_TARGET_RATE if fmt == SND_FORMAT_WMA else
            ADPCM_TARGET_RATE, before / MB, after / MB))
    if rp.images and (rp.images.primaries or rp.images.halved):
        print("  lightmaps: %d primary left out, %d halved: %.2f -> %.2f MB" % (
            rp.images.primaries, rp.images.halved, rp.images.bytes[0] / MB, rp.images.bytes[1] / MB))
    if rp.models and getattr(rp.models, "dedup", None):
        print("  %d surfaces share an identical earlier one's data (%.2f MB of copies left out)" % (
            len(rp.models.dedup), rp.models.dedup_bytes / 1048576.0))
    if getattr(rp, "anims_dropped", None):
        print("  %d anims nothing uses emptied, %.2f MB of stream" % rp.anims_dropped)
    if rp.collision and rp.collision.surfaces:
        print("  collision: %d model surfaces simplified, %d -> %d triangles" % (
            rp.collision.surfaces, rp.collision.tris[0], rp.collision.tris[1]))
    if getattr(rp, "mono", 0):
        print("  %d stereo sounds only 3D aliases play made mono" % rp.mono)
    for a, why in rp.sounds_kept:
        print("  kept MS ADPCM sound at stream %#x as it was: %s" % (a, why))
    if getattr(rp, "compact", None):
        n, clamped, largest = rp.compact
        print("  world vertices compact: %d vertices, %.2f -> %.2f MB (tangent and binormal sign left out)" % (
            n, 44 * n / MB, 36 * n / MB))
    if rp.models:
        m = rp.models
        kept = ", ".join("%d %s" % (n, why) for why, n in sorted(m.skipped.items()))
        if getattr(m, "sight_checks", None):
            checks = m.sight_checks
            print("  sights: %d first-person weapons checked, view change max %.1f%% (kept: %d over %.1f%%)" % (
                len(checks), 100 * max(checks), m.skipped.get("sights would change", 0),
                100 * model_lowpoly.SIGHT_MAX_CHANGE))
        if getattr(m, "emptied_models", 0):
            print("  %d models nothing shows any more left without geometry" % m.emptied_models)
        print("  low poly: %d models without LOD 0 (%d without LOD 1 too); %d simplified, %d -> %d triangles (%d surfaces "
              "sharing theirs); not touched: %s" % (m.dropped_models, m.extra_dropped, m.simplified[0], m.simplified[1],
                                                    m.simplified[2], m.aliases, kept or "none"))
    print("  stream %.2f -> %.2f MB" % (stream_in / MB, stream_out / MB))
    for b in range(7):
        if blocks[b] or new_blocks[b]:
            print("  block %-16s %10d -> %10d (%+.2f MB)" % (BLOCK_NAMES[b], blocks[b], new_blocks[b],
                                                          (new_blocks[b] - blocks[b]) / MB))


def convert_sounds(stream, events, ffmpeg, cache_dir, formats):
    """the zone's loaded sounds of these formats -> MS ADPCM (in parallel, ffmpeg processes): snd_asset offset ->
    convert_sound's result; a WMA sound whose result did not match is reported (MS ADPCM ones keep their data)"""
    jobs = Replay(stream, events, set(), convert=formats).run().sound_jobs
    if not jobs:
        return {}
    os.makedirs(cache_dir, exist_ok=True)
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=FFMPEG_THREADS) as pool:
        futures = {pool.submit(convert_sound, job, ffmpeg, cache_dir): a for a, job in jobs.items()}
        for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
            results[futures[future]] = future.result()
            if done % 200 == 0:
                print("  converted %d of %d sounds" % (done, len(jobs)))
    for a, (adpcm, frames, rate, problem) in sorted(results.items()):
        if problem and jobs[a][0] == SND_FORMAT_WMA:
            print("  WARNING: WMA sound at stream %#x does not match its conversion: %s" % (a, problem))
    return results


def process(mode, ff, zmap, out, drop_shaders, convert_wma=False, ffmpeg="ffmpeg", cache_dir="build/vita-sndcache",
            drop_lod0=False, low_poly=False, shrink_adpcm=False, shrink_lightmaps=False, world_gpu=False,
            budget=None, collision=False, dead_anims=None, world_compact=False):
    head, stream, trailer = read_ff(ff)
    zone, events = read_zmap(zmap)
    consumed = max((e[4] + e[5] for e in events if e[0] == ZM_READ), default=0)
    if consumed != len(stream):
        sys.exit("%s: the map read %d stream bytes, the file has %d (map of another version?)" % (zone, consumed, len(stream)))
    anims_keep = set()
    renamed, rename_refs, rename_names = [], set(), {}
    if RENAME_MODELS and mode == "repack":  # (the models that step aside: also left without geometry)
        rename_names = model_names_at(stream, events)
        rename_refs = referenced_models(stream, events)
        renamed = [n for n in sorted(RENAME_MODELS) if n in rename_names and n not in rename_refs]
        model_lowpoly.EMPTY_MODELS = frozenset(model_lowpoly.EMPTY_MODELS | set(renamed))

    drop = {1, 2} if (mode == "repack" and drop_shaders) else set()
    convert = frozenset(([SND_FORMAT_WMA] if convert_wma else []) + ([SND_FORMAT_MSADPCM] if shrink_adpcm else [])) \
        if mode == "repack" else frozenset()
    lod0 = mode == "repack" and (drop_lod0 or low_poly)
    simplify = mode == "repack" and low_poly
    if lod0 and not any(e[0] == ZM_MARK_BEGIN and e[2] == MARK_XMODEL for e in events):
        print("%s: no model marks in the map (no models, or recorded before the marks existed)" % zone)
    results = convert_sounds(stream, events, ffmpeg, cache_dir, convert) if convert else None
    compact = None  # --world-verts-compact: (read offset, new bytes, (vertices, clamped, largest change))
    if world_compact and mode == "repack":
        if world_gpu:
            sys.exit("%s: --world-verts-compact and --world-verts-gpu together are not supported" % zone)
        at = world_vertex_read(events)
        if at is None:
            print("  %s: no world vertex array found (compact: nothing to do)" % zone)
        else:
            made = compact_world_vertices(stream[at[0]:at[0] + at[1]])
            if isinstance(made, str):
                print("  %s: world vertices NOT compact: %s" % (zone, made))
            else:
                compact = (at[0], made[0], (at[1] // 44, made[1], made[2]))
    keep, cache = set(), {}
    while True:  # models whose left-out / replaced data something points at stay as they are; replay without them
        anims = plan_drop_assets(stream, events, dead_anims, frozenset(anims_keep))             if (mode == "repack" and dead_anims) else None
        models = model_lowpoly.plan(stream, events, lod0, simplify, frozenset(keep), cache) if lod0 else None
        images = lightmap_pack.plan(stream, events) if (mode == "repack" and shrink_lightmaps) else None
        coll = model_collision.plan(stream, events, frozenset(keep)) if (mode == "repack" and collision) else None
        rp = Replay(stream, events, drop, convert=convert, sound_results=results, models=models, images=images,
                    world_gpu=mode == "repack" and world_gpu, collision=coll)
        if compact:  # (the same read, 36 bytes a vertex: only the array's start is pointed at)
            rp.read_replace[compact[0]] = (compact[1], [(0, 1, 0)])
            rp.compact = compact[2]
        if coll:
            rp.patches.update(coll.patches)
        if anims:
            rp.drop_from = anims[0]
            rp.patches.update(anims[1])
        if models:
            rp.patches.update(models.alias_patches)
        if images:
            rp.patches.update(images.patches)
        rp.run()
        if rp.models and rp.models.dedup:
            rp.finish_dedup()
        if rp.asset_unsafe - anims_keep:  # (anims whose data something points into: kept, replayed again)
            anims_keep |= rp.asset_unsafe
            continue
        if not rp.model_unsafe or rp.model_unsafe <= keep:  # (nothing new to keep: replaying again changes nothing)
            break
        keep |= rp.model_unsafe
    if anims:
        rp.anims_dropped = (anims[2], anims[3] / 1048576.0)
    if RENAME_MODELS and mode == "repack":
        for n in renamed:
            rp.patches[rename_names[n][0]] = b"~"
        print("  %d models step aside for the game's own (renamed, geometry left out)%s" % (
            len(renamed), "; kept, pointed at: " + ", ".join(sorted(n for n in RENAME_MODELS if n in rename_refs))
            if any(n in rename_refs for n in RENAME_MODELS) else ""))
    new_stream, blocks, new_blocks = rp.build()
    if VERIFY_POINTERS:
        problems = rp.verify_pointers(new_stream)
        print("  verify: %d pointers checked, %d lead to other bytes than before; not compared: %s" % (
            len(rp.checks), len(problems), rp.verify_skipped))
        for pr in problems[:12]:
            print("    event %d field %#x block %d orig %#x new %#x: was %s, now %s" % pr)
    report(zone, rp, blocks, new_blocks, len(stream), len(new_stream))
    if budget:  # (--budget: the zone's memory by asset type and model, as JSON)
        import json
        info = rp.budget(stream, events)
        info["zone"] = zone
        info["blocks"] = new_blocks
        with open(budget, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=0)
        top = sorted(info["types"].items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:6]
        print("  budget: pool %.2f MB, gpu %.2f MB; %s" % (info["pool"], info["gpu"], ", ".join(
            "%s %.1f+%.1f" % (k, v[0], v[1]) for k, v in top)))
    if compact:  # the loader reads 36-byte vertices (db_load.cpp Load_GfxWorldVertexData)
        head = head[:8] + struct.pack("<I", VERSION_WORLD_COMPACT) + head[12:]
    if rp.moved_bytes:  # the loader reads the moved array into the physical block (db_load.cpp)
        head = head[:8] + struct.pack("<I", VERSION_WORLD_GPU) + head[12:]
        print("  world vertices in the physical block: %.2f MB" % (rp.moved_bytes / 1048576.0))
    elif world_gpu and mode == "repack" and struct.unpack_from("<I", head, 8)[0] == VERSION and \
            any(e[0] == ZM_ASSET and e[2] == ASSET_GFXWORLD for e in events):
        print("  %s: a world but no world vertex mark in the map (recorded before the mark existed)" % zone)
    if rp.errors:
        for e in rp.errors[:20]:
            print("  ERROR " + e)
        sys.exit("%s: %d errors" % (zone, len(rp.errors)))
    if mode == "check":
        if new_stream != stream or new_blocks != blocks:
            first = next((i for i in range(min(len(new_stream), len(stream))) if new_stream[i] != stream[i]), None)
            sys.exit("%s: replay without changes differs from the original (first byte %s)" % (zone, first))
        print("  check ok: the replay reproduces the zone byte for byte")
        return
    if not out:
        sys.exit("repack needs an output path")
    if os.path.abspath(out) == os.path.abspath(ff):
        sys.exit("refusing to overwrite the input zone")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "wb") as f:
        f.write(head)
        f.write(zlib.compress(new_stream, 9))
        f.write(trailer)
    print("  wrote %s (%.2f MB, was %.2f MB)" % (out, os.path.getsize(out) / 1048576.0, os.path.getsize(ff) / 1048576.0))


def main():
    global ADPCM_TARGET_RATE  # (--adpcm-rate)
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["check", "repack", "all"])
    ap.add_argument("ff", help="zone .ff (all: the zone folder)")
    ap.add_argument("zmap", help="zone map (all: the folder of maps)")
    ap.add_argument("out", nargs="?", help="output .ff (all: the output folder)")
    ap.add_argument("--drop-shaders", action="store_true")
    ap.add_argument("--convert-wma", action="store_true")
    ap.add_argument("--shrink-adpcm", action="store_true")
    ap.add_argument("--shrink-lightmaps", action="store_true")
    ap.add_argument("--drop-lod0", action="store_true")
    ap.add_argument("--low-poly", action="store_true")
    ap.add_argument("--world-verts-gpu", action="store_true",
                    help="the world's vertex array in the zone's physical (GPU) block instead of the zone pool")
    ap.add_argument("--world-verts-compact", action="store_true",
                    help="the world's vertices in 36 bytes instead of 44 (Vita engine; lightmap coordinates 16-bit)")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--sound-cache", default="build/vita-sndcache")
    # a big mod (hundreds of weapon models) does not fit the Vita at the retail budgets: < 1 lowers the triangle
    # budgets by that factor and lets the simplifier move the surface that much further (its error limit / factor)
    ap.add_argument("--low-poly-scale", type=float, default=1.0)
    # a big mod short of video memory (its loaded sounds live there, zone blocks of the physical type): --shrink-adpcm
    # resamples the game's MS ADPCM sounds above this rate to it (16000: two thirds of the default's data; WMA sounds
    # become 16 kHz anyway). Checked like every conversion; part of the sound cache's key
    ap.add_argument("--adpcm-rate", type=int, default=ADPCM_TARGET_RATE)
    # a big map's models: one more of their own LODs left out (model_lowpoly.EXTRA_LOD_DROP) - for every model with
    # one ('all') or only where the LOD would be simplified ('over')
    ap.add_argument("--drop-lods", choices=["over", "all"])
    # the re-packed zone's memory by asset type and model (JSON; check / repack)
    ap.add_argument("--budget")
    # a file of model names (one a line): each of them one more of its own LODs lower (as --drop-lods, for these only)
    ap.add_argument("--drop-lods-models")
    # models' collision surfaces over model_collision.BUDGET triangles simplified
    ap.add_argument("--simplify-collision", action="store_true")
    # models whose geometry is left out (a file of names, one a line: model_lowpoly.EMPTY_MODELS)
    ap.add_argument("--empty-models")
    # anims left out whole (a file of names, one a line: those no PC run looked up)
    ap.add_argument("--drop-anims")
    # stereo sounds only 3D aliases play made mono: the zone reader's snddump_usage.txt (see MONO_NAMES)
    ap.add_argument("--mono-3d")
    # --low-poly-scale for the weapons' models too (they keep the retail budgets otherwise: model_lowpoly.is_weapon)
    ap.add_argument("--low-poly-weapons", nargs="?", const="all", choices=["all", "gentle", "gentle-world"])
    # the gentle weapons' own budget scale (model_lowpoly.WEAPON_BUDGETS)
    ap.add_argument("--weapon-scale", type=float)
    # the characters' and zombies' own scale (model_lowpoly.CHARACTER_BUDGETS / CHARACTER_ERRORS)
    ap.add_argument("--character-scale", type=float)
    # models that give way to the game's own of the same name (a file of names: RENAME_MODELS)
    ap.add_argument("--rename-models")
    # every re-encoded pointer checked: its new target holds what its old one did (Replay.verify_pointers)
    ap.add_argument("--verify-pointers", action="store_true")
    # surfaces with the same data as an earlier one share it (model_lowpoly.DEDUP)
    ap.add_argument("--dedup-surfaces", action="store_true")
    args = ap.parse_args()
    model_lowpoly.SCALE_WEAPONS = {"all": True, "gentle": "gentle", "gentle-world": "gentle-world"}.get(
        args.low_poly_weapons, False)
    dead_anims = frozenset(line.split("\t")[0].strip().lower() for line in open(
        args.drop_anims, encoding="latin-1") if line.strip()) if args.drop_anims else None
    if args.rename_models:
        global RENAME_MODELS
        RENAME_MODELS = frozenset(line.split("\t")[0].strip().lower() for line in open(
            args.rename_models, encoding="latin-1") if line.strip())
    if args.empty_models:
        model_lowpoly.EMPTY_MODELS = frozenset(
            line.split("	")[0].strip().lower() for line in open(args.empty_models, encoding="latin-1") if line.strip())
    if args.mono_3d:
        global MONO_NAMES
        names = set()
        for line in open(args.mono_3d, encoding="latin-1"):
            f = line.rstrip("\n").split("\t")
            if len(f) == 4 and f[3] == "2" and int(f[1]) > 0 and int(f[2]) == 0:
                names.add(f[0].lower())
        MONO_NAMES = frozenset(names)
    model_lowpoly.EXTRA_LOD_DROP = args.drop_lods
    if args.drop_lods_models:
        model_lowpoly.EXTRA_LOD_MODELS = frozenset(
            line.strip().lower() for line in open(args.drop_lods_models, encoding="utf-8") if line.strip())
        print("  %d models one LOD lower" % len(model_lowpoly.EXTRA_LOD_MODELS))
    if args.adpcm_rate != ADPCM_TARGET_RATE:
        ADPCM_TARGET_RATE = args.adpcm_rate
        print("  MS ADPCM sounds above %d Hz resampled to it" % ADPCM_TARGET_RATE)
    model_lowpoly.DEDUP = args.dedup_surfaces
    global VERIFY_POINTERS
    VERIFY_POINTERS = args.verify_pointers
    if args.character_scale:
        model_lowpoly.CHARACTER_BUDGETS = {kind: max(200, int(v * args.character_scale * args.low_poly_scale))
                                           for kind, v in model_lowpoly.BASE_BUDGETS.items()}
        model_lowpoly.CHARACTER_ERRORS = {kind: v / (args.character_scale * args.low_poly_scale)
                                          for kind, v in model_lowpoly.BASE_ERRORS.items()}
        print("  characters' budgets %s" % model_lowpoly.CHARACTER_BUDGETS)
    if args.weapon_scale:
        model_lowpoly.WEAPON_BUDGETS = {kind: max(200, int(v * args.weapon_scale))
                                        for kind, v in model_lowpoly.BASE_BUDGETS.items()}
        print("  weapons' budgets %s" % model_lowpoly.WEAPON_BUDGETS)
    if args.low_poly_scale != 1.0:
        for kind in model_lowpoly.BUDGETS:
            model_lowpoly.BUDGETS[kind] = max(200, int(model_lowpoly.BUDGETS[kind] * args.low_poly_scale))
            model_lowpoly.TARGET_ERRORS[kind] = model_lowpoly.TARGET_ERRORS[kind] / args.low_poly_scale
        print("  low poly budgets %s, errors %s" % (model_lowpoly.BUDGETS, model_lowpoly.TARGET_ERRORS))
    if args.mode != "all":
        process(args.mode, args.ff, args.zmap, args.out, args.drop_shaders, args.convert_wma, args.ffmpeg,
                args.sound_cache, args.drop_lod0, args.low_poly, args.shrink_adpcm, args.shrink_lightmaps,
                args.world_verts_gpu, args.budget, args.simplify_collision, dead_anims, args.world_verts_compact)
        return
    if not args.out:
        sys.exit("all needs an output folder")
    if os.path.abspath(args.out).startswith(os.path.abspath(args.ff)):
        sys.exit("refusing to write into the input zone folder")
    for name in sorted(os.listdir(args.zmap)):
        if not name.endswith(".zmap"):
            continue
        zone = name[:-5]
        found = [(sub, os.path.join(args.ff, sub, zone + ".ff")) for sub in sorted(os.listdir(args.ff))
                 if os.path.isfile(os.path.join(args.ff, sub, zone + ".ff"))]
        if not found:
            print("%s: no .ff under %s" % (zone, args.ff))
            continue
        sub, ff = found[0]
        process("repack", ff, os.path.join(args.zmap, name), os.path.join(args.out, sub, zone + ".ff"),
                args.drop_shaders, args.convert_wma, args.ffmpeg, args.sound_cache, args.drop_lod0, args.low_poly,
                args.shrink_adpcm, args.shrink_lightmaps, args.world_verts_gpu, None, args.simplify_collision,
                world_compact=args.world_verts_compact)


if __name__ == "__main__":
    main()
