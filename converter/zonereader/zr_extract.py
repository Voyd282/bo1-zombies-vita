"""zr_extract.py - what the converter took from the engine's zone reader run (bo1_zonereader with bo1_zonemap,
bo1_imagestats, bo1_snddump), from the zones alone:

    python -I tools/zonereader/zr_extract.py <out dir> <zone.ff> [<zone.ff> ...]   (the zones in the engine's load order)

writes into <out dir>: zonemap/<zone>.zmap, record.log ('imagestats' and 'matstats' lines, as the engine logs them),
snddump_streams.txt and snddump_usage.txt (as sound/snd_db.cpp SND_BO1Dump writes them).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import zr_runtime  # noqa: E402
import zr_load_gen  # noqa: E402,F401

ASSET_SOUND, ASSET_SOUND_PATCH, ASSET_SNDDRIVER_GLOBALS = 9, 10, 0x1B


class Recorder(zr_runtime.Loader):
    """the loader with the engine's measurement output (r_image.cpp Load_Texture, db_registry.cpp Load_MaterialAsset)"""

    def __init__(self, zone, out):
        super().__init__(zone)
        self.out = out  # the log's lines

    def cstr(self, addr):
        if not addr:
            return None
        m, o = self.z.region(addr)
        end = m.index(b'\0', o)
        return m[o:end].decode('latin-1')

    def Load_Texture(self, remoteLoadDef, image):
        z = self.z
        loadDef = z.rd(remoteLoadDef, 'I')
        if not loadDef:
            return
        resource = z.rd(loadDef + 8, 'i')
        self.out.append("imagestats '%s' %s map %u sem %u cat %u size %ux%ux%u levels %u flags 0x%x format %d inline %d "
                        "external %d" % (z.name, self.cstr(z.rd(image + 44, 'I')), z.rd(image + 4, 'B'),
                                         z.rd(image + 5, 'B'), z.rd(image + 6, 'B'), z.rd(image + 20, 'H'),
                                         z.rd(image + 22, 'H'), z.rd(image + 24, 'H'), z.rd(loadDef, 'B'),
                                         z.rd(loadDef + 1, 'B'), z.rd(loadDef + 4, 'i'), resource,
                                         0 if resource else z.rd(image + 12, 'i')))

    def Load_MaterialAsset(self, materialPtr):
        z = self.z
        material = z.rd(materialPtr, 'I')
        if not material or not z.rd(material, 'I'):
            return
        line = "matstats '%s' %s" % (z.name, self.cstr(z.rd(material, 'I')))
        table = z.rd(material + 180, 'I')
        for i in range(z.rd(material + 170, 'B')):
            if len(line) >= 1024 - 80:
                break
            tex = table + 16 * i
            image = z.rd(tex + 12, 'I')
            if z.rd(tex + 7, 'B') != 11 and image:
                name = z.rd(image + 44, 'I')
                if name:
                    # (',name': a reference to an image of an earlier zone - the engine's pointer is that image, its
                    # name without the comma)
                    line += ' ' + self.cstr(name).lstrip(',')
        self.out.append(line)
        self.link_asset(materialPtr, 192)


