"""Unit tests for extended memlint features (budget, page-size, logs, conflicts, config)."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

import memlint


SCRIPT = Path(__file__).with_name('memlint.py').absolute()


class MemlintExtendedTests(unittest.TestCase):
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
        return path

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=self.base, capture_output=True, text=True, timeout=10,
        )

    def test_exempt_logs_ignores_daily_and_runs_but_flags_pages(self):
        self.write('agent/index.md', '# Index\n')
        self.write('daily/2026-11-20-claude.md', '# Log\n')
        self.write('runs/2026-11-20-inbox.md', '# Run\n')
        self.write('notes/orphan.md', '# Orphan\n')

        res = self.run_cli('memory', '--exempt-logs', '--json')
        self.assertEqual(res.returncode, 1, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual([f['file'] for f in data['findings']], ['notes/orphan.md'])

    def test_allowlist_and_allow_broken_link(self):
        self.write('README.md', '[[ok-external]] [[placeholder]] [[truly-broken]]\n')
        allowfile = self.base / 'allow.txt'
        allowfile.write_text('# comment\nok-external\n\n', encoding='utf-8')

        res = self.run_cli('memory', '--allowlist', str(allowfile),
                           '--allow-broken-link', 'placeholder', '--json')
        self.assertEqual(res.returncode, 1, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual([(f['check'], f['target']) for f in data['findings']],
                         [('L1', 'truly-broken')])

    def test_configurable_roots_and_decisions(self):
        self.write('HOME.md', '[[notes/a]]\n')
        self.write('notes/a.md', '# A\n')
        self.write('log/dec.md', '## Undated\n\nNo source.\n')
        self.write('HOME.md', '[[notes/a]] [[log/dec]]\n')

        res = self.run_cli('memory', '--roots', 'HOME.md', '--decisions', 'log/dec.md', '--json')
        self.assertEqual(res.returncode, 1, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual([(f['check'], f['file']) for f in data['findings']],
                         [('D1', 'log/dec.md'), ('D2', 'log/dec.md')])

    def test_budget_check_and_stanza_check(self):
        self.write('AGENTS.md', '\n'.join(f'line {i}' for i in range(150)) + '\n')
        self.write('agent/index.md', '\n'.join(f'- [[notes/p{i}]]' for i in range(60)) + '\n')
        stanza = self.base / 'stanza.md'
        stanza.write_text('\n'.join(f'line {i}' for i in range(11)) + '\n', encoding='utf-8')

        res = self.run_cli('memory', '--budget', '--stanza', str(stanza), '--json')
        self.assertEqual(res.returncode, 1, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data['counts']['B1'], 1)
        self.assertEqual(data['counts']['B2'], 1)
        self.assertEqual(data['metrics']['budget']['lines'], 210)
        self.assertEqual(data['metrics']['stanza']['lines'], 11)

        # Within budget passes
        self.write('AGENTS.md', '# Rules\n')
        self.write('agent/index.md', '# Index\n\n- [[AGENTS]]\n')
        stanza.write_text('## Shared memory\nLine 2\nLine 3\n', encoding='utf-8')
        res2 = self.run_cli('memory', '--budget', '--stanza', str(stanza), '--json')
        self.assertEqual(res2.returncode, 0, res2.stderr)
        data2 = json.loads(res2.stdout)
        self.assertEqual(data2['counts']['B1'], 0)
        self.assertEqual(data2['counts']['B2'], 0)

    def test_page_size_check_flags_bloated_page_only(self):
        self.write('agent/index.md', '- [[projects/compact]]\n- [[projects/bloated]]\n')
        self.write('daily/2026-11-10.md', '# Day 1\n\n' + ('detail ' * 40) + '\n')
        self.write('daily/2026-11-11.md', '# Day 2\n\n' + ('context ' * 40) + '\n')
        self.write('projects/compact.md', '# Compact\n\nSummary of [[daily/2026-11-10]] and [[daily/2026-11-11]].\n')
        self.write('projects/bloated.md', '# Bloated\n\nFrom [[daily/2026-11-10]]: ' + ('padding ' * 80) + '\n')

        res = self.run_cli('memory', '--exempt-logs', '--page-size', '--json')
        self.assertEqual(res.returncode, 1, res.stderr)
        data = json.loads(res.stdout)
        s1 = [f for f in data['findings'] if f['check'] == 'S1']
        self.assertEqual(len(s1), 1)
        self.assertEqual(s1[0]['file'], 'projects/bloated.md')
        self.assertGreater(s1[0]['page_bytes'], s1[0]['source_bytes'])

    def test_conflicts_ignore_code_blocks_and_inline_spans(self):
        self.write('agent/index.md', '- [[notes/a]]\n')
        self.write('notes/a.md', '\n'.join([
            '# Notes',
            'Inline `CONFLICT: example` does not trigger.',
            '```',
            'CONFLICT: inside fence does not trigger.',
            '```',
            'CONFLICT: real disagreement between sources.',
        ]) + '\n')

        res = self.run_cli('memory', '--conflicts', '--json')
        self.assertEqual(res.returncode, 1, res.stderr)
        data = json.loads(res.stdout)
        c1 = [f for f in data['findings'] if f['check'] == 'C1']
        self.assertEqual([(f['file'], f['line']) for f in c1], [('notes/a.md', 6)])

    def test_review_dates_and_read_pages_helpers(self):
        self.write('projects/alpha.md', '---\nreview_after: 2026-11-01\n---\n# Alpha\n')
        self.write('notes/beta.md', '# Beta\n\nReview after: 2026-12-15\n')
        self.write('daily/2026-11-18.md', '# Session\n\nRead: [[projects/alpha]], [[notes/beta]]\n')
        self.write('daily/2026-11-19.md', '# Session\n\nPages read: `projects/alpha.md`\n')

        dates = memlint.find_review_dates(self.root)
        self.assertEqual(dates, [
            {'file': 'notes/beta.md', 'line': 3, 'review_after': '2026-12-15'},
            {'file': 'projects/alpha.md', 'line': 2, 'review_after': '2026-11-01'},
        ])
        reads = memlint.extract_read_pages(self.root)
        self.assertEqual(reads, {'projects/alpha.md': 2, 'notes/beta.md': 1})


if __name__ == '__main__':
    unittest.main()
