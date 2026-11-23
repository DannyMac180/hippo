"""Unit tests for examples/setup_hippo.py (Obsidian URI, file://, merge, idempotent harness install)."""

import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


SCRIPT = Path(__file__).resolve().with_name('setup_hippo.py')


class SetupHippoTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.home = self.tmp / 'home'
        self.home.mkdir()

    def run_setup(self, *args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True, text=True, timeout=20,
        )

    def test_obsidian_uri_resolution_and_non_destructive_merge(self):
        vault = self.tmp / 'MyVault'
        agents = vault / 'Agents'
        agents.mkdir(parents=True)
        existing_note = agents / 'AGENTS.md'
        existing_note.write_text('# Custom existing instructions\n', encoding='utf-8')

        obs_cfg = self.tmp / 'obsidian.json'
        obs_cfg.write_text(json.dumps({
            'vaults': {'abc123': {'path': str(vault), 'open': True}}
        }), encoding='utf-8')

        res = self.run_setup(
            'obsidian://open?vault=MyVault',
            '--obsidian-config', str(obs_cfg),
            '--harness', 'claude', '--harness', 'codex', '--harness', 'antigravity',
            '--home', str(self.home),
            '--json',
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(Path(data['memory_dir']).resolve(), agents.resolve())
        self.assertIn('AGENTS.md', data['scaffold']['skipped_existing'])
        self.assertEqual(existing_note.read_text(encoding='utf-8'), '# Custom existing instructions\n')
        self.assertEqual(data['verification']['memlint_findings'], 0)
        self.assertFalse(data['verification']['dream_should_run'])

        # Confirm all three harness global files got the stanza
        for rel in ('.claude/CLAUDE.md', '.codex/AGENTS.md', '.gemini/GEMINI.md'):
            text = (self.home / rel).read_text(encoding='utf-8')
            self.assertIn('<!-- hippo:start -->', text)
            self.assertIn(str(agents), text)

        # Re-running with read-first updates idempotently without duplicating markers
        res2 = self.run_setup(
            'obsidian://open?vault=MyVault',
            '--obsidian-config', str(obs_cfg),
            '--harness', 'claude', '--profile', 'read-first',
            '--home', str(self.home),
            '--json',
        )
        self.assertEqual(res2.returncode, 0, res2.stderr)
        claude_text = (self.home / '.claude/CLAUDE.md').read_text(encoding='utf-8')
        self.assertEqual(claude_text.count('<!-- hippo:start -->'), 1)
        self.assertIn('Before answering a question about past decisions', claude_text)

    def test_file_url_and_dry_run_write_nothing(self):
        target = self.tmp / 'notes-folder'
        res = self.run_setup(
            target.as_uri(),
            '--harness', 'all',
            '--home', str(self.home),
            '--dry-run', '--json',
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertTrue(data['dry_run'])
        self.assertFalse(target.exists())
        self.assertEqual(len(data['harnesses']), 3)
        self.assertFalse((self.home / '.claude/CLAUDE.md').exists())


if __name__ == '__main__':
    unittest.main()
