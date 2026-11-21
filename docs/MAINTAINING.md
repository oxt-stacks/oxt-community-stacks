# Looking after the community stacks

For the admin: how a shared stack gets from a pull request to the
gallery, what to look at before merging, and the few things that are set
up once.

## The path of a stack

1. Somebody opens a pull request that adds `stacks/<name>/<version>/`.
   (A stack posted on the forum is put into a pull request by an admin:
   see "A stack that came as a package" below.)
2. **Scan** (`.github/workflows/scan.yml`) reads it and comments on the
   pull request: it can be shared, with its labels, or what stands in
   the way. **Checks** builds the gallery with it and plays it in a
   browser.
3. An admin reviews it (below) and merges.
4. **Publish** (`.github/workflows/publish.yml`) reads every stack
   again, builds the site, signs the list of stacks and puts it on the
   web.

Nothing in a pull request is ever run by Scan: it uses the tools of the
main branch and treats the pull request's files as data. The top of
`scan.yml` says how, and what never to add to it.

## A stack that came as a package

Many authors do not use GitHub. OXT-Beyond's File > Share Stack... packs
a stack, its listing and a screenshot into one `.zip`, and the author
posts it on the forum. To bring it in:

```sh
python3 tools/accept.py ~/Downloads/beat-box-1.0.zip
```

The tool believes nothing in the package: every path must stay inside
one `stacks/<name>/<version>/` folder, links are refused, and it never
unpacks more than a stack may hold, whatever sizes the `.zip` claims.
The unpacked stack goes through the same checker as a pull request's.
Only when it can be shared is the folder put into `stacks/`, and the
report is printed either way, to pass on to the author. Then open a pull
request with the new folder, as in step 1, and review it as any other.

Never unpack a package by hand into the repository, and never open a
stack from one in the IDE to "have a look": read it on the pull request,
where the checker lists what it does.

## Reviewing a stack

The checker has already refused passwords, native code, other programs
and stacks that cannot play on the web. What it cannot judge is intent.
Before merging:

- **Read the checker's comment.** "The lines these come from" lists
  every line that uses the internet, files or JavaScript. See where the
  stack sends things and whether that fits what it says it does.
- **Read the notes.** `do`, `send`, `value` and `set the script` build
  script while the stack runs, which the checker cannot follow. They are
  ordinary in honest stacks; look at what is built.
- **Play it** in OXT-Beyond's Stack Player (download the stack from the
  pull request and drop it on the player's page). Does it do what the
  description says? Does it ask for a password, an email
  address or money, or pretend to be something else (a sign-in page, a
  system message)? Refuse those.
- **Look at the listing**: a real description, a licence the author can
  give, a screenshot of the stack and not of something else.
- **A new version of a stack that is already here** must come from the
  same author. The checker's comment says when this is the case.
- Only merge when both **Scan** and **Checks** are green.

To take a stack down, remove its folder in a pull request (or straight
on main) and Publish rebuilds the gallery without it.

## Reviewing a library

A library (`libraries/<name>/<version>/`) takes the same path as a
stack: a pull request, Scan, Checks, a review, Publish. The review
matters more, because **nothing boxes a library in**. People install it
into their IDE and build it into their own programs. What stands in for
the box is that the library is plain text, that you have read it, and
that the package is made here from the text you read.

The checker has refused what it can: anything that is not one
script-only stack, a name or a handler that is somebody else's, native
code, other programs, script fetched from an address. Before merging:

- **Read the whole script**, on the pull request. It is one text file.
  If it is too long or too tangled to read, that is a reason to say no.
- **Read the checker's notes.** "Handles messages that the engine
  sends" means the library sees those messages from every stack once it
  is loaded: check that each such handler passes the message on and
  does only what the description says. `do`, `send`, `value` and
  `set the script` build script while it runs: look at what is built.
- **See where it sends things.** "Uses the internet" and "Opens or
  saves files" each list their lines. A library has the user's own
  rights there, on their own computer.
- **Look at the names.** A short prefix on every handler, and a name on
  the first line that is the author's own.
- **Try the demo**, if there is one, in the Stack Player. The demo is a
  stack and goes through the stack review too. `tools/tests`
  (`test_a_demo_carries_its_library_word_for_word`) holds a demo to the
  library it is listed with.
