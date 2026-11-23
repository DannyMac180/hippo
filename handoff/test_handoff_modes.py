"""Unit tests for stanza.py, extended handoff_test.py modes, and pages_benchmark.py."""

from datetime import date
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


HERE = Path(__file__).resolve().parent
HANDOFF_SCRIPT = HERE / 'handoff_test.py'
STANZA_SCRIPT = HERE / 'stanza.py'
BENCH_SCRIPT = HERE / 'pages_benchmark.py'


NO_HINT_STUB = """
import re, sys
from pathlib import Path

prompt, behavior = sys.argv[1], sys.argv[2]
m = re.search(r"Shared memory for all my agents lives at (.+?)\\. It holds", prompt)
assert m, "stanza missing memory dir"
mem = Path(m.group(1))
# Confirm question does NOT name the memory dir outside the stanza
q = prompt.split("Question:", 1)[1]
assert str(mem) not in q, "question leaked memory folder path"
if behavior == "needs_read_first" and "Before answering a question about past decisions" not in prompt:
    print("I don't know.")
    sys.exit(0)
note = max((mem / "daily").glob("handoff-test-*.md"), key=lambda p: p.stat().st_mtime_ns)
print(note.read_text())
print(note.name)
"""

WRITER_STUB = """
import re, secrets, sys
from datetime import date
from pathlib import Path

prompt, behavior = sys.argv[1], sys.argv[2]
m = re.search(r"Shared memory for all my agents lives at (.+?)\\. It holds", prompt)
mem = Path(m.group(1))
dec = re.search(r"Decision: (decision-[0-9a-f]+)", prompt).group(1)
src = re.search(r"Source: (session-[0-9a-f]+)", prompt).group(1)
daily = mem / "daily"
daily.mkdir(exist_ok=True)
if behavior == "good":
    p = daily / f"{date.today().isoformat()}-stub-{secrets.token_hex(3)}.md"
    p.write_text(f"# Note {date.today().isoformat()}\\n\\nDecision: {dec}\\nSource: {src}\\n")
elif behavior == "missing_source":
    p = daily / f"{date.today().isoformat()}-stub-{secrets.token_hex(3)}.md"
    p.write_text(f"# Note {date.today().isoformat()}\\n\\nDecision: {dec}\\n")
print("saved")
"""

CORRECTION_STUB = """
import sys
from pathlib import Path

mem, behavior = Path(sys.argv[1]), sys.argv[2]
init_note = sorted((mem / "daily").glob("handoff-init-*.md"))[-1]
corr_note = sorted((mem / "daily").glob("handoff-corr-*.md"))[-1]
if behavior == "corrected":
    print(corr_note.read_text())
    print("Original:", init_note.name, "Correction:", corr_note.name)
else:
    print(init_note.read_text())
    print("Original:", init_note.name)
"""

CONCURRENT_STUB = """
import fcntl, re, sys, time
from datetime import date
from pathlib import Path

mem, prompt, behavior = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
shared = Path(re.search(r"Re-read (.+?) immediately", prompt).group(1))
token = re.search(r"(line-[ab]-[0-9a-f]+)", prompt).group(1)
writer = re.search(r"writer id '(writer-[ab])'", prompt).group(1)
daily = mem / "daily"
daily.mkdir(exist_ok=True)
(daily / f"{date.today().isoformat()}-{writer}.md").write_text(f"# {writer}\\n\\n{token}\\n")
line = f"- [{date.today().isoformat()}] {token} (writer: {writer})\\n"
if behavior == "safe":
    with shared.open("r+", encoding="utf-8") as sf:
        fcntl.flock(sf, fcntl.LOCK_EX)
        content = sf.read()
        sf.seek(0)
        sf.write(content + line)
        sf.truncate()
        sf.flush()
        sf.seek(0)
        assert token in sf.read()
        fcntl.flock(sf, fcntl.LOCK_UN)
else:
    # Deliberately clobber: both read before sleeping, then overwrite
    content = shared.read_text()
    time.sleep(0.15)
    shared.write_text(content + line)
print("done")
"""

BENCH_STUB = """
import sys
from pathlib import Path

mem = Path(sys.argv[1])
prompt = sys.argv[2]
# Answer correctly only if synthesized pages exist, else give weaker search-only answers
has_pages = (mem / "projects/kestrel.md").is_file()
q_lower = prompt.lower()
answers = {
    "kestrel port": "7443" if has_pages else "7001",
    "latency": "85ms" if has_pages else "120ms",
    "how many engineers": "5",
    "kestrel evals": "Jordan" if has_pages else "Priya",
    "osprey qa": "Priya",
    "retention": "30",
    "storage engine": "SQLite",
    "cloud cap": "$550" if has_pages else "$420",
    "release freeze": "2026-12-18",
    "weekly syncs": "Thursday",
}
for needle, ans in answers.items():
    if needle in q_lower:
        print(ans)
        sys.exit(0)
print("unknown")
"""


class HandoffExtendedTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.mem = self.root / 'memory'
        self.mem.mkdir()
        (self.mem / 'AGENTS.md').write_text('# Rules\n', encoding='utf-8')
        (self.mem / 'agent').mkdir()
        (self.mem / 'agent' / 'index.md').write_text('# Index\n\n- [[notes/a]]\n', encoding='utf-8')
        self.cfg = self.root / 'cfg.json'

    def write_cfg(self, harnesses, timeout=15):
        self.cfg.write_text(json.dumps({'timeout_seconds': timeout, 'harnesses': harnesses}))

    def run_handoff(self, *args):
        return subprocess.run(
            [sys.executable, str(HANDOFF_SCRIPT), '--memory-dir', str(self.mem),
             '--config', str(self.cfg), *args],
            capture_output=True, text=True, timeout=30,
        )

    def test_stanza_profiles_and_budget(self):
        for profile in ('pointer', 'read-first', 'inject'):
            res = subprocess.run(
                [sys.executable, str(STANZA_SCRIPT), '--memory-dir', str(self.mem),
                 '--profile', profile, '--json'],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            data = json.loads(res.stdout)
            self.assertEqual(data['base_lines'], 7)
            self.assertTrue(data['within_stanza_budget'])
            if profile == 'read-first':
                self.assertIn('Before answering a question about past decisions', data['text'])
            elif profile == 'inject':
                self.assertIn('[[notes/a]]', data['text'])

    def test_p1_no_hint_pointer_vs_read_first(self):
        self.write_cfg([{
            'name': 'weaker-model',
            'command': [sys.executable, '-c', NO_HINT_STUB, '{prompt}', 'needs_read_first'],
        }])
        res = self.run_handoff('--mode', 'no-hint', '--profile', 'pointer', '--runs', '3', '--json')
        self.assertEqual(res.returncode, 1, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data['summary'][0]['pass_rate'], 0.0)
        self.assertEqual(data['summary'][0]['recommendation'],
                         {'action': 'step_up', 'next_profile': 'read-first'})

        res2 = self.run_handoff('--mode', 'no-hint', '--profile', 'read-first', '--runs', '3', '--json')
        self.assertEqual(res2.returncode, 0, res2.stderr)
        data2 = json.loads(res2.stdout)
        self.assertEqual(data2['summary'][0]['pass_rate'], 1.0)
        self.assertEqual(data2['summary'][0]['recommendation'],
                         {'action': 'stay', 'next_profile': 'read-first'})

    def test_p1b_write_mode_verifies_date_and_source_and_cleans_up(self):
        self.write_cfg([{
            'name': 'good-writer',
            'command': [sys.executable, '-c', WRITER_STUB, '{prompt}', 'good'],
        }])
        res = self.run_handoff('--mode', 'write', '--runs', '2', '--json')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertFalse((self.mem / 'daily').exists())

        self.write_cfg([{
            'name': 'bad-writer',
            'command': [sys.executable, '-c', WRITER_STUB, '{prompt}', 'missing_source'],
        }])
        res2 = self.run_handoff('--mode', 'write', '--runs', '1', '--json')
        self.assertEqual(res2.returncode, 1)
        self.assertFalse(json.loads(res2.stdout)['results'][0]['found']['source'])
        self.assertFalse((self.mem / 'daily').exists())

    def test_f2_correction_propagation(self):
        self.write_cfg([
            {'name': 'fresh-ok', 'command': [sys.executable, '-c', CORRECTION_STUB, '{memory_dir}', 'corrected']},
            {'name': 'stale', 'command': [sys.executable, '-c', CORRECTION_STUB, '{memory_dir}', 'stale']},
        ])
        res = self.run_handoff('--mode', 'correction', '--json')
        self.assertEqual(res.returncode, 1, res.stderr)
        rows = json.loads(res.stdout)['results']
        self.assertTrue(rows[0]['passed'])
        self.assertFalse(rows[0]['reported_stale_only'])
        self.assertFalse(rows[1]['passed'])
        self.assertTrue(rows[1]['reported_stale_only'])
        self.assertFalse((self.mem / 'daily').exists())

    def test_f1_concurrent_writers_pass_and_clobber_detection(self):
        self.write_cfg([{
            'name': 'safe-writer',
            'command': [sys.executable, '-c', CONCURRENT_STUB, '{memory_dir}', '{prompt}', 'safe'],
        }])
        res = self.run_handoff('--mode', 'concurrent', '--runs', '3', '--json')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertFalse((self.mem / 'notes').exists())
        self.assertFalse((self.mem / 'daily').exists())

        self.write_cfg([{
            'name': 'racy-writer',
            'command': [sys.executable, '-c', CONCURRENT_STUB, '{memory_dir}', '{prompt}', 'clobber'],
        }])
        res2 = self.run_handoff('--mode', 'concurrent', '--runs', '1', '--json')
        self.assertEqual(res2.returncode, 1)
        found = json.loads(res2.stdout)['results'][0]['found']
        self.assertFalse(found['line_a'] and found['line_b'])

    def test_p3b_pages_benchmark_passes_when_pages_smaller_and_improve_score(self):
        self.write_cfg([{
            'name': 'bench-stub',
            'command': [sys.executable, '-c', BENCH_STUB, '{memory_dir}', '{prompt}'],
        }])
        res = subprocess.run(
            [sys.executable, str(BENCH_SCRIPT), '--config', str(self.cfg), '--json'],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertTrue(data['passed'])
        self.assertEqual(data['page_size_findings'], [])
        self.assertEqual(data['results'][0]['search_only']['correct'], 6)
        self.assertEqual(data['results'][0]['search_plus_pages']['correct'], 10)


if __name__ == '__main__':
    unittest.main()
