"""End-to-end acceptance checks for docs/design-2026-11-21.md §8 (P1-P3b, F1-F2, V1-V3).

Uses synthetic temporary memory folders and deterministic Python stub harnesses so
no real vault is touched and no network/API calls are made.
"""

from datetime import date
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def run_py(*args, cwd=None, timeout=60):
    return subprocess.run(
        [sys.executable, *map(str, args)],
        cwd=cwd, capture_output=True, text=True, timeout=timeout,
    )


class DesignAcceptance(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)

    def _init_starter(self, name='mem', *, git=True):
        target = self.tmp / name
        args = [ROOT / 'examples/init_memory.py', target]
        if not git:
            args.append('--no-git')
        res = run_py(*args)
        self.assertEqual(res.returncode, 0, res.stderr)
        return target

    def _write_cfg(self, name, harnesses, timeout=20):
        cfg = self.tmp / name
        cfg.write_text(json.dumps({'timeout_seconds': timeout, 'harnesses': harnesses}), encoding='utf-8')
        return cfg

    def test_p1_steering_profile_ten_runs(self):
        mem = self._init_starter('p1-mem', git=False)
        stub = (
            "import re, sys; from pathlib import Path; "
            "p = sys.argv[1]; "
            "m = re.search(r'Shared memory for all my agents lives at (.+?)\\. It holds', p); "
            "mem = Path(m.group(1)); "
            "note = max((mem / 'daily').glob('handoff-test-*.md'), key=lambda x: x.stat().st_mtime_ns); "
            "print(note.read_text()); print(note.name)"
        )
        cfg = self._write_cfg('p1.json', [
            {'name': 'strong-stub', 'command': [sys.executable, '-c', stub, '{prompt}']},
        ])
        res = run_py(ROOT / 'handoff/handoff_test.py', '--memory-dir', mem, '--config', cfg,
                     '--mode', 'no-hint', '--profile', 'pointer', '--runs', '10', '--json')
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data['summary'][0]['passed_runs'], 10)
        self.assertEqual(data['summary'][0]['recommendation'],
                         {'action': 'stay', 'next_profile': 'pointer'})

    def test_p1b_write_when_asked_five_runs(self):
        mem = self._init_starter('p1b-mem', git=False)
        stub = (
            "import re, secrets, sys; from datetime import date; from pathlib import Path; "
            "p = sys.argv[1]; "
            "mem = Path(re.search(r'Shared memory for all my agents lives at (.+?)\\. It holds', p).group(1)); "
            "dec = re.search(r'Decision: (decision-[0-9a-f]+)', p).group(1); "
            "src = re.search(r'Source: (session-[0-9a-f]+)', p).group(1); "
            "d = mem / 'daily'; d.mkdir(exist_ok=True); "
            "(d / f'{date.today().isoformat()}-stub-{secrets.token_hex(3)}.md').write_text("
            "f'# Note {date.today().isoformat()}\\nDecision: {dec}\\nSource: {src}\\n')"
        )
        cfg = self._write_cfg('p1b.json', [
            {'name': 'writer-stub', 'command': [sys.executable, '-c', stub, '{prompt}']},
        ])
        res = run_py(ROOT / 'handoff/handoff_test.py', '--memory-dir', mem, '--config', cfg,
                     '--mode', 'write', '--runs', '5', '--json')
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data['summary'][0]['passed_runs'], 5)
        self.assertTrue(data['cleaned_up'])

    def test_p2_dreaming_on_need_and_dream_log_invariant(self):
        mem = self._init_starter('p2-mem', git=False)
        marker = self.tmp / 'model-invoked'
        cfg = self._write_cfg('p2.json', [{
            'name': 'dream-stub',
            'command': [
                sys.executable, '-c',
                f'open({str(marker)!r}, "w").write("yes"); '
                'print("## 1. Verification\\nVerified.\\n## 2. Conflict resolutions\\nKeep CONFLICT.")',
            ],
        }])

        # 1. Clean folder: no trigger, no model call
        c1 = run_py(ROOT / 'dream/dream_check.py', mem, '--json')
        self.assertEqual(c1.returncode, 0, c1.stderr)
        self.assertEqual(json.loads(c1.stdout)['trigger_ids'], [])

        p1 = run_py(ROOT / 'dream/dream_pass.py', '--memory-dir', mem, '--config', cfg, '--json')
        self.assertEqual(p1.returncode, 0, p1.stderr)
        self.assertFalse(json.loads(p1.stdout)['ran'])
        self.assertFalse(marker.exists())

        # 2. Plant CONFLICT: mark -> T1 fires, pass writes proposal + log naming T1
        page = mem / 'projects/example-project.md'
        page.write_text(page.read_text(encoding='utf-8') + '\nCONFLICT: port 8080 vs 9090.\n',
                        encoding='utf-8')
        c2 = run_py(ROOT / 'dream/dream_check.py', mem, '--json')
        self.assertEqual(c2.returncode, 1, c2.stderr)
        self.assertIn('T1', json.loads(c2.stdout)['trigger_ids'])

        p2 = run_py(ROOT / 'dream/dream_pass.py', '--memory-dir', mem, '--config', cfg, '--json')
        self.assertEqual(p2.returncode, 0, p2.stderr)
        pdata = json.loads(p2.stdout)
        self.assertTrue(pdata['ran'])
        self.assertTrue(pdata['passed'])
        self.assertEqual(pdata['trigger_ids'], ['T1'])
        self.assertTrue(marker.exists())

        # 3. Validate dream log: every entry names a trigger
        v = run_py(ROOT / 'dream/dream_check.py', mem, '--validate-log', '--json')
        self.assertEqual(v.returncode, 0, v.stderr)
        self.assertTrue(json.loads(v.stdout)['valid'])

    def test_p3a_hierarchy_reachability_and_budgets(self):
        mem = self._init_starter('p3a-mem', git=False)
        stanza_file = self.tmp / 'stanza.md'
        st = run_py(ROOT / 'handoff/stanza.py', '--memory-dir', mem, '--profile', 'pointer')
        self.assertEqual(st.returncode, 0, st.stderr)
        stanza_file.write_text(st.stdout, encoding='utf-8')

        res = run_py(ROOT / 'memlint/memlint.py', mem, '--all', '--stanza', stanza_file, '--json')
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data['findings'], [])
        self.assertFalse(data['metrics']['budget']['exceeded'])
        self.assertFalse(data['metrics']['stanza']['exceeded'])

    def test_p3b_pages_earn_their_place(self):
        stub = (
            "import sys; from pathlib import Path; "
            "mem, prompt = Path(sys.argv[1]), sys.argv[2].lower(); "
            "has_pages = (mem / 'projects/kestrel.md').is_file(); "
            "ans = {'kestrel port': '7443' if has_pages else '7001', 'latency': '85ms', "
            "'how many engineers': '5', 'kestrel evals': 'Jordan', 'osprey qa': 'Priya', "
            "'retention': '30', 'storage engine': 'SQLite', 'cloud cap': '$550', "
            "'release freeze': '2026-12-18', 'weekly syncs': 'Thursday'}; "
            "print(next((v for k, v in ans.items() if k in prompt), 'unknown'))"
        )
        cfg = self._write_cfg('p3b.json', [
            {'name': 'bench-stub', 'command': [sys.executable, '-c', stub, '{memory_dir}', '{prompt}']},
        ])
        res = run_py(ROOT / 'handoff/pages_benchmark.py', '--config', cfg, '--json')
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        data = json.loads(res.stdout)
        self.assertTrue(data['passed'])
        self.assertEqual(data['questions'], 10)
        self.assertEqual(data['page_size_findings'], [])

    def test_f1_concurrent_writers_ten_runs(self):
        mem = self._init_starter('f1-mem', git=False)
        stub = "\n".join([
            "import fcntl, re, sys",
            "from datetime import date",
            "from pathlib import Path",
            "mem, p = Path(sys.argv[1]), sys.argv[2]",
            "shared = Path(re.search(r'Re-read (.+?) immediately', p).group(1))",
            "tok = re.search(r'(line-[ab]-[0-9a-f]+)', p).group(1)",
            "w = re.search(r\"writer id '(writer-[ab])'\", p).group(1)",
            "d = mem / 'daily'",
            "d.mkdir(exist_ok=True)",
            "(d / f'{date.today().isoformat()}-{w}.md').write_text(f'# {w}\\n{tok}\\n')",
            "with shared.open('r+', encoding='utf-8') as sf:",
            "    fcntl.flock(sf, fcntl.LOCK_EX)",
            "    body = sf.read()",
            "    sf.seek(0)",
            "    sf.write(body + f'- [{date.today().isoformat()}] {tok} (writer: {w})\\n')",
            "    sf.truncate()",
            "    sf.flush()",
            "    fcntl.flock(sf, fcntl.LOCK_UN)",
        ])
        cfg = self._write_cfg('f1.json', [
            {'name': 'concurrent-stub', 'command': [sys.executable, '-c', stub, '{memory_dir}', '{prompt}']},
        ])
        res = run_py(ROOT / 'handoff/handoff_test.py', '--memory-dir', mem, '--config', cfg,
                     '--mode', 'concurrent', '--runs', '10', '--json')
        self.assertEqual(res.returncode, 0, res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data['summary'][0]['passed_runs'], 10)

    def test_f2_correction_propagation(self):
        mem = self._init_starter('f2-mem', git=False)
        stub = (
            "import sys; from pathlib import Path; mem = Path(sys.argv[1]); "
            "i = sorted((mem / 'daily').glob('handoff-init-*.md'))[-1]; "
            "c = sorted((mem / 'daily').glob('handoff-corr-*.md'))[-1]; "
            "print(c.read_text()); print(i.name, c.name)"
        )
        cfg = self._write_cfg('f2.json', [
            {'name': 'f2-stub', 'command': [sys.executable, '-c', stub, '{memory_dir}']},
        ])
        res = run_py(ROOT / 'handoff/handoff_test.py', '--memory-dir', mem, '--config', cfg,
                     '--mode', 'correction', '--json')
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(json.loads(res.stdout)['results'][0]['passed'])

    def test_v1_third_harness_config_passes_without_code_change(self):
        mem = self._init_starter('v1-mem', git=False)
        # Validate harnesses.third-party.example.json structure and run with a shim on PATH
        third_cfg = ROOT / 'handoff/harnesses.third-party.example.json'
        self.assertTrue(third_cfg.is_file())
        shim_dir = self.tmp / 'bin'
        shim_dir.mkdir()
        shim = shim_dir / 'third-party-agent'
        shim.write_text(
            f"#!{sys.executable}\n"
            "import sys; from pathlib import Path\n"
            "assert sys.argv[1] == '--memory-dir'\n"
            "mem = Path(sys.argv[2])\n"
            "prompt = sys.stdin.read()\n"
            "assert str(mem) in prompt\n"
            "note = max((mem / 'daily').glob('handoff-test-*.md'), key=lambda p: p.stat().st_mtime_ns)\n"
            "print(note.read_text())\n"
            "print(note.name)\n",
            encoding='utf-8',
        )
        shim.chmod(0o700)
        import os
        env = dict(os.environ, PATH=str(shim_dir) + os.pathsep + os.environ['PATH'])
        res = subprocess.run(
            [sys.executable, str(ROOT / 'handoff/handoff_test.py'),
             '--memory-dir', str(mem), '--config', str(third_cfg), '--json'],
            capture_output=True, text=True, env=env, timeout=20,
        )
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        data = json.loads(res.stdout)
        self.assertEqual(data['results'][0]['harness'], 'third-party')
        self.assertTrue(data['results'][0]['passed'])

    def test_v2_and_v3_public_export_stranger_flow_and_history_scan(self):
        import secrets
        exporter = ROOT / 'scripts/export_public.py'
        if exporter.is_file():
            public_repo = self.tmp / 'hippo-public'
            canary = 'CANARY-' + secrets.token_hex(8)
            exp = run_py(
                exporter, '--out', public_repo,
                '--canary', canary,
                '--json',
            )
            self.assertEqual(exp.returncode, 0, exp.stdout + exp.stderr)
            exp_data = json.loads(exp.stdout)
            self.assertTrue(exp_data['scan']['passed'])
            self.assertEqual(exp_data['scan']['hit_count'], 0)
            self.assertFalse((public_repo / 'evals').exists())
            target_repo = public_repo
        else:
            # Running inside an already-exported public repository: run V3 directly on ROOT
            scan = run_py(ROOT / 'scripts/scan_public.py', ROOT, '--json')
            self.assertEqual(scan.returncode, 0, scan.stdout + scan.stderr)
            target_repo = ROOT

        # V2: Stranger flow from the exported repo alone
        starter = self.tmp / 'stranger-memory'
        r1 = run_py(target_repo / 'examples/init_memory.py', starter)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        self.assertTrue((starter / '.git').is_dir())
        r2 = run_py(target_repo / 'memlint/memlint.py', starter, '--all', '--json')
        self.assertEqual(r2.returncode, 0, r2.stdout + r2.stderr)
        r3 = run_py(target_repo / 'dream/dream_check.py', starter, '--json')
        self.assertEqual(r3.returncode, 0, r3.stdout + r3.stderr)
        cfg = self._write_cfg('v2.json', [{
            'name': 'stub',
            'command': [sys.executable, str(target_repo / 'acceptance/handoff/stub.py'),
                        'correct', '{memory_dir}', '{prompt}'],
        }])
        r4 = run_py(target_repo / 'handoff/handoff_test.py', '--memory-dir', starter,
                    '--config', cfg, '--json')
        self.assertEqual(r4.returncode, 0, r4.stdout + r4.stderr)

        # V3 negative check: verify scanner catches both a canary and a fake key in git history
        dirty = self.tmp / 'dirty-repo'
        dirty.mkdir()
        subprocess.run(['git', 'init', '-q'], cwd=dirty, check=True)
        (dirty / 'leak.txt').write_text('secret=' + 'sk' + '-ant-1234567890abcdefghij\n', encoding='utf-8')
        subprocess.run(['git', 'add', '-A'], cwd=dirty, check=True)
        subprocess.run(
            ['git', '-c', 'user.name=t', '-c', 'user.email=t@example.org', 'commit', '-q', '-m', 'add'],
            cwd=dirty, check=True,
        )
        (dirty / 'leak.txt').write_text('clean now\n', encoding='utf-8')
        subprocess.run(['git', 'add', '-A'], cwd=dirty, check=True)
        subprocess.run(
            ['git', '-c', 'user.name=t', '-c', 'user.email=t@example.org', 'commit', '-q', '-m', 'remove'],
            cwd=dirty, check=True,
        )
        bad_scan = run_py(target_repo / 'scripts/scan_public.py', dirty, '--json')
        self.assertEqual(bad_scan.returncode, 1)
        bad_data = json.loads(bad_scan.stdout)
        self.assertGreaterEqual(bad_data['hit_count'], 1)
        self.assertEqual(bad_data['hits'][0]['kind'], 'secret')


if __name__ == '__main__':
    unittest.main()
