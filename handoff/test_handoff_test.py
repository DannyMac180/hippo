"""Exercise the public CLI with temporary memory and fresh Python processes."""

from datetime import date
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().with_name("handoff_test.py")
READER = """
import json, sys
from pathlib import Path

root, prompt, mode, log = sys.argv[1:]
assert root in prompt and Path(root).is_absolute()
assert not list(Path.cwd().iterdir())
Path("scratch").touch()
with open(log, "a") as stream:
    print(json.dumps([mode, str(Path.cwd())]), file=stream)
note = max((Path(root) / "daily").glob("handoff-test-*.md"),
           key=lambda path: path.stat().st_mtime_ns)
fields = dict(line.split(": ", 1) for line in note.read_text().splitlines() if ": " in line)
assert all(secret not in prompt for secret in [fields["Decision"], fields["Rationale"], note.name])
print(fields["Decision"])
print(fields["Rationale"], file=sys.stderr if mode == "partial" else sys.stdout)
print(note.name)
"""


class HandoffTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.memory = self.root / "shared memory {prompt} & notes"
        self.memory.mkdir()
        (self.memory / "existing.md").write_bytes(b"original\r\n")
        self.config = self.root / "config.json"
        self.log = self.root / "working-directories"
        self.cli = [sys.executable, str(SCRIPT), "--memory-dir", str(self.memory),
                    "--config", str(self.config)]

    def reader(self, name):
        return {"name": name, "command": [sys.executable, "-c", READER, "{memory_dir}",
                                          "{prompt}", name, str(self.log)]}

    def configure(self, *harnesses, **settings):
        self.config.write_text(json.dumps(dict(harnesses=harnesses, **settings)))

    def invoke(self, *args):
        return subprocess.run(self.cli + list(args), capture_output=True, text=True, timeout=10)

    def snapshot(self):
        return {path.relative_to(self.memory): (path.read_bytes(), path.stat().st_mtime_ns)
                if path.is_file() else None for path in self.memory.rglob("*")}

    def test_fresh_readers_pass_and_new_daily_is_removed(self):
        self.configure(self.reader("first"), self.reader("second"))
        before = self.snapshot()
        run = self.invoke("--json")
        self.assertEqual(run.returncode, 0, run.stderr)
        report = json.loads(run.stdout)
        self.assertRegex(report["note"], r"^daily/handoff-test-[0-9a-f]{8}\.md$")
        self.assertTrue(report["cleaned_up"])
        self.assertEqual(["first", "second"], [row["harness"] for row in report["results"]])
        for row in report["results"]:
            self.assertTrue(row["passed"])
            self.assertEqual(row["found"], dict(decision=True, reason=True, source=True))
            self.assertEqual(row["exit_code"], 0)
            self.assertFalse(row["timed_out"])
            self.assertGreaterEqual(row["seconds"], 0)
        logs = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual([name for name, _ in logs], ["first", "second"])
        self.assertNotEqual(logs[0][1], logs[1][1])
        self.assertFalse(any(Path(cwd).exists() for _, cwd in logs))
        self.assertEqual(before, self.snapshot())

    def test_partial_answer_and_existing_daily_are_preserved(self):
        daily = self.memory / "daily"
        daily.mkdir()
        (daily / "archive.md").write_bytes(b"previous decision")
        before = self.snapshot()
        self.configure(self.reader("partial"))
        run = self.invoke()
        self.assertEqual(run.returncode, 1)
        line, summary = run.stdout.splitlines()
        self.assertIn("FAIL", line)
        self.assertIn("missing reason", line)
        self.assertIn("0/1 passed", summary)
        self.assertEqual(before, self.snapshot())

    def test_timeout_and_start_failure_continue_to_next_harness(self):
        (self.memory / "daily").mkdir()
        before = self.snapshot()
        self.configure({"name": "slow", "command": [sys.executable, "-c",
                                                    "import time; time.sleep(30)"]},
                       {"name": "absent", "command": [str(self.root / "absent")]},
                       self.reader("last"), timeout_seconds=0.5)
        run = self.invoke("--json")
        self.assertEqual(run.returncode, 1, run.stderr)
        report = json.loads(run.stdout)
        slow, absent, last = report["results"]
        self.assertTrue(slow["timed_out"])
        self.assertFalse(absent["timed_out"])
        for row in [slow, absent]:
            self.assertFalse(row["passed"])
            self.assertIsNone(row["exit_code"])
        self.assertTrue(last["passed"])
        self.assertTrue(report["cleaned_up"])
        self.assertEqual(before, self.snapshot())

    def test_keep_and_new_tokens(self):
        self.configure(self.reader("reader"))
        notes = set()
        tokens = set()
        for _ in range(2):
            run = self.invoke("--keep", "--json")
            self.assertEqual(run.returncode, 0, run.stderr)
            report = json.loads(run.stdout)
            self.assertFalse(report["cleaned_up"])
            note = self.memory / report["note"]
            notes.add(note)
            body = note.read_text()
            self.assertIn(date.today().isoformat(), body)
            fields = dict(line.split(": ", 1) for line in body.splitlines() if ": " in line)
            self.assertEqual(fields["Source"], report["note"])
            for key in ("Decision", "Rationale"):
                self.assertGreaterEqual(len(fields[key]), 8)
                tokens.add(fields[key])
        self.assertEqual(len(tokens), 4)
        self.assertEqual(notes, set((self.memory / "daily").iterdir()))
        self.assertEqual(len(notes), 2)

    def test_config_errors_plant_nothing(self):
        before = self.snapshot()
        for config in [None, "{", "[]", '{"harnesses":[]}',
                       json.dumps({"harnesses": [self.reader("first"), {"name": "bad"}]}),
                       json.dumps({"timeout_seconds": 0, "harnesses": [self.reader("bad")]})]:
            with self.subTest(config=config):
                if config is not None:
                    self.config.write_text(config)
                run = self.invoke("--keep", "--json")
                self.assertEqual(run.returncode, 2, run.stderr)
                self.assertFalse(self.log.exists())
                self.assertEqual(before, self.snapshot())
        self.configure(self.reader("reader"))
        missing = self.root / "nonexistent"
        run = self.invoke("--memory-dir", str(missing))
        self.assertEqual(run.returncode, 2)
        self.assertFalse(missing.exists())

    @unittest.skipUnless(os.name == "posix", "requires POSIX signals")
    def test_interrupt_removes_note(self):
        before = self.snapshot()
        self.configure({"name": "interrupt", "command": [sys.executable, "-c",
                        "import os, signal, time; os.kill(os.getppid(), signal.SIGINT); time.sleep(30)"]})
        run = self.invoke("--json")
        self.assertEqual(run.returncode, 128 + signal.SIGINT, run.stderr)
        self.assertEqual(before, self.snapshot())

    def test_daily_symlink_is_rejected(self):
        target = self.root / "elsewhere"
        target.mkdir()
        (self.memory / "daily").symlink_to(target, target_is_directory=True)
        self.configure(self.reader("reader"))
        self.assertEqual(self.invoke("--keep").returncode, 2)
        self.assertEqual(list(target.iterdir()), [])
        self.assertTrue((self.memory / "daily").is_symlink())

    @unittest.skipUnless(os.name == "posix", "uses executable stand-ins on PATH")
    def test_example_commands_pass_paths_and_prompt_as_data(self):
        quoted = self.root / 'shared "notes" {memory_dir}'
        self.memory.rename(quoted)
        self.config.write_bytes(SCRIPT.with_name("harnesses.example.json").read_bytes())
        shim = """
import json, os, sys
from pathlib import Path

root = Path(os.environ["TEST_MEMORY"]).resolve()
args = sys.argv[1:]
assert args[-2] == "--" and str(root) in args[-1]
if Path(sys.argv[0]).name == "codex":
    prefix = "permissions.handoff.workspace_roots={"
    assert prefix + json.dumps(str(root), ensure_ascii=False) + "=true}" in args
else:
    assert args[args.index("--add-dir") + 1] == str(root)
note, = (root / "daily").glob("handoff-test-*.md")
print(note.read_text())
"""
        for name in ("codex", "claude"):
            executable = self.root / name
            executable.write_text(f"#!{sys.executable}\n" + shim)
            executable.chmod(0o700)
        env = dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ["PATH"],
                   TEST_MEMORY=str(quoted))
        run = subprocess.run(self.cli + ["--memory-dir", str(quoted), "--json"],
                             capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(run.returncode, 0, (run.stdout, run.stderr))
        self.assertEqual([r["harness"] for r in json.loads(run.stdout)["results"]],
                         ["codex", "claude"])
        self.assertEqual(list(quoted.iterdir()), [quoted / "existing.md"])


if __name__ == "__main__":
    unittest.main()