class Sounds:
    """the sound assets of the loaded zones (the asset pool: a later zone's asset of a name replaces the earlier one)"""

    def __init__(self):
        self.banks = {}    # name: [(alias name, flags, pan, file)]
        self.patches = {}  # name: [file]
        self.pans = None   # the sound globals' pan names
        # file: ('loaded', name, channels) | ('stream', filename, primed) | None

    def file(self, r, addr):
        z = r.z
        if not addr:
            return None
        t = z.rd(addr + 4, 'B')
        u = z.rd(addr, 'I')
        if t == 1:
            return ('loaded', r.cstr(z.rd(u, 'I')) if u else None, z.rd(u + 4 + 12, 'I') if u else 0)
        if not u:
            return None
        return ('stream', r.cstr(z.rd(u, 'I')), 1 if z.rd(u + 4, 'I') else 0)

    def asset(self, r, type_, var):
        z = r.z
        header = z.rd(var + 4, 'I')
        if not header:
            return
        if type_ == ASSET_SOUND:
            aliases = []
            lists, count = z.rd(header + 8, 'I'), z.rd(header + 4, 'I')
            for l in range(count if lists else 0):
                lst = lists + 20 * l
                head, n = z.rd(lst + 8, 'I'), z.rd(lst + 12, 'i')
                for i in range(n if head else 0):
                    a = head + 84 * i
                    aliases.append((r.cstr(z.rd(a, 'I')), z.rd(a + 20, 'I'), z.rd(a + 75, 'B'),
                                    self.file(r, z.rd(a + 16, 'I'))))
            self.banks[r.cstr(z.rd(header, 'I'))] = aliases
        elif type_ == ASSET_SOUND_PATCH:
            files = []
            fl, n = z.rd(header + 16, 'I'), z.rd(header + 12, 'I')
            for i in range(n if fl else 0):
                files.append(self.file(r, fl + 8 * i))
            self.patches[r.cstr(z.rd(header, 'I'))] = files
        elif type_ == ASSET_SNDDRIVER_GLOBALS and r.cstr(z.rd(header, 'I')) == 'singleton':
            pans, n = z.rd(header + 24, 'I'), z.rd(header + 20, 'I')
            self.pans = [r.cstr(pans + 60 * i) for i in range(n if pans else 0)]

    def is3d(self, flags, pan):
        if not (flags & 2) or self.pans is None or pan >= len(self.pans):
            return False
        name = self.pans[pan]
        return bool(name) and name.lower() == 'default'

    def dump(self, out_dir):
        streams, primed, usage = set(), set(), {}

        def one(f, alias=None):
            if f and f[0] == 'loaded' and f[1]:
                use = usage.setdefault(f[1], [0, 0, 0])
                use[0 if alias is not None and self.is3d(*alias) else 1] += 1
                use[2] = f[2]
            if f and f[0] == 'stream' and f[1]:
                streams.add(f[1])
                if f[2]:
                    primed.add(f[1])
        for aliases in self.banks.values():
            for name, flags, pan, f in aliases:
                one(f, (flags, pan))
        for files in self.patches.values():
            for f in files:
                one(f)
        key = lambda s: s.encode('latin-1')
        with open(os.path.join(out_dir, 'snddump_streams.txt'), 'w', encoding='latin-1', newline='\n') as f:
            for name in sorted(streams, key=key):
                f.write('%s%s\n' % (name, '\tprimed' if name in primed else ''))
        with open(os.path.join(out_dir, 'snddump_usage.txt'), 'w', encoding='latin-1', newline='\n') as f:
            for name in sorted(usage, key=key):
                u = usage[name]
                f.write('%s\t%u\t%u\t%u\n' % (name, u[0], u[1], u[2]))
        return len(streams), len(usage)


def record(out_dir, zones, log=print):
    """zones: [(name, .ff path)] in the engine's load order"""
    os.makedirs(os.path.join(out_dir, 'zonemap'), exist_ok=True)
    lines = []
    sounds = Sounds()
    for name, path in zones:
        zone = zr_runtime.Zone(open(path, 'rb').read(), name)
        r = Recorder(zone, lines)
        r.load_zone(asset_hook=lambda rr, t, v: sounds.asset(rr, t, v) if t in (9, 10, 0x1B) else None)
        zone.write_zmap(os.path.join(out_dir, 'zonemap', name + '.zmap'))
        log('%s: %d events' % (name, len(zone.events)))
    with open(os.path.join(out_dir, 'record.log'), 'w', encoding='latin-1', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    return sounds.dump(out_dir)


if __name__ == '__main__':
    out = sys.argv[1]
    zs = [(os.path.splitext(os.path.basename(p))[0], p) for p in sys.argv[2:]]
    print('streams, loaded sounds:', record(out, zs))
