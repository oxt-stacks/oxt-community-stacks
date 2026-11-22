"""tools/scan.py without an engine: the files a stack may bring, and
reading the engine's dump."""
import base64
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import lcscan  # noqa: E402
import scan  # noqa: E402

README = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                      'README.md')


def b64(text):
    return base64.b64encode(text.encode('utf-8')).decode('ascii')


def stack_line(role='main', name='S', externals='', stack_files='', password='0', script_only='0'):
    return '\t'.join(['stack', role, b64(name), b64(externals), b64(stack_files), password, script_only])


def object_line(type_='button', id_='1004', name='Go', path='card 1', readable='1', script='', kind='',
                behavior='', file_name=''):
    return '\t'.join(['object', type_, id_, b64(name), b64(path), readable, b64(script), b64(kind),
                      b64(behavior), b64(file_name)])


def dump(*lines, end=None):
    stacks = sum(1 for line in lines if line.startswith('stack\t'))
    objects = sum(1 for line in lines if line.startswith('object\t'))
    return '\n'.join(['OXTSCAN 1'] + list(lines) + [end or 'END %d %d' % (stacks, objects)]) + '\n'


class ParseDump(unittest.TestCase):
    def test_good(self):
        stacks = scan.parse_dump(dump(stack_line(externals='a.dll\nb.so'),
                                      object_line('stack', '1002', 'S', 'stack', script='on x\nend x'),
                                      object_line(script='-- h\u00e9 \u2713'),
                                      stack_line('sub', 'T'), object_line('widget', kind='com.x.y')))
        self.assertEqual([s['name'] for s in stacks], ['S', 'T'])
        self.assertEqual(stacks[0]['externals'], 'a.dll\nb.so')
        self.assertEqual(stacks[0]['objects'][1]['script'], '-- h\u00e9 \u2713')
        self.assertEqual(stacks[1]['objects'][0]['kind'], 'com.x.y')
        self.assertFalse(stacks[0]['password'])

    def test_password_flag_and_unreadable_script(self):
        self.assertTrue(scan.parse_dump(dump(stack_line(password='1')))[0]['password'])
        self.assertTrue(scan.parse_dump(dump(stack_line(), object_line(readable='0')))[0]['password'])

    def bad(self, text):
        with self.assertRaises(ValueError):
            scan.parse_dump(text)

    def test_a_dump_is_believed_entirely_or_not_at_all(self):
        good = dump(stack_line(), object_line())
        self.bad('')
        self.bad(good.replace('OXTSCAN 1', 'OXTSCAN 2'))
        self.bad('noise\n' + good)
        # stopped before the end
        self.bad('\n'.join(good.split('\n')[:-2]) + '\n')
        # the counts on the end line are the engine's own
        self.bad(dump(stack_line(), object_line(), end='END 1 2'))
        self.bad(dump(stack_line(), object_line(), end='END 2 1'))
        # a line that is not part of a dump
        self.bad(dump(stack_line(), 'ERROR\tread'))
        self.bad(dump(stack_line(), object_line() + '\textra'))
        self.bad(dump(stack_line(), object_line()[:-1] + '\t!!!'))
        self.bad(dump(stack_line(role='other')))
        self.bad(dump(stack_line(password='2')))
        self.bad(dump(object_line()))
        self.bad(dump(stack_line('sub')))
        self.bad(dump(stack_line(), object_line(id_='x')))
        self.bad(dump(stack_line(), object_line(type_='but ton')))
        self.bad(dump())

    def test_a_script_cannot_forge_a_line(self):
        # whatever a script holds arrives as base64: tabs and line ends in
        # it do not become fields or lines
        script = 'x\nEND 1 1\nstack\tmain\t\t\t\t0\t0\n'
        stacks = scan.parse_dump(dump(stack_line(), object_line(script=script)))
        self.assertEqual(len(stacks), 1)
        self.assertEqual(stacks[0]['objects'][0]['script'], script)


