#!/usr/bin/env python3
"""Checks one shared stack, or one shared script library: its listing,
its files and its scripts.

    python3 tools/scan.py stacks/<name>/<version> --engine <OXT-Beyond engine>
        [--json report.json] [--markdown report.md] [--no-network]
    python3 tools/scan.py libraries/<name>/<version> ...

A library is checked as a stack is, and then as a library (see "a
library" below): the folder it is in says which of the two it is.

A shared stack is a folder, stacks/<name>/<version>/, that holds

    listing.json     what the gallery says about it (tools/listing.py)
    the stack        the file listing.json calls "main"
    anything else it needs: pictures, sounds, text, more stacks

The check answers one question: may this be offered to strangers with a
Play button? It looks at three things, in this order, and stops trusting
the folder as soon as one fails:

  1. the listing and the files (names, sizes, kinds: no programs, no
     archives, nothing hidden);
  2. each stack file, read by the OXT-Beyond engine itself with
     tools/scan-dump.livecodescript, which never opens the stack and has
     given up every permission before it looks (see that file). A stack
     with a password, with native externals, or that the engine cannot
     read is refused;
  3. every script, with tools/lcscan.py: what it reaches (the badges), and
     what a shared stack may not do at all.

Exit code 0: the stack can be shared. 1: it is refused; the report says
why in plain words. 2: the check itself could not run (no engine, a
crash): never a pass.

Everything in the folder is somebody else's data. Nothing in it is ever
run, imported or unpacked by this script.
"""
import argparse
import base64
import binascii
import collections
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import lcscan  # noqa: E402
import listing as listing_rules  # noqa: E402

DUMP_SCRIPT = os.path.join(HERE, 'scan-dump.livecodescript')

# ---------------------------------------------------------------- limits

MAX_FILES = 200
MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_DEPTH = 4
ENGINE_SECONDS = 120
MAX_DUMP_BYTES = 64 * 1024 * 1024

STACK_EXTENSIONS = ('.oxtstack', '.livecode', '.rev', '.livecodescript')
# What a stack may bring with it: things to show, play and read. No
# programs, no libraries, no archives (an archive hides its files from
# this check).
DATA_EXTENSIONS = (
    '.png', '.jpg', '.jpeg', '.gif', '.bmp', '.svg', '.webp', '.ico',
    '.wav', '.mp3', '.ogg', '.oga', '.aif', '.aiff', '.au', '.m4a', '.mid', '.midi',
    '.mp4', '.webm', '.mov', '.m4v',
    '.txt', '.md', '.json', '.csv', '.tsv', '.xml', '.html', '.htm', '.css', '.ini',
    '.ttf', '.otf', '.woff', '.woff2',
    '.sqlite', '.sqlite3', '.db',
)
# The first bytes of the files that are programs whatever they are called
PROGRAM_MAGIC = (
    (b'MZ', 'a Windows program or library'),
    (b'\x7fELF', 'a Linux program or library'),
    (b'\xfe\xed\xfa\xce', 'a Mac program or library'),
    (b'\xfe\xed\xfa\xcf', 'a Mac program or library'),
    (b'\xce\xfa\xed\xfe', 'a Mac program or library'),
    (b'\xcf\xfa\xed\xfe', 'a Mac program or library'),
    (b'\xca\xfe\xba\xbe', 'a Mac program or a Java class'),
    (b'PK\x03\x04', 'a zip archive'),
    (b'PK\x05\x06', 'a zip archive'),
    (b'\x1f\x8b', 'a compressed archive'),
    (b'7z\xbc\xaf\x27\x1c', 'a 7-Zip archive'),
    (b'Rar!', 'a RAR archive'),
    (b'\x00asm', 'a WebAssembly program'),
    (b'LCMD', 'a compiled extension'),
    (b'dex\n', 'an Android program'),
)

# What the reader of a refusal is told to do about it
ADVICE = {
    'listing': 'Fix listing.json and share the stack again.',
    'files': 'Take the file out, or save it in one of the kinds a stack may bring.',
    'password': 'Remove the password (in the stack\'s Inspector), save the stack and share it again. '
                'Everyone must be able to read a shared stack\'s scripts.',
    'externals': 'A shared stack cannot load native code. The libraries that come with OXT-Beyond\'s '
                 'web engine can be used without it.',
    'unreadable': 'Open the stack in OXT-Beyond, save it again and share that file.',
    'stackfiles': 'Keep the other stacks in the same folder and name them without a path.',
    'process': 'Shared stacks play in a web browser, where no other program can be run. '
               'Take that part out, or keep this stack for the desktop.',
    'other-language': 'In a web browser the only other language is "JavaScript".',
    'registry': 'There is no registry in a web browser.',
    'remote-code': 'Put the stack or the script in the folder you share, so that it can be checked '
                   'with the rest.',
    'suite-not-on-web': 'The web engine has SodiumXT, CoinXT, Box2Dxt, box3dxt and DataChannelXT, '
                        'and the NostrXT relay library. ENetXT, TorrentXT and OnionXT need the desktop.',
}

