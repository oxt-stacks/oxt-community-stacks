#!/usr/bin/env python3
"""Builds the gallery: the web site that lists the shared stacks and plays
them.

    python3 tools/build_site.py --engine <engine> --player <StackPlayer folder>
        --out _site [--stacks stacks] [--no-network] [--strict]

Every stack is checked again here, with tools/scan.py, whatever checked
it before: the site only ever holds what this run of the scanner passed.
A stack the scanner refuses is left out and named in a warning (--strict
makes that an error instead).

What it writes:

    index.html                      the gallery
    s/<name>/index.html             a stack's own page
    s/<name>/play.html              the stack, playing
    s/<name>/<version>/<name>-<version>.zip    the stack and its files
    s/<name>/<version>/source.html  every script of it, to read
    s/<name>/<version>/screenshot.* its picture
    safety.html, share.html, 404.html
    index.json                      the same list, for the IDE's gallery
    index.sig                       index.json's signature, when the
                                    signing key is there (tools/sign.py)
    player/                         OXT-Beyond's Stack Player

and for the script libraries of libraries/ (below, "libraries"):

    libraries.html                  the list of them
    l/<name>/index.html             a library's own page
    l/<name>/<version>/<name>-<version>.lce    the library, as the
                                    Extension Manager installs it
    l/<name>/<version>/source.html  its script, to read
    libraries.json, libraries.sig   the list for the IDE, and its signature

How a stack is kept in its box. The stack plays in OXT-Beyond's Stack
Player, in a frame that the page marks sandbox="allow-scripts ..."
without allow-same-origin. The browser then treats the frame as a site of
its own that nobody else has ever visited: the stack's script, and any
JavaScript it runs, cannot read this site's storage or cookies, cannot
see another stack's saved data, cannot move the page it is on somewhere
else, and has no disk, no programs and no native code at all. The
player's own page is changed in one way (GUARD below): opened outside
such a frame it goes back to the gallery instead of playing, so there is
no address at which a stack runs as this site.

Nothing a stack brings is ever served as a page of this site: its files
go into one .zip, and the pictures are served under names made here.
Every word that came from a listing or a script is escaped.

A library has no box. It is made to be installed into the IDE and built
into people's own programs, where it runs as they do. Nothing of a
library ever runs on this site: its page shows what it offers and every
line of it, and hands out one package. What keeps people safe there is
that the script is plain text which the checker and a person have read,
that the package is made here from that text, and that its page says
plainly what installing means. A library may name a stack of the
gallery as its demo; that stack plays in the box as any other does.
"""
import argparse
import html
import io
import json
import os
import re
import shutil
import sys
import urllib.parse
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import listing as listing_rules  # noqa: E402
import scan  # noqa: E402
import sign  # noqa: E402

SITE_ASSETS = os.path.join(ROOT, 'site')
# Where this repository lives. GitHub's workflows say (GITHUB_REPOSITORY),
# so the pages keep pointing at it when it moves to another owner.
_REPOSITORY = os.environ.get('GITHUB_REPOSITORY', '')
if not re.match(r'^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9_.-]+$', _REPOSITORY):
    _REPOSITORY = 'oxt-stacks/oxt-community-stacks'
REPOSITORY_URL = 'https://github.com/' + _REPOSITORY
FORUM_URL = 'https://openxtalk.org/forum/'
OXT_URL = 'https://github.com/SethMorrowSoftware/OpenXTalk-Beyond'

# What the frame around a playing stack may do. Never allow-same-origin
# (see the top of this file), never allow-top-navigation.
SANDBOX = 'allow-scripts allow-downloads allow-popups allow-popups-to-escape-sandbox allow-pointer-lock'

# Put at the top of the player's own page: only ever play in a frame
# that the browser has boxed in (there the page's origin is "null").
GUARD = ('<script data-cfasync="false">if(self.origin!=="null"||self===top)'
         '{location.replace("../../")}</script>')

IMAGE_MAGIC = {
    '.png': (b'\x89PNG\r\n\x1a\n',),
    '.jpg': (b'\xff\xd8\xff',), '.jpeg': (b'\xff\xd8\xff',),
    '.gif': (b'GIF87a', b'GIF89a'),
    '.webp': (b'RIFF',),
}

BADGES = [
    # key, words, what it means in a sentence
    ('internet', 'Uses the internet', 'It gets or sends information over the internet.'),
    ('files', 'Opens or saves files', 'It keeps things in files. In your browser these stay inside the '
     'page and are gone when you close it.'),
    ('javascript', 'Runs JavaScript', 'It runs JavaScript in its own boxed-in page, for example to make sound.'),
]


class BuildError(Exception):
    pass


def esc(text):
    return html.escape(str(text), quote=True)


def url(path):
    return urllib.parse.quote(path, safe='/')


# ---------------------------------------------------------------- collecting

def find_versions(stacks_dir):
    """[(slug, version, folder)] of every stacks/<slug>/<version>, names
    as they are on disk (the scanner judges them)."""
    found = []
    if not os.path.isdir(stacks_dir):
        return found
    for slug in sorted(os.listdir(stacks_dir)):
        folder = os.path.join(stacks_dir, slug)
        if not os.path.isdir(folder) or os.path.islink(folder):
            continue
        for version in sorted(os.listdir(folder)):
            sub = os.path.join(folder, version)
            if os.path.isdir(sub) and not os.path.islink(sub):
                found.append((slug, version, sub))
    return found