class Files(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = self.tmp.name
        self.addCleanup(self.tmp.cleanup)

    def put(self, rel, data=b'x'):
        path = os.path.join(self.folder, *rel.split('/'))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        return path

    def check(self):
        files, problems = scan.check_files(self.folder)
        return {f['path']: f for f in files}, problems

    def test_kinds(self):
        self.put('listing.json', b'{}')
        self.put('My Stack.oxtstack', b'REVO7000')
        self.put('lib/extra.livecode', b'REVO7000')
        self.put('art/pic one.PNG', b'\x89PNG\r\n\x1a\n')
        self.put('notes.txt', b'hello')
        files, problems = self.check()
        self.assertEqual(problems, [])
        self.assertEqual({p: f['kind'] for p, f in files.items()},
                         {'listing.json': 'listing', 'My Stack.oxtstack': 'stack', 'lib/extra.livecode': 'stack',
                          'art/pic one.PNG': 'data', 'notes.txt': 'data'})
        self.assertEqual(files['notes.txt']['size'], 5)
        self.assertEqual(files['notes.txt']['sha256'],
                         '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824')

    def test_kinds_a_stack_may_not_bring(self):
        for name in ('run.exe', 'lib.dll', 'lib.so', 'lib.dylib', 'go.sh', 'go.bat', 'go.command', 'a.zip',
                     'ext.lcm', 'ext.lce', 'ext.lcb', 'page.js', 'x.py', 'x.jar', 'x.app', 'noextension',
                     'x.lnk', 'x.ps1', 'x.msi', 'x.scpt', 'x.wasm'):
            with self.subTest(name=name):
                path = self.put(name)
                files, problems = self.check()
                self.assertEqual(files, {})
                self.assertEqual(len(problems), 1)
                self.assertEqual(problems[0].rule, 'files')
                os.remove(path)

    def test_a_program_under_another_name(self):
        for magic in (b'MZ\x90\x00', b'\x7fELF\x02', b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'PK\x03\x04',
                      b'\x1f\x8b\x08', b'\x00asm\x01', b'Rar!\x1a'):
            for name in ('picture.png', 'Stack.oxtstack', 'data.db'):
                with self.subTest(magic=magic, name=name):
                    path = self.put(name, magic + b'\x00' * 64)
                    files, problems = self.check()
                    self.assertEqual(files, {})
                    self.assertIn('whatever its name says', problems[0].text)
                    os.remove(path)

    def test_names(self):
        for name in ('.hidden.txt', '-dash.txt', 'semi;colon.txt', 'back`tick.txt', 'dollar$.txt',
                     'new\nline.txt', 'uni\u202ecode.txt', 'a<b>.txt', 'quote".txt'):
            with self.subTest(name=name):
                path = self.put(name)
                files, problems = self.check()
                self.assertEqual(files, {})
                self.assertEqual(len(problems), 1)
                os.remove(path)

    def test_hidden_folder(self):
        self.put('.git/config.txt')
        files, problems = self.check()
        self.assertEqual(files, {})
        self.assertEqual(len(problems), 1)

    @unittest.skipIf(os.name == 'nt', 'needs symbolic links')
    def test_links_are_not_followed(self):
        outside = tempfile.NamedTemporaryFile(suffix='.txt', delete=False)
        outside.write(b'secret')
        outside.close()
        self.addCleanup(os.remove, outside.name)
        os.symlink(outside.name, os.path.join(self.folder, 'link.txt'))
        os.symlink(os.path.dirname(outside.name), os.path.join(self.folder, 'linkdir'))
        files, problems = self.check()
        self.assertEqual(files, {})
        self.assertEqual(len(problems), 2)

    def test_too_deep(self):
        self.put('a/b/c/d/e/deep.txt')
        files, problems = self.check()
        self.assertEqual(files, {})
        self.assertIn('nested too deeply', problems[0].text)

    def test_size_limits(self):
        old = scan.MAX_FILE_BYTES, scan.MAX_TOTAL_BYTES, scan.MAX_FILES
        self.addCleanup(lambda: setattr(scan, 'MAX_FILE_BYTES', old[0]))
        self.addCleanup(lambda: setattr(scan, 'MAX_TOTAL_BYTES', old[1]))
        self.addCleanup(lambda: setattr(scan, 'MAX_FILES', old[2]))
        scan.MAX_FILE_BYTES, scan.MAX_TOTAL_BYTES, scan.MAX_FILES = 10, 15, 3
        self.put('big.txt', b'x' * 11)
        self.assertIn('bigger than', self.check()[1][0].text)
        os.remove(os.path.join(self.folder, 'big.txt'))
        self.put('a.txt', b'x' * 8)
        self.put('b.txt', b'x' * 8)
        self.assertIn('together', self.check()[1][0].text)
        self.put('a.txt', b'x')
        self.put('b.txt', b'x')
        self.put('c.txt', b'x')
        self.assertEqual(self.check()[1], [])
        self.put('d.txt', b'x')
        self.assertIn('more than 3 files', self.check()[1][0].text)


class StackFiles(unittest.TestCase):
    def problems(self, text, paths=('Lib.oxtstack', 'sub/Other.livecode')):
        stack = {'stackFiles': text}
        return scan._check_stack_files(stack, set(paths), 'x')

    def test_a_file_of_the_folder(self):
        self.assertEqual(self.problems('Lib,Lib.oxtstack\nOther,sub/Other.livecode\n'), [])
        self.assertEqual(self.problems(''), [])

    def test_anything_else(self):
        for path in ('/etc/passwd', '../Lib.oxtstack', 'sub/../../x', 'C:\\x.livecode', 'C:/x.livecode',
                     'http://example.org/x.livecode', 'Missing.oxtstack', ''):
            with self.subTest(path=path):
                self.assertEqual(len(self.problems('Lib,' + path)), 1)
        self.assertEqual(len(self.problems('no comma here')), 1)


class WebLibraries(unittest.TestCase):
    """The libraries of the xTalk Suite that the web engine has are named
    twice by hand, in the checker's advice to a stack that uses one it
    lacks and in the README's rules, and both change when tools/pins.json
    moves to a web engine with more. A stranger may read either, so they
    must name the same ones, and the ones they send to the desktop must be
    the ones the checker refuses (suite-handlers.json)."""

    @staticmethod
    def named(text):
        return {w.lower() for w in re.findall(r'\b[A-Za-z0-9]{3,}xt\b', text, re.I)}

    def readme_rule(self):
        with open(README, encoding='utf-8') as f:
            text = f.read()
        start = text.index("- The libraries that come with OXT-Beyond's web engine")
        return text[start:text.index('\n- ', start)]

    def refused(self):
        return {member for member, does in lcscan.load_suite().values() if does == 'blocked'}

    def test_the_advice_and_the_readme_name_the_same_libraries(self):
        advice = self.named(scan.ADVICE['suite-not-on-web'])
        self.assertEqual(advice, self.named(self.readme_rule()))
        self.assertGreater(len(advice), len(self.refused()))

    def test_the_ones_sent_to_the_desktop_are_the_ones_refused(self):
        advice = scan.ADVICE['suite-not-on-web']
        self.assertEqual(self.named(advice[advice.rindex('. ') + 2:]), self.refused())
        refused_here = re.search(r'\(([^)]*)\) are refused', self.readme_rule())
        self.assertIsNotNone(refused_here)
        self.assertEqual(self.named(refused_here.group(1)), self.refused())

    def test_midixt_is_on_the_web(self):
        # web-2026.11.22's web engine links midixt in, over the browser's
        # Web MIDI (OXT-Beyond pull request 630)
        self.assertIn('midixt', self.named(scan.ADVICE['suite-not-on-web']) - self.refused())
        self.assertIn('midixt', self.named(self.readme_rule()) - self.refused())


if __name__ == '__main__':
    unittest.main()