- **The licence** is one the author can give, and one of the listed
  ones.

A library that came on the forum as a bare file: make the folder
`libraries/<name>/<version>/` yourself, with the file and a
`listing.json`, and open a pull request. Never load the library in your
IDE to "have a look".

The site's `libraries.json` is the list the IDE reads. It is signed
with the same key as `index.json`, in the same job, and `index.json`
itself holds no library: IDEs that were released before libraries came
read it as they always did.

## Where the gallery may live

Never on an address shared with something a visitor is signed in to, or
that keeps their data. Each stack plays boxed in, and an address of the
gallery's own is the second wall behind that box.

- Not on `openxtalk.org` or a subdomain of it: a subdomain shares the
  site's cookies.
- Not on `sethmorrowsoftware.github.io`: that is the address of
  OXT-Beyond's web IDE and its Stack Player, which keep people's own
  stacks in the browser.
- **On a free GitHub organization of its own**, whose `github.io`
  address holds nothing else.

It lives at **<https://oxt-stacks.github.io/oxt-community-stacks/>**:
the repository belongs to the `oxt-stacks` organization, made for the
gallery alone. The deploy job of `publish.yml` runs only while the
repository belongs to that organization, so a fork, or a move anywhere
else, builds the gallery but never puts it on the web.

How it was set up, once (and how to do it again, should it ever move):

1. Create a free organization on GitHub.
2. Transfer this repository to it (Settings > General > Transfer).
3. Change the owner in the `if:` of the deploy job of `publish.yml`, and
   the fallback in `tools/build_site.py`, to the new organization.
4. In the repository, Settings > Pages > Source: **GitHub Actions**.
5. Run the Publish workflow (Actions > Publish > Run workflow), or merge
   anything. Its last job plays every stack on the real address.

Keep the organization for the gallery alone. Any other site published
from it would share the gallery's address.

## The signing key

The IDE's gallery (in OXT-Beyond) reads the list of stacks,
`index.json`, from the web site, and believes it only when its
signature, `index.sig`, is good. The key that signs is made once:

```sh
python3 tools/sign.py new-key --secret-out ~/stacks-signing.secret
```

- The secret half goes into the file you name and is never shown. Open
  the file, copy its one line into the repository's secret
  `STACKS_SIGNING_KEY` (Settings > Secrets and variables > Actions > New
  repository secret), then delete the file, or keep it somewhere safe
  and offline.
- The public half is written to `signing-key.pub`. Commit that file.
  OXT-Beyond carries the same line, so that the IDE can check the
  signature.

Only the `sign` job of `publish.yml` sees the secret. It starts from a
fresh copy of the tools, reads no stack and runs no browser.

Until the secret is there, Publish says so in a warning and publishes
the site unsigned. The web gallery works either way.

If the secret is ever exposed: make a new key the same way, replace the
secret and `signing-key.pub`, and ship the new public key in OXT-Beyond.

## Settings worth having

- Branch protection on `main`: require a pull request, and require the
  checks **Scan** and **Checks** to pass.
- Actions > General > "Require approval for first-time contributors":
  leave it on. (Scan runs without it by design; Checks, which runs the
  pull request's own code, waits for it.)

## Moving to a newer OXT-Beyond

`tools/pins.json` names the release whose engine reads the stacks and
whose Stack Player plays them, with the SHA-256 of each download. To
move: change the tag, the two asset names and the two digests (the
release's `SHA256SUMS` has them) in a pull request of its own. Checks
then runs every test, and plays every stack, with the new release.

The tag can also be a web pre-release, `web-<label>`. When OXT-Beyond's
web engine gains something a shared stack needs (an xTalk Suite library,
say) before a release carries it, OXT-Beyond's "Web pre-release"
workflow publishes main's Stack Player and Linux engine under such a tag,
with a `SHA256SUMS` of its own. Pin it the same way. Its file names carry
the version main had then, so the tag, not the names, says which build
it is.

## The list of xTalk Suite handlers

`tools/suite-handlers.json` tells the checker which handlers belong to
which xTalk Suite library, so that it can label the ones that use the
internet and refuse the ones with no web version. It is made from a
checkout of the suite:

```sh
python3 tools/make_suite_handlers.py <path to xtalk-suite>
```

Run it again when a library gains handlers or the web engine gains a
library.