def make_zip(folder, files):
    """The bytes of a .zip of a stack's files (not its listing), the same
    bytes for the same files every time."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as z:
        for f in sorted(files, key=lambda f: f['path']):
            if f['kind'] == 'listing':
                continue
            info = zipfile.ZipInfo(f['path'], date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            with open(os.path.join(folder, *f['path'].split('/')), 'rb') as src:
                z.writestr(info, src.read())
    return buffer.getvalue()


def collect(stacks_dir, engine, no_network=False, log=print):
    """(stacks, refused): stacks is {slug: [version records, newest
    first]}, each record the scanner's report plus 'folder'; refused is
    [(slug, version, report)]."""
    stacks, refused = {}, []
    for slug, version, folder in find_versions(stacks_dir):
        report = scan.scan_folder(folder, engine, no_network)
        if not report['ok']:
            refused.append((slug, version, report))
            log('::warning::%s %s is left out: %s' % (slug, version, report['problems'][0]['text']))
            continue
        report['folder'] = folder
        stacks.setdefault(slug, []).append(report)
    for versions in stacks.values():
        versions.sort(key=lambda r: listing_rules.version_key(r['version']), reverse=True)
    return stacks, refused


# ---------------------------------------------------------------- the family bar

# The same slim strip of links across the top of the three sites of the
# OpenXTalk family: OXT-Beyond's (its tools/pages/family.py, the master
# copy), the xTalk Suite's extensions page (its
# tools/build-extensions-site.py), and this gallery. FAMILY, FAMILY_CSS,
# MARK and family_bar are copies of the master's: change the three
# together. Its words are this file's own and its addresses fixed ones,
# escaped all the same; it is inline and draws its mark in SVG, so it
# asks no host for anything. The page a stack plays on has no bar: the
# stack has the window.

BEYOND = 'https://sethmorrowsoftware.github.io/OpenXTalk-Beyond/'
GALLERY = 'https://oxt-stacks.github.io/oxt-community-stacks/'
FORUM = 'https://openxtalk.org/forum/'
OPENXTALK = 'https://openxtalk.org/'

# (key, words, address): the bar's links, in their order, the same on
# every site. A site gives its own pages by key (relative, so a preview
# or a fork links itself) and leaves out a key it maps to None.
FAMILY = (
    ('beyond', 'OXT-Beyond', BEYOND),
    ('ide', 'Web IDE', BEYOND + 'ide/'),
    ('lessons', 'Lessons', BEYOND + 'lessons/'),
    ('extensions', 'Extensions', BEYOND + 'extensions/'),
    ('stacks', 'Community stacks', GALLERY),
    ('forum', 'Forum', FORUM),
)

# The bar's look, whole: every rule is under .oxt-family, and it resets
# what a page's own rules for nav, ul, li and a could give it, so it looks
# the same on every page of every site, light or dark.
FAMILY_CSS = (
    '.oxt-family{position:relative;display:flex;align-items:center;gap:0 10px;margin:0;'
    'padding:0 max(16px,env(safe-area-inset-right)) 0 max(16px,env(safe-area-inset-left));'
    'min-height:40px;background:#0a0f1a;border-bottom:1px solid rgba(255,255,255,.09);color:#c3cddc;'
    'font:500 13.5px/1.2 system-ui,-apple-system,"Segoe UI",Roboto,Ubuntu,sans-serif;letter-spacing:0;'
    'text-align:left;white-space:nowrap;overflow-x:auto;scrollbar-width:none;-webkit-font-smoothing:antialiased;'
    # a row longer than a phone scrolls sideways; its right edge fades to
    # say so (on a wide window the edge is empty, and the fade unseen)
    '-webkit-mask-image:linear-gradient(90deg,#000 calc(100% - 28px),transparent);'
    'mask-image:linear-gradient(90deg,#000 calc(100% - 28px),transparent)}'
    '.oxt-family::-webkit-scrollbar{display:none}'
    '.oxt-family a{color:inherit;font:inherit;letter-spacing:inherit;text-transform:none;text-decoration:none;'
    'background:none;box-shadow:none;border:0;border-radius:0;margin:0;min-height:0}'
    '.oxt-family .oxt-family-home{display:inline-flex;flex:none;align-items:center;gap:8px;padding:8px 6px 8px 0;'
    'color:#eef3ff;font-weight:700}'
    '.oxt-family .oxt-family-home svg{display:block;width:22px;height:22px;flex:none}'
    '.oxt-family ul{display:flex;flex:none;gap:0;margin:0;padding:0;list-style:none;background:none}'
    '.oxt-family li{margin:0;padding:0;list-style:none}'
    '.oxt-family li::before{content:none}'
    '.oxt-family li a{display:block;padding:12px 9px 10px;border-bottom:2px solid transparent}'
    '.oxt-family li:last-child a{margin-right:20px}'
    '.oxt-family li a:hover{color:#fff;border-bottom-color:rgba(255,179,71,.55)}'
    '.oxt-family li a[aria-current="page"]{color:#fff;border-bottom-color:#ff7a00}'
    '.oxt-family a:focus-visible{outline:2px solid #ffb347;outline-offset:-3px;border-radius:4px}'
    '@media (max-width:640px){.oxt-family .oxt-family-home span{display:none}}'
    '@media print{.oxt-family{display:none}}'
)

# The family's mark: a small tile in OXT-Beyond's fire, with its bolt
MARK = ('<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
        '<defs><linearGradient id="oxt-family-fire" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#ffc24d"/><stop offset=".55" stop-color="#ff7700"/>'
        '<stop offset="1" stop-color="#ff3d00"/></linearGradient></defs>'
        '<rect x="1" y="1" width="22" height="22" rx="6" fill="url(#oxt-family-fire)"/>'
        '<path d="M13.6 4.5 7.5 13.2h4.1l-1.3 6.3 6.2-8.8h-4.1z" fill="#1a1206"/></svg>')


SHOW_CURRENT = ('<script>(function (n) {'
                'var c = n && n.querySelector(\'[aria-current="page"]\');'
                'if (c && n.scrollWidth > n.clientWidth) n.scrollLeft = c.offsetLeft - 48;'
                '})(document.currentScript && document.currentScript.previousElementSibling);</script>')


def family_bar(current=None, links=None):
    """The bar, as HTML: its <style> and its <nav>. `current` is the key
    of the part the page is in, which the bar marks for the eye and for
    screen readers; `links` maps a key to this site's own address for it,
    or to None to leave it out."""
    links = links or {}
    items = []
    for key, words, href in FAMILY:
        href = links.get(key, href)
        if href is None:
            continue
        items.append('<li><a href="%s"%s>%s</a></li>' % (
            html.escape(href, quote=True), ' aria-current="page"' if key == current else '', html.escape(words)))
    # On a phone the row scrolls sideways: the one line of script after it
    # scrolls it so that the part the page is in shows, not only the first
    return ('<style>%s</style>\n<nav class="oxt-family" aria-label="OpenXTalk sites">'
            '<a class="oxt-family-home" href="%s">%s<span>OpenXTalk</span></a><ul>%s</ul></nav>\n%s\n'
            % (FAMILY_CSS, OPENXTALK, MARK, ''.join(items), SHOW_CURRENT))


# ---------------------------------------------------------------- pages

NAV = [('index.html', 'All stacks'), ('libraries.html', 'Libraries'), ('safety.html', 'Is it safe?'),
       ('share.html', 'Share a stack')]


def page(title, body, root='', description='', here=None):
    """One whole HTML page. `root` is the way from the page to the top
    of the site ('' or '../../'); `here` is the NAV page it is, if any,
    which the menu marks for the eye and for screen readers."""
    nav = '\n    '.join(
        '<a href="%s%s"%s%s>%s</a>' % (root, target, ' class="cta"' if target == 'share.html' else '',
                                     ' aria-current="page"' if target == here else '', words)
        for target, words in NAV)
    return '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>%(title)s</title>
%(description)s<link rel="stylesheet" href="%(root)sassets/site.css">
<link rel="icon" type="image/svg+xml" href="%(root)sassets/mark.svg">
</head>
<body>
<a class="skip" href="#main">Skip to the content</a>
%(family)s<header class="top">
  <a class="brand" href="%(root)sindex.html"><img src="%(root)sassets/mark.svg" alt="" width="36" height="36"><span>OXT Community Stacks</span></a>
  <nav aria-label="Site">
    %(nav)s
  </nav>
</header>
<main id="main" tabindex="-1">
%(body)s
</main>
<footer>
  <p>Stacks made with <a href="%(oxt)s">OXT-Beyond</a>, shared by the people who made them. Each stack belongs to its author, under the licence on its page.</p>
</footer>
</body>
</html>
''' % {'title': esc(title), 'body': body, 'root': root, 'oxt': OXT_URL, 'nav': nav,
       # this gallery is the family's Community stacks, linked as its own
       # front page
       'family': family_bar('stacks', {'stacks': root + 'index.html'}),
       'description': '<meta name="description" content="%s">\n' % esc(description) if description else ''}


def badges_html(uses):
    if not uses:
        return '<ul class="badges"><li class="badge quiet">Keeps to itself</li></ul>'
    return '<ul class="badges">%s</ul>' % ''.join(
        '<li class="badge">%s</li>' % esc(words) for key, words, _ in BADGES if key in uses)


def screenshot_html(record, root, cls='shot', alt=''):
    if record.get('screenshot_url'):
        return '<img class="%s" src="%s%s" alt="%s" loading="lazy">' % (cls, root, url(record['screenshot_url']),
                                                                        esc(alt))
    return '<div class="%s none" aria-hidden="true"><span>%s</span></div>' % (cls, esc(record['title'][:1].upper()))


def tag_url(root, tag):
    """The gallery, showing only the stacks with this tag."""
    return '%sindex.html?tag=%s' % (root, urllib.parse.quote(tag, safe=''))


def tags_html(root, tags):
    return '<ul class="tags">%s</ul>' % ''.join(
        '<li><a href="%s">%s</a></li>' % (esc(tag_url(root, t)), esc(t)) for t in tags)


def card_html(e, root=''):
    """A stack's card, as the gallery and "More stacks" show it."""
    page_url = '%ss/%s/index.html' % (root, e['slug'])
    return '''<li class="card" data-find="%(find)s" data-tags="%(tags)s">
  <a class="shotlink" href="%(page)s" tabindex="-1" aria-hidden="true">%(shot)s</a>
  <div class="body">
    <h3><a href="%(page)s">%(title)s</a></h3>
    <p class="by">by %(author)s</p>
    <p class="what">%(description)s</p>
    %(badges)s
  </div>
  <a class="play" href="%(root)ss/%(slug)s/play.html"><span aria-hidden="true">&#9654;</span> Play<span class="sr"> %(title)s</span></a>
</li>''' % {'slug': e['slug'], 'page': page_url, 'root': root, 'title': esc(e['title']),
            'author': esc(e['author']), 'description': esc(first_sentence(e['description'])),
            'badges': badges_html(e['uses']), 'shot': screenshot_html(e, root),
            'tags': esc('|'.join(e['tags'])),
            'find': esc(' '.join([e['title'], e['author'], e['description']] + e['tags']).lower())}


