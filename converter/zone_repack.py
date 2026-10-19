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
--drop-shaders: the D3D9 vertex / pixel shader programs of every technique (the Vita renderer brings its own);
the load defs keep their size field at 0 and a null program, so the loader reads nothing for them. Zones made with
it are for the Vita (or a PC dedicated server) only: a PC client cannot create its shaders from them.

Never writes next to its input: the input .ff files are the game's own.
"""
import argparse
import bisect
import concurrent.futures
import hashlib
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

HEADER = b"IWffu100"
VERSION = 473
REC = struct.Struct("<BBHIII")
ZM_READ, ZM_ALIGN, ZM_INC, ZM_RESET, ZM_OFFSET, ZM_MARK_BEGIN, ZM_MARK_END, ZM_ASSET = range(1, 9)
MARK_NAMES = {1: "vertex shader programs", 2: "pixel shader programs", 3: "WMA seek tables", 4: "WMA sound data"}
MARK_SND_SEEK, MARK_SND_DATA = 3, 4
SND_FORMAT_MSADPCM, SND_FORMAT_WMA = 6, 7
WMA_TARGET_RATE = 16000
# bytes a channel of an MS ADPCM block: ffmpeg's encoder takes powers of 2 only (the game's own ADPCM has 262); the
# Vita's driver reads it from block_size, which the game's loaded sounds otherwise fill with 67072 (a stream size)
ADPCM_BLOCK = 256
BLOCK_NAMES = ["temp", "runtime", "large_runtime", "physical_runtime", "virtual", "large", "physical"]


def read_ff(path):
    raw = open(path, "rb").read()
    if raw[:8] != HEADER or struct.unpack_from("<I", raw, 8)[0] != VERSION:
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


def wma_to_adpcm(job, ffmpeg, cache_dir):
    """job: (channels, rate, seek table bytes, data bytes) -> (adpcm bytes, frame count); cached by content"""
    channels, rate, seek, data = job
    key = hashlib.sha1(struct.pack("<II", channels, rate) + seek + data).hexdigest()
    cached = os.path.join(cache_dir, key + ".adpcm")
    if os.path.isfile(cached):
        raw = open(cached, "rb").read()
        return raw[4:], struct.unpack_from("<I", raw, 0)[0]
    block_align = 4096 if channels != 1 else 2230  # as iSND_CreateVoice describes it to XAudio2
    packets = len(seek) // 4
    payload = data[:packets * block_align]
    fmt = struct.pack("<HHIIHHH", 0x161, channels, rate, 6000 * channels, block_align, 16, 0)
    riff = (b"XWMA" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"dpds" + struct.pack("<I", len(seek)) + seek
            + b"data" + struct.pack("<I", len(payload)) + payload)
    work = tempfile.mkdtemp(prefix="bo1snd_")
    try:
        src, dst = os.path.join(work, "in.xwma"), os.path.join(work, "out.wav")
        with open(src, "wb") as f:
            f.write(b"RIFF" + struct.pack("<I", len(riff)) + riff)
        cmd = [ffmpeg, "-v", "error", "-y", "-i", src, "-ar", str(WMA_TARGET_RATE), "-ac", str(channels),
               "-c:a", "adpcm_ms", "-block_size", str(ADPCM_BLOCK * channels), dst]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        wav = open(dst, "rb").read()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    # the WAV's chunks: fmt (its block align must be ours), fact (the frame count), data
    pos, frames, adpcm, align = 12, None, None, None
    while pos + 8 <= len(wav):
        cid, size = wav[pos:pos + 4], struct.unpack_from("<I", wav, pos + 4)[0]
        body = wav[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            align = struct.unpack_from("<H", body, 12)[0]
        elif cid == b"fact":
            frames = struct.unpack_from("<I", body, 0)[0]
        elif cid == b"data":
            adpcm = body
        pos += 8 + size + (size & 1)
    if adpcm is None or align != ADPCM_BLOCK * channels:
        raise RuntimeError("ffmpeg made no MS ADPCM of %d-byte blocks (block align %s)" % (ADPCM_BLOCK * channels, align))
    if frames is None:  # whole blocks
        frames = len(adpcm) // align * ((ADPCM_BLOCK - 7) * 2 + 2)
    with open(cached, "wb") as f:
        f.write(struct.pack("<I", frames) + adpcm)
    return adpcm, frames


class Replay:
    """the loader's block positions for the original stream and for the re-packed one"""

    def __init__(self, stream, events, drop_marks, convert_wma=False, wma_results=None):
        self.stream = stream
        self.events = events
        self.drop_marks = drop_marks
        self.convert_wma = convert_wma
        self.wma_results = wma_results  # snd_asset stream offset -> (adpcm bytes, frames); None: collect jobs
        self.wma_jobs = {}              # snd_asset stream offset -> (channels, rate, seek bytes, data bytes)
        self.inserts = {}               # stream offset -> bytes put in place of the removed range starting there
        self.converted = [0, 0, 0]      # sounds, WMA bytes, ADPCM bytes
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
        for ev in self.events:
            kind, block, extra, a, b, c = ev
            if kind == ZM_READ:
                if sound is not None and c:
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
                if not dropping:
                    self.new[block] = (self.new[block] + extra - 1) & ~(extra - 1)
                self.note(block)
            elif kind == ZM_INC:
                if self.orig[block] != a:
                    self.errors.append("inc in block %d at %#x, expected %#x" % (block, a, self.orig[block]))
                if sound is not None:
                    sound[3] = block  # the original data's room: the new data takes it (at MARK_END)
                elif dropping:
                    if block in (2, 3):
                        self.errors.append("dropped data in delayed block %d" % block)
                else:
                    if b:
                        self.intervals[block].append((a, a + b, self.new[block]))
                        if block:
                            self.starts[block].append(a)
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
                new = self.lookup(tblock, toff)
                if new is None:
                    self.errors.append("offset %#x (block %d %#x) points at no loaded data" % (b, tblock, toff))
                    continue
                enc = ((tblock << 29) | new) + 1
                if enc != b:
                    self.offsets_changed += 1
                    self.patches[a] = struct.pack("<I", enc)
            elif kind == ZM_MARK_BEGIN and extra in (MARK_SND_SEEK, MARK_SND_DATA):
                if not self.convert_wma or a == 0xFFFFFFFF or snd_header(self.stream, a)[3] != SND_FORMAT_WMA:
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
        return self

    def end_sound(self, sound):
        """a WMA sound's data: collect the job, or put the converted data in its place"""
        a, data, first, block = sound
        frames, rate, channels, fmt = snd_header(self.stream, a)
        seek = getattr(self, "seek_bytes", {}).get(a, b"")
        if self.wma_results is None:
            self.wma_jobs[a] = (channels, rate, seek, bytes(data))
            adpcm, new_frames = b"", 0
        else:
            adpcm, new_frames = self.wma_results[a]
        if first is None or block is None:
            self.errors.append("WMA sound at stream %#x: no data read" % a)
            return
        self.inserts[first] = adpcm
        self.intervals[block].append((self.orig[block] - len(data), self.orig[block], self.new[block]))
        if block:
            self.starts[block].append(self.orig[block] - len(data))
        self.new[block] += len(adpcm)
        self.note(block)
        self.patches[a + 4] = struct.pack("<I", new_frames)          # frame_count
        self.patches[a + 8] = struct.pack("<I", WMA_TARGET_RATE)     # frame_rate
        self.patches[a + 20] = struct.pack("<I", ADPCM_BLOCK * channels)  # block_size: the ADPCM blocks' bytes
        self.patches[a + 28] = struct.pack("<I", SND_FORMAT_MSADPCM) # format
        self.patches[a + 48] = struct.pack("<I", len(adpcm))         # data_size
        self.converted[0] += 1
        self.converted[1] += len(data)
        self.converted[2] += len(adpcm)

    def note(self, block):
        if self.orig[block] > self.orig_max[block]:
            self.orig_max[block] = self.orig[block]
        if self.new[block] > self.new_max[block]:
            self.new_max[block] = self.new[block]
        if block == 0:
            self.temp_new_at[self.orig[0]] = self.new[0]

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


