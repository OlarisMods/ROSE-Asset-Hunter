"""rose_hunt_core.py -- find asset names in the archive, and write markers.

This is the part that does the work. The window in rose_hunt.py only calls into
here, so everything can be tested from a command line without a screen.

Nothing outside the standard library is used, on purpose: the whole tool should
be a folder you can copy and run.

THE IDEA
    Assets in ROSE do not announce themselves. A texture is just pixels; a mesh
    is just triangles; and a particle record does not even contain its own
    filename. So the only reliable way to learn what a thing is, is to replace
    it with something that says so and then look at it in game.

    Replace every texture with its own name written across it, and the game
    labels itself.
"""

import os
import re
import struct
import json
import time

import rose_font


# ---------------------------------------------------------------- scanning

# Names are stored in the archive as plain text, so they can be read out of it
# directly. This deliberately re-scans every time rather than trusting a saved
# list: an earlier inventory of particle textures turned out to cover only 62%
# of what the archive actually names, and every search built on it was doomed
# before it started.
NAME_PATTERN = re.compile(rb'[0-9A-Za-z_\-./\\]{1,120}\.(dds|zms)', re.I)

CHUNK = 32 << 20          # read the archive in 32 MB pieces
OVERLAP = 256             # so a name split across two pieces is not lost


def scan_archive(vfs_path, extension, contains=None, progress=None,
                 should_stop=None):
    """Every distinct name ending in `extension` that the archive mentions.

    IMPORTANT: the archive writes some paths with SINGLE backslashes and others
    with DOUBLE --

        3ddata\\effect\\particles\\texture\\_beam_piller_01.dds     single
        3ddata\\\\effect\\\\particles\\\\texture\\\\_arrow_01.dds       double

    They are the same file. An earlier version matched only the single form and
    silently returned about a third of what existed, which sent a search off for
    days looking through a set that never held the answer. Runs of backslashes
    are collapsed to one before anything else happens.
    """
    size = os.path.getsize(vfs_path)
    found = {}
    with open(vfs_path, 'rb') as handle:
        position = 0
        while position < size:
            handle.seek(position)
            buffer = handle.read(CHUNK + OVERLAP)
            if not buffer:
                break
            for match in NAME_PATTERN.finditer(buffer):
                name = match.group(0).decode('ascii', 'replace')
                if not name.lower().endswith('.' + extension.lower()):
                    continue
                tidy = re.sub(r'[\\/]+', '\\\\', name)
                if contains and contains.lower() not in tidy.lower():
                    continue
                found[tidy.lower()] = found.get(tidy.lower(), 0) + 1
            position += CHUNK
            if progress:
                progress(min(1.0, position / float(size)))
            if should_stop and should_stop():
                break
    return sorted(found)


def group_by_folder(names):
    """Sort names into their containing folder.

    This is what the picker is built from. Offering the folders that ACTUALLY
    EXIST beats offering a list of guesses -- the first version had presets for
    "character textures" and "item textures" that matched nothing at all,
    because the folders were not called what I assumed.
    """
    groups = {}
    for name in names:
        parts = name.split('\\')
        folder = '\\'.join(parts[:-1]) if len(parts) > 1 else '(no folder)'
        groups.setdefault(folder, []).append(name)
    return groups


def scan_everything(vfs_path, progress=None, should_stop=None):
    """Both kinds at once, so the picker can be built from one pass."""
    result = {}
    for index, extension in enumerate(('dds', 'zms')):
        def sub(fraction, index=index):
            if progress:
                progress((index + fraction) / 2.0)
        result[extension] = scan_archive(vfs_path, extension, None, sub, should_stop)
    return result


def write_key_file(game_root, pairs):
    """Save what each marker means, so a colour hunt can be read afterwards.

    A colour tells you a file changed but not which one, and holding twelve
    colour-to-name pairs in your head while playing does not work. This writes
    them next to the game where they can be looked at.
    """
    path = os.path.join(game_root, 'ROSE_HUNT_KEY.txt')
    with open(path, 'w') as handle:
        handle.write("What each marker means\r\n")
        handle.write("=" * 60 + "\r\n\r\n")
        for label, names in pairs:
            handle.write("%s\r\n" % label)
            for name in names:
                handle.write("    %s\r\n" % name)
            handle.write("\r\n")
    return path


# ---------------------------------------------------------------- DDS output

def _dds_header(width, height):
    """An uncompressed 32-bit RGBA DDS header.

    Uncompressed on purpose. DXT compression would make the files smaller but
    blurs small text, and legibility is the entire point of a marker.
    """
    header = bytearray(128)
    header[0:4] = b'DDS '
    struct.pack_into('<I', header, 4, 124)            # header size
    struct.pack_into('<I', header, 8, 0x0002100F)     # caps|height|width|pitch|pixelformat
    struct.pack_into('<I', header, 12, height)
    struct.pack_into('<I', header, 16, width)
    struct.pack_into('<I', header, 20, width * 4)     # pitch
    struct.pack_into('<I', header, 24, 1)             # depth
    struct.pack_into('<I', header, 28, 1)             # mip count
    struct.pack_into('<I', header, 76, 32)            # pixel format size
    struct.pack_into('<I', header, 80, 0x41)          # RGB | ALPHAPIXELS
    struct.pack_into('<I', header, 88, 32)            # bits per pixel
    struct.pack_into('<I', header, 92, 0x000000ff)    # red mask
    struct.pack_into('<I', header, 96, 0x0000ff00)    # green
    struct.pack_into('<I', header, 100, 0x00ff0000)   # blue
    struct.pack_into('<I', header, 104, 0xff000000)   # alpha
    struct.pack_into('<I', header, 108, 0x1000)       # caps: texture
    return bytes(header)


