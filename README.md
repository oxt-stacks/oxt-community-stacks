# OXT Community Stacks

Stacks that people made with [OXT-Beyond](https://github.com/SethMorrowSoftware/OpenXTalk-Beyond)
and want to share. This repository holds the stacks, the checker that
reads each one before it is listed, and the builder of the gallery: a web
site where anyone can press **Play** and try a stack in their browser,
with nothing to install.

**The gallery: <https://oxt-stacks.github.io/oxt-community-stacks/>**

## Is it safe to play a stack from here?

A stack is a small program, and a program does what its author wrote. So
the gallery does not rely on trusting every author. It relies on where a
stack runs:

- **In your browser, never on your computer.** A stack in the gallery
  plays in OXT-Beyond's web engine. A web page cannot open your files or
  start programs, and neither can a stack that plays in one.
- **In a box of its own.** The page tells the browser to treat the
  playing stack as a stranger to the site: it cannot read the site's
  saved data or cookies, cannot see another stack's, and cannot send you
  to another page.
- **Read before it is listed.** A checker reads every script of every
  stack, without running any of it, and writes the labels on the stack's
  page ("Uses the internet", "Runs JavaScript"). Stacks with a password,
  stacks that bring native code and stacks that start other programs are
  refused. Then a person looks at it.
- **Open to read.** Every script of every stack is on its page.

One thing to be careful with: a stack you **download** and open in
OXT-Beyond on your own computer is no longer in a box. Read its scripts
first, and open stacks only from people you trust.

## Share a stack

You need your stack (an `.oxtstack`, `.livecode` or `.rev` file, with any
pictures or sounds it loads from files beside it), a title, your name as
you want it shown, a sentence or two about what it does, and a licence.

**The easy way:** in OXT-Beyond, open your stack and choose **File >
Share Stack...** It asks for the title, your name, what the stack does
and a licence, takes the picture, and puts one file on your Desktop.
Attach that file to a post on the
[OpenXTalk forum](https://openxtalk.org/forum/) and say you would like it
in the community stacks. Somebody will do the rest.

If your File menu has no Share Stack (it came after OXT-Beyond
0.2.8-dp.1), attach the stack itself to your post, with its title, your
name, what it does and the licence you choose.

**With GitHub:** open a pull request that adds one folder and nothing
else:

```
stacks/
  beat-box/                 your stack's name: lower-case letters, digits, hyphens
    1.0/                    its version: 1, 1.2 or 1.2.3
      Beat Box.oxtstack     the stack
      listing.json          what the gallery shows (below)
      screenshot.png        a picture, if you can (PNG, JPEG, GIF or WebP)
```

`listing.json`:

```json
{
  "title": "Beat Box",
  "author": "A. Name",
  "description": "A drum machine with sixteen steps. Click the squares to lay down a beat.",
  "licence": "GPL-3.0-only",
  "main": "Beat Box.oxtstack",
  "screenshot": "screenshot.png",
  "tags": ["music", "sound"]
}
```

| Field | What it is |
|---|---|
| `title` | The stack's name, 2 to 60 characters. |
| `author` | Your name as you want it shown, 2 to 40 characters. |
| `description` | What the stack does, 10 to 600 characters. Plain text: no links, no `<` or `>`. |
| `licence` | `GPL-3.0-only` or `GPL-3.0-or-later`. Every community stack is GPL 3, the licence of OXT-Beyond itself: others may use, change and share it, as long as they share their changes the same way. |
| `main` | The file name of the stack to open. |
| `screenshot` | Optional. The file name of a picture in the folder. |
| `tags` | Optional. Up to five short lower-case words. |
| `fill` | Optional. `true` for a stack laid out for a big screen, such as a studio or an editor: it is scaled to fill the window it plays in, as big as fits, and fitted again when the window changes size. Left out, the stack plays at its own size, in the middle. |

You do not say what your stack uses (the internet, files). The checker
reads that from the scripts, so a listing cannot say less than the stack
does.

A minute or two after you open the pull request, the checker answers on
it in plain words: either the stack can be shared, with the labels it
will carry, or what stands in the way and how to change it. Push again
and it checks again. Then an admin looks at the stack and merges it, and
it appears in the gallery.

To change a stack that is already listed, share it again as a higher
version in a new folder (`stacks/beat-box/1.1/`). A published version is
never changed, because people may already have it.

### What makes a stack work here

- It plays in a web browser, so it must work in OXT-Beyond's **Stack
  Player**. Try it there first.
- No password on the stack. Everyone must be able to read a shared
  stack's scripts.
- No `shell`, no starting other programs, no AppleScript, no registry,
  no externals of its own.
- The libraries that come with OXT-Beyond's web engine are fine:
  SodiumXT, CoinXT, Box2Dxt, box3dxt, render3dxt, DataChannelXT, audioxt,
  xlsxt, gamepadxt, webpxt, barcodext and NostrXT. ENetXT, TorrentXT and
  OnionXT have no web version, so a stack that uses them is refused,
  because it could not play.
- Using the internet (`put url`), files and JavaScript is fine. The
  stack's page will say so.
- At most 200 files, 25 MB each and 50 MB together.
- What a stack saves (a high score, a note) lasts only while its page is
  open. Give people an export or download button for anything to keep.

## Share a script library

A script library is a script that gives other people's stacks new
commands and functions. The gallery lists these too, on its
[Libraries](https://oxt-stacks.github.io/oxt-community-stacks/libraries.html)
page.

**A library is not played in a box.** People install it into OXT-Beyond
and build it into their own programs, where it runs as their own scripts
do. So a library is held to more than a stack is: it must be plain text
that anyone can read, the checker and a person read all of it, the
package people download is made here from that same text, and its page
says plainly what installing means. That is care, not a guarantee:
install libraries only from people you trust.

Post the library file on the
[OpenXTalk forum](https://openxtalk.org/forum/) with its title, your
name, what it does and a licence, or open a pull request that adds one
folder and nothing else:

```
libraries/
  easing/                   your library's name: lower-case letters, digits, hyphens
    1.0/                    its version
      easing.livecodescript the library: a script-only stack
      listing.json          what the gallery shows
```

`listing.json` has the fields a stack's has, with these differences:

- `main` is the library's file. It must be a script-only stack: a text
  file ending in `.livecodescript` whose first line is
  `script "<name>"`.
- `licence` may be `GPL-3.0-only` or `GPL-3.0-or-later`, or one of the
  short licences that can be used together with GPL 3: `MIT`,
  `BSD-2-Clause`, `BSD-3-Clause`, `Apache-2.0`, `LGPL-3.0-only` or
  `LGPL-3.0-or-later`. Whatever the library's licence, a program built
  with OXT-Beyond holds its GPL 3 engine and is shared under GPL 3 as a
  whole.
- `demo` (optional) is the folder name of a stack in `stacks/` that
  shows the library at work. The library's page then has a **Try the
  demo** button, and the demo plays in the browser, boxed in, like any
  stack. A demo carries its own copy of the library.
- There is no `screenshot`.

### What makes a library work here

- One script file, and beside it only text or pictures that explain it.
- A name of its own on the first line, with no spaces, such as
  `anna.easing`. Names that begin like OXT-Beyond's own (`rev...`,
  `com.livecode.`, `org.openxtalk.`) are refused.
- Handler names of its own. Two libraries cannot answer to one name, so
  a handler that a listed library or the xTalk Suite already has is
  refused. Start each with a short prefix (`ezCurve`, `ezBetween`).
- At least one command or function that is not `private`.
- Everything a stack may not do, a library may not: no `shell`, no
  starting other programs, no native code, no script fetched from an
  address.
- Handling the engine's messages (`openStack`, `keyDown`) is allowed
  and is pointed out to the reviewer, because a loaded library gets
  them from every stack. Pass them on.

The gallery packs each library as an extension package (`.lce`), which
the Extension Manager of OXT-Beyond, or of any IDE built on LiveCode
Community 9, installs. The IDE loads a script library by sending it
`extensionInitialize`; a library that handles neither that nor
`extensionFinalize` gets the usual pair added at its end, and its
script page shows the added lines.

## Report a problem

Every stack's page has a *Report a problem* link, which opens an issue
here. A stack that turns out to be a problem is taken down.

## For people who work on this repository

- `tools/scan.py` checks one stack folder or one library folder;
  `tools/lcscan.py` holds the rules for scripts and `tools/listing.py`
  the rules for `listing.json`.
- `tools/accept.py` takes a stack that came as a package (from
  OXT-Beyond's File > Share Stack...) into `stacks/`, if it can be
  shared.
- `tools/build_site.py` builds the gallery and packs the libraries;
  `tools/sign.py` signs its lists of stacks and of libraries for the IDE.
- `tools/tests/` tests all of it, with OXT-Beyond's real engine and a
  real browser.
- [docs/MAINTAINING.md](docs/MAINTAINING.md) says how a stack is
  reviewed, how the site is published, where it may live and how the
  signing key is made.

The tools need only Python 3 (no packages) and, for the browser test,
Node with Playwright. To run the tests:

```sh
export OXT_ENGINE=$(python3 tools/fetch.py engine ~/oxt-cache)
export OXT_PLAYER=$(python3 tools/fetch.py player ~/oxt-cache)
python3 -m unittest discover -s tools/tests
```

The tools and pages are under the MIT licence ([LICENSE](LICENSE)). Each
stack and each library belongs to its author, under the licence in its
`listing.json`.
