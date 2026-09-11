"""ROSE Asset Hunter -- find out what any texture or model in the game is called.

Double-click this file. Nothing to install.

WHAT IT IS FOR
    ROSE assets do not tell you what they are. A texture is pixels, a model is
    triangles, and a particle file does not even hold its own filename. So the
    first problem in any mod is finding out WHICH file you need, and nothing in
    the game will tell you.

    This makes the game tell you. It replaces assets with markers carrying their
    own names, so you go in, look at whatever you care about, and read the
    answer off it. Then press Restore and everything goes back.

TWO WAYS ROUND
    FORWARD   "what is that thing?"     mark a folder, go and look
    BACKWARD  "where is this used?"     mark one file by name, see what changes
"""

import os
import sys
import threading
import time

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError:
    sys.exit("This needs Python with tkinter, which normally comes with it.\n"
             "On Windows, reinstall Python and tick 'tcl/tk and IDLE'.")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rose_hunt_core as core
import rose_names


# A dark theme, because the first version looked like a 1995 dialog box.
BG = "#1b1b21"
PANEL = "#24242c"
INK = "#e6e6ee"
DIM = "#9a9aab"
ACCENT = "#e8a521"
GOOD = "#66d17a"
WARN = "#e2705a"

# Colours far enough apart to name out loud, and deliberately NOT the colours
# effects usually are. Green, blue and white are what half the game already
# looks like, and a marker you mistake for the real thing teaches you nothing.
PALETTE = [
    ("red", (255, 0, 0)), ("orange", (255, 120, 0)), ("yellow", (255, 240, 0)),
    ("magenta", (255, 0, 220)), ("purple", (140, 0, 255)), ("pink", (255, 150, 190)),
    ("brown", (140, 70, 20)), ("black", (20, 20, 20)), ("lime", (170, 255, 0)),
    ("teal", (0, 190, 150)), ("maroon", (140, 0, 50)), ("grey", (150, 150, 150)),
]

# Folders where a wrong-sized texture tends to take the client down rather than
# just look odd, so the warning can be specific instead of vague.
RISKY = ('map', 'avatar', 'npc')

# Terrain tiles cannot be marked at all, and this is not caution -- it is a
# wall, with the game's own words for it:
#
#     All terrain textures must be the same dimensions, image
#     'S002_01.dds' with size 128 x 128 does not match previous image
#     'T025_01.dds' with size of 256 x 256.
#
#     All terrain textures must be the same compression format, image
#     'S002_01.dds' with format RGBA8 does not match previous image
#     'T025_01.dds' with format of BC3.
#
# A tile set is loaded as a SET and every member must agree on size and
# format. Markers are 128x128 uncompressed; those tiles are 256x256 BC3.
#
# Marking the whole folder does not save it either. Every one of the 109 names
# the archive gives for junon\jg was marked and the game still objected about
# T025_01.dds -- a tile that is loaded but is not in the name list. The set
# cannot be made uniform because the set cannot be seen.
#
# When the game refuses the set it ends up with nothing loaded and then reads
# through it: access violation at address 0. Both crash dumps say exactly that.
TERRAIN = ('terrain', 'junon', 'eldeon', 'luna', 'oro')

# Models that are known to crash the client when marked, found by marking a
# folder one file at a time. 133 meshes in 3ddata\effect\effectmesh were
# tested; 124 were fine and these nine were not.
#
# Why these nine is not known yet. They fall into three families, which
# suggests something structural rather than nine unrelated faults -- but
# nothing in the app can see the difference without the mesh data in hand.
#
# Tested, not guessed. Add to it when something new is found.
KNOWN_BAD_MESHES = (
    '_jemitem_01.zms', '_jemitem_02.zms', '_jemitem_03.zms',
    '_jemitem_04.zms', '_jemitem_05.zms', '_jemitem_06.zms',
    '_pumpkin_01.zms',
    '_warp_join_01.zms', '_warp_join_02.zms',
)


# A name table, if one happens to be sitting next to the app. NONE IS SHIPPED.
# Without it everything works exactly as it always has -- offsets instead of
# names. With it, every entry is checked against this machine's own archive
# before it is believed, and anything that does not match is dropped.
HERE = os.path.dirname(os.path.abspath(__file__))

NAMES = {}
NAME_REPORT = {}


def name_for(offset, fallback=None):
    """What the archive calls the thing at this offset, if anything does."""
    got = NAMES.get(offset)
    if not got:
        return fallback
    return got.rsplit('\\', 1)[-1]


def full_name_for(offset):
    return NAMES.get(offset, '')


class SizeDialog(object):
    """Ask which sizes to pull out, so the dump stays small enough to search."""

    PRESETS = [
        ("Small -- icons and particles      (up to 64)", 0, 64),
        ("Medium -- most effect textures    (65 to 256)", 65, 256),
        ("Large -- interface atlases        (257 to 1024)", 257, 1024),
        ("Everything", 0, 4096),
    ]

    def __init__(self, parent):
        self.result = None
        top = self.top = tk.Toplevel(parent)
        top.title("Which textures?")
        top.configure(bg=BG)
        top.transient(parent)
        top.grab_set()
        ttk.Label(top, text="Pulling everything gives a few thousand files.\n"
                            "Narrowing by size usually gives a few dozen.",
                  style='Dim.TLabel').pack(anchor='w', padx=16, pady=(14, 8))
        self.choice = tk.IntVar(value=1)
        for index, (label, _, _) in enumerate(self.PRESETS):
            ttk.Radiobutton(top, text=label, variable=self.choice,
                            value=index).pack(anchor='w', padx=16, pady=2)
        row = ttk.Frame(top)
        row.pack(fill='x', padx=16, pady=14)
        ttk.Button(row, text="Extract", style='Go.TButton',
                   command=self.ok).pack(side='left')
        ttk.Button(row, text="Cancel", command=top.destroy).pack(side='left', padx=8)
        parent.wait_window(top)

    def ok(self):
        _label, low, high = self.PRESETS[self.choice.get()]
        self.result = (low, high)
        self.top.destroy()


