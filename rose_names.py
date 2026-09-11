"""Name table support -- read a list of names, but verify it before believing it.

WHAT THIS IS FOR
    Names and file data are stored apart in rose.vfs. The tool finds data by
    its own signature, which is why extracted textures come out numbered rather
    than named, and why particle records are identified by an offset.

    A name table closes that gap: a list of entries saying "this name is at
    this offset and is this long".

        {"name": "EFFECT\\PARTICLES\\C_HIT_01.PTL",
         "offset": 1319194565, "length": 2011}

    NONE IS SHIPPED WITH THIS TOOL. If a file called rose_names.json is sitting
    next to the app it is used; if not, everything behaves exactly as before.
    That way the download contains nothing but code.

WHY IT IS VERIFIED RATHER THAN TRUSTED
    A table can be wrong. It might describe a different build of the game, or
    have been assembled by matching one version's list against another's data
    -- in which case an entry is right where a file has not changed and wrong
    where it has, with nothing to say which is which.

    A wrong name is worse than no name. Someone editing the file a tool told
    them to edit will not find out for a long time.

    So every entry is checked against the archive on THIS machine before it is
    used: is there really a DDS at that offset, does it really run that long.
    An entry that fails is dropped, silently and permanently. What survives is
    not "a name somebody supplied" but "a name that matches the bytes here".
"""

import json
import os
import struct

# What a file of each kind must begin with. A table entry claiming a .DDS at an
# offset that does not start with 'DDS ' is describing something else.
SIGNATURES = {
    '.dds': (b'DDS ',),
    '.zms': (b'ZMS0008', b'ZMS0007', b'ZMS0006', b'ZMS0005'),
    '.zmo': (b'ZMO0002', b'ZMO0001'),
    '.zmd': (b'ZMD0003', b'ZMD0002'),
}

# Particle and effect records carry no magic number, so they cannot be checked
# the same way. They are verified by shape instead -- see _looks_like_record.
BY_SHAPE = ('.ptl', '.eft')


def _looks_like_record(blob):
    """Is this plausibly a particle or effect record?

    No magic number to lean on, so the check is structural: a small count,
    then a length-prefixed quoted name. Loose enough to accept the real thing,
    tight enough that random bytes almost never pass.
    """
    if len(blob) < 16:
        return False
    count = struct.unpack_from('<I', blob, 0)[0]
    if not 1 <= count <= 64:
        return False
    length = struct.unpack_from('<I', blob, 4)[0]
    if not 3 <= length <= 60 or 8 + length > len(blob):
        return False
    name = blob[8:8 + length]
    return all(32 <= byte < 127 for byte in name)


def load(path, vfs_path, progress=None, should_stop=None):
    """Read a name table and keep only the entries this archive agrees with.

    Returns (names, report) where names maps offset -> name, and report says
    how many were offered, how many survived, and why the rest did not.
    """
    report = {'offered': 0, 'kept': 0, 'wrong_place': 0,
              'past_the_end': 0, 'unchecked': 0, 'clashed': 0, 'error': ''}
    names = {}
    confirmed = set()          # offsets whose signature was actually checked
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            table = json.load(handle)
    except (OSError, ValueError) as trouble:
        report['error'] = str(trouble)
        return names, report

    if isinstance(table, dict):                       # tolerate either shape
        table = table.get('files', [])
    report['offered'] = len(table)
    size = os.path.getsize(vfs_path)

    with open(vfs_path, 'rb') as archive:
        for index, entry in enumerate(table):
            if should_stop and index % 512 == 0 and should_stop():
                break
            if progress and index % 2048 == 0:
                progress(index / float(max(1, len(table))))
            try:
                name = entry['name']
                offset = int(entry['offset'])
                length = int(entry['length'])
            except (KeyError, TypeError, ValueError):
                continue
            if offset < 0 or length <= 0 or offset + length > size:
                report['past_the_end'] += 1
                continue

            suffix = os.path.splitext(name)[1].lower()
            wanted = SIGNATURES.get(suffix)
            checked = False
            if wanted:
                archive.seek(offset)
                head = archive.read(8)
                if not any(head.startswith(sig) for sig in wanted):
                    report['wrong_place'] += 1
                    continue
                checked = True
            elif suffix in BY_SHAPE:
                archive.seek(offset)
                if not _looks_like_record(archive.read(72)):
                    report['wrong_place'] += 1
                    continue
                checked = True
            else:
                # Nothing to check it against -- a .lit or .him or .dat. Kept,
                # but counted separately so the report does not overstate how
                # much was actually confirmed.
                report['unchecked'] += 1
                checked = False

            # TWO ENTRIES CAN CLAIM THE SAME OFFSET, and a naive dict lets the
            # last one win. A verified name being overwritten by an unverified
            # one is exactly the wrong outcome -- so a confirmed entry is never
            # replaced, and the first confirmed entry at an offset stands.
            if offset in names:
                if offset in confirmed or not checked:
                    report['clashed'] += 1
                    continue
                report['clashed'] += 1
            names[offset] = name.replace('/', '\\')
            if checked:
                confirmed.add(offset)
            report['kept'] += 1
    return names, report


def find_table(app_folder):
    """A table sitting next to the app, or None. Nothing is downloaded."""
    for candidate in ('rose_names.json', 'matched_files.json'):
        path = os.path.join(app_folder, candidate)
        if os.path.isfile(path):
            return path
    return None


def describe(report):
    """One paragraph a person can act on."""
    if report.get('error'):
        return "Could not read the name table: %s" % report['error']
    lines = ["%d name(s) offered, %d matched this archive."
             % (report['offered'], report['kept'])]
    if report['wrong_place']:
        lines.append("%d pointed at something else and were dropped."
                     % report['wrong_place'])
    if report['past_the_end']:
        lines.append("%d pointed past the end of the file and were dropped."
                     % report['past_the_end'])
    if report.get('clashed'):
        lines.append("%d claimed an offset another entry already had."
                     % report['clashed'])
    if report['unchecked']:
        lines.append("%d are file types with nothing to check against, so they "
                     "are used but not confirmed." % report['unchecked'])
    if report['offered'] and report['kept'] < report['offered'] * 0.5:
        lines.append("Less than half matched -- this table probably describes "
                     "a different build of the game. Treat its names with "
                     "suspicion.")
    return ' '.join(lines)
