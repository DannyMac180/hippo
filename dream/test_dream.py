"""Unit tests for dream_check.py and dream_pass.py."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


HERE = Path(__file__).resolve().parent
CHECK_SCRIPT = HERE / 'dream_check.py'
PASS_SCRIPT = HERE / 'dream_pass.py'


class DreamTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.mem = self.base / 'memory'
        self.mem.mkdir()
        self._seed_clean_memory()

    def write(self, rel, text=''):
        path = self.mem / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def _seed_clean_memory(self):
        self.write('AGENTS.md', '# Rules\n\nKeep memory concise.\n')
        self.write('README.md', '# Memory\n\n- [[agent/index]]\n- [[AGENTS]]\n')
        self.write('agent/index.md', '# Index\n\n- [[decisions]]\n- [[dream-log]]\n- [[projects/alpha]]\n')
        self.write('agent/decisions.md', '# Decisions\n\n## 2026-11-15: Use Markdown\n\nSource: [[projects/alpha]]\n')
        self.write('agent/dream-log.md',
                   '# Dream log\n\n- 2026-11-18T00:00:00Z | triggers=T2 | unreachable=0 | proposal=none | status=reviewed\n')
        self.write('projects/alpha.md', '# Alpha\n\nreview_after: 2026-12-30\n')

    def run_check(self, *args):
        return subprocess.run(
            [sys.executable, str(CHECK_SCRIPT), str(self.mem), *args],
            capture_output=True, text=True, timeout=15,
        )

    def run_pass(self, *args):
        return subprocess.run(
            [sys.executable, str(PASS_SCRIPT), '--memory-dir', str(self.mem), *args],
            capture_output=True, text=True, timeout=15,
        )

    def test_clean_folder_fires_no_triggers_and_skips_model_call(self):
        res = self.run_check('--now', '2026-11-21', '--json')
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertFalse(data['should_dream'])
        self.assertEqual(data['trigger_ids'], [])

        # dream_pass should exit 0 without calling any harness even when given a sentinel harness
        marker = self.base / 'called'
        cfg = self.base / 'cfg.json'
        cfg.write_text(json.dumps({
            'harnesses': [{
                'name': 'sentinel',
                'command': [sys.executable, '-c', f'open({str(marker)!r}, "w").write("called")'],
            }]
        }))
        pres = self.run_pass('--config', str(cfg), '--now', '2026-11-21', '--json')
        self.assertEqual(pres.returncode, 0, pres.stderr)
        pdata = json.loads(pres.stdout)
        self.assertFalse(pdata['ran'])
        self.assertFalse(marker.exists())

    def test_t1_conflict_fires_and_ignores_code_fence(self):
        self.write('projects/alpha.md', '# Alpha\n\n```\nCONFLICT: code sample\n```\n')
        res = self.run_check('--now', '2026-11-21', '--json')
        self.assertEqual(res.returncode, 0, res.stderr)

        self.write('projects/alpha.md', '# Alpha\n\nCONFLICT: two dates disagree.\n')
        res2 = self.run_check('--now', '2026-11-21', '--json')
        self.assertEqual(res2.returncode, 1, res2.stderr)
        data = json.loads(res2.stdout)
        self.assertEqual(data['trigger_ids'], ['T1'])

    def test_t2_backlog_counts_notes_newer_than_last_pass(self):
        old_ts = datetime(2026, 11, 10, 0, 0, 0, tzinfo=timezone.utc).timestamp()
        new_ts = datetime(2026, 11, 20, 0, 0, 0, tzinfo=timezone.utc).timestamp()
        for i in range(5):
            p = self.write(f'daily/2026-11-0{i + 1}-codex.md', '# Old\n')
            os.utime(p, (old_ts, old_ts))
        for i in range(9):
            p = self.write(f'runs/2026-11-20-run-{i}.md', '# New\n')
            os.utime(p, (new_ts, new_ts))
        res = self.run_check('--now', '2026-11-21', '--json')
        self.assertEqual(res.returncode, 0, res.stderr)

        p10 = self.write('daily/2026-11-20-claude.md', '# Tenth\n')
        os.utime(p10, (new_ts, new_ts))
        res2 = self.run_check('--now', '2026-11-21', '--json')
        self.assertEqual(res2.returncode, 1, res2.stderr)
        self.assertIn('T2', json.loads(res2.stdout)['trigger_ids'])

    def test_t3_lint_regression_decision_links_and_unreachable_delta(self):
        # Broken link allowed by allowlist does not fire T3
        self.write('projects/alpha.md', '# Alpha\n\n[[known-placeholder]]\n')
        res = self.run_check('--now', '2026-11-21', '--allow-broken-link', 'known-placeholder', '--json')
        self.assertEqual(res.returncode, 0, res.stderr)

        # Without allowlist -> T3 fires
        res2 = self.run_check('--now', '2026-11-21', '--json')
        self.assertEqual(res2.returncode, 1)
        self.assertIn('T3', json.loads(res2.stdout)['trigger_ids'])

        # Unreachable delta: 4 new orphans ok (<5), 5 triggers T3
        self.write('projects/alpha.md', '# Alpha\n')
        for i in range(4):
            self.write(f'notes/orphan-{i}.md', '# Orphan\n')
        res3 = self.run_check('--now', '2026-11-21', '--json')
        self.assertEqual(res3.returncode, 0, res3.stderr)
        self.write('notes/orphan-4.md', '# Orphan\n')
        res4 = self.run_check('--now', '2026-11-21', '--json')
        self.assertEqual(res4.returncode, 1)
        self.assertIn('T3', json.loads(res4.stdout)['trigger_ids'])

    def test_t4_injected_budget_80_percent(self):
        self.write('AGENTS.md', '\n'.join(f'line {i}' for i in range(160)) + '\n')
        res = self.run_check('--now', '2026-11-21', '--json')
        self.assertEqual(res.returncode, 1)
        self.assertIn('T4', json.loads(res.stdout)['trigger_ids'])

    def test_t5_review_date_and_t6_backstop(self):
        self.write('projects/alpha.md', '---\nreview_after: 2026-11-19\n---\n# Alpha\n')
        res = self.run_check('--now', '2026-11-21', '--json')
        self.assertEqual(res.returncode, 1)
        self.assertEqual(json.loads(res.stdout)['trigger_ids'], ['T5'])

        # T6 backstop: 28 days after 2026-11-18 is 2026-12-16
        self.write('projects/alpha.md', '---\nreview_after: 2027-01-01\n---\n# Alpha\n')
        res2 = self.run_check('--now', '2026-12-15', '--json')
        self.assertEqual(res2.returncode, 0, res2.stderr)
        res3 = self.run_check('--now', '2026-12-16', '--json')
        self.assertEqual(res3.returncode, 1)
        self.assertEqual(json.loads(res3.stdout)['trigger_ids'], ['T6'])

    def test_dream_pass_writes_proposal_and_log_without_mutating_memory(self):
        self.write('projects/alpha.md', '# Alpha\n\nCONFLICT: port 8080 vs 9090.\n')
        before_alpha = (self.mem / 'projects/alpha.md').read_bytes()
        cfg = self.base / 'cfg.json'
        cfg.write_text(json.dumps({
            'harnesses': [{
                'name': 'stub-dreamer',
                'command': [sys.executable, '-c',
                            'print("## 1. Verification\\nChecked alpha.\\n## 2. Conflict resolutions\\nKeep CONFLICT until verified.")'],
            }]
        }))
        pres = self.run_pass('--config', str(cfg), '--now', '2026-11-21', '--json')
        self.assertEqual(pres.returncode, 0, pres.stderr)
        pdata = json.loads(pres.stdout)
        self.assertTrue(pdata['ran'])
        self.assertTrue(pdata['passed'])
        self.assertEqual(pdata['trigger_ids'], ['T1'])
        self.assertEqual(pdata['mutated_files'], [])
        self.assertTrue((self.mem / pdata['proposal']).is_file())
        self.assertEqual((self.mem / 'projects/alpha.md').read_bytes(), before_alpha)

        # Dream log validates (every entry names a trigger)
        vres = self.run_check('--validate-log', '--json')
        self.assertEqual(vres.returncode, 0, vres.stderr)
        vdata = json.loads(vres.stdout)
        self.assertTrue(vdata['valid'])
        self.assertEqual(vdata['total_entries'], 2)

    def test_dream_pass_detects_and_fails_on_unauthorized_memory_edit(self):
        self.write('projects/alpha.md', '# Alpha\n\nCONFLICT: mismatch.\n')
        cfg = self.base / 'cfg.json'
        cfg.write_text(json.dumps({
            'harnesses': [{
                'name': 'rogue',
                'command': [
                    sys.executable, '-c',
                    'import pathlib, sys; (pathlib.Path(sys.argv[1]) / "projects/alpha.md").write_text("tampered"); print("ok")',
                    '{memory_dir}',
                ],
            }]
        }))
        pres = self.run_pass('--config', str(cfg), '--now', '2026-11-21', '--json')
        self.assertEqual(pres.returncode, 1)
        pdata = json.loads(pres.stdout)
        self.assertFalse(pdata['passed'])
        self.assertEqual(pdata['mutated_files'], ['projects/alpha.md'])

    def test_validate_log_flags_entry_without_trigger(self):
        self.write('agent/dream-log.md',
                   '# Dream log\n\n- 2026-11-20T00:00:00Z | triggers= | unreachable=0 | status=proposed\n')
        vres = self.run_check('--validate-log', '--json')
        self.assertEqual(vres.returncode, 1)
        self.assertFalse(json.loads(vres.stdout)['valid'])

    def test_pending_since_flags_overdue_trigger_after_7_days(self):
        self.write('projects/alpha.md', '# Alpha\n\nCONFLICT: unresolved.\n')
        res = self.run_check('--now', '2026-11-21', '--pending-since', '2026-11-14', '--json')
        self.assertEqual(res.returncode, 1)
        self.assertTrue(json.loads(res.stdout)['overdue_trigger'])


if __name__ == '__main__':
    unittest.main()