class Canvas(object):
    """A plain pixel buffer. Small enough not to need an imaging library."""

    def __init__(self, size, fill=(0, 0, 0, 0)):
        self.size = size
        self.pixels = bytearray(size * size * 4)
        if fill != (0, 0, 0, 0):
            for index in range(size * size):
                self.pixels[index * 4:index * 4 + 4] = bytes(fill)

    def put(self, x, y, colour):
        if 0 <= x < self.size and 0 <= y < self.size:
            offset = (y * self.size + x) * 4
            self.pixels[offset:offset + 4] = bytes(colour)

    def to_dds(self):
        return _dds_header(self.size, self.size) + bytes(self.pixels)


def marker_colour(size, colour):
    """A flat colour, and nothing else.

    The bluntest marker and often the best. Colour survives where text cannot:
    the blessing beam's particles were a few pixels wide, so lettering
    compressed into an unreadable sliver, but a lit pixel still carries its hue.
    """
    return Canvas(size, tuple(colour) + (255,)).to_dds()


def marker_text(size, label, colour=(90, 255, 140), background=(12, 10, 16),
                vertical=False):
    """The name, tiled across the texture.

    Tiled rather than written once because a sprite is usually a CROP of the
    texture -- whatever piece the game happens to show has to contain a whole
    name, or you read half a word and learn nothing.
    """
    canvas = Canvas(size, tuple(background) + (255,))
    scale = max(1, size // 110)
    step_x = rose_font.text_width(label, scale) + 10 * scale
    step_y = (rose_font.GLYPH_H + 5) * scale

    def plot(px, py):
        canvas.put(px, py, tuple(colour) + (255,))

    row = 0
    y = 2
    while y < size:
        offset = (row % 2) * (step_x // 2)
        x = -step_x + offset
        while x < size:
            rose_font.draw_text(plot, label, x, y, scale)
            x += step_x
        y += step_y
        row += 1

    if vertical:
        # Rotate a quarter turn, so the name runs along the LONG axis of a tall
        # narrow sprite instead of being squeezed flat across a short one.
        rotated = Canvas(size)
        for y in range(size):
            for x in range(size):
                offset = (y * size + x) * 4
                pixel = canvas.pixels[offset:offset + 4]
                rotated.pixels[((size - 1 - x) * size + y) * 4:
                               ((size - 1 - x) * size + y) * 4 + 4] = pixel
        canvas = rotated
    return canvas.to_dds()


# ---------------------------------------------------------------- ZMS output

def marker_mesh(shape='cube', scale=1.0):
    """A simple solid, written as a ZMS the game can load.

    Replacing a model with a recognisable primitive says what it is by its
    SHAPE, which text cannot do on geometry.

    A warning belongs here: a mesh the game does not expect can crash the
    client outright. It is recoverable -- delete the file -- but it is not a
    quiet failure like a wrong texture.
    """
    if shape == 'cube':
        s = 0.5 * scale
        points = [(-s, -s, 0), (s, -s, 0), (s, s, 0), (-s, s, 0),
                  (-s, -s, 2 * s), (s, -s, 2 * s), (s, s, 2 * s), (-s, s, 2 * s)]
        faces = [(0, 1, 2), (0, 2, 3), (4, 6, 5), (4, 7, 6),
                 (0, 4, 5), (0, 5, 1), (1, 5, 6), (1, 6, 2),
                 (2, 6, 7), (2, 7, 3), (3, 7, 4), (3, 4, 0)]
    elif shape == 'pyramid':
        s = 0.6 * scale
        points = [(-s, -s, 0), (s, -s, 0), (s, s, 0), (-s, s, 0), (0, 0, 2 * s)]
        faces = [(0, 1, 2), (0, 2, 3), (0, 4, 1), (1, 4, 2), (2, 4, 3), (3, 4, 0)]
    else:  # a tall spike, easy to spot at a distance
        s = 0.25 * scale
        points = [(-s, -s, 0), (s, -s, 0), (s, s, 0), (-s, s, 0), (0, 0, 6 * s)]
        faces = [(0, 1, 2), (0, 2, 3), (0, 4, 1), (1, 4, 2), (2, 4, 3), (3, 4, 0)]

    out = bytearray()
    out += b'ZMS0008\0'
    out += struct.pack('<I', 2 | 4 | 8)        # position, normal, uv0
    lo = tuple(min(p[i] for p in points) for i in range(3))
    hi = tuple(max(p[i] for p in points) for i in range(3))
    out += struct.pack('<3f', *lo)
    out += struct.pack('<3f', *hi)
    out += struct.pack('<H', 0)                # no bones
    out += struct.pack('<H', len(points))
    for point in points:
        out += struct.pack('<3f', *point)
    for point in points:
        length = max(1e-6, sum(v * v for v in point) ** 0.5)
        out += struct.pack('<3f', *[v / length for v in point])
    for index, _ in enumerate(points):
        out += struct.pack('<2f', (index % 2), (index // 2) % 2)
    out += struct.pack('<H', len(faces))
    for face in faces:
        out += struct.pack('<3H', *face)
    return bytes(out)


# ---------------------------------------------------------------- installing

MANIFEST = 'rose_hunt_installed.json'


def install(game_root, names, make_bytes, note='', on_step=None):
    """Write a marker for every name, and record exactly what was written.

    The record is what makes Restore trustworthy. Earlier hunts left a hundred
    and fifty files behind and no way to know which were ours, which meant
    tidying up by hand from a zip you had to still have.
    """
    written = []
    for index, name in enumerate(names):
        relative = name.lstrip('\\/')
        destination = os.path.join(game_root, relative.replace('\\', os.sep))
        folder = os.path.dirname(destination)
        try:
            if folder and not os.path.isdir(folder):
                os.makedirs(folder)
            existed = os.path.exists(destination)
            with open(destination, 'wb') as handle:
                handle.write(make_bytes(name))
            written.append({'path': relative, 'existed_before': existed})
        except OSError as error:
            # A file we cannot write is worth reporting, not worth stopping for.
            written.append({'path': relative, 'error': str(error)})
        if on_step:
            on_step(index + 1, len(names))

    manifest_path = os.path.join(game_root, MANIFEST)
    record = {'when': time.strftime('%Y-%m-%d %H:%M:%S'),
              'note': note,
              'files': written}
    with open(manifest_path, 'w') as handle:
        json.dump(record, handle, indent=1)
    return record


def what_is_installed(game_root):
    path = os.path.join(game_root, MANIFEST)
    if not os.path.exists(path):
        return None
    with open(path) as handle:
        return json.load(handle)


def restore(game_root, on_step=None):
    """Remove every file this tool wrote, and tidy the folders it created."""
    record = what_is_installed(game_root)
    if not record:
        return 0, 0
    removed = 0
    missing = 0
    folders = set()
    entries = [f for f in record['files'] if 'error' not in f]
    for index, entry in enumerate(entries):
        target = os.path.join(game_root, entry['path'].replace('\\', os.sep))
        if os.path.exists(target):
            try:
                os.remove(target)
                removed += 1
                folders.add(os.path.dirname(target))
            except OSError:
                pass
        else:
            missing += 1
        if on_step:
            on_step(index + 1, len(entries))
    for folder in sorted(folders, key=len, reverse=True):
        while os.path.normcase(folder) != os.path.normcase(game_root):
            try:
                if os.path.isdir(folder) and not os.listdir(folder):
                    os.rmdir(folder)
                    folder = os.path.dirname(folder)
                else:
                    break
            except OSError:
                break
    try:
        os.remove(os.path.join(game_root, MANIFEST))
    except OSError:
        pass
    return removed, missing


# ------------------------------------------------------------ extracting

def _png(width, height, rgba):
    """Write a PNG without an imaging library.

    zlib is in the standard library and a PNG is four chunks around a zlib
    stream, so this keeps the promise that the tool needs nothing installed.
    """
    import zlib
    raw = bytearray()
    for y in range(height):
        raw.append(0)                                  # filter: none
        raw += rgba[y * width * 4:(y + 1) * width * 4]

    def chunk(tag, payload):
        body = tag + payload
        return (struct.pack('>I', len(payload)) + body
                + struct.pack('>I', zlib.crc32(body) & 0xffffffff))

    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(bytes(raw), 6))
            + chunk(b'IEND', b''))


def _decode_dxt1(data, width, height):
    """DXT1 to RGBA. Enough of the format to see what a texture is."""
    out = bytearray(width * height * 4)
    offset = 0
    for by in range(0, height, 4):
        for bx in range(0, width, 4):
            if offset + 8 > len(data):
                return bytes(out)
            c0, c1 = struct.unpack_from('<2H', data, offset)
            bits = struct.unpack_from('<I', data, offset + 4)[0]
            offset += 8
            palette = []
            for packed in (c0, c1):
                palette.append((((packed >> 11) & 31) * 255 // 31,
                                ((packed >> 5) & 63) * 255 // 63,
                                (packed & 31) * 255 // 31))
            if c0 > c1:
                palette.append(tuple((2 * palette[0][i] + palette[1][i]) // 3
                                     for i in range(3)))
                palette.append(tuple((palette[0][i] + 2 * palette[1][i]) // 3
                                     for i in range(3)))
            else:
                palette.append(tuple((palette[0][i] + palette[1][i]) // 2
                                     for i in range(3)))
                palette.append((0, 0, 0))
            for row in range(4):
                for col in range(4):
                    x, y = bx + col, by + row
                    if x >= width or y >= height:
                        continue
                    index = (bits >> (2 * (row * 4 + col))) & 3
                    colour = palette[index]
                    at = (y * width + x) * 4
                    out[at:at + 3] = bytes(colour)
                    out[at + 3] = 255
    return bytes(out)


def dds_to_png(blob):
    """Turn a carved DDS into a PNG, or None if it is a format we do not read."""
    if len(blob) < 128 or blob[:4] != b'DDS ':
        return None
    height = struct.unpack_from('<I', blob, 12)[0]
    width = struct.unpack_from('<I', blob, 16)[0]
    if not (0 < width <= 4096 and 0 < height <= 4096):
        return None
    fourcc = blob[84:88]
    body = blob[128:]
    if fourcc == b'DXT1':
        rgba = _decode_dxt1(body, width, height)
    elif fourcc in (b'\x00\x00\x00\x00',):
        need = width * height * 4
        if len(body) < need:
            return None
        rgba = bytearray(body[:need])
        red_mask = struct.unpack_from('<I', blob, 92)[0]
        if red_mask == 0x00ff0000:                     # stored BGRA
            for index in range(0, need, 4):
                rgba[index], rgba[index + 2] = rgba[index + 2], rgba[index]
        rgba = bytes(rgba)
    else:
        return None
    return _png(width, height, rgba)


def carve_documents(vfs_path, out_folder, progress=None, should_stop=None):
    """Pull the interface documents and stylesheets out as text.

    These are the files worth having most often -- every interface change
    starts by reading one -- and unlike textures they CAN be named, because
    they say what they are:

        a document declares  <body id="hud">
        a stylesheet declares  src: /3ddata/control/res/stateicon.dds

    So these come out with real filenames rather than numbers.
    """
    if not os.path.isdir(out_folder):
        os.makedirs(out_folder)
    docs = os.path.join(out_folder, 'documents')
    sheets = os.path.join(out_folder, 'stylesheets')
    for folder in (docs, sheets):
        if not os.path.isdir(folder):
            os.makedirs(folder)

    size = os.path.getsize(vfs_path)
    body = re.compile(rb'<body\s+id="([^"]+)"')
    sheet = re.compile(rb'@spritesheet\s+[A-Za-z0-9_\-]*\s*\{')
    src = re.compile(rb'src\s*:\s*([^;\r\n]+);', re.I)
    written = {'documents': 0, 'stylesheets': 0}
    used = set()

    window = 16 << 20
    overlap = 1 << 20
    with open(vfs_path, 'rb') as handle:
        position = 0
        while position < size:
            handle.seek(position)
            buffer = handle.read(window + overlap)
            if not buffer:
                break

            # documents -- walk out to the <rml> wrapper around the body tag
            for match in body.finditer(buffer):
                if match.start() >= window:
                    continue
                name = match.group(1).decode('ascii', 'replace')
                start = buffer.rfind(b'<rml', 0, match.start())
                end = buffer.find(b'</rml>', match.end())
                if start < 0 or end < 0 or end - start > 400000:
                    continue
                text = buffer[start:end + 6]
                stem = re.sub(r'[^A-Za-z0-9_.-]', '_', name)
                path = os.path.join(docs, stem + '.html')
                if path in used:
                    continue
                used.add(path)
                with open(path, 'wb') as f:
                    f.write(text)
                written['documents'] += 1

            # stylesheets -- walk the braces to the end of the block
            for match in sheet.finditer(buffer):
                start = match.start()
                if start >= window:
                    continue
                depth = 0
                index = start
                while index < len(buffer):
                    char = buffer[index:index + 1]
                    if char == b'{':
                        depth += 1
                    elif char == b'}':
                        depth -= 1
                        if depth == 0:
                            break
                    index += 1
                block = buffer[start:index + 1]
                if not (40 < len(block) < 400000):
                    continue
                image = src.search(block)
                if image:
                    stem = os.path.splitext(os.path.basename(
                        image.group(1).decode('ascii', 'replace')
                        .strip().replace('\\', '/')))[0]
                else:
                    stem = 'sheet_%d' % (position + start)
                stem = re.sub(r'[^A-Za-z0-9_.-]', '_', stem)
                path = os.path.join(sheets, stem + '.css')
                counter = 2
                while path in used:
                    path = os.path.join(sheets, '%s_%d.css' % (stem, counter))
                    counter += 1
                used.add(path)
                with open(path, 'wb') as f:
                    f.write(block)
                written['stylesheets'] += 1

            position += window
            if progress:
                progress(min(1.0, position / float(size)))
            if should_stop and should_stop():
                break
    return written


def carve_textures(vfs_path, out_folder, limit=None, progress=None,
                   min_side=0, max_side=4096, formats=None,
                   should_stop=None, keep_in_memory=False):
    """Pull every DDS out of the archive by its magic number.

    The archive index cannot help: its offsets are not seek positions, so
    asking it for a file by name lands in unrelated data. But a DDS announces
    itself -- four bytes, then a header that says how big it is -- so they can
    be found directly and lifted out whole.

    What you cannot get this way is the NAME. The pixels and the filename live
    apart, so there is no way to ask for one file by name -- this is the honest
    limit of the whole approach.

    What CAN be narrowed is the shape of what comes out. `min_side`, `max_side`
    and `formats` cut the dump down to a size worth looking through: asking for
    only 256x256 uncompressed textures turns several thousand files into a
    few dozen.
    """
    # Everything goes inside a folder of our own, sorted by format and then by
    # size. An earlier version wrote loose files straight into whatever folder
    # was chosen, which is unpleasant if that folder was Downloads.
    stamp = time.strftime('%Y-%m-%d_%H%M')
    out_folder = os.path.join(out_folder, 'ROSE_Extract_' + stamp)
    if not os.path.isdir(out_folder):
        os.makedirs(out_folder)
    size = os.path.getsize(vfs_path)
    found = 0
    window = 8 << 20
    overlap = 1 << 20                                  # a large texture spans reads
    with open(vfs_path, 'rb') as handle:
        position = 0
        while position < size:
            handle.seek(position)
            buffer = handle.read(window + overlap)
            if not buffer:
                break
            start = 0
            while True:
                at = buffer.find(b'DDS ', start)
                if at < 0 or at >= window:
                    break
                start = at + 4
                if at + 128 > len(buffer):
                    break
                height = struct.unpack_from('<I', buffer, at + 12)[0]
                width = struct.unpack_from('<I', buffer, at + 16)[0]
                if not (0 < width <= 4096 and 0 < height <= 4096):
                    continue
                if not (min_side <= min(width, height)
                        and max(width, height) <= max_side):
                    continue
                fourcc = buffer[at + 84:at + 88]
                if formats:
                    tag = 'raw' if fourcc == b'\x00\x00\x00\x00' else \
                        fourcc.decode('ascii', 'replace')
                    if tag not in formats:
                        continue
                if fourcc == b'DXT1':
                    length = 128 + max(1, width // 4) * max(1, height // 4) * 8
                elif fourcc in (b'DXT3', b'DXT5'):
                    length = 128 + max(1, width // 4) * max(1, height // 4) * 16
                else:
                    length = 128 + width * height * 4
                if at + length > len(buffer) or length > 40 << 20:
                    continue
                blob = buffer[at:at + length]
                kind = 'raw' if fourcc == b'\x00\x00\x00\x00' else \
                    fourcc.decode('ascii', 'replace')
                bucket = os.path.join(out_folder, kind, '%dx%d' % (width, height))
                if not os.path.isdir(bucket):
                    os.makedirs(bucket)
                stem = '%09d' % (position + at)
                with open(os.path.join(bucket, stem + '.dds'), 'wb') as f:
                    f.write(blob)
                png = dds_to_png(blob)
                if png:
                    with open(os.path.join(bucket, stem + '.png'), 'wb') as f:
                        f.write(png)
                found += 1
                if limit and found >= limit:
                    return found, out_folder
            position += window
            if progress:
                progress(min(1.0, position / float(size)))
            if should_stop and should_stop():
                break
    return found, out_folder


def thumbnail_png(blob, width, height, longest=128):
    """A small PNG of a texture, for showing hundreds at once.

    Deliberately NOT the full image. Holding thousands of full textures in
    memory is gigabytes -- a 512x512 uncompressed one is a megabyte on its own.
    A thumbnail is a few kilobytes, and the full data is re-read from the
    archive only when something is actually saved.

    That is what lets the viewer show everything rather than the first few
    hundred.
    """
    rgba = _decode_to_rgba(blob, width, height)
    if rgba is None:
        return None
    step = max(1, int(max(width, height) / longest))
    if step == 1:
        return _png(width, height, rgba), width, height
    small_w = max(1, width // step)
    small_h = max(1, height // step)
    out = bytearray(small_w * small_h * 4)
    for y in range(small_h):
        source_row = (y * step) * width * 4
        target_row = y * small_w * 4
        for x in range(small_w):
            source = source_row + (x * step) * 4
            out[target_row + x * 4:target_row + x * 4 + 4] = rgba[source:source + 4]
    return _png(small_w, small_h, bytes(out)), small_w, small_h


def _decode_to_rgba(blob, width, height):
    """DDS pixels as RGBA, or None for a format we do not read."""
    fourcc = blob[84:88]
    body = blob[128:]
    if fourcc == b'DXT1':
        return _decode_dxt1(body, width, height)
    if fourcc == b'\x00\x00\x00\x00':
        need = width * height * 4
        if len(body) < need:
            return None
        rgba = bytearray(body[:need])
        if struct.unpack_from('<I', blob, 92)[0] == 0x00ff0000:
            for index in range(0, need, 4):
                rgba[index], rgba[index + 2] = rgba[index + 2], rgba[index]
        return bytes(rgba)
    return None


def read_at(vfs_path, offset, length):
    """Lift a known stretch of the archive back out, for saving one file."""
    with open(vfs_path, 'rb') as handle:
        handle.seek(offset)
        return handle.read(length)


def colour_breakdown(blob, width, height, bands=6):
    """The commonest colours in a texture, roughly.

    Buckets each channel into four steps, counts, and returns the top few. It is
    crude, but it answers "what colour IS this thing" at a glance, which is what
    it is for.
    """
    rgba = _decode_to_rgba(blob, width, height)
    if rgba is None:
        return []
    counts = {}
    total = 0
    for index in range(0, len(rgba), 16):
        if rgba[index + 3] < 40:
            continue
        key = (rgba[index] // 64, rgba[index + 1] // 64, rgba[index + 2] // 64)
        counts[key] = counts.get(key, 0) + 1
        total += 1
    if not total:
        return []
    ordered = sorted(counts.items(), key=lambda kv: -kv[1])[:bands]
    return [((r * 64 + 32, g * 64 + 32, b * 64 + 32), n * 100.0 / total)
            for (r, g, b), n in ordered]


def browse_textures(vfs_path, min_side=0, max_side=4096, limit=None,
                    progress=None, should_stop=None, on_found=None):
    """Find textures and hand back THUMBNAILS, without writing anything.

    Only a small preview and the file's position are kept. The full pixels are
    re-read from the archive if and when something is saved, so the viewer can
    hold every texture in the game instead of the first few hundred.
    """
    size = os.path.getsize(vfs_path)
    results = []
    window = 8 << 20
    overlap = 1 << 20
    with open(vfs_path, 'rb') as handle:
        position = 0
        while position < size and (limit is None or len(results) < limit):
            handle.seek(position)
            buffer = handle.read(window + overlap)
            if not buffer:
                break
            start = 0
            while limit is None or len(results) < limit:
                at = buffer.find(b'DDS ', start)
                if at < 0 or at >= window:
                    break
                start = at + 4
                if at + 128 > len(buffer):
                    break
                height = struct.unpack_from('<I', buffer, at + 12)[0]
                width = struct.unpack_from('<I', buffer, at + 16)[0]
                if not (0 < width <= 4096 and 0 < height <= 4096):
                    continue
                if not (min_side <= min(width, height)
                        and max(width, height) <= max_side):
                    continue
                fourcc = buffer[at + 84:at + 88]
                if fourcc == b'DXT1':
                    length = 128 + max(1, width // 4) * max(1, height // 4) * 8
                elif fourcc in (b'DXT3', b'DXT5'):
                    length = 128 + max(1, width // 4) * max(1, height // 4) * 16
                else:
                    length = 128 + width * height * 4
                if at + length > len(buffer) or length > 40 << 20:
                    continue
                # NOTE: the picture is NOT decoded here.
                #
                # Decoding every texture during the scan is what made this
                # crawl -- thousands of DXT blocks unpacked in Python, nearly
                # all of them for textures nobody will ever look at. Only the
                # position is recorded now, and a thumbnail is made when a page
                # is actually shown. Scanning got several times faster for it.
                kind = 'raw' if fourcc == b'\x00\x00\x00\x00' \
                    else fourcc.decode('ascii', 'replace')
                item = {'offset': position + at, 'length': length,
                        'width': width, 'height': height, 'format': kind}
                results.append(item)
                if on_found:
                    on_found(item)
            position += window
            if progress:
                progress(min(1.0, position / float(size)))
            if should_stop and should_stop():
                break
    return results


def ensure_thumbnail(item, vfs_path):
    """Make the small picture for one texture, if it does not have one yet.

    Called when a page is displayed rather than while scanning, so the cost
    lands on the sixty things you are looking at instead of on all nine
    thousand.
    """
    if item.get('png'):
        return True
    blob = read_at(vfs_path, item['offset'], item['length'])
    made = thumbnail_png(blob, item['width'], item['height'])
    if not made:
        return False
    item['png'], item['thumb_w'], item['thumb_h'] = made
    return True


def average_colour(item):
    """The mean colour of a texture, for sorting a wall of thumbnails by hue.

    Taken from the THUMBNAIL, which is already in memory -- the full pixels are
    not kept, and an average does not need them.
    """
    png = item.get('png')
    if not png:
        return (128, 128, 128)
    rgba = _png_to_rgba(png)
    if not rgba:
        return (128, 128, 128)
    totals = [0, 0, 0]
    count = 0
    for index in range(0, len(rgba), 8):
        if rgba[index + 3] < 40:
            continue
        totals[0] += rgba[index]
        totals[1] += rgba[index + 1]
        totals[2] += rgba[index + 2]
        count += 1
    if not count:
        return (0, 0, 0)
    return tuple(value // count for value in totals)


def _png_to_rgba(png):
    """Read back a PNG we wrote ourselves. Only handles our own output."""
    import zlib
    try:
        at = 8
        width = height = 0
        data = b''
        while at < len(png):
            length = struct.unpack_from('>I', png, at)[0]
            tag = png[at + 4:at + 8]
            payload = png[at + 8:at + 8 + length]
            if tag == b'IHDR':
                width, height = struct.unpack_from('>II', payload, 0)
            elif tag == b'IDAT':
                data += payload
            at += 12 + length
        raw = zlib.decompress(data)
        out = bytearray()
        stride = width * 4
        for y in range(height):
            start = y * (stride + 1) + 1
            out += raw[start:start + stride]
        return bytes(out)
    except Exception:                                  # noqa: BLE001
        return None


def hue_of(rgb):
    """A single number for sorting by colour: hue, then brightness."""
    red, green, blue = [value / 255.0 for value in rgb]
    high, low = max(red, green, blue), min(red, green, blue)
    if high == low:
        return (0.0, -high)
    span = high - low
    if high == red:
        hue = (60 * ((green - blue) / span) + 360) % 360
    elif high == green:
        hue = 60 * ((blue - red) / span) + 120
    else:
        hue = 60 * ((red - green) / span) + 240
    return (hue, -high)


# ---------------------------------------------------------------- meshes

def read_zms(blob):
    """Enough of a ZMS to draw it. Points and triangles, nothing else.

    Being able to LOOK at a model matters more here than it sounds: replacing
    models to find out what they are can crash the client, and no crash report
    says which file did it. Reading them instead means never having to.
    """
    if blob[:7] != b'ZMS0008' and blob[:7] != b'ZMS0007':
        return None
    try:
        offset = 8
        fmt = struct.unpack_from('<I', blob, offset)[0]
        offset += 4
        offset += 24                                    # bounding box
        bones = struct.unpack_from('<H', blob, offset)[0]
        offset += 2 + bones * 2
        count = struct.unpack_from('<H', blob, offset)[0]
        offset += 2
        if not (0 < count <= 65535):
            return None
        points = []
        if fmt & 2:
            for _ in range(count):
                points.append(struct.unpack_from('<3f', blob, offset))
                offset += 12
        else:
            return None
        if fmt & 4:
            offset += count * 12                        # normals
        if fmt & 8:
            offset += count * 8                         # uv0
        for flag, size in ((0x10, 8), (0x20, 8), (0x40, 8),
                           (0x80, 16), (0x100, 16), (0x200, 12)):
            if fmt & flag:
                offset += count * size
        faces = struct.unpack_from('<H', blob, offset)[0]
        offset += 2
        if not (0 < faces <= 65535):
            return None
        triangles = []
        for _ in range(faces):
            triangles.append(struct.unpack_from('<3H', blob, offset))
            offset += 6
        return {'points': points, 'faces': triangles}
    except struct.error:
        return None


def browse_meshes(vfs_path, limit=None, progress=None, should_stop=None,
                  on_found=None):
    """Find models in the archive and read their shape, without installing
    anything.

    This is the safe answer to the model problem. Marking models can take the
    client down and the crash report cannot say which one did it -- but nothing
    stops us simply READING them and drawing what we find.
    """
    size = os.path.getsize(vfs_path)
    results = []
    window = 8 << 20
    overlap = 4 << 20
    with open(vfs_path, 'rb') as handle:
        position = 0
        while position < size and (limit is None or len(results) < limit):
            handle.seek(position)
            buffer = handle.read(window + overlap)
            if not buffer:
                break
            start = 0
            while limit is None or len(results) < limit:
                at = buffer.find(b'ZMS000', start)
                if at < 0 or at >= window:
                    break
                start = at + 6
                blob = buffer[at:at + (4 << 20)]
                mesh = read_zms(blob)
                if not mesh:
                    continue
                mesh['offset'] = position + at
                results.append(mesh)
                if on_found:
                    on_found(mesh)
            position += window
            if progress:
                progress(min(1.0, position / float(size)))
            if should_stop and should_stop():
                break
    return results


def save_one(item, out_folder, vfs_path=None):
    """Write a single browsed texture out, as both DDS and PNG.

    The full pixels are re-read from the archive here rather than being carried
    around in memory -- which is what lets the viewer hold every texture at
    once.
    """
    if not os.path.isdir(out_folder):
        os.makedirs(out_folder)
    stem = '%09d_%dx%d_%s' % (item['offset'], item['width'], item['height'],
                              item['format'])
    blob = item.get('dds')
    if blob is None and vfs_path:
        blob = read_at(vfs_path, item['offset'], item['length'])
    dds_path = os.path.join(out_folder, stem + '.dds')
    with open(dds_path, 'wb') as handle:
        handle.write(blob)
    full = dds_to_png(blob) if blob else None
    with open(os.path.join(out_folder, stem + '.png'), 'wb') as handle:
        handle.write(full or item['png'])
    return dds_path


def colour_distance(a, b):
    """How far apart two colours are. Plain squared distance is enough here."""
    return sum((a[i] - b[i]) ** 2 for i in range(3))


def parse_colour(text):
    """Read '#e8a521', 'e8a521', or 'amber' into an RGB triple."""
    named = {'red': (220, 40, 40), 'orange': (240, 140, 30), 'amber': (232, 165, 33),
             'yellow': (240, 220, 60), 'green': (60, 200, 90), 'teal': (0, 180, 160),
             'cyan': (60, 220, 230), 'blue': (60, 100, 230), 'violet': (150, 90, 220),
             'purple': (140, 60, 200), 'magenta': (230, 60, 200),
             'pink': (240, 150, 190), 'brown': (140, 90, 40),
             'white': (240, 240, 240), 'grey': (140, 140, 140),
             'gray': (140, 140, 140), 'black': (20, 20, 20)}
    text = (text or '').strip().lower().lstrip('#')
    if text in named:
        return named[text]
    if len(text) == 6:
        try:
            return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return None
    if len(text) == 3:
        try:
            return tuple(int(text[i] * 2, 16) for i in range(3))
        except ValueError:
            return None
    return None


def save_many_as_zip(items, zip_path, vfs_path):
    """Write several browsed textures into one zip.

    Dropping a dozen loose files into somebody's folder is the same rudeness as
    the old extractor scattering into Downloads. One file is tidier and easier
    to move about.
    """
    import zipfile
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for item in items:
            stem = '%09d_%dx%d_%s' % (item['offset'], item['width'],
                                      item['height'], item['format'])
            blob = read_at(vfs_path, item['offset'], item['length'])
            bundle.writestr(stem + '.dds', blob)
            full = dds_to_png(blob)
            bundle.writestr(stem + '.png', full or item['png'])
    return zip_path


def save_meshes_as_zip(meshes, zip_path):
    """The same for models, written as plain point-and-triangle lists."""
    import zipfile
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for mesh in meshes:
            lines = ["found at %d in the archive" % mesh['offset'],
                     "%d points, %d triangles" % (len(mesh['points']),
                                                  len(mesh['faces'])), ""]
            lines += ["v %.4f %.4f %.4f" % point for point in mesh['points']]
            lines += ["f %d %d %d" % face for face in mesh['faces']]
            bundle.writestr('mesh_%09d.txt' % mesh['offset'],
                            "\r\n".join(lines))
    return zip_path


# ---------------------------------------------------------------- particles

EVENT_NAMES = {1: 'Scale', 2: 'Timer', 3: 'Red', 4: 'Green', 5: 'Blue',
               6: 'Alpha', 7: 'Colour', 8: 'VelX', 9: 'VelY', 10: 'VelZ',
               11: 'Velocity', 12: 'Texture', 13: 'Rotation'}
EVENT_FLOATS = {0: 0, 1: 4, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2, 7: 8,
                8: 2, 9: 2, 10: 2, 11: 6, 12: 2, 13: 2}


def parse_particle(buffer, at):
    """Read a particle record, or return None if these bytes are not one.

    Particle files have no magic number, so the only way to find them is to try
    to parse and see whether it works out. That sounds fragile and is not: a
    record has enough internal agreement -- string lengths, event types, counts
    -- that random bytes fail within the first emitter.

    This is what makes "which effects use this texture" answerable. The record
    does not carry its own filename, but it DOES name every texture it draws,
    and that is usually the question worth asking.
    """
    try:
        offset = at
        count = struct.unpack_from('<I', buffer, offset)[0]
        if not (1 <= count <= 64):
            return None
        offset += 4
        emitters = []
        for _ in range(count):
            name_len = struct.unpack_from('<I', buffer, offset)[0]
            if not (0 <= name_len <= 128):
                return None
            name = buffer[offset + 4:offset + 4 + name_len].decode('ascii', 'replace')
            offset += 4 + name_len
            floats = struct.unpack_from('<23f', buffer, offset)
            offset += 23 * 4
            path_len = struct.unpack_from('<I', buffer, offset)[0]
            if not (4 <= path_len <= 256):
                return None
            texture = buffer[offset + 4:offset + 4 + path_len].decode('ascii', 'replace')
            if '.dds' not in texture.lower():
                return None
            offset += 4 + path_len
            ints = struct.unpack_from('<10i', buffer, offset)
            offset += 40
            events = []
            if not (0 <= ints[9] <= 256):
                return None
            for _ in range(ints[9]):
                kind = struct.unpack_from('<i', buffer, offset)[0]
                if kind not in EVENT_FLOATS:
                    return None
                when = struct.unpack_from('<f', buffer, offset + 4)[0]
                fade = buffer[offset + 12]
                span = EVENT_FLOATS[kind]
                values = struct.unpack_from('<%df' % span, buffer, offset + 13) \
                    if span else ()
                events.append({'kind': EVENT_NAMES.get(kind, str(kind)),
                               'at': when, 'fade': fade, 'values': values})
                offset += 13 + span * 4
            # Field positions taken from our own working reader, not guessed:
            # atlas sits at 3 and 4, blend at 6, 7 and 8.
            emitters.append({'name': name.strip('"'), 'texture': texture,
                             'life': (floats[0], floats[1]),
                             'emit_rate': (floats[2], floats[3]),
                             'loops': struct.unpack_from(
                                 '<i', struct.pack('<f', floats[4]))[0],
                             'count': ints[0], 'align': ints[1],
                             'update_coord': ints[2],
                             'atlas': (ints[3], ints[4]),
                             'blend': (ints[6], ints[7], ints[8]),
                             'events': events})
        return {'emitters': emitters, 'length': offset - at}
    except (struct.error, UnicodeDecodeError, IndexError):
        return None


def browse_particles(vfs_path, progress=None, should_stop=None, on_found=None):
    """Find particle records in the archive and read what they draw.

    Tries to parse at every position where a plausible emitter count sits. That
    is a lot of attempts, but almost all fail on the first field, so it is far
    cheaper than it sounds.
    """
    size = os.path.getsize(vfs_path)
    results = []
    window = 8 << 20
    overlap = 1 << 20
    with open(vfs_path, 'rb') as handle:
        position = 0
        while position < size:
            handle.seek(position)
            buffer = handle.read(window + overlap)
            if not buffer:
                break
            # A record begins with a small count, so only look where the next
            # four bytes could be one.
            for at in range(0, min(window, len(buffer) - 8), 4):
                head = struct.unpack_from('<I', buffer, at)[0]
                if not (1 <= head <= 64):
                    continue
                record = parse_particle(buffer, at)
                if not record:
                    continue
                record['offset'] = position + at
                results.append(record)
                if on_found:
                    on_found(record)
            position += window
            if progress:
                progress(min(1.0, position / float(size)))
            if should_stop and should_stop():
                break
    return results


def describe_particle(record):
    """A particle record written out the way you would want to read it."""
    lines = ["found at %d in the archive" % record['offset'],
             "%d emitter(s)" % len(record['emitters']), ""]
    for number, emitter in enumerate(record['emitters']):
        lines.append("emitter %d   %s" % (number + 1, emitter['name']))
        lines.append("   texture      %s" % emitter['texture'])
        lines.append("   life         %.4g to %.4g" % emitter['life'])
        lines.append("   emit rate    %.4g to %.4g" % emitter['emit_rate'])
        lines.append("   count        %d" % emitter['count'])
        lines.append("   align        %d      follows you  %d"
                     % (emitter['align'], emitter['update_coord']))
        if emitter['atlas'] != (0, 0) and emitter['atlas'] != (1, 1):
            lines.append("   atlas        %d x %d" % emitter['atlas'])
        lines.append("   blend        src %d  dst %d  op %d" % emitter['blend'])
        if emitter['loops'] == 0:
            lines.append("   loops        forever")
        elif emitter['loops'] > 0:
            lines.append("   loops        %d" % emitter['loops'])
        if emitter['events']:
            lines.append("   %d event(s):" % len(emitter['events']))
            for event in emitter['events'][:24]:
                values = ' '.join('%.4g' % v for v in event['values'])
                lines.append("      %-9s t %-8.4g fade %d  %s"
                             % (event['kind'], event['at'], event['fade'], values))
        lines.append("")
    return "\n".join(lines)


def value_at(events, kind, age, life, default=0.0):
    """What a property is worth at a given moment in a particle's life.

    Events are keyframes: a value at a time, and whether it eases in from the
    one before. This walks them and interpolates, which is what the game does
    and is what makes a preview behave like the real thing rather than being a
    guess at it.
    """
    points = [e for e in events if e['kind'] == kind and e['values']]
    if not points:
        return default
    points.sort(key=lambda e: e['at'])
    previous = None
    for event in points:
        if age <= event['at']:
            value = sum(event['values'][:2]) / min(2, len(event['values']))
            if previous is None or not event['fade']:
                return value
            span = event['at'] - previous['at']
            if span <= 0:
                return value
            before = sum(previous['values'][:2]) / min(2, len(previous['values']))
            share = (age - previous['at']) / span
            return before + (value - before) * share
        previous = event
    last = points[-1]
    return sum(last['values'][:2]) / min(2, len(last['values']))


def simulate(record, seconds, count=140):
    """Where every particle is, and how it looks, at one moment.

    Not the game's renderer -- there is no blending here and no camera. But the
    numbers driving it are the record's own, so the SHAPE of the thing is real:
    how many there are, how big, how fast they fade, whether they rise or fall
    or sit still.

    That is usually what you want to know, and reading the numbers off a page
    does not tell you.
    """
    import math
    import random
    drawn = []
    for index, emitter in enumerate(record['emitters']):
        life_lo, life_hi = emitter['life']
        life = max(0.2, (life_lo + life_hi) / 2.0)
        rate = max(1.0, (emitter['emit_rate'][0] + emitter['emit_rate'][1]) / 2.0)
        # emit_rate is milliseconds between births in this format
        alive = min(count, max(1, int(emitter['count'] * (1000.0 / rate) * life)))
        alive = min(alive, 40)
        generator = random.Random(index * 7919)
        for particle in range(alive):
            age = ((seconds + particle * life / max(1, alive)) % life)
            scale = value_at(emitter['events'], 'Scale', age, life, 10.0)
            alpha = value_at(emitter['events'], 'Alpha', age, life, 0.8)
            rise = value_at(emitter['events'], 'Velocity', age, life, 0.0)
            spread = generator.uniform(-1, 1), generator.uniform(-1, 1)
            drawn.append({'emitter': index,
                          'x': spread[0], 'y': spread[1],
                          'lift': rise * age * 0.05,
                          'scale': max(0.5, scale),
                          'alpha': max(0.0, min(1.0, alpha)),
                          'texture': emitter['texture']})
    return drawn


def save_particles_as_zip(records, zip_path, vfs_path):
    """Save particle records, both as usable .ptl files and as readable text."""
    import zipfile
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for record in records:
            stem = 'particle_%09d' % record['offset']
            bundle.writestr(stem + '.ptl',
                            read_at(vfs_path, record['offset'], record['length']))
            bundle.writestr(stem + '.txt', describe_particle(record))
    return zip_path


def find_game_folder():
    """A sensible first guess, so most people never have to browse for it."""
    candidates = [r"C:\Program Files\ROSE Online",
                  r"C:\Program Files (x86)\ROSE Online",
                  r"C:\ROSE Online"]
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, 'rose.vfs')):
            return candidate
    return ''