class ParticleViewer(object):
    """Read the game's effect files, and search them by the textures they draw.

    This answers a question nothing else could: WHICH EFFECTS USE THIS TEXTURE.

    Effect files carry no name of their own -- that is the wall behind half of
    this tool -- but they DO name every texture they draw. So while you still
    cannot ask "show me damage_up01", you can ask "show me everything that
    draws star_01", which is usually the question that actually matters.
    """

    def __init__(self, parent, vfs_path):
        self.found = []
        self.shown = []
        self.stop_flag = False
        self.vfs_path = vfs_path

        top = self.top = tk.Toplevel(parent)
        top.title("Browse effects")
        top.geometry("1280x820")
        top.configure(bg=BG)

        bar = ttk.Frame(top)
        bar.pack(side='bottom', fill='x', padx=12, pady=10)
        self.status = ttk.Label(bar, text="reading ...", style='Dim.TLabel')
        self.status.pack(side='left')
        self.warning = tk.Label(bar, text="  reading the archive -- it will feel "
                                          "sluggish until this finishes",
                                bg=BG, fg=WARN)
        self.warning.pack(side='left', padx=10)
        ttk.Button(bar, text="Close", command=self.close).pack(side='right')
        self.save_button = ttk.Button(bar, text="Save selected", style='Go.TButton',
                                      command=self.save, state='disabled')
        self.save_button.pack(side='right', padx=8)
        ttk.Button(bar, text="Stop reading", command=self.stop).pack(side='right')

        search = ttk.Frame(top)
        search.pack(fill='x', padx=12, pady=(12, 0))
        ttk.Label(search, text="Effects that draw this texture:",
                  style='Dim.TLabel').pack(side='left')
        self.wanted = tk.StringVar()
        entry = ttk.Entry(search, textvariable=self.wanted, width=26)
        entry.pack(side='left', padx=6)
        entry.bind('<Return>', lambda _e: self.apply_filter())
        ttk.Button(search, text="Find", command=self.apply_filter).pack(side='left')
        ttk.Button(search, text="Show all",
                   command=self.clear_filter).pack(side='left', padx=6)
        ttk.Button(search, text="Open a file...",
                   command=self.open_loose).pack(side='left', padx=(14, 0))
        ttk.Label(search, text="   a texture like star_01, an effect like "
                              "c_hit_01, or open one of your own",
                  style='Dim.TLabel').pack(side='left', padx=6)

        body = ttk.Frame(top)
        body.pack(fill='both', expand=True, padx=12, pady=10)

        left = ttk.Frame(body)
        left.pack(side='left', fill='both')
        scroll = ttk.Scrollbar(left)
        scroll.pack(side='right', fill='y')
        self.listing = tk.Listbox(left, width=48, yscrollcommand=scroll.set,
                                  bg=PANEL, fg=INK, selectbackground=ACCENT,
                                  selectforeground=BG, highlightthickness=0,
                                  relief='flat', activestyle='none',
                                  selectmode='extended', font=('Consolas', 9))
        self.listing.pack(side='left', fill='both', expand=True)
        scroll.config(command=self.listing.yview)
        self.listing.bind('<<ListboxSelect>>', self.on_pick)

        right = ttk.Frame(body)
        right.pack(side='left', fill='both', expand=True, padx=(12, 0))

        # A moving preview. Reading "alpha 0.6 at t=4.6, fade 1" tells you very
        # little unless you already think in these terms; watching it fade in
        # over four seconds tells you immediately.
        ttk.Label(right, text="Roughly what it does. Not the game's renderer -- "
                              "no blending, no camera -- but the timings and "
                              "sizes are the file's own.",
                  style='Dim.TLabel').pack(anchor='w')
        self.stage = tk.Canvas(right, height=240, bg="#0d0d12",
                               highlightthickness=0)
        self.stage.pack(fill='x', pady=(4, 8))

        self.detail = tk.Text(right, wrap='none', state='disabled', bg=PANEL,
                              fg=INK, relief='flat', padx=10, pady=10,
                              font=('Consolas', 9))
        self.detail.pack(fill='both', expand=True)

        self.playing = None
        self.clock = 0.0
        self._tick()
        threading.Thread(target=self._load, daemon=True).start()

    def _tick(self):
        """Advance the preview. Runs whether or not anything is selected, so
        starting and stopping needs no bookkeeping."""
        if self.playing:
            self.clock += 0.06
            self._draw_stage()
        try:
            self.top.after(60, self._tick)
        except tk.TclError:
            pass

    def _draw_stage(self):
        self.stage.delete('all')
        width = max(200, self.stage.winfo_width())
        height = max(120, self.stage.winfo_height())
        # a ground line and a figure, so sizes and heights mean something
        self.stage.create_line(0, height - 30, width, height - 30, fill="#22222c")
        cx = width // 2
        feet = height - 30
        self.stage.create_oval(cx - 6, feet - 74, cx + 6, feet - 62,
                               outline="#33333f")
        self.stage.create_line(cx, feet - 62, cx, feet - 24, fill="#33333f")
        self.stage.create_line(cx, feet - 24, cx - 9, feet, fill="#33333f")
        self.stage.create_line(cx, feet - 24, cx + 9, feet, fill="#33333f")

        drawn = core.simulate(self.playing, self.clock)
        tints = ["#c88cff", "#ffcf6a", "#7fd8a0", "#ff8f7a", "#8ac6ff", "#e0e0ea"]

        # Rings rather than discs.
        #
        # Solid fills hid each other -- one big particle covered everything
        # behind it and the effect became a blob. A canvas has no real
        # transparency, but a bright outline over a stippled fill reads as one:
        # overlaps stay visible, and you can still tell two emitters apart when
        # they are similar colours.
        #
        # Bigger and older particles are drawn FIRST, so small bright ones end
        # up on top instead of being buried.
        drawn.sort(key=lambda p: -p['scale'])
        for particle in drawn:
            if particle['alpha'] < 0.12:
                continue
            size = max(2.5, particle['scale'] * 0.55)
            x = cx + particle['x'] * 60
            y = feet - 40 - particle['lift'] * 6 + particle['y'] * 30
            shade = tints[particle['emitter'] % len(tints)]
            # NO FILL AT ALL. A stippled fill still reads as a solid disc and
            # buries whatever is behind it, which was the whole complaint.
            # An outline alone leaves the middle genuinely see-through.
            #
            # Two rings: a faint wide one for the glow, a brighter thin one for
            # the edge. Together they read as a lit particle rather than a
            # circle drawn on a page.
            bright = particle['alpha'] > 0.5
            self.stage.create_oval(x - size, y - size, x + size, y + size,
                                   outline=shade, width=3 if bright else 2,
                                   stipple='gray25')
            inner = size * 0.72
            self.stage.create_oval(x - inner, y - inner, x + inner, y + inner,
                                   outline=shade, width=1)
        self.stage.create_text(8, 10, anchor='nw', fill="#6a6a7a",
                               text="t = %.1fs" % self.clock,
                               font=('Consolas', 9))
        # Which colour is which TEXTURE.
        #
        # The preview cannot draw a star as a star -- the record names its
        # texture and nothing more, and a texture cannot be fetched by name. So
        # a ring is the honest shape: "something is here, this big, this
        # bright", without inventing what it looks like.
        #
        # What it CAN say is which file each part is drawn from, and that is
        # usually the question. Read the name here, then go and find it in the
        # texture browser.
        seen = []
        for index, emitter in enumerate(self.playing['emitters']):
            leaf = os.path.basename(emitter['texture'].replace('\\', '/'))
            if leaf not in [name for _c, name in seen]:
                seen.append((tints[index % len(tints)], leaf))
        for row, (colour, leaf) in enumerate(seen[:8]):
            top = 10 + row * 15
            self.stage.create_oval(width - 150, top, width - 141, top + 9,
                                   outline=colour, width=2)
            self.stage.create_text(width - 134, top + 4, anchor='w',
                                   fill=colour, text=leaf[:22],
                                   font=('Consolas', 8))

    def stop(self):
        self.stop_flag = True

    def close(self):
        self.stop_flag = True
        self.top.destroy()

    def _load(self):
        def found(record):
            self.found.append(record)
            if len(self.found) % 40 == 0:
                self.top.after(0, self.refresh)

        def progress(fraction):
            self.top.after(0, lambda: self.status.configure(
                text="reading ... %d%%   (%d found)"
                     % (int(fraction * 100), len(self.everything))))
        core.browse_particles(self.vfs_path, progress=progress,
                              should_stop=lambda: self.stop_flag, on_found=found)
        self.top.after(0, self.refresh)
        self.top.after(0, lambda: self.warning.configure(text=""))
        self.top.after(0, lambda: self.status.configure(
            text="%d effect(s) found -- everything is smooth now."
                 % len(self.found)))

    def refresh(self):
        self.shown = list(self.found)
        self._fill()

    @staticmethod
    def _leaf(texture):
        """Just the filename, lowercased.

        Paths in the archive use DOUBLED backslashes, but not always, so both
        separators are handled rather than relying on os.path -- which only
        understands backslashes when it happens to be running on Windows.
        """
        flat = texture.replace(chr(92), '/')
        return flat.rsplit('/', 1)[-1].strip().lower()

    @staticmethod
    def _matches(leaf, wanted):
        """Does this texture name match what was typed?

        A plain "is it in there" test was too loose in a way that was hard to
        spot. Searching ring_03.dds returned records drawing
        stick_firing_03.dds -- because "ring_03.dds" really is inside
        "stick_firing_03.dds". Seven results and not one of them the thing
        asked for.

        So a match has to begin at a SEGMENT boundary: the start of the name,
        or just after an underscore. ring_03 finds ring_03.dds and
        _ring_03.dds, and never firing_03. A fragment like "ring" still finds
        anything with ring as a word in it.
        """
        return leaf.startswith(wanted) or ('_' + wanted) in leaf

    def _fill(self):
        """One row per record, named after the texture that MATCHED.

        Rows used to be labelled with each record's FIRST emitter, so a search
        returning several records that happen to open the same way gave rows
        that all read alike -- and none of which mentioned what was searched
        for. The matching texture is the useful label; the first one is an
        accident of the file's own order.
        """
        self.listing.delete(0, 'end')
        wanted = self.wanted.get().strip().lower()
        for record in self.shown:
            leaves = [self._leaf(e['texture']) for e in record['emitters']]
            shown = None
            if wanted:
                for leaf in leaves:
                    if self._matches(leaf, wanted):
                        shown = leaf
                        break
            if shown is None:
                shown = leaves[0] if leaves else '?'
            others = len(record['emitters']) - 1
            tail = "  +%d more" % others if others > 0 else ''
            real = name_for(record['offset'])
            if real:
                self.listing.insert('end', "%-26s %-2d  %s%s"
                                    % (real[:26], len(record['emitters']),
                                       shown[:22], tail))
            else:
                self.listing.insert('end', "%-5d %-2d  %s%s"
                                    % (record['offset'] % 100000,
                                       len(record['emitters']), shown[:26], tail))
        self.save_button.configure(state='disabled')

    def apply_filter(self):
        wanted = self.wanted.get().strip().lower()
        if not wanted:
            return
        # Match on the effect's own filename first when a name table is loaded.
        # Searching for c_hit_01 should find c_hit_01, not every effect that
        # happens to draw a texture with those letters in it.
        by_name = [r for r in self.found
                   if wanted in full_name_for(r['offset']).lower()]
        self.shown = by_name or [r for r in self.found
                      if any(self._matches(self._leaf(e['texture']), wanted)
                             for e in r['emitters'])]
        self._fill()
        self.status.configure(
            text="%d effect(s) draw something matching '%s'"
                 % (len(self.shown), wanted))
    def open_loose(self):
        """Read a .PTL from disk instead of from the archive.

        This browser could only ever read rose.vfs, so you could inspect the
        game's effects and not your own -- which is backwards, since the ones
        you are working on are the ones you most want to check. A loose file
        opens here now and is shown exactly the same way.
        """
        path = filedialog.askopenfilename(
            title="Open a particle file",
            filetypes=[("Particle files", "*.ptl *.PTL"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, 'rb') as handle:
                blob = handle.read()
        except OSError as trouble:
            messagebox.showerror("Cannot open it", str(trouble))
            return
        record = core.parse_particle(blob, 0)
        if not record:
            messagebox.showerror(
                "Not a particle file",
                "%s does not read as one.\n\nA .PTL starts with a count of "
                "emitters followed by a quoted name -- this does not."
                % os.path.basename(path))
            return
        record['offset'] = 0
        record['name'] = os.path.basename(path)
        self.found.insert(0, record)
        self.shown = list(self.found)
        self._fill()
        self.listing.selection_clear(0, 'end')
        self.listing.selection_set(0)
        self.listing.see(0)
        self.on_pick()
        self.status.configure(text="opened %s -- %d emitter(s)"
                              % (os.path.basename(path), len(record['emitters'])))

    def clear_filter(self):
        self.shown = list(self.found)
        self._fill()
        self.status.configure(text="%d effect(s)" % len(self.shown))

    def on_pick(self, _event=None):
        picks = self.listing.curselection()
        if not picks:
            return
        self.save_button.configure(state='normal')
        record = self.shown[picks[0]]
        # If a name table is loaded, the record gets its real filename here so
        # the detail pane can lead with it instead of an offset.
        record.setdefault('name', full_name_for(record['offset']))
        self.playing = record
        self.clock = 0.0
        self.detail.configure(state='normal')
        self.detail.delete('1.0', 'end')
        self.detail.insert('end', core.describe_particle(record))
        self.detail.configure(state='disabled')

    def save(self):
        picks = self.listing.curselection()
        if not picks:
            return
        path = filedialog.asksaveasfilename(
            title="Save as", defaultextension=".zip",
            initialfile="rose_effects_%d.zip" % len(picks),
            filetypes=[("Zip archive", "*.zip")])
        if not path:
            return
        core.save_particles_as_zip([self.shown[i] for i in picks], path,
                                   self.vfs_path)
        self.status.configure(text="saved %d effect(s) into %s"
                              % (len(picks), os.path.basename(path)))


class MeshViewer(object):
    """Look at the game's models without installing anything.

    This is the safe answer to the model problem. Replacing a model to find out
    what it is can crash the client, and the crash report cannot say which file
    did it -- so instead of replacing them, read them and draw what is there.

    Each is drawn as a wireframe seen from an angle. That is enough to tell a
    sword from a shield from a ring, which is what you were asking.
    """

    SIZE = 120

    def __init__(self, parent, vfs_path):
        self.meshes = []
        self.cells = []
        self.chosen = set()
        self.stop_flag = False

        top = self.top = tk.Toplevel(parent)
        top.title("Browse models")
        top.geometry("1180x800")
        top.configure(bg=BG)

        bar = ttk.Frame(top)
        bar.pack(side='bottom', fill='x', padx=12, pady=10)
        self.status = ttk.Label(bar, text="reading ...", style='Dim.TLabel')
        self.status.pack(side='left')
        self.warning = tk.Label(bar, text="  reading the archive -- it will feel "
                                          "sluggish until this finishes",
                                bg=BG, fg=WARN)
        self.warning.pack(side='left', padx=10)
        ttk.Button(bar, text="Close", command=self.close).pack(side='right')
        self.save_button = ttk.Button(bar, text="Save selected", style='Go.TButton',
                                      command=self.save, state='disabled')
        self.save_button.pack(side='right', padx=8)
        ttk.Button(bar, text="Stop reading", command=self.stop).pack(side='right')

        ttk.Label(top, text="Models read straight out of the archive -- nothing is "
                            "installed, so nothing can crash.",
                  style='Dim.TLabel').pack(anchor='w', padx=12, pady=(10, 0))

        sorter = ttk.Frame(top)
        sorter.pack(fill='x', padx=12, pady=(8, 0))
        ttk.Label(sorter, text="Sort by:", style='Dim.TLabel').pack(side='left')
        self.sort_by = tk.StringVar(value='found')
        for label, value in (("where it was found", 'found'),
                             ("detail  (triangles)", 'detail'),
                             ("tall or wide", 'shape')):
            ttk.Radiobutton(sorter, text=label, variable=self.sort_by,
                            value=value, command=self.resort).pack(side='left', padx=6)

        ttk.Label(sorter, text="   show only:", style='Dim.TLabel').pack(side='left')
        self.filter_text = tk.StringVar()
        filter_entry = ttk.Entry(sorter, textvariable=self.filter_text, width=14)
        filter_entry.pack(side='left', padx=4)
        filter_entry.bind('<Return>', lambda _e: self.apply_filter())
        ttk.Button(sorter, text="Go", width=4,
                   command=self.apply_filter).pack(side='left')
        ttk.Label(sorter, text="a name, or tall / flat / big / small, or a number of triangles",
                  style='Dim.TLabel').pack(side='left', padx=6)

        pager = ttk.Frame(top)
        pager.pack(fill='x', padx=12, pady=(6, 0))
        ttk.Button(pager, text="< back", command=self.page_back).pack(side='left')
        self.page_label = ttk.Label(pager, text="", style='Dim.TLabel')
        self.page_label.pack(side='left', padx=10)
        ttk.Button(pager, text="next >", command=self.page_next).pack(side='left')

        body = ttk.Frame(top)
        body.pack(fill='both', expand=True, padx=12, pady=(8, 0))

        holder = ttk.Frame(body)
        holder.pack(side='left', fill='both', expand=True)
        self.canvas = tk.Canvas(holder, bg=PANEL, highlightthickness=0)
        scroll = ttk.Scrollbar(holder, command=self.canvas.yview)
        scroll.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.inner = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.inner.bind('<Configure>', lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self._reflow)
        for widget in (self.canvas, self.inner):
            widget.bind('<MouseWheel>',
                        lambda e: self.canvas.yview_scroll(int(-e.delta / 120) * 3,
                                                           'units'))

        self.detail = ttk.Frame(body)
        self.detail.pack(side='left', fill='both', expand=True, padx=(12, 0))
        ttk.Label(self.detail, text="Drag inside the box to turn the model.",
                  style='Dim.TLabel').pack(anchor='w', pady=(0, 6))
        self.big = tk.Canvas(self.detail, bg="#101014", highlightthickness=0)
        self.big.pack(fill='both', expand=True)
        self.big.bind('<Configure>', lambda _e: self._draw_big())
        self.big.bind('<Button-1>', self._drag_start)
        self.big.bind('<B1-Motion>', self._drag)
        self.info = tk.Text(self.detail, height=11, wrap='word', state='disabled',
                            bg=PANEL, fg=INK, relief='flat', padx=8, pady=8,
                            font=('Consolas', 9))
        self.info.pack(fill='x', pady=(8, 0))

        self.turn = 0.6
        self.tilt = 0.5
        self.showing = None
        self.page = 0
        self.per_page = 56
        self.everything = []            # every mesh found, always
        self.found = []                 # the ones currently on show
        self.column = 0
        self.row = 0
        self.vfs_path = vfs_path
        self._shapes = {}
        threading.Thread(target=self._load, daemon=True).start()

    def geometry(self, mesh):
        """The vertices and triangles for one mesh, read when actually needed.

        Only a summary is held for the thousands that are not on screen, so
        this fetches the shape for the handful that are. A small cache keeps
        the current page instant without holding the whole archive.
        """
        offset = mesh['offset']
        got = self._shapes.get(offset)
        if got is None:
            got = core.read_mesh_at(self.vfs_path, offset) or {'points': [], 'faces': []}
            if len(self._shapes) > 60:
                self._shapes.clear()
            self._shapes[offset] = got
        return got

    def show_page(self):
        for child in self.inner.winfo_children():
            child.destroy()
        self.cells = []
        self.meshes = []
        self.chosen.clear()
        self.column = 0
        self.row = 0
        start = self.page * self.per_page
        for mesh in self.found[start:start + self.per_page]:
            self._add(mesh)
        pages = max(1, (len(self.found) + self.per_page - 1) // self.per_page)
        self.page_label.configure(text="page %d of %d   (%d found)"
                                  % (self.page + 1, pages, len(self.found)))
        self.save_button.configure(state='disabled')

    def page_next(self):
        if (self.page + 1) * self.per_page < len(self.found):
            self.page += 1
            self.show_page()

    def page_back(self):
        if self.page > 0:
            self.page -= 1
            self.show_page()

    def apply_filter(self):
        """Narrow by name, by shape, or by size.

        Name comes first when a name table is loaded -- nobody scrolls eight
        thousand wireframes looking for something they can already name. With
        no table the questions are about shape, which is what tells a beam from
        a ring in the first place.
        """
        text = self.filter_text.get().strip().lower()
        if not text:
            self.found = list(self.everything)
        else:
            keep = []
            for mesh in self.everything:
                spans = [mesh['max'][i] - mesh['min'][i] for i in range(3)]
                flat = max(spans[0], spans[1]) or 1e-6
                ratio = spans[2] / flat
                faces = mesh['nfaces']
                match = False
                # Search by NAME first, if a name table is loaded. That is what
                # anyone actually wants -- nobody scrolls 8000 wireframes
                # looking for one thing when they know what it is called.
                real = full_name_for(mesh['offset']).lower()
                if real and text in real:
                    match = True
                elif text.isdigit():
                    match = faces >= int(text)
                elif text.startswith('tall'):
                    match = ratio > 1.6
                elif text.startswith('flat'):
                    match = ratio < 0.25
                elif text.startswith('big'):
                    match = faces > 500
                elif text.startswith('small'):
                    match = faces <= 60
                keep.append(mesh) if match else None
            self.found = keep
        self.page = 0
        self.show_page()
        self.status.configure(text="%d model(s) match" % len(self.found))

    def resort(self):
        key = self.sort_by.get()
        if key == 'detail':
            self.found.sort(key=lambda m: -m['nfaces'])
        elif key == 'shape':
            def ratio(mesh):
                spans = [mesh['max'][i] - mesh['min'][i] for i in range(3)]
                flat = max(spans[0], spans[1]) or 1e-6
                return -(spans[2] / flat)
            self.found.sort(key=ratio)
        else:
            self.found.sort(key=lambda m: m['offset'])
        self.page = 0
        self.show_page()

    def _drag_start(self, event):
        self._last = (event.x, event.y)

    def _drag(self, event):
        """Turn the model by dragging. A still wireframe hides a lot; a moving
        one is usually recognisable within a second."""
        if self.showing is None:
            return
        dx = event.x - self._last[0]
        dy = event.y - self._last[1]
        self._last = (event.x, event.y)
        self.turn += dx * 0.01
        self.tilt = max(-1.5, min(1.5, self.tilt + dy * 0.01))
        self._draw_big()

    def stop(self):
        self.stop_flag = True

    def close(self):
        self.stop_flag = True
        self.top.destroy()

    def _load(self):
        def found(mesh):
            # EVERYTHING is the master list and is always appended to.
            # FOUND is only what is currently on show.
            #
            # These used to be the same list until the scan finished, so
            # filtering mid-scan replaced the list the scan was still writing
            # into -- and clearing the filter afterwards restored an empty one,
            # because `everything` had not been filled yet. The models were all
            # still there; nothing could see them.
            self.everything.append(mesh)
            if not self.filter_text.get().strip():
                self.found = self.everything
            if len(self.everything) <= self.per_page:
                self.top.after(0, self._add, mesh)
            elif len(self.everything) % 150 == 0:
                self.top.after(0, self.show_page)

        def progress(fraction):
            self.top.after(0, lambda: self.status.configure(
                text="reading ... %d%%   (%d found)"
                     % (int(fraction * 100), len(self.everything))))
        core.browse_meshes(self.vfs_path, progress=progress,
                           should_stop=lambda: self.stop_flag, on_found=found)
        if not self.filter_text.get().strip():
            self.found = self.everything
        self.top.after(0, self.show_page)
        self.top.after(0, lambda: self.warning.configure(text=""))
        self.top.after(0, lambda: self.status.configure(
            text="%d model(s) found -- everything is smooth now.%s"
                 % (len(self.everything),
                    ("   (%s could not be read)" %
                     ', '.join("%d x %s" % (n, v)
                               for v, n in sorted(core.SKIPPED_VERSIONS.items())))
                    if core.SKIPPED_VERSIONS else "")))

    def _draw(self, canvas, mesh):
        """A wireframe, seen from a corner.

        No lighting and no hidden-surface removal -- this only has to be enough
        to recognise a shape, and a plain wireframe does that while staying
        fast enough to draw hundreds of them.
        """
        shape = self.geometry(mesh)
        points = shape['points']
        if not points:
            return
        # look down onto the model from an angle, so depth reads
        projected = []
        for x, y, z in points:
            screen_x = (x - y) * 0.7071
            screen_y = (x + y) * 0.4082 - z * 0.8165
            projected.append((screen_x, screen_y))
        xs = [p[0] for p in projected]
        ys = [p[1] for p in projected]
        span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
        scale = (self.SIZE - 16) / span
        offset_x = self.SIZE / 2 - (min(xs) + max(xs)) / 2 * scale
        offset_y = self.SIZE / 2 - (min(ys) + max(ys)) / 2 * scale
        flat = [(p[0] * scale + offset_x, p[1] * scale + offset_y) for p in projected]

        edges = set()
        for a, b, c in shape['faces'][:1200]:
            if max(a, b, c) >= len(flat):
                continue
            for start, end in ((a, b), (b, c), (c, a)):
                edges.add((min(start, end), max(start, end)))
        for start, end in list(edges)[:3000]:
            canvas.create_line(flat[start][0], flat[start][1],
                               flat[end][0], flat[end][1], fill="#8fd8a6")

    # Tiles used to wrap at a fixed number of columns -- seven here, eight in
    # the texture browser. The grid area is only about a third of the window,
    # so three fitted and the rest were drawn past the edge with no horizontal
    # scrollbar to reach them. Every screenshot had a sliced column in it.
    #
    # So the count comes from the actual width instead, and the tiles re-flow
    # when the window is resized.
    def _columns(self):
        try:
            width = self.canvas.winfo_width()
        except Exception:                                   # noqa: BLE001
            width = 0
        if width <= 1:                                      # not drawn yet
            return 3
        return max(1, int(width - 4) // (self.SIZE + 22))

    def _reflow(self, _event=None):
        """Re-place every tile for the width we now have."""
        columns = self._columns()
        if columns == getattr(self, '_last_columns', None):
            return
        self._last_columns = columns
        for index, cell in enumerate(self.cells):
            cell.grid(row=index // columns, column=index % columns, padx=6, pady=6)
        self.row = len(self.cells) // columns
        self.column = len(self.cells) % columns

    def _add(self, mesh):
        cell = tk.Frame(self.inner, bg=PANEL, padx=4, pady=4,
                        highlightthickness=2, highlightbackground=PANEL)
        picture = tk.Canvas(cell, width=self.SIZE, height=self.SIZE,
                            bg="#101014", highlightthickness=0)
        picture.pack()
        self._draw(picture, mesh)
        tk.Label(cell, text="%d pts  %d tris" % (mesh['npoints'],
                                                 mesh['nfaces']),
                 bg=PANEL, fg=DIM, font=('Consolas', 8)).pack()
        cell.grid(row=self.row, column=self.column, padx=6, pady=6)
        self.meshes.append(mesh)
        self.cells.append(cell)
        index = len(self.meshes) - 1
        for widget in (cell, picture):
            widget.bind('<Button-1>', lambda _e, i=index: self.pick(i, False))
            widget.bind('<Control-Button-1>', lambda _e, i=index: self.pick(i, True))
        self.column += 1
        if self.column >= self._columns():
            self.column = 0
            self.row += 1

    def pick(self, index, add):
        if not add:
            self.chosen = {index}
        elif index in self.chosen:
            self.chosen.discard(index)
        else:
            self.chosen.add(index)
        for position, cell in enumerate(self.cells):
            cell.configure(highlightbackground=(ACCENT if position in self.chosen
                                                else PANEL))
        if len(self.chosen) == 1:
            self.showing = self.meshes[next(iter(self.chosen))]
            self._draw_big()
            self._fill_info(self.showing)
        else:
            self.status.configure(text="%d selected" % len(self.chosen))
        self.save_button.configure(state='normal' if self.chosen else 'disabled')

    def _fill_info(self, mesh):
        shape = self.geometry(mesh)
        points = shape['points']
        spans = [max(p[i] for p in points) - min(p[i] for p in points)
                 for i in range(3)] if points else [0, 0, 0]
        lines = ["%d points" % len(points),
                 "%d triangles" % mesh['nfaces'],
                 "",
                 "size   x %.2f" % spans[0],
                 "       y %.2f" % spans[1],
                 "       z %.2f  (up)" % spans[2],
                 ""]
        tall = spans[2] > max(spans[0], spans[1]) * 1.6
        flat = spans[2] < max(spans[0], spans[1]) * 0.25
        if tall:
            lines.append("shape  tall -- a column, beam or spike")
        elif flat:
            lines.append("shape  flat -- a ring, plate or ground mark")
        else:
            lines.append("shape  roughly even")
        real = full_name_for(mesh['offset'])
        if real:
            lines += ["", real]
        lines += ["", "found at %d" % mesh['offset'],
                  "         (0x%X)" % mesh['offset']]
        self.info.configure(state='normal')
        self.info.delete('1.0', 'end')
        self.info.insert('end', "\n".join(lines))
        self.info.configure(state='disabled')
        self.status.configure(text="%d points, %d triangles, at %d"
                              % (mesh['npoints'], mesh['nfaces'], mesh['offset']))

    def _draw_big(self):
        """The selected model, large, at whatever angle it has been turned to."""
        import math
        self.big.delete('all')
        mesh = self.showing
        shape = self.geometry(mesh) if mesh else None
        if not shape or not shape['points']:
            return
        size_w = max(140, self.big.winfo_width())
        size_h = max(140, self.big.winfo_height())
        size = min(size_w, size_h)
        cos_t, sin_t = math.cos(self.turn), math.sin(self.turn)
        cos_p, sin_p = math.cos(self.tilt), math.sin(self.tilt)
        flat = []
        for x, y, z in shape['points']:
            rx = x * cos_t - y * sin_t
            ry = x * sin_t + y * cos_t
            sx = rx
            sy = ry * sin_p - z * cos_p
            flat.append((sx, sy))
        xs = [p[0] for p in flat]
        ys = [p[1] for p in flat]
        span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1.0
        scale = (size - 40) / span
        ox = size_w / 2 - (min(xs) + max(xs)) / 2 * scale
        oy = size_h / 2 - (min(ys) + max(ys)) / 2 * scale
        screen = [(p[0] * scale + ox, p[1] * scale + oy) for p in flat]
        edges = set()
        for a, b, c in shape['faces'][:4000]:
            if max(a, b, c) >= len(screen):
                continue
            for start, end in ((a, b), (b, c), (c, a)):
                edges.add((min(start, end), max(start, end)))
        for start, end in list(edges)[:6000]:
            self.big.create_line(screen[start][0], screen[start][1],
                                 screen[end][0], screen[end][1], fill="#8fd8a6")

    def save(self):
        if not self.chosen:
            return
        out = filedialog.asksaveasfilename(
            title="Save as", defaultextension=".zip",
            initialfile="rose_models_%d.zip" % len(self.chosen),
            filetypes=[("Zip archive", "*.zip")])
        if not out:
            return
        try:
            core.save_meshes_as_zip([self.meshes[i] for i in sorted(self.chosen)],
                                    out, vfs_path=self.vfs_path, names=NAMES)
        except Exception as trouble:                        # noqa: BLE001
            messagebox.showerror("Could not save",
                                 "Nothing was written:\n\n%s" % trouble)
            self.status.configure(text="save failed -- nothing written")
            return
        self.status.configure(text="saved %d model(s) into %s"
                              % (len(self.chosen), os.path.basename(out)))


class TextureViewer(object):
    """A grid of textures, read straight out of the archive.

    Tk can display a PNG from memory, and the core already turns a DDS into
    one -- so the pictures can be shown without anything touching the disk.

    That is the point. Extracting thousands of files to find one is what this
    replaces: look first, save only what you want.
    """

    THUMB = 96

    def __init__(self, parent, vfs_path, size_range):
        self.items = []
        self.images = []                # Tk drops images that are not referenced
        self.cells = []
        self.chosen = set()             # several at once, not one
        self.stop_flag = False

        top = self.top = tk.Toplevel(parent)
        top.title("Browse textures")
        top.geometry("1180x800")
        top.configure(bg=BG)

        bar = ttk.Frame(top)
        bar.pack(side='bottom', fill='x', padx=12, pady=10)
        self.status = ttk.Label(bar, text="reading ...", style='Dim.TLabel')
        self.status.pack(side='left')
        # Said plainly, because a tool that stutters without explaining itself
        # feels broken rather than busy.
        self.warning = tk.Label(bar, text="  reading the archive -- it will feel "
                                          "sluggish until this finishes",
                                bg=BG, fg=WARN)
        self.warning.pack(side='left', padx=10)
        ttk.Button(bar, text="Close", command=self.close).pack(side='right')
        self.save_button = ttk.Button(bar, text="Save selected",
                                      style='Go.TButton',
                                      command=self.save, state='disabled')
        self.save_button.pack(side='right', padx=8)
        self.stop_button = ttk.Button(bar, text="Stop reading", command=self.stop)
        self.stop_button.pack(side='right')

        sorter = ttk.Frame(top)
        sorter.pack(fill='x', padx=12, pady=(10, 0))
        ttk.Label(sorter, text="Sort by:", style='Dim.TLabel').pack(side='left')
        self.sort_by = tk.StringVar(value='found')
        for label, value in (("found", 'found'), ("size", 'size'),
                             ("format", 'format'), ("colour", 'colour')):
            ttk.Radiobutton(sorter, text=label, variable=self.sort_by,
                            value=value, command=self.resort).pack(side='left', padx=6)

        ttk.Label(sorter, text="   nearest to:", style='Dim.TLabel').pack(side='left')
        self.colour_wanted = tk.StringVar()
        colour_entry = ttk.Entry(sorter, textvariable=self.colour_wanted, width=12)
        colour_entry.pack(side='left', padx=4)
        colour_entry.bind('<Return>', lambda _e: self.sort_near_colour())
        ttk.Button(sorter, text="Go", width=4,
                   command=self.sort_near_colour).pack(side='left')
        self.swatch = tk.Canvas(sorter, width=22, height=22, bg=PANEL,
                                highlightthickness=1, highlightbackground="#3a3a46")
        self.swatch.pack(side='left', padx=6)
        ttk.Label(sorter, text="a hex code like e8a521, or a word like amber",
                  style='Dim.TLabel').pack(side='left', padx=6)

        namer = ttk.Frame(top)
        namer.pack(fill='x', padx=12, pady=(6, 0))
        ttk.Label(namer, text="Show only:", style='Dim.TLabel').pack(side='left')
        self.name_wanted = tk.StringVar()
        name_entry = ttk.Entry(namer, textvariable=self.name_wanted, width=28)
        name_entry.pack(side='left', padx=6)
        name_entry.bind('<Return>', lambda _e: self.filter_by_name())
        ttk.Button(namer, text="Find", width=6,
                   command=self.filter_by_name).pack(side='left')
        ttk.Button(namer, text="Show all", width=9,
                   command=self.clear_name).pack(side='left', padx=6)
        self.name_note = ttk.Label(namer, text="", style='Dim.TLabel')
        self.name_note.pack(side='left', padx=8)

        pager = ttk.Frame(top)
        pager.pack(fill='x', padx=12, pady=(6, 0))
        ttk.Button(pager, text="< back", command=self.page_back).pack(side='left')
        self.page_label = ttk.Label(pager, text="", style='Dim.TLabel')
        self.page_label.pack(side='left', padx=10)
        ttk.Button(pager, text="next >", command=self.page_next).pack(side='left')
        ttk.Label(pager, text="   click to select, ctrl-click for several",
                  style='Dim.TLabel').pack(side='left', padx=14)

        body = ttk.Frame(top)
        body.pack(fill='both', expand=True, padx=12, pady=(12, 0))

        holder = ttk.Frame(body)
        holder.pack(side='left', fill='both', expand=True)
        self.canvas = tk.Canvas(holder, bg=PANEL, highlightthickness=0)
        scroll = ttk.Scrollbar(holder, command=self.canvas.yview)
        scroll.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scroll.set)
        self.inner = ttk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.inner.bind('<Configure>', lambda _e: self.canvas.configure(
            scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', self._reflow)
        for widget in (self.canvas, self.inner):
            widget.bind('<MouseWheel>', self._wheel)
            widget.bind('<Button-4>', lambda _e: self.canvas.yview_scroll(-3, 'units'))
            widget.bind('<Button-5>', lambda _e: self.canvas.yview_scroll(3, 'units'))

        # The right-hand pane. The window was mostly empty space before, and
        # everything worth knowing about a texture was already computed.
        # The detail pane takes whatever room is going. On a wide screen the
        # picture should be big -- that is the point of looking at it.
        self.detail = ttk.Frame(body)
        self.detail.pack(side='left', fill='both', expand=True, padx=(12, 0))
        self.big = tk.Canvas(self.detail, bg="#101014", highlightthickness=0)
        self.big.pack(fill='both', expand=True)
        self.big.bind('<Configure>', lambda _e: self._redraw_big())
        self.info = tk.Text(self.detail, height=11, wrap='word', state='disabled',
                            bg=PANEL, fg=INK, relief='flat', padx=8, pady=8,
                            font=('Consolas', 9))
        self.info.pack(fill='x', pady=(8, 0))

        self.page = 0
        self.per_page = 60
        self.everything = []            # every texture found, always
        self.found = []                 # the ones currently on show
        self.column = 0
        self.row = 0
        self.vfs_path = vfs_path
        threading.Thread(target=self._load, args=(vfs_path, size_range),
                         daemon=True).start()

    # -------- paging. Drawing thousands of thumbnails at once is painful even
    # -------- though holding them is cheap, so only a page is ever on screen.

    def show_page(self):
        for child in self.inner.winfo_children():
            child.destroy()
        self.cells = []
        self.images = []
        self.items = []
        self.chosen.clear()
        self.column = 0
        self.row = 0
        start = self.page * self.per_page
        for item in self.found[start:start + self.per_page]:
            self._add(item)
        pages = max(1, (len(self.found) + self.per_page - 1) // self.per_page)
        self.page_label.configure(
            text="page %d of %d   (%d found)"
                 % (self.page + 1, pages, len(self.found)))
        self.save_button.configure(state='disabled')

    def page_next(self):
        if (self.page + 1) * self.per_page < len(self.found):
            self.page += 1
            self.show_page()

    def page_back(self):
        if self.page > 0:
            self.page -= 1
            self.show_page()

    def filter_by_name(self):
        """Narrow to textures whose name contains this. Needs a name table."""
        wanted = self.name_wanted.get().strip().lower()
        if not wanted:
            return self.clear_name()
        if not NAMES:
            self.name_note.configure(
                text="no name table loaded -- searching by name needs one")
            return
        self.found = [i for i in self.everything
                      if wanted in full_name_for(i['offset']).lower()]
        self.page = 0
        self.name_note.configure(text="%d match '%s'" % (len(self.found), wanted))
        self.show_page()

    def clear_name(self):
        self.name_wanted.set('')
        self.found = list(self.everything)
        self.page = 0
        self.name_note.configure(text="")
        self.show_page()

    def sort_near_colour(self):
        """Order by how close each texture is to a colour you name.

        Sorting by hue puts things in rainbow order, which helps -- but when you
        already know you are after a particular violet, asking for that violet
        directly is far quicker.
        """
        wanted = core.parse_colour(self.colour_wanted.get())
        if not wanted:
            self.status.configure(text="I did not understand that colour")
            return
        self.swatch.configure(bg='#%02x%02x%02x' % wanted)
        self.status.configure(text="working out the colours -- this takes a moment")
        self.top.update_idletasks()
        for item in self.found:
            if 'avg' not in item:
                core.ensure_thumbnail(item, self.vfs_path)
                item['avg'] = core.average_colour(item)
        self.found.sort(key=lambda i: core.colour_distance(i['avg'], wanted))
        self.page = 0
        self.show_page()
        self.status.configure(text="closest to #%02X%02X%02X first" % wanted)

    def resort(self):
        """Redraw the grid in a different order.

        Sorting by COLOUR is the useful one nobody expects: effect textures are
        overwhelmingly one hue each, so all the fire lands together and all the
        ice lands together, and you can often spot what you want without
        knowing anything about it.
        """
        key = self.sort_by.get()
        if key == 'size':
            self.found.sort(key=lambda i: (-(i['width'] * i['height']), i['offset']))
        elif key == 'format':
            self.found.sort(key=lambda i: (i['format'], -(i['width'] * i['height'])))
        elif key == 'colour':
            self.status.configure(
                text="working out the colours -- this takes a moment")
            self.top.update_idletasks()
            for item in self.found:
                if 'hue' not in item:
                    core.ensure_thumbnail(item, self.vfs_path)
                    item['hue'] = core.hue_of(core.average_colour(item))
            self.found.sort(key=lambda i: i['hue'])
        else:
            self.found.sort(key=lambda i: i['offset'])
        self.page = 0
        self.show_page()

    def _wheel(self, event):
        self.canvas.yview_scroll(int(-event.delta / 120) * 3, 'units')

    def stop(self):
        self.stop_flag = True
        self.status.configure(text="stopping ...")

    def close(self):
        self.stop_flag = True
        self.canvas.unbind_all('<MouseWheel>')
        self.top.destroy()

    def _load(self, vfs_path, size_range):
        low, high = size_range

        def found(item):
            # Master list and view kept apart, so filtering by name mid-scan
            # cannot replace the list the scan is still writing into.
            self.everything.append(item)
            if not self.name_wanted.get().strip():
                self.found = self.everything
            if len(self.found) <= self.per_page:
                self.top.after(0, self._add, item)
            elif len(self.found) % 200 == 0:
                self.top.after(0, self.show_page)

        def progress(fraction):
            self.top.after(0, lambda: self.status.configure(
                text="reading ... %d%%   (%d found)"
                     % (int(fraction * 100), len(self.everything))))
        try:
            core.browse_textures(vfs_path, min_side=low, max_side=high,
                                 progress=progress,
                                 should_stop=lambda: self.stop_flag,
                                 on_found=found)
        except Exception as error:                     # noqa: BLE001
            self.top.after(0, lambda: self.status.configure(text=str(error)))
            return
        self.top.after(0, self.show_page)
        self.top.after(0, lambda: self.warning.configure(text=""))
        self.top.after(0, lambda: self.status.configure(
            text="%d texture(s) found -- everything is smooth now."
                 % len(self.found)))


    # Same story as the mesh browser: the wrap used to be a fixed eight
    # columns, which was more than the grid area could show, so the last one
    # was always sliced by the scrollbar.
    def _columns(self):
        try:
            width = self.canvas.winfo_width()
        except Exception:                                   # noqa: BLE001
            width = 0
        if width <= 1:
            return 3
        return max(1, int(width - 4) // (self.THUMB + 26))

    def _reflow(self, _event=None):
        columns = self._columns()
        if columns == getattr(self, '_last_columns', None):
            return
        self._last_columns = columns
        for index, cell in enumerate(self.cells):
            cell.grid(row=index // columns, column=index % columns, padx=6, pady=6)
        self.row = len(self.cells) // columns
        self.column = len(self.cells) % columns

    def _add(self, item):
        """Put one thumbnail into the grid, making it now if need be."""
        import base64
        if not core.ensure_thumbnail(item, self.vfs_path):
            return
        try:
            picture = tk.PhotoImage(data=base64.b64encode(item['png']))
        except tk.TclError:
            return
        # PhotoImage can only shrink by whole numbers, which is fine here --
        # these are thumbnails, not proofs.
        factor = max(1, int(max(item['width'], item['height']) / self.THUMB))
        if factor > 1:
            picture = picture.subsample(factor, factor)
        self.images.append(picture)
        self.items.append(item)

        cell = tk.Frame(self.inner, bg=PANEL, padx=4, pady=4,
                        highlightthickness=2, highlightbackground=PANEL)
        label = tk.Label(cell, image=picture, bg="#101014")
        label.pack()
        tk.Label(cell, text="%dx%d %s" % (item['width'], item['height'],
                                          item['format']),
                 bg=PANEL, fg=DIM, font=('Consolas', 8)).pack()
        cell.grid(row=self.row, column=self.column, padx=6, pady=6)
        index = len(self.items) - 1
        self.cells.append(cell)
        for widget in (cell, label):
            widget.bind('<Button-1>', lambda _e, i=index: self.pick(i, False))
            widget.bind('<Control-Button-1>', lambda _e, i=index: self.pick(i, True))
        self.column += 1
        if self.column >= self._columns():
            self.column = 0
            self.row += 1

    def pick(self, index, add):
        """Click replaces the selection, ctrl-click adds to it."""
        if not add:
            self.chosen = {index}
        elif index in self.chosen:
            self.chosen.discard(index)
        else:
            self.chosen.add(index)
        for position, cell in enumerate(self.cells):
            cell.configure(highlightbackground=(ACCENT if position in self.chosen
                                                else PANEL))
        if len(self.chosen) == 1:
            self.show_detail(self.items[next(iter(self.chosen))])
        else:
            self.status.configure(text="%d selected" % len(self.chosen))
        self.save_button.configure(state='normal' if self.chosen else 'disabled')

    def _redraw_big(self):
        if getattr(self, 'showing', None):
            self.show_detail(self.showing, keep=True)

    def show_detail(self, item, keep=False):
        """Fill the right-hand pane: the picture as large as the space allows."""
        import base64
        self.showing = item
        self.big.delete('all')
        width = max(120, self.big.winfo_width())
        height = max(120, self.big.winfo_height())
        try:
            picture = tk.PhotoImage(data=base64.b64encode(item['png']))
            room = min(width, height) - 20
            grow = max(1, int(room / max(item['thumb_w'], item['thumb_h'])))
            if grow > 1:
                picture = picture.zoom(grow, grow)
            self.detail_image = picture              # keep a reference
            self.big.create_image(width // 2, height // 2, image=picture)
        except tk.TclError:
            pass
        if keep:
            return

        blob = core.read_at(self.vfs_path, item['offset'], item['length'])
        real = full_name_for(item['offset'])
        lines = ([real, ""] if real else []) + [
            "%d x %d" % (item['width'], item['height']),
            "format      %s" % item['format'],
            "file size   %s" % _pretty_size(item['length']),
            "found at    %d" % item['offset'],
            "            (0x%X in the archive)" % item['offset'],
            "",
        ]
        breakdown = core.colour_breakdown(blob, item['width'], item['height'])
        if breakdown:
            lines.append("colours")
            for colour, share in breakdown:
                lines.append("   #%02X%02X%02X   %4.1f%%" % (colour + (share,)))
        else:
            lines.append("colours     (compressed -- not read)")
        self.info.configure(state='normal')
        self.info.delete('1.0', 'end')
        self.info.insert('end', "\n".join(lines))
        self.info.configure(state='disabled')
        self.status.configure(text="%dx%d %s at %d"
                              % (item['width'], item['height'],
                                 item['format'], item['offset']))

    def save(self):
        """Save into a zip rather than scattering files.

        Even one texture is two files, and a dozen is two dozen. A zip lands as
        one thing you can move where you like.
        """
        if not self.chosen:
            return
        picked = [self.items[i] for i in sorted(self.chosen)]
        path = filedialog.asksaveasfilename(
            title="Save as", defaultextension=".zip",
            initialfile="rose_textures_%d.zip" % len(picked),
            filetypes=[("Zip archive", "*.zip")])
        if not path:
            return
        core.save_many_as_zip(picked, path, self.vfs_path)
        self.status.configure(text="saved %d file(s) into %s"
                              % (len(picked), os.path.basename(path)))


class SoundViewer(object):
    """Browse the archive's audio.

    Nothing is decoded. An Ogg file states its own channels, sample rate and
    bitrate in plain fields before the compressed audio begins, and the last
    page carries a sample count -- so every sound can be listed with its
    length and quality without reading a single sample.

    Playing is handed to Windows, which has understood Ogg since Windows 10.
    If it will not, the file is written out and opened with whatever player
    the person already has, which is honest rather than silent.
    """

    def __init__(self, parent, vfs_path):
        self.vfs = vfs_path
        self.sounds = []
        self.shown = []
        self.stop_flag = False
        self.temp = None

        top = tk.Toplevel(parent)
        self.top = top
        top.title("Browse sounds")
        top.geometry("1000x720")
        top.configure(bg=BG)
        top.protocol('WM_DELETE_WINDOW', self.close)

        bar = ttk.Frame(top)
        bar.pack(side='bottom', fill='x', padx=12, pady=10)
        self.status = ttk.Label(bar, text="reading ...", style='Dim.TLabel')
        self.status.pack(side='left')
        ttk.Button(bar, text="Close", command=self.close).pack(side='right')
        ttk.Button(bar, text="Save selected", style='Go.TButton',
                   command=self.save).pack(side='right', padx=8)
        ttk.Button(bar, text="Stop reading",
                   command=self.stop).pack(side='right')

        head = ttk.Frame(top)
        head.pack(fill='x', padx=12, pady=(12, 0))
        ttk.Label(head, text="Sort by:  ", style='Dim.TLabel').pack(side='left')
        self.order = tk.StringVar(value='found')
        for label, value in (("where it was found", 'found'),
                             ("kind", 'kind'),
                             ("length", 'length'),
                             ("quality", 'quality')):
            ttk.Radiobutton(head, text=label, value=value, variable=self.order,
                            command=self.resort).pack(side='left', padx=(0, 12))
        ttk.Label(head, text="   WAV are sound effects and play in here. "
                             "OGG are music and open in your own player.",
                  style='Dim.TLabel').pack(side='left')

        ttk.Label(top, text="   where            kind  length    size       "
                            "channels  rate      bitrate",
                  style='Dim.TLabel').pack(fill='x', padx=12, pady=(10, 0))

        body = ttk.Frame(top)
        body.pack(fill='both', expand=True, padx=12, pady=(2, 10))
        scroll = ttk.Scrollbar(body)
        scroll.pack(side='right', fill='y')
        self.listing = tk.Listbox(body, bg=PANEL, fg=INK, relief='flat',
                                  font=('Consolas', 10), selectmode='extended',
                                  selectbackground=ACCENT, selectforeground=BG,
                                  yscrollcommand=scroll.set, activestyle='none')
        self.listing.pack(side='left', fill='both', expand=True)
        scroll.config(command=self.listing.yview)
        self.listing.bind('<<ListboxSelect>>', self.on_pick)
        self.listing.bind('<Double-Button-1>', lambda _e: self.play())

        note = ttk.Frame(top)
        note.pack(fill='x', padx=12)
        ttk.Button(note, text="Play / open", style='Go.TButton',
                   command=self.play).pack(side='left')
        ttk.Button(note, text="Stop", command=self.hush).pack(side='left', padx=6)
        self.detail = ttk.Label(note, text="", style='Dim.TLabel')
        self.detail.pack(side='left', padx=12)

        self.start()

    # ------------------------------------------------------------ reading

    def start(self):
        def work():
            def found(sound):
                self.sounds.append(sound)
                if len(self.sounds) % 10 == 0:
                    self.top.after(0, self.refresh)
            core.browse_sounds(self.vfs, on_found=found,
                               should_stop=lambda: self.stop_flag)
            self.top.after(0, self.refresh)
            self.top.after(0, lambda: self.status.configure(
                text="%d sound(s) found" % len(self.sounds)))
        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        self.stop_flag = True

    def refresh(self):
        self.resort()
        if not self.stop_flag:
            self.status.configure(text="reading ... (%d found)" % len(self.sounds))

    def resort(self):
        how = self.order.get()
        if how == 'kind':
            self.shown = sorted(self.sounds,
                                key=lambda s: (s.get('kind', 'ogg'), s['seconds']))
        elif how == 'length':
            self.shown = sorted(self.sounds, key=lambda s: -s['seconds'])
        elif how == 'quality':
            self.shown = sorted(self.sounds, key=lambda s: (-s['rate'], -s['bitrate']))
        else:
            self.shown = list(self.sounds)
        self.listing.delete(0, 'end')
        for sound in self.shown:
            # The offset is shown because you can sort by it -- a sort with no
            # visible key is just an order you have to take on trust. It is
            # also the only handle any of these has: sounds have no names.
            self.listing.insert('end',
                                "%-11s %-4s %-8s  %-9s %dch %6dHz  %5d kbps  %s"
                                % (name_for(sound['offset'], str(sound['offset'])),
                                   sound.get('kind', 'ogg').upper(),
                                   core.pretty_time(sound['seconds']),
                                   core.pretty_size(sound['length']),
                                   sound['channels'], sound['rate'],
                                   max(0, sound['bitrate']) // 1000,
                                   'music' if sound['seconds'] > 20 else ''))

    # ------------------------------------------------------------ using one

    def picked(self):
        marks = self.listing.curselection()
        return [self.shown[i] for i in marks if i < len(self.shown)]

    def on_pick(self, _event=None):
        chosen = self.picked()
        if not chosen:
            return
        sound = chosen[0]
        self.detail.configure(
            text="%s   %d channel(s)   %d Hz   %d kbps   %d page(s)   at %d"
                 % (core.pretty_time(sound['seconds']), sound['channels'],
                    sound['rate'], max(0, sound['bitrate']) // 1000,
                    sound['pages'], sound['offset']))

    def play(self):
        chosen = self.picked()
        if not chosen:
            return
        sound = chosen[0]
        folder = os.path.join(os.path.expanduser('~'), 'ROSE_Hunt_Play')
        self.temp = core.save_sound(self.vfs, sound, folder,
                                    'preview_%d.%s' % (sound['offset'],
                                                       sound.get('kind', 'ogg')))
        worked, why = core.play(self.temp)
        if worked:
            self.status.configure(text="playing %s" % os.path.basename(self.temp))
            return
        # Honest fallback: Windows would not play it in here, so hand it over.
        # Ogg is expected to land here: Windows will not open it through MCI.
        # A WAV reaching this point IS a surprise and worth the detail.
        if sound.get('kind') == 'ogg':
            self.status.configure(text="music opens in your own player "
                                       "(Windows will not play Ogg in here)")
        else:
            self.status.configure(text="could not play it here (%s) -- "
                                       "opening it in your own player" % why)
        try:
            os.startfile(self.temp)                      # noqa: S606
        except Exception as trouble:                     # noqa: BLE001
            messagebox.showerror("Cannot play it",
                                 "Saved it to:\n\n%s\n\nbut could not open "
                                 "it: %s" % (self.temp, trouble))

    def hush(self):
        core.stop()
        self.status.configure(text="stopped")

    def save(self):
        chosen = self.picked()
        if not chosen:
            messagebox.showinfo("Nothing picked", "Choose one or more first.")
            return
        folder = filedialog.askdirectory(title="Where should they go?")
        if not folder:
            return
        out = os.path.join(folder, 'ROSE_Sounds_' + time.strftime('%Y%m%d_%H%M'))
        for sound in chosen:
            core.save_sound(self.vfs, sound, out)
        self.status.configure(text="saved %d to %s" % (len(chosen), out))

    def close(self):
        self.stop_flag = True
        core.stop()
        self.top.destroy()


class HunterWindow(object):

    def __init__(self, root):
        self.root = root
        self.all_names = {'dds': [], 'zms': []}
        self.folders = {}
        self.folder_keys = []
        self.selected_names = []
        self.selected_kinds = set()
        self.busy = False
        self.stop_flag = False

        root.title("ROSE Asset Hunter")
        root.geometry("900x740")
        root.minsize(840, 680)
        root.configure(bg=BG)
        self._style()

        # Buttons are packed to the BOTTOM first, so the list growing can never
        # push them off screen. In the first version they were packed last and
        # disappeared at the default window size.
        self._build_actions()
        self._build_folder()
        self._build_body()

        self.say("Point me at your ROSE folder and press Read the archive.", ACCENT)
        self.say("")
        self.say("That lists every texture and model the game names, so you pick")
        self.say("from what is really there. It takes a couple of minutes.", DIM)
        installed = core.what_is_installed(self.folder.get()) if self.folder.get() else None
        if installed:
            self.say("")
            self.say("NOTE: markers from %s are still installed." % installed['when'], WARN)
            self.say("Press Restore everything before playing normally.", WARN)

    # ------------------------------------------------------------ appearance

    def _style(self):
        style = ttk.Style()
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        style.configure('.', background=BG, foreground=INK, fieldbackground=PANEL)
        style.configure('TFrame', background=BG)
        style.configure('TLabel', background=BG, foreground=INK)
        style.configure('Dim.TLabel', background=BG, foreground=DIM)
        style.configure('TLabelframe', background=BG, foreground=ACCENT,
                        bordercolor="#3a3a46")
        style.configure('TLabelframe.Label', background=BG, foreground=ACCENT)
        style.configure('TButton', background=PANEL, foreground=INK,
                        bordercolor="#3a3a46", focuscolor=BG, padding=6)
        style.map('TButton', background=[('active', "#33333e")])
        style.configure('Go.TButton', background="#3a2f18", foreground=ACCENT)
        style.map('Go.TButton', background=[('active', "#4a3c1e")])
        style.configure('TRadiobutton', background=BG, foreground=INK)
        style.map('TRadiobutton', background=[('active', BG)])
        style.configure('TCheckbutton', background=BG, foreground=INK)
        style.map('TCheckbutton', background=[('active', BG)])
        style.configure('TEntry', fieldbackground=PANEL, foreground=INK,
                        bordercolor="#3a3a46")
        style.configure('Horizontal.TProgressbar', background=ACCENT,
                        troughcolor=PANEL, bordercolor=PANEL,
                        lightcolor=ACCENT, darkcolor=ACCENT)

    # ------------------------------------------------------------ layout

    def _build_actions(self):
        bar = ttk.Frame(self.root)
        bar.pack(side='bottom', fill='x', padx=12, pady=(4, 12))

        self.status = ttk.Label(bar, text="", style='Dim.TLabel')
        self.status.pack(anchor='w')
        self.bar = ttk.Progressbar(bar, mode='determinate',
                                   style='Horizontal.TProgressbar')
        self.bar.pack(fill='x', pady=(2, 8))

        # TWO rows. Eight buttons on one line ran off the edge at the default
        # window size -- the same fault as before, caused by adding to a row
        # that was already full rather than making a second one.
        look = ttk.Frame(bar)
        look.pack(fill='x', pady=(0, 6))
        ttk.Label(look, text="Look at things:  ", style='Dim.TLabel').pack(side='left')
        ttk.Button(look, text="Textures", style='Go.TButton', width=11,
                   command=self.open_viewer).pack(side='left', padx=(0, 6))
        ttk.Button(look, text="Models", width=11,
                   command=self.open_mesh_viewer).pack(side='left', padx=(0, 6))
        ttk.Button(look, text="Effects", width=11,
                   command=self.open_particle_viewer).pack(side='left', padx=(0, 6))
        ttk.Button(look, text="Sounds", width=11,
                   command=self.open_sound_viewer).pack(side='left', padx=(0, 6))
        ttk.Label(look, text="   Pull out:  ", style='Dim.TLabel').pack(side='left')
        ttk.Button(look, text="All textures", width=13,
                   command=self.start_extract).pack(side='left', padx=(0, 6))
        ttk.Button(look, text="Interface files", width=14,
                   command=self.start_extract_text).pack(side='left')

        act = ttk.Frame(bar)
        act.pack(fill='x')
        self.install_button = ttk.Button(act, text="Install markers",
                                         style='Go.TButton',
                                         command=self.start_install, state='disabled')
        self.install_button.pack(side='left')
        ttk.Button(act, text="Restore everything",
                   command=self.start_restore).pack(side='left', padx=8)
        ttk.Button(act, text="What's installed?",
                   command=self.show_installed).pack(side='left')
        ttk.Button(act, text="Close", command=self.root.destroy).pack(side='right')
        self.cancel_button = ttk.Button(act, text="Cancel", command=self.cancel,
                                        state='disabled')
        self.cancel_button.pack(side='right', padx=8)

    def _build_folder(self):
        frame = ttk.LabelFrame(self.root, text=" 1.  Your ROSE Online folder ")
        frame.pack(fill='x', padx=12, pady=(12, 6))
        self.folder = tk.StringVar(value=core.find_game_folder())
        ttk.Entry(frame, textvariable=self.folder).pack(
            side='left', fill='x', expand=True, padx=10, pady=10)
        ttk.Button(frame, text="Browse...", command=self.pick_folder).pack(
            side='left', padx=(0, 6))
        ttk.Button(frame, text="Read the archive", style='Go.TButton',
                   command=self.start_scan).pack(side='left', padx=(0, 10))

    def _build_body(self):
        body = ttk.Frame(self.root)
        body.pack(fill='both', expand=True, padx=12, pady=6)

        left = ttk.LabelFrame(body, text=" 2.  What do you want to identify? ")
        left.pack(side='left', fill='both', expand=True)

        ttk.Label(left, text="Pick a folder. These are the folders the archive"
                             " really has.",
                  style='Dim.TLabel').pack(anchor='w', padx=10, pady=(8, 0))

        holder = ttk.Frame(left)
        holder.pack(fill='both', expand=True, padx=10, pady=8)
        scroll = ttk.Scrollbar(holder)
        scroll.pack(side='right', fill='y')
        self.folder_list = tk.Listbox(
            holder, selectmode='extended', yscrollcommand=scroll.set,
            bg=PANEL, fg=INK, selectbackground=ACCENT, selectforeground=BG,
            highlightthickness=0, relief='flat', activestyle='none',
            font=('Consolas', 9))
        self.folder_list.pack(side='left', fill='both', expand=True)
        scroll.config(command=self.folder_list.yview)
        self.folder_list.bind('<<ListboxSelect>>', self.on_folder_pick)

        # The files inside. ROSE folder names give nothing away, and a folder
        # often mixes textures and models -- so being able to take only the
        # models, or only three files, matters more here than it would
        # elsewhere.
        ttk.Label(left, text="Files inside. Leave empty to take the whole folder.",
                  style='Dim.TLabel').pack(anchor='w', padx=10, pady=(6, 0))

        filter_row = ttk.Frame(left)
        filter_row.pack(fill='x', padx=10, pady=2)
        self.kind_filter = tk.StringVar(value='all')
        for label, value in (("everything", 'all'),
                             ("textures only", 'dds'),
                             ("models only", 'zms'),
                             ("effects only", 'ptl')):
            ttk.Radiobutton(filter_row, text=label, variable=self.kind_filter,
                            value=value,
                            command=self.refresh_files).pack(side='left', padx=(0, 10))

        holder2 = ttk.Frame(left)
        holder2.pack(fill='both', expand=True, padx=10, pady=(2, 8))
        scroll2 = ttk.Scrollbar(holder2)
        scroll2.pack(side='right', fill='y')
        self.file_list = tk.Listbox(
            holder2, selectmode='extended', yscrollcommand=scroll2.set,
            bg=PANEL, fg=INK, selectbackground=ACCENT, selectforeground=BG,
            highlightthickness=0, relief='flat', activestyle='none',
            font=('Consolas', 9))
        self.file_list.pack(side='left', fill='both', expand=True)
        scroll2.config(command=self.file_list.yview)
        self.file_list.bind('<<ListboxSelect>>', self.on_file_pick)

        one = ttk.Frame(left)
        one.pack(fill='x', padx=10, pady=(0, 10))
        ttk.Label(one, text="or mark ONE file by name  --  finds everywhere it is used",
                  style='Dim.TLabel').pack(anchor='w')
        row = ttk.Frame(one)
        row.pack(fill='x', pady=4)
        self.single = tk.StringVar()
        entry = ttk.Entry(row, textvariable=self.single)
        entry.pack(side='left', fill='x', expand=True)
        entry.bind('<Return>', lambda _e: self.find_single())
        ttk.Button(row, text="Find it", command=self.find_single).pack(side='left', padx=6)

        right = ttk.Frame(body)
        right.pack(side='left', fill='both', expand=True, padx=(10, 0))

        marks = ttk.LabelFrame(right, text=" 3.  How should they be marked? ")
        marks.pack(fill='x')
        self.style_var = tk.StringVar(value='text')
        for label, value, hint in (
                ("Names written across it", 'text',
                 "the default -- good for anything reasonably large"),
                ("Names written sideways", 'vtext',
                 "for tall thin things, where flat text gets squeezed"),
                ("Flat colours", 'colour',
                 "for very small things -- a key is saved to your ROSE folder")):
            ttk.Radiobutton(marks, text=label, variable=self.style_var,
                            value=value).pack(anchor='w', padx=10, pady=(6, 0))
            ttk.Label(marks, text="      " + hint, style='Dim.TLabel').pack(anchor='w')

        self.mesh_ok = tk.BooleanVar(value=False)
        ttk.Checkbutton(marks, text="Allow models to be marked  (CAN CRASH THE GAME)",
                        variable=self.mesh_ok).pack(anchor='w', padx=10, pady=(10, 0))
        ttk.Label(marks, text="      models are animated by data expecting an exact\n"
                              "      shape -- some will not accept a replacement",
                  style='Dim.TLabel').pack(anchor='w', pady=(0, 10))

        log_frame = ttk.LabelFrame(right, text=" What's going on ")
        log_frame.pack(fill='both', expand=True, pady=(8, 0))
        self.log = tk.Text(log_frame, height=10, wrap='word', state='disabled',
                           bg=PANEL, fg=INK, relief='flat', padx=8, pady=8)
        self.log.pack(fill='both', expand=True, padx=8, pady=8)
        for tag, colour in (('accent', ACCENT), ('good', GOOD),
                            ('warn', WARN), ('dim', DIM)):
            self.log.tag_configure(tag, foreground=colour)

    # ------------------------------------------------------------ helpers

    def say(self, text, colour=None):
        tag = {ACCENT: 'accent', GOOD: 'good', WARN: 'warn', DIM: 'dim'}.get(colour)
        self.log.configure(state='normal')
        self.log.insert('end', text + "\n", tag or '')
        self.log.see('end')
        self.log.configure(state='disabled')
        self.root.update_idletasks()

    def set_status(self, text):
        self.status.configure(text=text)
        self.root.update_idletasks()

    def pick_folder(self):
        chosen = filedialog.askdirectory(title="Where is ROSE Online installed?")
        if chosen:
            self.folder.set(chosen)

    def check_folder(self):
        where = self.folder.get().strip()
        if not where or not os.path.isdir(where):
            messagebox.showerror("Not found", "Please choose your ROSE Online folder.")
            return None
        if not os.path.isfile(os.path.join(where, 'rose.vfs')):
            messagebox.showerror(
                "That doesn't look right",
                "There is no rose.vfs in that folder.\n\n"
                "Pick the folder that contains it -- usually\n"
                r"C:\Program Files\ROSE Online")
            return None
        return where

    def cancel(self):
        """Ask the running job to stop at its next checkpoint.

        Long jobs check this between chunks rather than being killed outright,
        so nothing is left half-written.
        """
        self.stop_flag = True
        self.set_status("stopping ...")

    def should_stop(self):
        return self.stop_flag

    def run_in_background(self, work):
        if self.busy:
            return
        self.busy = True
        self.stop_flag = False
        self.cancel_button.configure(state='normal')

        def wrapper():
            try:
                work()
            except Exception as error:                 # noqa: BLE001
                self.say("")
                self.say("Something went wrong: %s" % error, WARN)
            finally:
                if self.stop_flag:
                    self.say("")
                    self.say("Stopped.", WARN)
                self.busy = False
                self.stop_flag = False
                self.cancel_button.configure(state='disabled')
                self.set_status("")
                self.bar['value'] = 0
        threading.Thread(target=wrapper, daemon=True).start()

    # ------------------------------------------------------------ actions

    def start_scan(self):
        where = self.check_folder()
        if not where:
            return
        self.say("")
        self.say("Reading the archive ...", ACCENT)
        self.say("Every name is read fresh. A saved list once covered only about "
                 "62% of what was really there, and a long search failed because "
                 "the answer was never in the set being looked through.", DIM)

        def work():
            def progress(fraction):
                self.bar['value'] = fraction * 100
                self.set_status("reading the archive ... %d%%" % int(fraction * 100))

            # A name table, only if one happens to be sitting next to the app.
            # NONE IS SHIPPED. Every entry is checked against THIS archive
            # before it is believed -- a name that points at something else is
            # dropped, because a wrong name is worse than no name.
            vfs = os.path.join(where, 'rose.vfs')
            table = rose_names.find_table(HERE)
            if table:
                self.say("")
                self.say("Found a name table next to the app. Checking it "
                         "against your archive ...", ACCENT)
                globals()['NAMES'], globals()['NAME_REPORT'] = rose_names.load(
                    table, vfs, should_stop=self.should_stop)
                self.say(rose_names.describe(NAME_REPORT),
                         GOOD if NAMES else WARN)

            found = core.scan_everything(vfs, progress, self.should_stop)
            # Effect filenames are in the archive as plain strings, same as
            # texture and model names -- they just cannot be tied to the data.
            # For marking that does not matter: a marker is written to a PATH,
            # and the path is all we need.
            found['ptl'] = core.scan_archive(vfs, 'ptl', None, None,
                                             self.should_stop)
            self.all_names = found
            groups = {}
            for extension in ('dds', 'zms', 'ptl'):
                for folder, names in core.group_by_folder(found[extension]).items():
                    groups.setdefault((extension, folder), []).extend(names)
            self.folders = groups
            self.folder_list.delete(0, 'end')
            self.folder_keys = []
            for key in sorted(groups, key=lambda k: (k[0], k[1])):
                extension, folder = key
                kind = {'dds': 'textures', 'zms': 'MODELS',
                        'ptl': 'EFFECTS'}.get(extension, extension)
                self.folder_list.insert(
                    'end', "%-50s %5d  %s" % (folder[:50], len(groups[key]), kind))
                self.folder_keys.append(key)
            self.say("")
            self.say("%d textures, %d models and %d effects, in %d folders."
                     % (len(found['dds']), len(found['zms']),
                        len(found.get('ptl', [])), len(groups)), GOOD)
            self.say("Pick one or more folders on the left. Ctrl-click for several.")
        self.run_in_background(work)

    def on_folder_pick(self, _event=None):
        picks = self.folder_list.curselection()
        if not picks:
            return
        names = []
        kinds = set()
        for index in picks:
            key = self.folder_keys[index]
            names.extend(self.folders[key])
            kinds.add(key[0])
        self.folder_names = names
        self.selected_names = names
        self.selected_kinds = kinds
        self.refresh_files()
        self.install_button.configure(state='normal' if names else 'disabled')

        # Show what is actually IN there. ROSE folder names give very little
        # away -- "di68" tells you nothing -- so seeing the filenames is often
        # what tells you whether this is the folder you meant.
        self.say("")
        if len(picks) == 1:
            self.say("%s  --  %d file(s)" % (self.folder_keys[picks[0]][1], len(names)),
                     ACCENT)
        else:
            self.say("%d folders, %d file(s)" % (len(picks), len(names)), ACCENT)
        textures = [n for n in names if n.lower().endswith('.dds')]
        models = [n for n in names if n.lower().endswith('.zms')]
        if textures:
            self.say("    %d texture(s)" % len(textures), DIM)
        if models:
            self.say("    %d MODEL(s) -- these can crash the game" % len(models), WARN)
        self.say("    listed on the left -- pick individual files, or leave the",
                 DIM)
        self.say("    list unselected to take all of them", DIM)

    def refresh_files(self):
        """Fill the file list from the chosen folders, honouring the filter."""
        names = getattr(self, 'folder_names', [])
        wanted = self.kind_filter.get()
        if wanted == 'dds':
            names = [n for n in names if n.lower().endswith('.dds')]
        elif wanted == 'zms':
            names = [n for n in names if n.lower().endswith('.zms')]
        self.filtered_names = names
        self.file_list.delete(0, 'end')
        for name in names:
            leaf = os.path.basename(name.replace('\\', '/'))
            tag = 'MODEL' if leaf.lower().endswith('.zms') else ''
            self.file_list.insert('end', "%-40s %s" % (leaf[:40], tag))
        self.selected_names = names
        self.selected_kinds = set(
            'zms' if n.lower().endswith('.zms') else 'dds' for n in names)
        self.set_status("%d file(s) selected" % len(names))
        self.install_button.configure(state='normal' if names else 'disabled')

    def on_file_pick(self, _event=None):
        picks = self.file_list.curselection()
        if not picks:
            self.selected_names = getattr(self, 'filtered_names', [])
        else:
            self.selected_names = [self.filtered_names[i] for i in picks]
        self.selected_kinds = set(
            'zms' if n.lower().endswith('.zms') else 'dds' for n in self.selected_names)
        self.set_status("%d file(s) selected" % len(self.selected_names))
        self.install_button.configure(
            state='normal' if self.selected_names else 'disabled')

    def find_single(self):
        """Mark ONE named file, wherever it lives.

        The other direction: not "what is that?" but "where is this used?".
        Mark shine_03 and every effect drawing on it lights up at once.

        It doubles as a search. If nothing matches, the name does not exist --
        worth knowing before spending an evening looking for it.
        """
        wanted = self.single.get().strip().lower()
        if not wanted:
            return
        if not self.all_names['dds'] and not self.all_names['zms']:
            self.say("")
            self.say("Read the archive first.", WARN)
            return
        hits = []
        for extension in ('dds', 'zms'):
            for name in self.all_names[extension]:
                leaf = os.path.basename(name.replace('\\', '/'))
                if wanted in leaf or wanted in name:
                    hits.append((extension, name))
        self.say("")
        if not hits:
            self.say("Nothing in the archive is called anything like '%s'." % wanted, WARN)
            self.say("Either the name is different, or it is not there at all -- "
                     "which is itself an answer.", DIM)
            self.install_button.configure(state='disabled')
            return
        self.say("Found %d match(es) for '%s':" % (len(hits), wanted), GOOD)
        for _extension, name in hits[:12]:
            self.say("    %s" % name)
        if len(hits) > 12:
            self.say("    ... and %d more" % (len(hits) - 12), DIM)
        self.selected_names = [n for _, n in hits]
        self.selected_kinds = set(e for e, _ in hits)
        self.folder_list.selection_clear(0, 'end')
        self.install_button.configure(state='normal')
        self.set_status("%d file(s) selected" % len(hits))

    def install_effect_markers(self, where, names):
        """Put a numbered marker at each chosen effect and see which one fires.

        This is the trick that found c_hit_01 -- the effect that draws every
        ranged critical and has nothing in its name to say so. Searching by
        name had failed for two days; sixteen numbered markers answered it in
        one fight.

        A name table cannot do this. A table says what an effect is CALLED. It
        cannot say which one FIRES when you do something, and those come apart
        badly in this game.
        """
        if len(names) > 40:
            if not messagebox.askokcancel(
                    "That is a lot of effects",
                    "%d effects selected.\n\nEvery one gets its own number, and "
                    "reading a number off the screen only works if you can tell "
                    "them apart. Sixteen or so is comfortable.\n\nCarry on "
                    "anyway?" % len(names)):
                return
        if not messagebox.askokcancel(
                "Mark %d effect(s)?" % len(names),
                "Each one will be replaced by a marker drawing its own number.\n\n"
                "Go and do the thing you are chasing, then read the number. The "
                "key is written to ROSE_HUNT_KEY.txt next to the game.\n\n"
                "Press Restore everything when you are done."):
            return

        def work():
            self.say("")
            self.say("Writing %d effect marker(s) ..." % len(names), ACCENT)
            record, key = core.install_effect_markers(
                where, names,
                on_step=lambda done, total: self.set_status(
                    "writing markers ... %d of %d" % (done, total)))
            core.write_key_file(where, key)
            bad = [f for f in record['files'] if 'error' in f]
            self.say("Wrote %d file(s)." % (len(record['files']) - len(bad)), GOOD)
            for f in bad:
                self.say("  could not write %s -- %s" % (f['path'], f['error']), WARN)
            self.say("")
            self.say("Start the game and do the thing you are chasing. Whichever "
                     "number appears is the effect that fired.", ACCENT)
            self.say("The numbers are listed in ROSE_HUNT_KEY.txt next to the game.")
            self.say("Press Restore everything when you are done.")
        self.run_in_background(work)

    def start_install(self):
        where = self.check_folder()
        if not where or not self.selected_names:
            return
        names = self.selected_names
        style = self.style_var.get()

        # Effects are marked a different way -- a numbered particle record
        # rather than a texture -- so they are handled on their own and cannot
        # be mixed with textures in one go.
        if 'ptl' in self.selected_kinds:
            if len(self.selected_kinds) > 1:
                messagebox.showinfo(
                    "One kind at a time",
                    "Effects are marked differently from textures and models, "
                    "so pick effects on their own.")
                return
            return self.install_effect_markers(where, names)

        if 'zms' in self.selected_kinds and not self.mesh_ok.get():
            messagebox.showwarning(
                "Models are switched off",
                "Your selection includes models, and marking those can crash "
                "the game.\n\nIf you want to anyway, tick the box under the "
                "marking options first.")
            return

        # Names with no folder cannot be marked usefully. The archive stores
        # some paths as a bare filename, and there are 32,550 of them -- with
        # nothing to say where they belong, a marker can only be written to the
        # root of the game folder, where the client will never look for it.
        #
        # It used to write them anyway: thousands of files scattered into the
        # ROSE folder, achieving nothing and needing cleaning up afterwards.
        pathless = [n for n in names if '\\' not in n and '/' not in n]
        if pathless:
            messagebox.showerror(
                "These have no folder",
                "%d of these are stored in the archive as a bare filename with "
                "no folder.\n\nA marker has to be written to the path the game "
                "asks for, and there is no path here -- so it would land in the "
                "root of your ROSE folder, where nothing will ever read it. "
                "Thousands of files, no markers.\n\nUse Browse textures for "
                "these instead. Nothing is installed, so the missing path does "
                "not matter." % len(pathless))
            return

        bad = [n for n in names
               if os.path.basename(n.replace('\\', '/')).lower() in KNOWN_BAD_MESHES]
        if bad:
            if not messagebox.askokcancel(
                    "Some of these are known to crash",
                    "%d of these models have crashed the game every time they "
                    "were marked:\n\n    %s\n\nThe other 124 in that folder "
                    "were fine, so this is not models in general -- it is "
                    "these.\n\nSkip them and mark the rest?"
                    % (len(bad), '\n    '.join(
                        os.path.basename(n.replace('\\', '/')) for n in bad[:12]))):
                return
            names = [n for n in names if n not in bad]
            if not names:
                return

        terrain = [n for n in names if any(word in n.lower() for word in TERRAIN)]
        if terrain:
            messagebox.showerror(
                "Terrain tiles cannot be marked",
                "%d of these are terrain tiles, and marking those does not "
                "work -- it is not a risk, it fails every time.\n\n"
                "The game loads a tile set as one thing and refuses it unless "
                "every tile agrees on size and format. Markers are 128x128 "
                "uncompressed; the tiles are 256x256 BC3.\n\n"
                "Marking the whole folder does not help. Some tiles the game "
                "loads are not in the archive's name list at all, so the set "
                "can never be made to agree.\n\n"
                "Use Browse textures to look at these instead -- nothing is "
                "installed, so nothing can refuse it." % len(terrain))
            return

        risky = [n for n in names if any(word in n.lower() for word in RISKY)]
        if risky:
            if not messagebox.askokcancel(
                    "This selection is risky",
                    "%d of these are world, map or character textures.\n\n"
                    "Those are often loaded at a size the game expects, and "
                    "replacing them can crash it rather than just look odd.\n\n"
                    "Nothing is damaged either way -- press Restore and it is "
                    "fine. Carry on?" % len(risky)):
                return

        if len(names) > 400:
            if not messagebox.askokcancel(
                    "That is a lot of files",
                    "About to mark %d assets.\n\nMost of the game will look "
                    "very strange until you press Restore. That is the idea, "
                    "but it is worth expecting.\n\nCarry on?" % len(names)):
                return

        self.say("")
        self.say("Writing %d markers ..." % len(names), ACCENT)

        def maker(name):
            stem = os.path.splitext(os.path.basename(name.replace('\\', '/')))[0]
            if name.lower().endswith('.zms'):
                return core.marker_mesh('cube')
            if style == 'colour':
                return core.marker_colour(
                    64, PALETTE[abs(hash(stem)) % len(PALETTE)][1])
            return core.marker_text(128, stem[:18], vertical=(style == 'vtext'))

        def work():
            def step(done, total):
                self.bar['value'] = 100.0 * done / total
                self.set_status("writing %d of %d" % (done, total))
            record = core.install(where, names, maker,
                                  note='%d file(s)' % len(names), on_step=step)
            failed = [f for f in record['files'] if 'error' in f]
            self.say("Wrote %d." % (len(record['files']) - len(failed)), GOOD)
            if failed:
                self.say("%d could not be written -- is the game closed?"
                         % len(failed), WARN)

            if style == 'colour':
                buckets = {}
                for name in names:
                    stem = os.path.splitext(
                        os.path.basename(name.replace('\\', '/')))[0]
                    colour = PALETTE[abs(hash(stem)) % len(PALETTE)][0]
                    buckets.setdefault(colour, []).append(name)
                path = core.write_key_file(where, sorted(buckets.items()))
                self.say("")
                self.say("A colour says nothing on its own, so the key is saved "
                         "next to the game:", DIM)
                self.say("    %s" % path, ACCENT)

            self.say("")
            self.say("Start the game and look at what you are chasing.")
            self.say("Press Restore everything when you are done.")
        self.run_in_background(work)

    def start_restore(self):
        where = self.check_folder()
        if not where:
            return
        if not core.what_is_installed(where):
            self.say("")
            self.say("Nothing of mine is installed -- nothing to undo.", DIM)
            return

        def work():
            def step(done, total):
                self.bar['value'] = 100.0 * done / total
                self.set_status("removing %d of %d" % (done, total))
            removed, missing = core.restore(where, on_step=step)
            try:
                os.remove(os.path.join(where, 'ROSE_HUNT_KEY.txt'))
            except OSError:
                pass
            self.say("")
            self.say("Removed %d file(s)." % removed, GOOD)
            if missing:
                self.say("%d were already gone." % missing, DIM)
            self.say("The game is back to normal.")
        self.run_in_background(work)

    def start_extract(self):
        """Pull the actual textures out, so they can be edited rather than
        merely identified.

        Marking tells you WHAT something is. This gets you the pixels, which is
        what you need to recolour a thing instead of redrawing it -- and
        redrawing from scratch is how several of our own effects came out wrong.

        The archive index cannot fetch a file by name: its offsets are not seek
        positions and land in unrelated data. But a DDS announces itself with
        four bytes and a header giving its size, so they can be lifted out
        directly. What that cannot recover is the NAME -- pixels and filenames
        live apart -- so everything is saved numbered with a PNG beside it, and
        you find yours by looking.
        """
        where = self.check_folder()
        if not where:
            return
        out = filedialog.askdirectory(title="Where should the textures go?")
        if not out:
            return

        # Narrow it before it starts. Pulling everything gives a few thousand
        # files that are miserable to search; asking for one size band usually
        # gives a few dozen. You cannot ask for a file by NAME -- pixels and
        # filenames are stored apart -- but you can ask for its SHAPE.
        answer = SizeDialog(self.root).result
        if answer is None:
            return
        min_side, max_side = answer
        self.say("")
        self.say("Pulling textures out of the archive ...", ACCENT)
        self.say("They come out numbered, not named -- the pixels and the "
                 "filenames are stored apart, so the number is where it was "
                 "found. Look through the PNGs to find yours.", DIM)

        def work():
            def progress(fraction):
                self.bar['value'] = fraction * 100
                self.set_status("extracting ... %d%%" % int(fraction * 100))
            count, folder = core.carve_textures(
                os.path.join(where, 'rose.vfs'), out, progress=progress,
                min_side=min_side, max_side=max_side,
                should_stop=self.should_stop)
            self.say("")
            self.say("Saved %d texture(s) to:" % count, GOOD)
            self.say("    %s" % folder, ACCENT)
            self.say("Sorted into folders by format and then by size, inside a "
                     "folder of its own -- so it never scatters files into "
                     "whatever you picked.", DIM)
            self.say("Each has a .png beside it. Turn on large thumbnails and "
                     "the one you want is usually obvious.", DIM)
        self.run_in_background(work)

    def start_extract_text(self):
        """Pull the interface documents and stylesheets out as readable text.

        This is the thing we reach for most often. Every interface change starts
        by reading one of these, and unlike textures they can be NAMED -- a
        document says <body id="hud"> and a stylesheet says which image it
        draws from, so they come out as hud.html and stateicon.css rather than
        as numbers.
        """
        where = self.check_folder()
        if not where:
            return
        out = filedialog.askdirectory(title="Where should the interface files go?")
        if not out:
            return
        self.say("")
        self.say("Pulling interface documents and stylesheets ...", ACCENT)

        def work():
            def progress(fraction):
                self.bar['value'] = fraction * 100
                self.set_status("reading ... %d%%" % int(fraction * 100))
            written = core.carve_documents(os.path.join(where, 'rose.vfs'), out,
                                           progress, self.should_stop)
            self.say("")
            self.say("%d document(s) and %d stylesheet(s)."
                     % (written['documents'], written['stylesheets']), GOOD)
            self.say("    %s" % out, ACCENT)
            self.say("These come out properly named, because they say what they "
                     "are -- a document declares its own id and a stylesheet "
                     "names the image it uses.", DIM)
        self.run_in_background(work)

    def open_viewer(self):
        """Look at textures inside the app, before pulling anything.

        The answer to "can we see them without dumping thousands of files".
        Nothing reaches the disk until you pick one -- which also answers
        pulling a single file: find it here, press Save this one.
        """
        where = self.check_folder()
        if not where:
            return
        answer = SizeDialog(self.root).result
        if answer is None:
            return
        TextureViewer(self.root, os.path.join(where, 'rose.vfs'), answer)

    def open_mesh_viewer(self):
        """Look at models instead of replacing them.

        Marking models can crash the client, and no crash report says which one
        did it. Reading them is safe and answers the same question.
        """
        where = self.check_folder()
        if not where:
            return
        MeshViewer(self.root, os.path.join(where, 'rose.vfs'))

    def open_particle_viewer(self):
        """Read the effect files, and search them by the textures they draw."""
        where = self.check_folder()
        if not where:
            return
        ParticleViewer(self.root, os.path.join(where, 'rose.vfs'))

    def open_sound_viewer(self):
        """Every sound in the archive, with its length -- and playable."""
        where = self.check_folder()
        if not where:
            return
        SoundViewer(self.root, os.path.join(where, 'rose.vfs'))

    def show_installed(self):
        where = self.check_folder()
        if not where:
            return
        record = core.what_is_installed(where)
        self.say("")
        if not record:
            self.say("Nothing of mine is installed.", DIM)
            return
        self.say("Installed %s -- %s" % (record['when'], record.get('note', '')), ACCENT)
        for entry in record['files'][:6]:
            self.say("    %s" % entry['path'], DIM)
        if len(record['files']) > 6:
            self.say("    ... and %d more" % (len(record['files']) - 6), DIM)


def _pretty_size(count):
    for unit in ('bytes', 'KB', 'MB'):
        if count < 1024 or unit == 'MB':
            return "%.0f %s" % (count, unit) if unit == 'bytes' else "%.1f %s" % (count, unit)
        count /= 1024.0
    return "%d" % count


def main():
    root = tk.Tk()
    HunterWindow(root)
    root.mainloop()


if __name__ == '__main__':
    main()
