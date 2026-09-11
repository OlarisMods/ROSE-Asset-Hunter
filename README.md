# ROSE Asset Hunter

Find out what any texture or model in ROSE Online is called.

### [⬇ Download ROSE Asset Hunter](https://github.com/OlarisMods/ROSE-Asset-Hunter/releases/latest/download/ROSE_Asset_Hunter.zip)

Unzip it anywhere and double-click `ROSE Asset Hunter.pyw`. Keep the files
together in their folder.

*(That link always gives you the newest version. You do not need a GitHub
account, and there is nothing to sign up for.)*

---

## What you need

**Python 3 for Windows**, from [python.org](https://www.python.org/downloads/).
That is the only requirement — the tool itself installs nothing and uses only
what Python ships with.

When you install Python, make sure **"tcl/tk and IDLE"** is ticked. It is on by
default. That is what draws the window, and without it the app will not open.

If double-clicking the `.pyw` does nothing, Windows does not know what to do
with the file type. Right-click it once, choose **Open with**, and pick Python.

---

## The problem it solves

ROSE assets do not tell you what they are. A texture is just pixels, a model is
just triangles, and a particle file does not even contain its own filename.

So the first problem in any visual mod is working out *which* file you need, and
nothing in the game will tell you. People end up guessing, or replacing files
one at a time to see what changes.

This makes the game tell you instead.

**Forward — "what *is* that thing?"** Pick a folder, install markers, go and
look. Whatever you are chasing has its own name written across it.

**Backward — "where is this *used*?"** Type a filename and press Find it. Every
place that file is drawn lights up at once.

**Effects and sounds too.** Browse every particle effect in the archive and
watch its timeline play, or list every sound with its length — the music and
the effects, which nothing else has been able to show. If nothing matches, that name does
not exist in the archive — worth knowing before you spend an evening looking
for it.

---

## Is this safe for my game?

Yes, and here is exactly why.

ROSE loads loose files in preference to the ones in its archives. This tool only
ever writes loose files. **It never opens, edits or damages `rose.vfs` or
anything the game shipped with.** Deleting what it wrote puts everything back
precisely as it was.

It records every file it writes, so Restore removes exactly those and touches
nothing else.

It does not talk to the server, does not change how the game plays, and gives no
advantage. It is a way of reading labels.

**It is not an executable.** It is plain Python text files you can read in
Notepad, and every one of them is in this repository. There is no network code
anywhere in it — the full set of imports is `json`, `os`, `re`, `struct`, `sys`,
`threading`, `time`, `math`, `random`, `tkinter`, `zipfile` and `zlib`. No
`subprocess`, no `eval`, no downloads.

---

## Does it break the game's encryption?

**No. Nothing is decrypted, and the encrypted file is never even opened.**

The tool opens exactly one game file, `rose.vfs`, and only ever read-only.
`data.idx` — the index, which is encrypted on the current official client — is
never touched.

It works because two things in the archive are already plain:

- **The names are readable text.** Paths like
  `3ddata\effect\particles\texture\_beam_piller_01.dds` sit in `rose.vfs` as
  ordinary ASCII. The tool scans for them. That is a text search over a large
  file, not a decode — the same thing you would see opening it in a hex editor.

- **The files announce themselves.** A DDS texture starts with the four bytes
  `DDS ` followed by a header giving its size. A model starts with `ZMS`. So
  files can be found by their own signature and lifted out whole, without ever
  asking the index where anything is.

Marking is a third thing again, and touches none of that: the tool *generates a
new DDS* with the filename drawn across it and writes it to disk, where the
client's own loose-file-first loading picks it up.

**The proof is the limitation.** Extracted textures come out numbered, not
named — `00412.dds`, not `_beam_piller_01.dds`. Names and pixels are stored
apart, and the thing that links them is the encrypted index. If the encryption
had been circumvented, extraction would come out properly named. It does not.

---

## What it will not do

**Interface art cannot be read as text.** Window frames and gauges are cut from
an atlas a pixel or two at a time, so a name written across the file never
appears whole on screen. Marking still tells you *which* atlas changed, which is
usually the question.

**Terrain tiles cannot be marked.** The game loads a tile set as one thing and
refuses it unless every tile agrees on size and format, and some tiles it loads
are not named in the archive at all — so the set can never be made to agree.
The app refuses rather than letting you try. Use Browse textures for those.

**Some things are encrypted and out of reach entirely** — fonts, some model
chains, and part of the archive index.

---

## A warning about models

Replacing textures is safe. **Replacing models can crash the game.** Some are
animated by data that expects an exact shape and will not accept a substitute.

Models are switched off until you tick the box, and the game warns you again
before writing. Nothing is damaged if it crashes — press Restore and it is fine.

**Be clear about what the warnings are.** They do not prevent a crash and cannot;
there is no way to know in advance which assets the client will refuse. They tell
you a crash is *likely*, so it is not a surprise.

**And the crash report cannot tell you which file did it.** A Windows minidump
records addresses inside `trose.exe`. Turning those into names needs a symbol
file that is not shipped, and even then the answer would be "crashed inside the
mesh loader", not "crashed on this file".

So marking models *in bulk* is not much use — it will often just crash, and
nothing tells you which one caused it. Where it earns its place is when you
already know the name and want to see where it is used: one file, one question.

Some world, map and character textures can crash it too, for the same reason.
Those get a warning of their own.

Use `Browse models...` instead where you can. It reads models straight out of the
archive and draws each as a wireframe. Nothing is installed, so nothing can
crash.

---

## How to use it

1. Point it at your ROSE Online folder.
2. Press **Read the archive**. This lists every texture and model the game
   names, so you pick from what is really there rather than from a guess.
3. Pick a folder, or select individual files, or type one filename in the box.
4. **Close the game**, press **Install markers**, then start it and look.
5. Press **Restore everything** when you are done.

Anything slow can be stopped with Cancel. It stops at the next safe point rather
than being killed, so nothing is left half-written.

### Three ways of marking

- **Names across it** — the default, good for anything reasonably large.
- **Names sideways** — for tall thin things. Some effects draw particles as
  narrow vertical slivers and flat lettering gets squeezed into nothing.
- **Flat colours** — for very small things. One lit pixel still carries its hue,
  so when a sprite is a few pixels across, colour is all that survives. A key
  naming every colour is written to `ROSE_HUNT_KEY.txt`, and Restore removes it.

### Getting files out

- **Browse textures** shows them inside the app, read straight from the archive.
  Nothing touches your disk until you pick one. Sort by size, format, or **by
  colour** — effect textures are mostly one hue each, so all the fire lands
  together and all the ice lands together.
- **Extract textures** pulls everything out at once, as `.dds` to edit and
  `.png` to look at, sorted into folders by format and size.
- **Extract interface files** saves every interface document and stylesheet as
  readable text. These come out *properly named*, because they say what they
  are. This is the one worth reaching for most often.

---

## Will an update break it?

Probably not, because of how it finds things. It does not know any file paths,
offsets or version numbers — it finds assets by what they *are*, and those
formats have not changed in twenty years.

A game update should change nothing; names and positions move, and it re-reads
them every time. Private servers should work, sometimes better. Very old clients
are fine for textures; models are read as ZMS version 7 and 8.

**The one thing that would break it** is the archive being encrypted. `data.idx`
already is. If `rose.vfs` ever goes the same way, nothing here could read it and
no amount of fixing would help. Worth knowing before relying on it.

---

## What is in this folder

| File | |
|---|---|
| `ROSE Asset Hunter.pyw` | the app — double-click this |
| `rose_hunt_core.py` | scanning, markers, install, restore |
| `rose_font.py` | a hand-built font, so nothing needs installing |
| `rose_names.py` | reads a name table, if you have one |
| `READ ME FIRST.txt` | the same guide, offline |

All must stay together. Plain Python, standard library only — no installer, no
downloads, no network access.

---

## Licence

MIT. See [LICENSE](LICENSE).

That covers **this tool's code only**. ROSE Online's textures, models,
interface files and every other asset belong to Rednim Games, and nothing here
changes that. This tool reads and labels them locally on your own machine; it
does not redistribute them.