def common_tags(entries, least=2, most=8):
    """The tags worth a button on the gallery: those of at least `least`
    stacks, the most used first."""
    counts = {}
    for e in entries:
        for t in e['tags']:
            counts[t] = counts.get(t, 0) + 1
    ranked = sorted((t for t in counts if counts[t] >= least), key=lambda t: (-counts[t], t))
    return [(t, counts[t]) for t in ranked[:most]]


def gallery_page(entries):
    if not entries:
        body = '''<section class="hero"><h1>Stacks to play, made by people like you</h1>
<p>Nothing has been shared yet. <a href="share.html">Be the first to share a stack.</a></p></section>'''
        return page('OXT Community Stacks', body, here='index.html')
    chips = ''.join('<button type="button" class="chip" data-tag="%s" aria-pressed="false">%s <span class="n">%d</span></button>'
                    % (esc(t), esc(t), n) for t, n in common_tags(entries))
    body = '''<section class="hero">
  <p class="eyebrow">Made with OXT-Beyond</p>
  <h1>Stacks to play, made by people like you</h1>
  <p>Press <b>Play</b> and a stack opens right here in your browser. There is nothing to install, and a stack cannot reach your files or your computer. <a href="safety.html">How that works.</a></p>
</section>
<details class="newhere">
  <summary>New here? What is a stack?</summary>
  <ol class="three">
    <li><b>Play.</b> A stack is a small program: a game, a tool, a toy. Each one here plays in your browser, with nothing to install.</li>
    <li><b>Look inside.</b> Every stack's page lets you read all of its scripts, in plain English-like xTalk, to see how it works.</li>
    <li><b>Make your own.</b> Stacks are made with <a href="%(oxt)s">OXT-Beyond</a>, free and open source. Download one, open it there and change it, or <a href="share.html">share a stack you made</a>.</li>
  </ol>
</details>
<section class="filters" aria-label="Find a stack">
  <form class="find" role="search" action="index.html">
    <label for="find">Find a stack</label>
    <div class="field"><input id="find" name="q" type="search" autocomplete="off" placeholder="a name, a word, an author" aria-describedby="count"><button type="button" class="clear" id="clear" hidden aria-label="Clear the search">&times;</button></div>
  </form>
  <div class="chips" id="chips" role="group" aria-label="Show only" hidden><button type="button" class="chip" data-tag="" aria-pressed="true">All</button>%(chips)s</div>
  <p class="count" id="count" role="status" aria-live="polite">%(total)d stacks</p>
</section>
<h2 class="sr">The stacks</h2>
<ul class="cards" id="cards">
%(cards)s
</ul>
<div class="none" id="none" hidden>
  <p>No stack matches that.</p>
  <button type="button" class="ghost" id="all">Show all the stacks</button>
</div>
<script>
(function () {
  var box = document.getElementById('find'), clear = document.getElementById('clear');
  var chips = document.getElementById('chips'), count = document.getElementById('count');
  var none = document.getElementById('none'), total = %(total)d;
  var cards = [].slice.call(document.querySelectorAll('#cards .card'));
  var params = new URLSearchParams(location.search), tag = params.get('tag') || '';
  box.value = params.get('q') || '';
  chips.hidden = false;
  function extra() {
    // a tag with no button of its own (from a stack's page) gets one
    var known = [].some.call(chips.querySelectorAll('.chip'), function (b) { return b.getAttribute('data-tag') === tag; });
    if (tag && !known) {
      var b = document.createElement('button');
      b.type = 'button'; b.className = 'chip'; b.setAttribute('data-tag', tag); b.textContent = tag;
      chips.appendChild(b);
    }
  }
  function show() {
    var words = box.value.toLowerCase().split(/\\s+/).filter(Boolean), shown = 0;
    cards.forEach(function (c) {
      var text = c.getAttribute('data-find'), tags = c.getAttribute('data-tags').split('|');
      var on = words.every(function (w) { return text.indexOf(w) >= 0; }) && (!tag || tags.indexOf(tag) >= 0);
      c.hidden = !on;
      if (on) shown++;
    });
    [].forEach.call(chips.querySelectorAll('.chip'), function (b) {
      b.setAttribute('aria-pressed', String(b.getAttribute('data-tag') === tag));
    });
    clear.hidden = !box.value;
    none.hidden = shown > 0;
    count.textContent = shown === total ? total + ' stacks' : shown + ' of ' + total + ' stacks';
    var q = new URLSearchParams();
    if (box.value) q.set('q', box.value);
    if (tag) q.set('tag', tag);
    var s = q.toString();
    history.replaceState(null, '', s ? '?' + s : location.pathname);
  }
  extra();
  box.addEventListener('input', show);
  box.form.addEventListener('submit', function (ev) { ev.preventDefault(); show(); });
  box.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') { box.value = ''; show(); } });
  clear.addEventListener('click', function () { box.value = ''; show(); box.focus(); });
  chips.addEventListener('click', function (ev) {
    var b = ev.target.closest('.chip');
    if (!b) return;
    tag = b.getAttribute('data-tag');
    show();
  });
  document.getElementById('all').addEventListener('click', function () { box.value = ''; tag = ''; show(); box.focus(); });
  show();
})();
</script>''' % {'chips': chips, 'total': len(entries), 'oxt': OXT_URL, 'cards': '\n'.join(card_html(e) for e in entries)}
    return page('OXT Community Stacks', body,
                description='Stacks made with OXT-Beyond, to play in your browser.', here='index.html')