def report(zone, rp, blocks, new_blocks, stream_in, stream_out):
    MB = 1024.0 * 1024.0
    print("%s: %d events, %d block offsets (%d re-encoded)" % (zone, len(rp.events), rp.offsets, rp.offsets_changed))
    for kind, (count, nbytes) in sorted(rp.dropped.items()):
        print("  dropped %d %s, %.2f MB of stream" % (count, MARK_NAMES.get(kind, kind), nbytes / MB))
    if rp.converted[0]:
        print("  converted %d WMA sounds to %d Hz MS ADPCM: %.2f -> %.2f MB" % (rp.converted[0], WMA_TARGET_RATE,
                                                                             rp.converted[1] / MB, rp.converted[2] / MB))
    print("  stream %.2f -> %.2f MB" % (stream_in / MB, stream_out / MB))
    for b in range(7):
        if blocks[b] or new_blocks[b]:
            print("  block %-16s %10d -> %10d (%+.2f MB)" % (BLOCK_NAMES[b], blocks[b], new_blocks[b],
                                                          (new_blocks[b] - blocks[b]) / MB))


def convert_sounds(stream, events, ffmpeg, cache_dir):
    """the zone's WMA sounds -> MS ADPCM (in parallel, ffmpeg processes): snd_asset offset -> (bytes, frames)"""
    jobs = Replay(stream, events, set(), convert_wma=True).run().wma_jobs
    if not jobs:
        return {}
    os.makedirs(cache_dir, exist_ok=True)
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(2, os.cpu_count() or 2)) as pool:
        futures = {pool.submit(wma_to_adpcm, job, ffmpeg, cache_dir): a for a, job in jobs.items()}
        for done, future in enumerate(concurrent.futures.as_completed(futures), 1):
            results[futures[future]] = future.result()
            if done % 200 == 0:
                print("  converted %d of %d WMA sounds" % (done, len(jobs)))
    return results