# The same for a library, where it differs
LIBRARY_ADVICE = {
    'listing': 'Fix listing.json and share the library again.',
    'files': 'Take the file out. A library brings its one script file, and text or pictures '
             'that explain it.',
    'password': 'A library is a script-only stack, a text file. Save it as one and share that.',
    'externals': 'A shared library cannot load native code.',
    'unreadable': 'A library is a text file that starts with the line  script "<its name>"  '
                  'and holds its handlers below it.',
    'process': 'A shared library cannot start other programs.',
    'library': 'See the README, "Share a script library", for what a library looks like.',
    'library-name': 'Change the name on the first line of the file,  script "<name>" . A good '
                    'name has your own name or your project\'s in it, such as "anna.easing".',
    'name-taken': 'Two libraries cannot answer to the same name: whichever loaded last would '
                  'win. Give your handlers a short prefix of their own, such as "ezMove".',
}

Problem = collections.namedtuple('Problem', 'rule text where line code')


class ScanError(Exception):
    """The check could not run. Never a pass."""


# ---------------------------------------------------------------- the files

_NAME_OK = re.compile(r'^[A-Za-z0-9][A-Za-z0-9 ._()\'&,+!-]*$')


def check_files(folder):
    """([{path, size, sha256, kind}], [Problem]) for every file under
    the folder. kind is "listing", "stack" or "data"."""
    files, problems = [], []
    total = 0
    real_root = os.path.realpath(folder)
    for root, dirs, names in os.walk(folder, followlinks=False):
        dirs.sort()
        rel_root = os.path.relpath(root, folder)
        depth = 0 if rel_root == '.' else rel_root.count(os.sep) + 1
        if depth > MAX_DEPTH:
            problems.append(Problem('files', 'Folders are nested too deeply (at most %d).' % MAX_DEPTH,
                                    rel_root.replace(os.sep, '/'), 0, ''))
            dirs[:] = []
            continue
        for d in list(dirs):
            full = os.path.join(root, d)
            if os.path.islink(full) or not _NAME_OK.match(d):
                problems.append(Problem('files', 'A folder has a name that cannot be shared, or is a link.',
                                        os.path.relpath(full, folder).replace(os.sep, '/'), 0, ''))
                dirs.remove(d)
        for name in sorted(names):
            full = os.path.join(root, name)
            rel = os.path.relpath(full, folder).replace(os.sep, '/')
            if os.path.islink(full) or not os.path.isfile(full) \
                    or not os.path.realpath(full).startswith(real_root + os.sep):
                problems.append(Problem('files', 'This is a link or a special file, not a file.', rel, 0, ''))
                continue
            if not _NAME_OK.match(name):
                problems.append(Problem('files', 'The file\'s name has characters that cannot be shared '
                                        '(use letters, digits, spaces and . _ - only, and start with a '
                                        'letter or a digit).', rel, 0, ''))
                continue
            size = os.path.getsize(full)
            total += size
            if len(files) >= MAX_FILES:
                problems.append(Problem('files', 'There are more than %d files.' % MAX_FILES, rel, 0, ''))
                return files, problems
            if size > MAX_FILE_BYTES:
                problems.append(Problem('files', 'The file is bigger than %d MB.'
                                        % (MAX_FILE_BYTES // 1048576), rel, 0, ''))
                continue
            ext = os.path.splitext(name)[1].lower()
            if rel == 'listing.json':
                kind = 'listing'
            elif ext in STACK_EXTENSIONS:
                kind = 'stack'
            elif ext in DATA_EXTENSIONS:
                kind = 'data'
            else:
                problems.append(Problem('files', 'Files of this kind ("%s") cannot come with a shared stack.'
                                        % (ext or 'no extension'), rel, 0, ''))
                continue
            digest = hashlib.sha256()
            with open(full, 'rb') as f:
                head = f.read(16)
                digest.update(head)
                for block in iter(lambda: f.read(1 << 20), b''):
                    digest.update(block)
            what = _program(head)
            if what:
                problems.append(Problem('files', 'The file is %s, whatever its name says.' % what, rel, 0, ''))
                continue
            files.append({'path': rel, 'size': size, 'sha256': digest.hexdigest(), 'kind': kind})
    if total > MAX_TOTAL_BYTES:
        problems.append(Problem('files', 'The files are bigger than %d MB together.'
                                % (MAX_TOTAL_BYTES // 1048576), '', 0, ''))
    return files, problems


def _program(head):
    for magic, what in PROGRAM_MAGIC:
        if head.startswith(magic):
            return what
    return None


# ---------------------------------------------------------------- the engine's dump

def _unb64(text):
    try:
        return base64.b64decode(text.encode('ascii'), validate=True).decode('utf-8')
    except (binascii.Error, UnicodeError, ValueError):
        raise ValueError('a field of the dump is not base64 of UTF-8')


def parse_dump(text):
    """The stacks of a dump: [{role, name, externals, stackFiles,
    password, scriptOnly, objects: [{type, id, name, path, readable,
    script, kind, behavior, fileName}]}]. Raises ValueError for anything that is not a
    whole, well-formed dump: a dump is believed entirely or not at all."""
    lines = text.split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    if not lines or lines[0] != 'OXTSCAN 1':
        raise ValueError('the dump does not start as one')
    if len(lines) < 2 or not lines[-1].startswith('END '):
        raise ValueError('the dump has no end: the engine stopped before it had read the stack')
    stacks = []
    objects = 0
    for line in lines[1:-1]:
        f = line.split('\t')
        if f[0] == 'stack' and len(f) == 7:
            if f[1] not in ('main', 'sub') or f[5] not in ('0', '1') or f[6] not in ('0', '1'):
                raise ValueError('a stack line of the dump is malformed')
            stacks.append({'role': f[1], 'name': _unb64(f[2]), 'externals': _unb64(f[3]),
                           'stackFiles': _unb64(f[4]), 'password': f[5] == '1',
                           'scriptOnly': f[6] == '1', 'objects': []})
        elif f[0] == 'object' and len(f) == 10 and stacks:
            if not re.match(r'^[a-zA-Z]+$', f[1]) or not f[2].isdigit() or f[5] not in ('0', '1'):
                raise ValueError('an object line of the dump is malformed')
            stacks[-1]['objects'].append({
                'type': f[1], 'id': int(f[2]), 'name': _unb64(f[3]), 'path': _unb64(f[4]),
                'readable': f[5] == '1', 'script': _unb64(f[6]), 'kind': _unb64(f[7]),
                'behavior': _unb64(f[8]), 'fileName': _unb64(f[9])})
            # (a script the engine will not give is a password's doing)
            if f[5] == '0':
                stacks[-1]['password'] = True
            objects += 1
        else:
            raise ValueError('the dump has a line that is not part of one')
    if lines[-1] != 'END %d %d' % (len(stacks), objects):
        raise ValueError('the dump\'s end does not match what came before it')
    if not stacks or stacks[0]['role'] != 'main':
        raise ValueError('the dump has no main stack')
    return stacks


# The user "nobody": it owns nothing and may do nothing.
NOBODY = 65534


def _isolation(no_network):
    """The command that starts the engine locked away, where this machine
    can do that (Linux, as root or with sudo): in a network of its own
    with nothing in it, unable to see other programs, as the user
    "nobody", and unable ever to become anyone else (so no sudo). Reading
    a stack runs none of its script, but the reader is a big C++ program
    and the file comes from a stranger: if a crafted file ever took the
    reader over, this is all it would have."""
    if not no_network:
        return []
    unshare, setpriv = shutil.which('unshare'), shutil.which('setpriv')
    if not unshare or not setpriv:
        raise ScanError('--no-network needs the unshare and setpriv programs (Linux)')
    command = []
    if os.geteuid() != 0:
        sudo = shutil.which('sudo')
        if not sudo:
            raise ScanError('--no-network needs sudo when not run as root')
        command = [sudo, '-n']
    return command + [unshare, '--net', '--ipc', '--pid', '--fork', '--kill-child', '--',
                      setpriv, '--reuid=%d' % NOBODY, '--regid=%d' % NOBODY, '--clear-groups',
                      '--no-new-privs', '--inh-caps=-all', '--bounding-set=-all', '--']


def dump_stack(engine, stack_path, no_network=False):
    """parse_dump() of what the engine reads from a stack file. Raises
    ValueError when the engine cannot read it as a stack, ScanError when
    the engine could not be run."""
    if not engine or not os.path.isfile(engine):
        raise ScanError('there is no engine at %r' % (engine,))
    work = os.path.realpath(tempfile.mkdtemp(prefix='oxt-scan-'))
    try:
        os.chmod(work, 0o755)
        # A copy under a plain name: the engine is never given a name
        # that somebody else chose
        ext = os.path.splitext(stack_path)[1].lower()
        copy = os.path.join(work, 'stack' + (ext if ext in STACK_EXTENSIONS else '.oxtstack'))
        shutil.copyfile(stack_path, copy)
        os.chmod(copy, 0o644)
        # The dump script under a name nobody can know beforehand: the
        # engine keeps one stack of a name, so a stack that called itself
        # what the dump script is called would be confused with it
        dump = os.path.join(work, 'dump.livecodescript')
        with open(DUMP_SCRIPT, encoding='utf-8') as f:
            text = f.read()
        first, rest = text.split('\n', 1)
        if first != 'script "oxt_community_scan_dump"':
            raise ScanError('tools/scan-dump.livecodescript does not start as expected')
        with open(dump, 'w', encoding='utf-8', newline='\n') as f:
            f.write('script "oxt_scan_%s"\n%s' % (binascii.hexlify(os.urandom(16)).decode('ascii'), rest))
        os.chmod(dump, 0o644)
        command = _isolation(no_network) + [
            '/usr/bin/env', '-i', 'OXT_SCAN_FILE=' + copy, 'HOME=' + work, 'TMPDIR=' + work,
            'PATH=/usr/bin:/bin', 'LANG=C.UTF-8', os.path.abspath(engine), '-ui', dump]
        try:
            done = subprocess.run(command, cwd=work, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=ENGINE_SECONDS)
        except subprocess.TimeoutExpired:
            raise ValueError('the engine took more than %d seconds to read the stack' % ENGINE_SECONDS)
        except OSError as e:
            raise ScanError('the engine could not be started: %s' % e)
        if no_network and done.returncode in (125, 126, 127) and not done.stdout:
            # (env's own words for "could not start it": as "nobody" the
            # engine must be in a folder that everyone can read)
            raise ScanError('the engine could not be started as the user "nobody": %s'
                            % done.stderr[:300].decode('utf-8', 'replace').strip())
        if done.returncode == 2 or b'FATAL' in done.stdout[:4096]:
            raise ScanError('the engine could not run the dump: %s'
                            % done.stdout[:300].decode('utf-8', 'replace').strip())
        if len(done.stdout) > MAX_DUMP_BYTES:
            raise ValueError('the stack\'s scripts are too big to check')
        if done.returncode != 0:
            raise ValueError('the engine could not read the file as a stack')
        try:
            text = done.stdout.decode('ascii')
        except UnicodeError:
            raise ValueError('the dump is not plain text')
        return parse_dump(text)
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------- one folder

def object_label(obj):
    if obj['type'] == 'stack':
        return 'the stack script'
    name = obj['name']
    if name and name != '%s id %d' % (obj['type'], obj['id']):
        return '%s "%s" (%s)' % (obj['type'], name, obj['path'])
    return '%s id %d (%s)' % (obj['type'], obj['id'], obj['path'])


def _check_stack_files(stack, file_paths, where):
    """The stackFiles of a stack name files of the upload, without a
    path out of it."""
    problems = []
    for line in stack['stackFiles'].split('\n'):
        if not line.strip():
            continue
        path = line.split(',', 1)[1] if ',' in line else ''
        if not path or path.startswith('/') or '\\' in path or ':' in path \
                or '..' in path.split('/') or path not in file_paths:
            problems.append(Problem('stackfiles', 'The stack looks for another stack at "%s", which is '
                                    'not a file in the folder that was shared.' % path, where, 0, ''))
    return problems


# ---------------------------------------------------------------- a library

# A script library is one script-only stack: a text file whose first
# line is  script "<name>"  and whose handlers other stacks call once it
# is loaded. It is not played in a box as a stack is: it is installed
# into the IDE and built into people's own programs. So beyond what a
# stack is held to, a library must be plain text that anyone can read,
# may not take a name that is somebody else's, and has its reach into
# other stacks' messages pointed out to the person who reviews it.

_LIBRARY_NAME = re.compile(r'^[A-Za-z][A-Za-z0-9]*(?:[._-][A-Za-z0-9]+)*$')
# Names that are the IDE's, the engine's or the xTalk Suite's own
_RESERVED_NAME_STARTS = ('rev', 'com.livecode.', 'org.openxtalk.', 'community.openxtalk.')
_RESERVED_NAMES = ('home', 'ide', 'livecode', 'oxt', 'openxtalk', 'help', 'library', 'stack')
_HEADER = re.compile(r'^\ufeff?\s*script\s+"([^"\r\n]*)"', re.I)
# What the IDE sends a library when it loads it and when it lets it go
_LOAD_MESSAGES = ('extensioninitialize', 'extensionfinalize')

# Added by tools/build_site.py to a library that handles neither
# message itself, when it packs the library for the Extension Manager:
# the IDE loads a script library by sending it extensionInitialize, and
# one that does not answer would sit in memory with nobody able to call
# it. Put at the END, so that nothing above it moves. (The same lines
# the xTalk Suite and OXT-Beyond add to their own script libraries.)
LIBRARY_WRAPPER = '''
-- OXT Community Stacks: added when this library was packed as an
-- extension. The IDE sends extensionInitialize to a script library when
-- it loads it, and extensionFinalize when it lets it go.
on extensionInitialize
   if the target is not me then
      pass extensionInitialize
   end if

   if the long id of me is not among the lines of the backScripts then
      insert the script of me into back
   end if

   if the environment contains "development" then
      set the _ideoverride of me to true
   end if
end extensionInitialize

on extensionFinalize
   if the target is not me then
      pass extensionFinalize
   end if

   remove the script of me from back
end extensionFinalize
'''


def library_name_problem(name):
    """What is wrong with the name a library gives itself (the name of
    its script-only stack), or None."""
    if not 3 <= len(name) <= 60 or not _LIBRARY_NAME.match(name):
        return ('The library\'s name, "%s", must be 3 to 60 letters and digits, with single dots, '
                'hyphens or underscores between them and no spaces, such as "anna.easing".' % name[:80])
    if name.lower().startswith(_RESERVED_NAME_STARTS) or name.lower() in _RESERVED_NAMES:
        return ('The library\'s name, "%s", begins like the names of OXT-Beyond\'s own stacks and '
                'libraries, and could be taken for one of them.' % name)
    return None


def public_handlers(script):
    """[{kind, name, line, text}] of the handlers of a library that other
    scripts can call: every one that is not private."""
    lines = script.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    out = []
    for kind, name, private, line in lcscan.handlers(script):
        if not private:
            text = ' '.join(lines[line - 1].split()) if 0 < line <= len(lines) else name
            out.append({'kind': kind, 'name': name, 'line': line, 'text': text[:200]})
    return out


def library_names(libraries_dir, skip=None):
    """What the libraries already listed answer to: ({handler name, lower
    case: the library's folder name}, {stack name, lower case: folder
    name}), from the newest version of each. `skip` is the folder name of
    the library being checked. These are this repository's own files:
    each library's script is read as text and never run."""
    taken, names = {}, {}
    if not os.path.isdir(libraries_dir):
        return taken, names
    for slug in sorted(os.listdir(libraries_dir)):
        base = os.path.join(libraries_dir, slug)
        if slug == skip or not os.path.isdir(base) or os.path.islink(base):
            continue
        versions = [v for v in os.listdir(base) if listing_rules.VERSION.match(v)
                    and os.path.isdir(os.path.join(base, v))]
        if not versions:
            continue
        folder = os.path.join(base, max(versions, key=listing_rules.version_key))
        try:
            with open(os.path.join(folder, 'listing.json'), encoding='utf-8') as f:
                main = json.load(f).get('main')
            if not isinstance(main, str) or '/' in main or '\\' in main or main.startswith('.'):
                continue
            path = os.path.join(folder, main)
            if os.path.islink(path) or os.path.getsize(path) > MAX_FILE_BYTES:
                continue
            with open(path, encoding='utf-8', errors='replace') as f:
                text = f.read()
        except (OSError, ValueError, AttributeError):
            continue
        m = _HEADER.match(text)
        if m:
            names.setdefault(m.group(1).lower(), slug)
        for h in public_handlers(text):
            if h['name'].lower() not in _LOAD_MESSAGES:
                taken.setdefault(h['name'].lower(), slug)
    return taken, names


def _check_library(folder, listing, files, dumps, suite, taken, names):
    """(library record or None, [Problem], set of notes) for a folder
    that is shared as a library."""
    problems, notes = [], set()
    stack_files = [f['path'] for f in files if f['kind'] == 'stack']
    main = (listing or {}).get('main')
    if len(stack_files) > 1:
        problems.append(Problem('library', 'A library is one script file. The folder holds %d: %s.'
                                % (len(stack_files), ', '.join(stack_files[:5])), '', 0, ''))
    dump = dict(dumps).get(main)
    if not main or dump is None:
        return None, problems, notes
    stack = dump[0]
    scripted = [o for o in stack['objects'] if o['script'].strip()]
    if len(dump) > 1 or not stack['scriptOnly'] or [o['type'] for o in scripted] != ['stack']:
        problems.append(Problem('library', 'The library must be a script-only stack: a text file with '
                                'one script and no cards, buttons or substacks of its own.', main, 0, ''))
        return None, problems, notes
    with open(os.path.join(folder, main), 'rb') as f:
        data = f.read()
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        problems.append(Problem('library', 'The library\'s file is not UTF-8 text.', main, 0, ''))
        return None, problems, notes
    m = _HEADER.match(text)
    if not m or m.group(1) != stack['name']:
        problems.append(Problem('unreadable', 'The library\'s file does not start with the line  '
                                'script "<its name>" .', main, 0, ''))
        return None, problems, notes

    name = stack['name']
    wrong = library_name_problem(name)
    if wrong:
        problems.append(Problem('library-name', wrong, main, 1, ''))
    elif name.lower() in names:
        problems.append(Problem('library-name', 'The library\'s name, "%s", is the name of a library that '
                                'is already here ("%s").' % (name, names[name.lower()]), main, 1, ''))

    script = scripted[0]['script']
    handlers = public_handlers(script)
    loads = {h['name'].lower() for h in handlers} & set(_LOAD_MESSAGES)
    if len(loads) == 1:
        problems.append(Problem('library', 'The library handles only one of extensionInitialize and '
                                'extensionFinalize. Handle both, or neither: the gallery adds the usual '
                                'pair to a library that has neither.', main, 0, ''))
    offered, messages = [], []
    for h in handlers:
        low = h['name'].lower()
        if low in _LOAD_MESSAGES:
            continue
        # (the line in the file: the script starts below its first line)
        where_line = h['line'] + 1
        if low in lcscan.ENGINE_MESSAGES:
            messages.append(h['name'])
            continue
        offered.append(h)
        owner = suite.get(low)
        if owner:
            problems.append(Problem('name-taken', 'The library\'s handler "%s" has the name of a handler '
                                    'of the xTalk Suite (%s), which comes with OXT-Beyond.'
                                    % (h['name'], owner[0]), main, where_line, h['text']))
        elif low in taken:
            problems.append(Problem('name-taken', 'The library\'s handler "%s" has the name of a handler '
                                    'of a library that is already here ("%s").'
                                    % (h['name'], taken[low]), main, where_line, h['text']))
    if not [h for h in offered if h['kind'] in ('command', 'function')]:
        problems.append(Problem('library', 'The library has no command or function that other scripts '
                                'can call. (A private handler can only be called from inside it.)',
                                main, 0, ''))
    if messages:
        notes.add('catches-messages')
    if lcscan.message_path_lines(script):
        notes.add('message-path')
    record = {'name': name, 'file': main, 'handlers': offered, 'messages': sorted(set(messages), key=str.lower),
              'wrapped': not loads}
    return record, problems, notes


def library_package(folder, report):
    """The files of a checked library as the Extension Manager takes
    them, [(path in the package, bytes)], manifest.xml first: the
    manifest, the library (with LIBRARY_WRAPPER added when it handles
    neither of the IDE's two messages) and whatever text and pictures
    came with it. `report` is scan_folder()'s for the folder, and must
    be one that can be shared."""
    library, listing = report['library'], report['listing']

    def x(text):
        return (str(text).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                .replace('"', '&quot;'))

    manifest = '\n'.join([
        '<package version="0.0">',
        '  <name>%s</name>' % x(library['name']),
        '  <title>%s</title>' % x(listing['title']),
        '  <author>%s</author>' % x(listing['author']),
        '  <version>%s</version>' % x(report['version']),
        '  <type>library</type>',
        '  <license>community</license>',
        '</package>']) + '\n'
    out = [('manifest.xml', manifest.encode('utf-8'))]
    for f in sorted(report['files'], key=lambda f: f['path']):
        if f['kind'] == 'listing' or f['path'].lower() == 'manifest.xml':
            continue
        with open(os.path.join(folder, *f['path'].split('/')), 'rb') as src:
            data = src.read()
        if f['path'] == library['file'] and library['wrapped']:
            if not data.endswith(b'\n'):
                data += b'\n'
            data += LIBRARY_WRAPPER.encode('ascii')
        out.append((f['path'], data))
    return out


def scan_folder(folder, engine, no_network=False, suite=None, kind=None, taken=None, names=None):
    """The report of one stacks/<name>/<version> or libraries/<name>/
    <version> folder, a dict (see main() for how it is printed).
    report['ok'] says whether it can be shared. `kind` is "stack" or
    "library"; left out, the folder two above says which. `taken` and
    `names` are library_names() of the libraries already listed. Raises
    ScanError when the check could not run."""
    folder = os.path.abspath(folder)
    if suite is None:
        suite = lcscan.load_suite()
    version = os.path.basename(folder)
    slug = os.path.basename(os.path.dirname(folder))
    if kind is None:
        kind = listing_rules.KINDS.get(os.path.basename(os.path.dirname(os.path.dirname(folder))), 'stack')
    if kind not in listing_rules.FOLDERS:
        raise ScanError('%r is not a kind of thing that is shared here' % (kind,))
    report = {'format': 1, 'kind': kind, 'slug': slug, 'version': version, 'ok': False, 'listing': None,
              'files': [], 'stacks': [], 'uses': [], 'problems': [], 'notes': [], 'findings': []}
    problems = []
    sources = []          # (file, stack name, label, script): for the source page

    for text in listing_rules.check_path(slug, version, kind):
        problems.append(Problem('listing', text, '', 0, ''))
    if not os.path.isdir(folder):
        raise ScanError('%s is not a folder' % folder)

    files, file_problems = check_files(folder)
    problems += file_problems
    report['files'] = files
    paths = {f['path'] for f in files}

    listing = None
    listing_path = os.path.join(folder, 'listing.json')
    if 'listing.json' not in paths:
        if not any(p.where == 'listing.json' for p in problems):
            problems.append(Problem('listing', 'There is no listing.json in the folder.', '', 0, ''))
    else:
        listing, texts = listing_rules.read(listing_path, paths, kind)
        problems += [Problem('listing', t, 'listing.json', 0, '') for t in texts]
    report['listing'] = listing

    findings = []
    notes = set()
    defined = set()
    dumps = []
    # The files are only read by the engine when they are files a stack
    # may bring: a refused file is not looked into
    if not file_problems:
        for f in files:
            if f['kind'] != 'stack':
                continue
            try:
                stacks = dump_stack(engine, os.path.join(folder, f['path']), no_network)
            except ValueError as e:
                problems.append(Problem('unreadable', 'The stack could not be read: %s.' % e, f['path'], 0, ''))
                continue
            dumps.append((f['path'], stacks))
            for stack in stacks:
                for obj in stack['objects']:
                    defined |= lcscan.defined_handlers(obj['script'])

    for path, stacks in dumps:
        entry = {'file': path, 'name': stacks[0]['name'],
                 'substacks': [s['name'] for s in stacks[1:]],
                 'objects': sum(len(s['objects']) for s in stacks),
                 'script_lines': 0, 'widgets': []}
        for stack in stacks:
            where = '%s, stack "%s"' % (path, stack['name'])
            if stack['password']:
                problems.append(Problem('password', 'The stack has a password, so its scripts cannot '
                                        'be read.', where, 0, ''))
            if stack['externals'].strip():
                problems.append(Problem('externals', 'The stack names native externals to load: %s.'
                                        % ', '.join(stack['externals'].split('\n')), where, 0, ''))
            problems += _check_stack_files(stack, paths, where)
            for obj in stack['objects']:
                label = '%s: %s' % (where, object_label(obj))
                if obj['type'] == 'widget':
                    if obj['kind'] not in entry['widgets']:
                        entry['widgets'].append(obj['kind'])
                    if obj['kind'] == 'com.livecode.widget.browser':
                        findings.append(lcscan.Finding('url', 'uses:internet', label, 0,
                                                       'a browser widget, which shows web pages'))
                if lcscan._scheme(obj['fileName']) == 'url':
                    findings.append(lcscan.Finding('url', 'uses:internet', label, 0,
                                                   'its file is at an address: ' + obj['fileName'][:120]))
                if not obj['script'].strip():
                    continue
                entry['script_lines'] += obj['script'].count('\n') + 1
                sources.append((path, stack['name'], object_label(obj), obj['script']))
                findings += lcscan.scan_script(obj['script'], label, suite, defined)
        report['stacks'].append(entry)

    if listing and listing.get('main') in paths and not any(
            p for p in problems if p.rule == 'unreadable' and p.where == listing['main']):
        if listing['main'] not in [d[0] for d in dumps] and not file_problems:
            problems.append(Problem('listing', '"main" must name the stack file to open.',
                                    'listing.json', 0, ''))

    if kind == 'library':
        report['library'] = None
        if not file_problems:
            report['library'], library_problems, library_notes = _check_library(
                folder, listing, files, dumps, suite, taken or {}, names or {})
            problems += library_problems
            notes |= library_notes

    uses, refused, note_rules = lcscan.summarize(findings)
    for f in findings:
        if f.level == 'refuse':
            problems.append(Problem(f.rule, 'The %s %s.' % (kind, lcscan.RULES[f.rule][1]),
                                    f.where, f.line, f.text))
    notes |= set(note_rules)
    if 'internet' in uses and notes & {'do', 'value', 'set-script', 'send'}:
        notes.add('could-fetch-script')

    report['uses'] = uses
    report['notes'] = sorted(notes)
    report['problems'] = [p._asdict() for p in problems]
    report['findings'] = [f._asdict() for f in findings]
    report['ok'] = not problems
    report['_sources'] = sources
    return report


# ---------------------------------------------------------------- printing

USES_WORDS = collections.OrderedDict([
    ('internet', 'Uses the internet'),
    ('files', 'Opens or saves files'),
    ('javascript', 'Runs JavaScript in its page'),
])
NOTE_WORDS = dict((rule, text[1]) for rule, text in lcscan.RULES.items() if text[0] == 'note')
NOTE_WORDS['could-fetch-script'] = ('uses the internet and builds script while it runs: it could '
                                    'fetch script that was not checked here')
NOTE_WORDS['catches-messages'] = ('handles messages that the engine sends, and will get them from every '
                                  'stack once it is loaded')
NOTE_WORDS['message-path'] = ('puts a script where it gets the messages of every stack (start using, '
                              'insert script)')


def md(text):
    """Text from a stack or a listing, made safe to put in Markdown that
    GitHub shows: nothing in it becomes a link, a picture, a mention or
    markup."""
    text = ' '.join(str(text).split())
    text = re.sub(r'([\\`*_{}\[\]()#+!|~-])', r'\\\1', text)
    text = text.replace('@', '@\u200b').replace('&', '&amp;')
    # (an entity, not a backslash: "\<img" still has "<img" in it for
    # anything that reads the comment as HTML)
    text = text.replace('<', '&lt;').replace('>', '&gt;')
    # (GitHub makes a link of a bare address too)
    return text.replace('://', ':\u200b//').replace('www.', 'www\u200b.')


def md_code(text):
    """The same for a line of script, shown as code."""
    text = ' '.join(str(text).split()).replace('`', "'")
    return '`%s`' % text if text else ''


def markdown(report):
    """The report as a person reads it (a pull request's summary).
    Everything that came out of the folder goes through md() or
    md_code()."""
    title = (report['listing'] or {}).get('title') or report['slug']
    kind = report.get('kind', 'stack')
    advice = dict(ADVICE, **LIBRARY_ADVICE) if kind == 'library' else ADVICE
    out = ['## %s, version %s' % (md(title), md(report['version'])), '']
    if report['ok']:
        out.append('**This %s can be shared.**' % kind)
    else:
        out.append('**This %s cannot be shared yet.** What stands in the way:' % kind)
        out.append('')
        seen = set()
        for p in report['problems']:
            line = '- ' + md(p['text'])
            if p['where']:
                line += ' In %s' % md(p['where']) + (', line %d' % p['line'] if p['line'] else '') + '.'
            if p['code']:
                line += '\n  ' + md_code(p['code'])
            out.append(line)
            if p['rule'] in advice and p['rule'] not in seen:
                seen.add(p['rule'])
                out.append('  ' + advice[p['rule']])
    out.append('')
    if report['uses']:
        out.append('What it uses: ' + ', '.join(USES_WORDS[u] for u in report['uses']) + '.')
    elif report['ok']:
        out.append('It uses nothing outside itself: no internet, no files.')
    library = report.get('library')
    if library:
        out += ['', 'The library calls itself %s and offers %d handler%s to other scripts: %s.' % (
            md_code(library['name']), len(library['handlers']), '' if len(library['handlers']) == 1 else 's',
            ', '.join(md_code(h['name']) for h in library['handlers'][:60])
            + (' and %d more' % (len(library['handlers']) - 60) if len(library['handlers']) > 60 else ''))]
        out += ['', 'A library is not played in a box as a stack is: people install it and build it into '
                'their own programs. Read all of it before it is listed.']
    if report['notes']:
        out += ['', 'For the reviewer:']
        out += ['- It ' + NOTE_WORDS[n] + '.' for n in report['notes']]
        if library and library['messages']:
            out.append('- The engine\'s messages it handles: %s.'
                       % ', '.join(md_code(m) for m in library['messages'][:40]))
    uses = [f for f in report['findings'] if f['level'] != 'refuse']
    if uses:
        out += ['', '<details><summary>The lines these come from (%d)</summary>' % len(uses), '']
        for f in uses[:200]:
            out.append('- %s: %s%s  %s' % (lcscan.RULES[f['rule']][1], md(f['where']),
                                          ', line %d' % f['line'] if f['line'] else '',
                                          md_code(f['text'])))
        if len(uses) > 200:
            out.append('- ... and %d more' % (len(uses) - 200))
        out += ['', '</details>']
    out.append('')
    for s in report['stacks']:
        out.append('- %s: stack "%s", %d objects, %d lines of script%s' % (
            md_code(s['file']), md(s['name']), s['objects'], s['script_lines'],
            ', substacks: ' + md(', '.join(s['substacks'])) if s['substacks'] else ''))
    return '\n'.join(out) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description='Check one shared stack or library.')
    parser.add_argument('folder', help='stacks/<name>/<version> or libraries/<name>/<version>')
    parser.add_argument('--engine', default=os.environ.get('OXT_ENGINE'),
                        help='the OXT-Beyond engine (LiveCode-Community); or set OXT_ENGINE')
    parser.add_argument('--json', help='write the whole report here')
    parser.add_argument('--markdown', help='write the report for people here')
    parser.add_argument('--no-network', action='store_true',
                        help='run the engine with no network (Linux; needs root or sudo)')
    args = parser.parse_args(argv)
    try:
        folder = os.path.abspath(args.folder)
        taken = names = None
        above = os.path.dirname(os.path.dirname(folder))
        if os.path.basename(above) == 'libraries':
            taken, names = library_names(above, skip=os.path.basename(os.path.dirname(folder)))
        report = scan_folder(folder, args.engine, args.no_network, taken=taken, names=names)
    except ScanError as e:
        print('The check could not run: %s' % e, file=sys.stderr)
        return 2
    report.pop('_sources')
    text = markdown(report)
    if args.json:
        with open(args.json, 'w', encoding='utf-8', newline='\n') as f:
            json.dump(report, f, indent=1, sort_keys=True)
            f.write('\n')
    if args.markdown:
        with open(args.markdown, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
    print(text)
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