def first_sentence(text, limit=160):
    text = ' '.join(text.split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(' ', 1)[0]
    return cut.rstrip('.,;:') + '...'


def related(e, entries, most=3):
    """Other stacks to show at the foot of a stack's page: those that
    share the most tags with it first, then the rest by title."""
    others = [o for o in entries if o['slug'] != e['slug']]
    others.sort(key=lambda o: (-len(set(o['tags']) & set(e['tags'])), o['title'].lower(), o['slug']))
    return others[:most]


def stack_page(e, entries=()):
    root = '../../'
    meanings = ''.join('<li><b>%s.</b> %s</li>' % (esc(words), esc(meaning))
                       for key, words, meaning in BADGES if key in e['uses'])
    if not meanings:
        meanings = '<li><b>Keeps to itself.</b> It uses no internet and no files.</li>'
    older = ''
    if len(e['versions']) > 1:
        older = '<h2>Earlier versions</h2><ul class="plain">%s</ul>' % ''.join(
            '<li>Version %s: <a href="%s%s" download>download</a>, <a href="%s%s">scripts</a></li>'
            % (esc(v['version']), root, url(v['download']), root, url(v['source']))
            for v in e['versions'][1:])
    paragraphs = ''.join('<p>%s</p>' % esc(p) for p in e['description'].split('\n') if p.strip())
    report = '%s/issues/new?title=%s' % (REPOSITORY_URL, urllib.parse.quote('Report: ' + e['slug']))
    more = related(e, entries)
    more_html = ''
    if more:
        more_html = '''<section class="more" aria-labelledby="more">
  <h2 id="more">More stacks to play</h2>
  <ul class="cards small">%s</ul>
  <p><a href="%sindex.html">See all the stacks</a></p>
</section>''' % (''.join(card_html(o, root) for o in more), root)
    body = '''<nav class="crumbs" aria-label="Breadcrumb"><a href="%(root)sindex.html">All stacks</a> <span aria-hidden="true">/</span> <span aria-current="page">%(title)s</span></nav>
<article class="stack">
  <div class="lead">
    <a class="shotlink large" href="play.html" tabindex="-1" aria-hidden="true">%(shot)s</a>
    <div class="intro">
      <h1>%(title)s</h1>
      <p class="by">by %(author)s</p>
      %(badges)s
      <div class="actions">
        <a class="play big" href="play.html"><span aria-hidden="true">&#9654;</span> Play</a>
        <a class="ghost" href="%(root)s%(source)s">Read the scripts</a>
      </div>
      <p class="small">Plays right here in your browser, boxed in: it cannot reach your files or your computer. <a href="%(root)ssafety.html">How that works.</a></p>
    </div>
  </div>
  <div class="cols">
    <div class="main-col">
      <h2>About this stack</h2>
      <div class="about">%(paragraphs)s</div>
      <h2>What this stack uses</h2>
      <ul class="plain">%(meanings)s</ul>
      %(dev)s
      <h2>Open it in OXT-Beyond</h2>
      <ol class="three">
        <li><a class="ghost" href="%(root)s%(download)s" download>Download it (%(size)s)</a></li>
        <li>Unzip the file you get.</li>
        <li>Open <b>%(main)s</b> in <a href="%(oxt)s">OXT-Beyond</a> with <b>File &gt; Open Stack...</b> and change anything you like.</li>
      </ol>
      <p class="note">A stack you open on your own computer is no longer boxed in: it can do what any program can. Open stacks only from people you trust, and <a href="%(root)s%(source)s">read the scripts</a> first.</p>
      %(older)s
    </div>
    <aside class="facts" aria-label="Facts">
      <dl>
        <dt>Made by</dt><dd>%(author)s</dd>
        <dt>Version</dt><dd>%(version)s</dd>
        <dt>Licence</dt><dd>%(licence)s</dd>
        <dt>Download</dt><dd>%(size)s</dd>
        <dt>Scripts</dt><dd>%(lines)s lines</dd>
        <dt>Objects</dt><dd>%(objects)s</dd>
        %(tags)s
      </dl>
      <p class="small"><a href="%(report)s">Report a problem with this stack</a>, or tell us on the <a href="%(forum)s">OpenXTalk forum</a>.</p>
    </aside>
  </div>
</article>
%(more)s''' % {
        'shot': screenshot_html(e, root, 'shot large', 'A picture of %s' % e['title']), 'title': esc(e['title']),
        'author': esc(e['author']),
        'badges': badges_html(e['uses']), 'paragraphs': paragraphs, 'meanings': meanings, 'root': root,
        'source': url(e['source']), 'download': url(e['download']), 'lines': e['script_lines'],
        'size': human_size(e['size']), 'version': esc(e['version']),
        'licence': esc(listing_rules.LICENCES[e['licence']]),
        'tags': '<dt>Tags</dt><dd>%s</dd>' % tags_html(root, e['tags']) if e['tags'] else '',
        'report': esc(report), 'forum': FORUM_URL, 'older': older, 'more': more_html,
        'dev': dev_html(e, root), 'main': esc(e['main']), 'oxt': OXT_URL,
        'objects': sum(st['objects'] for st in e['_dev']['stacks'])}
    return page('%s - OXT Community Stacks' % e['title'], body, root, first_sentence(e['description']))


def source_anchor(index, line=None):
    """The id of a script (and one of its lines) on a source page."""
    return 's%d' % index if line is None else 's%d-L%d' % (index, line)


def script_index(sources):
    """{'file, stack "name": label': its place on the source page}, the
    way the scanner names where a finding is."""
    return {'%s, stack "%s": %s' % (f, st, label): i for i, (f, st, label, _) in enumerate(sources)}


def dev_html(e, root):
    """For people who write xTalk: what the stack is made of, and the
    very lines that make each label, each a link into its scripts."""
    dev = e['_dev']
    rows = []
    for st in dev['stacks']:
        extra = ''
        if st.get('substacks'):
            extra += ', with %d substack%s' % (len(st['substacks']), '' if len(st['substacks']) == 1 else 's')
        if st.get('widgets'):
            extra += '; widgets: %s' % ', '.join(st['widgets'])
        rows.append('<li><b>%s</b> (%s): %d objects, %d lines of script%s</li>'
                    % (esc(st['name']), esc(st['file']), st['objects'], st['script_lines'], esc(extra)))
    where = script_index(dev['sources'])
    found = []
    for key, words, _ in BADGES:
        lines = [f for f in dev['findings'] if f['rule'] == key or f['level'] == 'uses:' + key]
        if not lines:
            continue
        items = []
        for f in lines[:12]:
            i = where.get(f['where'])
            place = esc('%s, line %s' % (f['where'].split(': ', 1)[-1], f['line']))
            if i is not None and f.get('line'):
                place = '<a href="%s%s#%s">%s</a>' % (root, url(e['source']), source_anchor(i, f['line']), place)
            text = f['text']
            if i is not None and f.get('line'):
                # (the line as its author wrote it, not as the checker reads it)
                written = dev['sources'][i][3].split('\n')
                if 0 < f['line'] <= len(written) and written[f['line'] - 1].strip():
                    text = written[f['line'] - 1].strip()
            items.append('<li>%s<br><code>%s</code></li>' % (place, esc(text)))
        more = '<li class="small">and %d more</li>' % (len(lines) - 12) if len(lines) > 12 else ''
        found.append('<h4>%s</h4><ul class="lines">%s%s</ul>' % (esc(words), ''.join(items), more))
    notes = ''
    if dev['notes']:
        notes = '<h4>Script it builds as it runs</h4><ul class="plain">%s</ul>' % ''.join(
            '<li>%s</li>' % esc(n['text'] if isinstance(n, dict) else n) for n in dev['notes'][:12])
    return '''<details class="dev">
  <summary>For xTalk developers: how it is built</summary>
  <ul class="plain">%s</ul>
  %s%s
  <p class="small">The labels come from the checker, which reads every script without running any of it. <a href="%s%s">Read all the scripts</a>.</p>
</details>''' % (''.join(rows), ''.join(found) or '<p>The checker found nothing that reaches outside the stack.</p>',
                notes, root, url(e['source']))


def human_size(size):
    if size < 1024 * 1024:
        return '%d KB' % max(1, round(size / 1024))
    return '%.1f MB' % (size / 1048576)


def play_page(e):
    """The page a stack plays on: a slim bar, and the player in its box.
    A stack whose listing asks to fill the window ("fill": true) is
    scaled by the player to fill its frame (the player's ?fill=yes); any
    other plays at its own size, in the middle."""
    root = '../../'
    frame = '%splayer/play/index.html?stack=%s' % (root, urllib.parse.quote('../../' + e['download'], safe='/.'))
    if e.get('_dev', {}).get('fill'):
        frame += '&fill=yes'
    return '''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="referrer" content="no-referrer">
<title>%(title)s - OXT Community Stacks</title>
<link rel="stylesheet" href="%(root)sassets/site.css">
<link rel="icon" type="image/svg+xml" href="%(root)sassets/mark.svg">
</head>
<body class="playing">
<header class="bar">
  <a class="back" href="%(root)sindex.html">&larr; All stacks</a>
  <p class="now"><b>%(title)s</b> <span>by %(author)s</span></p>
  <a class="more" href="index.html">About this stack</a>
  <button id="full" type="button">Full screen</button>
</header>
<iframe id="stack" title="%(title)s" sandbox="%(sandbox)s" allow="fullscreen; autoplay; gamepad" allowfullscreen src="%(frame)s"></iframe>
<script>
(function () {
  var frame = document.getElementById('stack'), full = document.getElementById('full');
  if (!frame.requestFullscreen) { full.hidden = true; return; }
  full.addEventListener('click', function () { frame.requestFullscreen(); });
})();
</script>
</body>
</html>
''' % {'title': esc(e['title']), 'author': esc(e['author']), 'root': root, 'sandbox': SANDBOX, 'frame': esc(frame)}


def source_page(e, version, sources, kind='stack', added=''):
    """Every script of one version, with a number on each line. `added`
    is script the gallery itself adds to a library's package, shown
    apart from the author's."""
    root = '../../../'
    parts, toc = [], []
    for i, (file, stack, label, script) in enumerate(sources):
        lines = script.split('\n')
        numbered = ''.join('<span class="ln" id="%s">%s\n</span>' % (source_anchor(i, n), esc(text))
                           for n, text in enumerate(lines, 1))
        parts.append('<section id="%s"><h2>%s</h2><p class="small">%s, stack &quot;%s&quot;, %d lines</p><pre>%s</pre></section>'
                     % (source_anchor(i), esc(label), esc(file), esc(stack), len(lines), numbered))
        toc.append('<li><a href="#%s">%s</a> <span class="small">%s, %d lines</span></li>'
                   % (source_anchor(i), esc(label), esc(stack), len(lines)))
    if not parts:
        parts.append('<p>This stack has no scripts.</p>')
    if added:
        parts.append('<section id="added"><h2>Added by the gallery</h2><p class="small">The package ends with '
                     'these lines, which this library did not have: they let the Extension Manager load '
                     'the library and let it go. They are the same for every library.</p><pre>%s</pre></section>'
                     % esc(added.strip('\n')))
    contents = ''
    if len(toc) > 1:
        contents = '<details class="toc" open><summary>%d scripts in this stack</summary><ol>%s</ol></details>' % (
            len(toc), ''.join(toc))
    if kind == 'library':
        top, top_words = root + 'libraries.html', 'Libraries'
        words = ('This is the script exactly as its author shared it, and as it is in the package this '
                 'site hands out.')
        heading = 'The script of %s' % esc(e['title'])
    else:
        top, top_words = root + 'index.html', 'All stacks'
        words = 'These are the scripts exactly as they are in the stack that plays here.'
        heading = 'The scripts of %s' % esc(e['title'])
    body = '''<nav class="crumbs" aria-label="Breadcrumb"><a href="%s">%s</a> <span aria-hidden="true">/</span> <a href="../index.html">%s</a> <span aria-hidden="true">/</span> <span aria-current="page">Scripts</span></nav>
<article class="source">
<h1>%s</h1>
<p>Version %s. %s Each line has its number, and a link to a line keeps it.</p>
%s
%s
</article>''' % (top, top_words, esc(e['title']), heading, esc(version), words, contents, '\n'.join(parts))
    return page('Scripts of %s - OXT Community Stacks' % e['title'], body, root)


# ---------------------------------------------------------------- libraries

LIBRARY_BADGES = [
    ('internet', 'Uses the internet', 'It gets or sends information over the internet.'),
    ('files', 'Opens or saves files', 'It reads or writes files on the computer it runs on.'),
    ('javascript', 'Runs JavaScript', 'It runs JavaScript, which only works in a stack that plays on the web.'),
]


def collect_libraries(libraries_dir, engine, no_network=False, log=print):
    """(libraries, refused), as collect() gives them for stacks. A
    library whose names are another's (one that comes before it by its
    folder's name) is refused: two libraries cannot answer to one name."""
    libraries, refused = {}, []
    taken, names = {}, {}
    by_slug = {}
    for slug, version, folder in find_versions(libraries_dir):
        by_slug.setdefault(slug, []).append((version, folder))
    for slug in sorted(by_slug):
        for version, folder in by_slug[slug]:
            report = scan.scan_folder(folder, engine, no_network, kind='library', taken=taken, names=names)
            if not report['ok']:
                refused.append((slug, version, report))
                log('::warning::library %s %s is left out: %s' % (slug, version, report['problems'][0]['text']))
                continue
            report['folder'] = folder
            libraries.setdefault(slug, []).append(report)
        if slug in libraries:
            libraries[slug].sort(key=lambda r: listing_rules.version_key(r['version']), reverse=True)
            newest = libraries[slug][0]['library']
            names.setdefault(newest['name'].lower(), slug)
            for h in newest['handlers']:
                taken.setdefault(h['name'].lower(), slug)
    return libraries, refused


def make_package(entries):
    """The bytes of a library's .lce package from [(path, bytes)]: a
    .zip with manifest.xml first (the Extension Manager wants it there),
    the same bytes for the same files every time."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as z:
        for path, data in entries:
            info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    return buffer.getvalue()


def library_entry_of(slug, versions, stack_slugs, log=print):
    """A library's line of libraries.json, from its checked versions
    (newest first). `stack_slugs` are the stacks the gallery plays: a
    demo is only named when it is one of them."""
    latest = versions[0]
    listing, library = latest['listing'], latest['library']
    entry = {'slug': slug, 'title': listing['title'], 'author': listing['author'],
             'description': listing['description'], 'licence': listing['licence'],
             'tags': listing.get('tags', []), 'uses': latest['uses'], 'notes': latest['notes'],
             'version': latest['version'], 'name': library['name'], 'main': library['file'],
             'handlers': [{'kind': h['kind'], 'name': h['name'], 'text': h['text']} for h in library['handlers']],
             'messages': library['messages'],
             'page': 'l/%s/index.html' % slug,
             'script_lines': sum(st['script_lines'] for st in latest['stacks']),
             'versions': [],
             '_dev': {'findings': latest.get('findings', []), 'sources': latest.get('_sources', [])}}
    demo = listing.get('demo')
    if demo and demo in stack_slugs:
        entry['demo'] = demo
    elif demo:
        log('::warning::library %s: its demo "%s" is not a stack of the gallery, so the page has no Play'
            % (slug, demo))
    for report in versions:
        base = 'l/%s/%s/' % (slug, report['version'])
        entry['versions'].append({
            'version': report['version'], 'name': report['library']['name'], 'uses': report['uses'],
            'download': base + '%s-%s.lce' % (slug, report['version']),
            'source': base + 'source.html'})
    return entry


def library_card_html(e):
    return '''<li class="card lib" data-find="%(find)s" data-tags="%(tags)s">
  <div class="body">
    <h3><a href="%(page)s">%(title)s</a></h3>
    <p class="by">by %(author)s</p>
    <p class="what">%(description)s</p>
    %(badges)s
  </div>
</li>''' % {'page': e['page'], 'title': esc(e['title']), 'author': esc(e['author']),
            'description': esc(first_sentence(e['description'])), 'badges': badges_html(e['uses']),
            'tags': esc('|'.join(e['tags'])),
            'find': esc(' '.join([e['title'], e['author'], e['description'], e['name']] + e['tags']
                                 + [h['name'] for h in e['handlers']]).lower())}


def libraries_page(entries):
    intro = '''<section class="hero">
  <h1>Script libraries to build with</h1>
  <p>A library is a script that gives your own stacks new commands and functions. These were shared by the people who wrote them, for anyone to use.</p>
</section>
<p class="note"><b>A library is not a stack you play.</b> You install it into <a href="%(oxt)s">OXT-Beyond</a>, where it runs on your computer as any program does: there is no box around it. Every line of each library is here to read, a checker and a person have read it before it was listed, and its page says what it reaches for. Install libraries only from people you trust. <a href="safety.html#libraries">More about this.</a></p>''' % {'oxt': OXT_URL}
    if not entries:
        body = intro + '''
<p>No library has been shared yet. <a href="share-library.html">Be the first to share one.</a></p>'''
        return page('Libraries - OXT Community Stacks', body, here='libraries.html')
    body = intro + '''
<p class="count">%(total)d %(noun)s. <a href="share-library.html">Share one you wrote.</a></p>
<h2 class="sr">The libraries</h2>
<ul class="cards libs" id="libs">
%(cards)s
</ul>''' % {'total': len(entries), 'noun': 'library' if len(entries) == 1 else 'libraries',
           'cards': '\n'.join(library_card_html(e) for e in entries)}
    return page('Libraries - OXT Community Stacks', body,
                description='Script libraries for OXT-Beyond, shared by the people who wrote them.',
                here='libraries.html')


def library_page(e):
    root = '../../'
    meanings = ''.join('<li><b>%s.</b> %s</li>' % (esc(words), esc(meaning))
                       for key, words, meaning in LIBRARY_BADGES if key in e['uses'])
    if not meanings:
        meanings = '<li><b>Keeps to itself.</b> The checker found no internet and no files in it.</li>'
    if e['messages']:
        meanings += ('<li><b>Listens to every stack.</b> It handles messages that the engine sends (%s), and '
                     'gets them from every stack while it is loaded.</li>'
                     % ', '.join('<code>%s</code>' % esc(m) for m in e['messages'][:20]))
    notes = [n for n in e['notes'] if n in ('do', 'send', 'value', 'set-script', 'could-fetch-script')]
    if notes:
        meanings += ('<li><b>Builds script as it runs.</b> The checker cannot follow script that is put '
                     'together while a program runs, so its labels may say less than the library does.</li>')
    rows = ['<li><code>%s</code></li>' % esc(h['text'] or h['name']) for h in e['handlers']]
    older = ''
    if len(e['versions']) > 1:
        older = '<h2>Earlier versions</h2><ul class="plain">%s</ul>' % ''.join(
            '<li>Version %s: <a href="%s%s" download>download</a>, <a href="%s%s">script</a></li>'
            % (esc(v['version']), root, url(v['download']), root, url(v['source']))
            for v in e['versions'][1:])
    demo = ''
    if e.get('demo'):
        demo = ('<a class="play big" href="%ss/%s/play.html"><span aria-hidden="true">&#9654;</span> '
                'Try the demo</a>' % (root, e['demo']))
    paragraphs = ''.join('<p>%s</p>' % esc(p) for p in e['description'].split('\n') if p.strip())
    report = '%s/issues/new?title=%s' % (REPOSITORY_URL, urllib.parse.quote('Report: library ' + e['slug']))
    body = '''<nav class="crumbs" aria-label="Breadcrumb"><a href="%(root)slibraries.html">Libraries</a> <span aria-hidden="true">/</span> <span aria-current="page">%(title)s</span></nav>
<article class="stack library">
  <div class="intro">
    <h1>%(title)s</h1>
    <p class="by">a script library by %(author)s</p>
    %(badges)s
    <div class="actions">
      %(demo)s
      <a class="ghost" href="%(root)s%(source)s">Read the script</a>
    </div>
    %(demohint)s
  </div>
  <div class="cols">
    <div class="main-col">
      <h2>About this library</h2>
      <div class="about">%(paragraphs)s</div>
      <h2>What it gives your scripts</h2>
      <ul class="lines">%(rows)s</ul>
      <h2>What this library uses</h2>
      <ul class="plain">%(meanings)s</ul>
      <h2>Install it in OXT-Beyond</h2>
      <p class="note">An installed library runs on your computer as any program does, every time OXT-Beyond starts, and in every program you build with it. There is no box around it. <a href="%(root)s%(source)s">Read the script</a> first, and install libraries only from people you trust.</p>
      <p>In OXT-Beyond 0.2.8-dp.2 and later, the easy way is <b>File &gt; Community Stacks...</b>: choose <b>Libraries</b>, pick this one and press <b>Install</b>. It checks the file against this site's signed list before it installs it. Or by hand:</p>
      <ol class="three">
        <li><a class="ghost" href="%(root)s%(download)s" download>Download it (%(size)s)</a> Leave the file as it is: do not unzip it.</li>
        <li>In <a href="%(oxt)s">OXT-Beyond</a>, choose <b>Tools &gt; Extension Manager</b>, click <b>+</b> and choose the file.</li>
        <li>Call its handlers from your own scripts. To take it out again, select it in the Extension Manager and remove it.</li>
      </ol>
      <p class="small">The file is an extension package (<code>.lce</code>) that this site made from the script on this page. Any IDE built on LiveCode Community 9 installs it the same way. To put the library into a program you build, tick it under <b>Inclusions</b> in the Standalone Application Settings.</p>
      %(older)s
    </div>
    <aside class="facts" aria-label="Facts">
      <dl>
        <dt>Made by</dt><dd>%(author)s</dd>
        <dt>Version</dt><dd>%(version)s</dd>
        <dt>Licence</dt><dd>%(licence)s</dd>
        <dt>Its name in scripts</dt><dd><code>%(name)s</code></dd>
        <dt>Script</dt><dd>%(lines)s lines</dd>
        <dt>Download</dt><dd>%(size)s</dd>
        %(tags)s
      </dl>
      <p class="small">%(licencenote)s</p>
      <p class="small"><a href="%(report)s">Report a problem with this library</a>, or tell us on the <a href="%(forum)s">OpenXTalk forum</a>.</p>
    </aside>
  </div>
</article>''' % {
        'title': esc(e['title']), 'author': esc(e['author']), 'badges': badges_html(e['uses']),
        'paragraphs': paragraphs, 'meanings': meanings, 'root': root, 'source': url(e['source']),
        'download': url(e['download']), 'lines': e['script_lines'], 'size': human_size(e['size']),
        'version': esc(e['version']), 'licence': esc(listing_rules.LIBRARY_LICENCES[e['licence']]),
        'name': esc(e['name']), 'rows': ''.join(rows), 'older': older, 'demo': demo,
        'demohint': ('<p class="small">The demo is a stack that uses this library. It plays right here in your '
                     'browser, boxed in, so trying it puts nothing on your computer.</p>') if demo else '',
        'tags': '<dt>Tags</dt><dd>%s</dd>' % ''.join(esc(t) + ' ' for t in e['tags']).strip() if e['tags'] else '',
        'licencenote': ('A program built with OXT-Beyond holds its GPL 3 engine, so the program as a whole is '
                        'shared under GPL 3, whatever the licence of a library inside it.'),
        'report': esc(report), 'forum': FORUM_URL, 'oxt': OXT_URL}
    return page('%s - OXT Community Stacks' % e['title'], body, root, first_sentence(e['description']))


def build_libraries(libraries_dir, engine, out, stack_slugs, no_network=False, log=print):
    """Writes the libraries' pages and packages into `out`. Returns
    (the list as libraries.json holds it, refused)."""
    libraries, refused = collect_libraries(libraries_dir, engine, no_network, log)
    entries = []
    for slug in sorted(libraries):
        versions = libraries[slug]
        entry = library_entry_of(slug, versions, stack_slugs, log)
        for report, record in zip(versions, entry['versions']):
            files = scan.library_package(report['folder'], report)
            data = make_package(files)
            target = os.path.join(out, *record['download'].split('/'))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, 'wb') as f:
                f.write(data)
            record['size'] = len(data)
            record['sha256'] = scan.hashlib.sha256(data).hexdigest()
            # (what is in the package, by the bytes it holds)
            record['files'] = [{'path': path, 'size': len(content),
                                'sha256': scan.hashlib.sha256(content).hexdigest()} for path, content in files]
            write(os.path.join(out, *record['source'].split('/')),
                  source_page(entry, report['version'], report['_sources'], 'library',
                              scan.LIBRARY_WRAPPER if report['library']['wrapped'] else ''))
        latest = entry['versions'][0]
        entry.update({'download': latest['download'], 'source': latest['source'], 'size': latest['size'],
                      'sha256': latest['sha256']})
        entries.append(entry)
    entries.sort(key=lambda e: (e['title'].lower(), e['slug']))
    for entry in entries:
        write(os.path.join(out, 'l', entry['slug'], 'index.html'), library_page(entry))
    write(os.path.join(out, 'libraries.html'), libraries_page(entries))
    listed = []
    for e in entries:
        item = dict(e)
        item.pop('notes')
        item.pop('_dev')
        listed.append(item)
    return listed, refused


# ---------------------------------------------------------------- building

def entry_of(slug, versions):
    """A stack's line of the index, from its checked versions (newest
    first)."""
    latest = versions[0]
    listing = latest['listing']
    entry = {'slug': slug, 'title': listing['title'], 'author': listing['author'],
             'description': listing['description'], 'licence': listing['licence'],
             'tags': listing.get('tags', []), 'uses': latest['uses'], 'notes': latest['notes'],
             'version': latest['version'], 'main': listing['main'],
             'page': 's/%s/index.html' % slug, 'play': 's/%s/play.html' % slug,
             'script_lines': sum(s['script_lines'] for s in latest['stacks']),
             'versions': [],
             # (for the pages only, not the index the IDE reads)
             '_dev': {'stacks': latest['stacks'], 'findings': latest.get('findings', []),
                      'notes': latest['notes'], 'sources': latest.get('_sources', []),
                      'fill': listing.get('fill') is True}}
    for report in versions:
        base = 's/%s/%s/' % (slug, report['version'])
        entry['versions'].append({
            'version': report['version'], 'main': report['listing']['main'], 'uses': report['uses'],
            'download': base + '%s-%s.zip' % (slug, report['version']),
            'source': base + 'source.html',
            'files': [{'path': f['path'], 'size': f['size'], 'sha256': f['sha256']}
                      for f in report['files'] if f['kind'] != 'listing']})
    return entry


def copy_player(player, out):
    """OXT-Beyond's Stack Player, with only what playing one stack needs,
    and the guard at the top of its page."""
    play = os.path.join(player, 'play', 'index.html')
    if not os.path.isfile(play):
        raise BuildError('%s is not a Stack Player: it has no play/index.html' % player)
    dest = os.path.join(out, 'player')
    os.makedirs(os.path.join(dest, 'play'))
    for name in sorted(os.listdir(player)):
        full = os.path.join(player, name)
        # (the player's own home page and its samples stay behind: on
        # this site a stack is only ever played in its box)
        if name in ('index.html', 'samples', 'play') or os.path.islink(full):
            continue
        if os.path.isdir(full):
            shutil.copytree(full, os.path.join(dest, name))
        else:
            shutil.copyfile(full, os.path.join(dest, name))
    with open(play, encoding='utf-8') as f:
        text = f.read()
    if text.count('<head>') != 1:
        raise BuildError('the Stack Player\'s play page has changed: no single <head> to put the guard in')
    if 'function sameSite(' not in text:
        raise BuildError('the Stack Player\'s play page has changed: it no longer keeps to its own site')
    text = text.replace('<head>', '<head>\n' + GUARD, 1)
    with open(os.path.join(dest, 'play', 'index.html'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)


def static_page(name, root=''):
    here = name if name in dict(NAV) else None
    with open(os.path.join(SITE_ASSETS, name), encoding='utf-8') as f:
        text = f.read()
    title, body = text.split('\n', 1)
    return page(title.strip(), body.replace('@REPOSITORY@', REPOSITORY_URL).replace('@FORUM@', FORUM_URL)
                .replace('@OXT@', OXT_URL), root, here=here)


def build(stacks_dir, engine, player, out, no_network=False, strict=False, log=print, env=None,
          libraries_dir=None):
    """Builds the site into `out` (which must not exist or be empty).
    Returns (index, refused). The libraries are those of `libraries_dir`
    (left out: the folder "libraries" beside the stacks' folder)."""
    if libraries_dir is None:
        libraries_dir = os.path.join(os.path.dirname(os.path.abspath(stacks_dir)), 'libraries')
    if os.path.exists(out) and os.listdir(out):
        raise BuildError('%s is not empty' % out)
    os.makedirs(out, exist_ok=True)
    stacks, refused = collect(stacks_dir, engine, no_network, log)
    if strict and refused:
        raise BuildError('%d stack(s) were refused' % len(refused))

    entries = []
    for slug in sorted(stacks):
        versions = stacks[slug]
        entry = entry_of(slug, versions)
        for report, record in zip(versions, entry['versions']):
            data = make_zip(report['folder'], report['files'])
            target = os.path.join(out, *record['download'].split('/'))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, 'wb') as f:
                f.write(data)
            record['size'] = len(data)
            record['sha256'] = scan.hashlib.sha256(data).hexdigest()
            shot = report['listing'].get('screenshot')
            record['screenshot'] = None
            if shot:
                ext = os.path.splitext(shot)[1].lower()
                with open(os.path.join(report['folder'], *shot.split('/')), 'rb') as f:
                    picture = f.read()
                # (served under a name made here, and only when it starts
                # as a picture of its kind does)
                if any(picture.startswith(m) for m in IMAGE_MAGIC.get(ext, ())):
                    record['screenshot'] = 's/%s/%s/screenshot%s' % (slug, report['version'], ext)
                    with open(os.path.join(out, *record['screenshot'].split('/')), 'wb') as f:
                        f.write(picture)
                else:
                    log('::warning::%s %s: the screenshot is not a picture of its kind, left out'
                        % (slug, report['version']))
        latest = entry['versions'][0]
        entry.update({'download': latest['download'], 'source': latest['source'], 'size': latest['size'],
                      'sha256': latest['sha256'], 'screenshot': latest['screenshot']})
        entry['screenshot_url'] = entry['screenshot']
        entries.append(entry)
        for report, record in zip(versions, entry['versions']):
            write(os.path.join(out, *record['source'].split('/')),
                  source_page(entry, report['version'], report['_sources']))
        write(os.path.join(out, 's', slug, 'play.html'), play_page(entry))

    entries.sort(key=lambda e: (e['title'].lower(), e['slug']))
    for entry in entries:
        write(os.path.join(out, 's', entry['slug'], 'index.html'), stack_page(entry, entries))
    write(os.path.join(out, 'index.html'), gallery_page(entries))
    for name in ('safety.html', 'share.html', 'share-library.html', '404.html'):
        write(os.path.join(out, name), static_page(name))
    shutil.copytree(os.path.join(SITE_ASSETS, 'assets'), os.path.join(out, 'assets'))
    write(os.path.join(out, '.nojekyll'), '')
    copy_player(player, out)

    index = {'format': 1, 'name': 'OXT Community Stacks', 'stacks': []}
    for e in entries:
        item = dict(e)
        item.pop('screenshot_url')
        item.pop('notes')
        item.pop('_dev')
        index['stacks'].append(item)
    text = json.dumps(index, indent=1, sort_keys=True, ensure_ascii=True) + '\n'
    write(os.path.join(out, 'index.json'), text)

    # The libraries, in a list of their own: the list of stacks stays
    # what the IDEs already out there read
    listed, refused_libraries = build_libraries(libraries_dir, engine, out, {e['slug'] for e in entries},
                                                no_network, log)
    if strict and refused_libraries:
        raise BuildError('%d librar(ies) were refused' % len(refused_libraries))
    refused = refused + refused_libraries
    libraries = {'format': 1, 'name': 'OXT Community Libraries', 'libraries': listed}
    libraries_text = json.dumps(libraries, indent=1, sort_keys=True, ensure_ascii=True) + '\n'
    write(os.path.join(out, 'libraries.json'), libraries_text)

    seed = sign.secret_seed(env)
    public = sign.read_public()
    if seed is not None:
        if public is None or sign.ed25519.public_key(seed) != public:
            raise BuildError('the signing key is not the one signing-key.pub names')
        write(os.path.join(out, 'index.sig'), sign.sign_bytes(seed, text.encode('utf-8')) + '\n')
        write(os.path.join(out, 'libraries.sig'), sign.sign_bytes(seed, libraries_text.encode('utf-8')) + '\n')
        log('index.json and libraries.json are signed')
    else:
        # (the Publish workflow signs in a job of its own, which is the
        # only one that holds the key)
        log('index.json is not signed in this run: there is no signing key here (tools/sign.py)')
    return index, refused


def main(argv=None):
    parser = argparse.ArgumentParser(description='Build the gallery site.')
    parser.add_argument('--stacks', default=os.path.join(ROOT, 'stacks'))
    parser.add_argument('--engine', default=os.environ.get('OXT_ENGINE'), required=not os.environ.get('OXT_ENGINE'))
    parser.add_argument('--player', required=True, help='the unpacked StackPlayer folder (tools/fetch.py player)')
    parser.add_argument('--out', required=True)
    parser.add_argument('--no-network', action='store_true')
    parser.add_argument('--strict', action='store_true', help='fail when a stack is refused')
    args = parser.parse_args(argv)
    try:
        index, refused = build(args.stacks, args.engine, args.player, args.out, args.no_network, args.strict)
    except (BuildError, scan.ScanError, sign.KeyError_) as e:
        print('The site could not be built: %s' % e, file=sys.stderr)
        return 2
    with open(os.path.join(args.out, 'libraries.json'), encoding='utf-8') as f:
        libraries = json.load(f)['libraries']
    print('%d stack(s) and %d librar(ies) in the gallery, %d left out'
          % (len(index['stacks']), len(libraries), len(refused)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