def process(mode, ff, zmap, out, drop_shaders, convert_wma=False, ffmpeg="ffmpeg", cache_dir="build/vita-sndcache"):
    head, stream, trailer = read_ff(ff)
    zone, events = read_zmap(zmap)
    consumed = max((e[4] + e[5] for e in events if e[0] == ZM_READ), default=0)
    if consumed != len(stream):
        sys.exit("%s: the map read %d stream bytes, the file has %d (map of another version?)" % (zone, consumed, len(stream)))
    drop = {1, 2} if (mode == "repack" and drop_shaders) else set()
    convert = mode == "repack" and convert_wma
    results = convert_sounds(stream, events, ffmpeg, cache_dir) if convert else None
    rp = Replay(stream, events, drop, convert_wma=convert, wma_results=results).run()
    new_stream, blocks, new_blocks = rp.build()
    report(zone, rp, blocks, new_blocks, len(stream), len(new_stream))
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
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["check", "repack", "all"])
    ap.add_argument("ff", help="zone .ff (all: the zone folder)")
    ap.add_argument("zmap", help="zone map (all: the folder of maps)")
    ap.add_argument("out", nargs="?", help="output .ff (all: the output folder)")
    ap.add_argument("--drop-shaders", action="store_true")
    ap.add_argument("--convert-wma", action="store_true")
    ap.add_argument("--ffmpeg", default="ffmpeg")
    ap.add_argument("--sound-cache", default="build/vita-sndcache")
    args = ap.parse_args()
    if args.mode != "all":
        process(args.mode, args.ff, args.zmap, args.out, args.drop_shaders, args.convert_wma, args.ffmpeg,
                args.sound_cache)
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
                args.drop_shaders, args.convert_wma, args.ffmpeg, args.sound_cache)


if __name__ == "__main__":
    main()
