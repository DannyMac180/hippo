import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).with_name('memlint.py').absolute()


class MemlintTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.root = self.base / 'memory'
        self.root.mkdir()

    def write(self, name, text=''):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.base,
                              capture_output=True, text=True, timeout=10)

    def check_json(self, expected):
        result = self.run_cli('./memory/', '--json')
        self.assertEqual(result.returncode, int(bool(expected)), result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['memory_dir'], './memory/')
        found = []
        for f in data['findings']:
            keys = {'check', 'file', 'line', 'message'}
            if f['check'] == 'L1':
                keys.add('target')
            self.assertEqual(set(f), keys)
            self.assertTrue(f['message'])
            found.append((f['check'], f['file'], f['line'], f.get('target')))
        self.assertEqual(found, expected)
        self.assertEqual(data['counts'], {c: sum(row[0] == c for row in expected)
                                          for c in ('D1', 'D2', 'L1', 'I1')})

    def test_decisions_and_sources(self):
        decision = 'agent/decisions.md'
        self.write('agent/index.md', '[[decisions]]')
        self.write(decision, '\n'.join([
            '# History', 'Source: [[index]]', '## Undated', 'Mention [[index]]',
            '## 2024-02-29: One', 'Related: [[index|the index]]',
            '### Details', '## 2026-02-30: Two', 'Source: https://example.org/record',
            '## 2026-05-18 Three', 'Related: `../notes with spaces/doc.md`',
            '## 2026-05-19: Four', 'Source: just words', 'prefix Related: [[index]]',
            '~~~', 'Source: [[index]]', '## Code example', '~~~',
            '## 2026-05-20: Five', 'Source: `record.txt`',
            '## 2026-05-21: Six', 'Source: `[[index]]`',
        ]))
        self.check_json([('D1', decision, 3, None), ('D2', decision, 3, None),
                         ('D1', decision, 8, None), ('D1', decision, 10, None),
                         ('D2', decision, 12, None), ('D2', decision, 21, None)])

    def test_link_resolution_and_reachability(self):
        self.write('README.md', '[[p/first.md|Start]] [[templates/through]]')
        self.write('agent/index.md', '[[own]]')
        self.write('agent/own.md')
        first = 'p/first.md'
        self.write(first, '\n'.join([
            '![[second#heading|there]] [[../README]] [[deep/last]]',
            '[[unique]] [[#local]] [[absent.md|alias]] ![[MISSING#header]]',
            '[[../../README]] [[/README]] [[wrong/unique]] [[UNIQUE]]',
        ]))
        self.write('p/second.md', '[[first]]')
        self.write('second.md')  # Relative match takes priority.
        self.write('deep/last.md')
        self.write('deep/unique.md')
        self.write('templates/through.md', '[[via-template]]')
        self.write('via-template.md')
        self.write('templates/nested/exempt.md', '[[broken]]')
        self.write('other/templates/lost.md')
        self.write('ignored.md.bak-2026-10-16', '[[broken]]')
        self.check_json([('I1', 'other/templates/lost.md', None, None),
                         ('L1', first, 2, 'absent.md'), ('L1', first, 2, 'MISSING'),
                         ('L1', first, 3, '../../README'), ('L1', first, 3, '/README'),
                         ('L1', first, 3, 'wrong/unique'), ('L1', first, 3, 'UNIQUE'),
                         ('I1', 'second.md', None, None),
                         ('L1', 'templates/nested/exempt.md', 1, 'broken')])

    def test_filename_fallback_and_root_precedence(self):
        self.write('agent/index.md', '[[record]] [[shared]]')
        self.write('record.md', '[[agent/index]]')
        self.write('a/record.md')
        self.write('a/shared.md')
        self.write('b/shared.md')
        self.write('disconnected/a.md', '[[b]]')
        self.write('disconnected/b.md', '[[a]]')
        self.check_json([('I1', note, None, None) for note in
                         ('a/record.md', 'disconnected/a.md', 'disconnected/b.md')])

    def test_code_spans_and_fences(self):
        self.write('README.md', '\n'.join([
            '``a ` [[hidden]]`` [[missing1]] `[[hidden]]`',
            '   ````md', '[[hidden]]', '```', '[[hidden]]', ' ````` ',
            '~~~markdown', '[[hidden]]', '~~~~', '[[missing2]]',
            'See `split', '[[hidden]]` and [[missing3]]',
            '', 'An unmatched ` and [[missing4]]',
        ]))
        self.check_json([('L1', 'README.md', n, 'missing' + str(i))
                         for i, n in enumerate((1, 10, 12, 14), 1)])

    def test_missing_roots_hidden_notes_and_read_only(self):
        name = '.hidden/orphan.md'
        self.write(name, '[[absent]]')
        path = self.root / name
        before = path.stat()
        self.check_json([('I1', name, None, None), ('L1', name, 1, 'absent')])
        self.assertEqual(path.stat().st_mtime_ns, before.st_mtime_ns)
        self.assertEqual(path.read_bytes(), b'[[absent]]')
        self.assertEqual(sorted(self.root.rglob('*')), [path.parent, path])

    def test_symlinks_do_not_import_outside_notes(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'other.md').write_text('[[external]]', encoding='utf-8')
        (self.root / 'link.md').symlink_to(outside / 'other.md')
        (self.root / 'dir').symlink_to(outside, target_is_directory=True)
        self.write('README.md', '[[link]] [[dir/other]] [[../outside/other]]')
        self.check_json([('L1', 'README.md', 1, target)
                         for target in ('link', 'dir/other', '../outside/other')])

    def test_success_and_text_output(self):
        self.check_json([])
        self.write('README.md', '[[logs/start]]')
        self.write('logs/start.md', '[[../README]]')
        self.check_json([])
        self.write('logs/start.md', '[[broken]]')
        result = self.run_cli('memory')
        self.assertEqual(result.returncode, 1)
        finding, summary = result.stdout.splitlines()
        for value in ('logs/start.md:1', 'L1', 'broken'):
            self.assertIn(value, finding)
        self.assertIn('L1=1', summary)

    def test_usage_errors(self):
        self.write('README.md')
        for args in ((), ('absent', '--json'), ('memory/README.md',), ('memory', '--unknown')):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2)
                self.assertTrue(result.stderr)
                self.assertEqual(result.stdout, '')


if __name__ == '__main__':
    unittest.main()
