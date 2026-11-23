"""Acceptance checks for the `hippo` launcher's update channel and release versioning.

Builds a synthetic upstream (a local bare git repo with release tags) from this checkout,
installs from it into a temp directory, and drives `hippo setup` / `hippo update` with a
temporary home and config. No network, no real harness files, no real vault.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import release_version  # noqa: E402


GIT_ID = ['-c', 'user.name=hippo-test', '-c', 'user.email=hippo@example.org']
CODE_DIRS = ('examples', 'handoff', 'memlint', 'dream')


def git(cwd, *args, check=True):
    return subprocess.run(['git', *GIT_ID, *args], cwd=cwd, capture_output=True,
                          text=True, check=check)


class ReleaseVersionTests(unittest.TestCase):
    def test_first_release_and_bumps(self):
        nt = release_version.next_tag
        self.assertEqual(nt([], set()), 'v0.1.0')
        self.assertEqual(nt([], {'release:major'}), 'v1.0.0')
        tags = ['v0.1.0', 'v0.1.1', 'v0.2.0', 'not-a-version', 'v0.10.0-rc1']
        self.assertEqual(nt(tags, set()), 'v0.2.1')
        self.assertEqual(nt(tags, {'release:minor'}), 'v0.3.0')
        self.assertEqual(nt(tags, {'release:major', 'release:minor'}), 'v1.0.0')
        self.assertIsNone(nt(tags, {'release:skip', 'release:major'}))
        # Numeric, not lexical, ordering
        self.assertEqual(nt(['v0.9.0', 'v0.10.0'], set()), 'v0.10.1')

    def test_label_parsing(self):
        self.assertEqual(release_version.parse_labels(' Release:Minor , docs ,'),
                         {'release:minor', 'docs'})
        self.assertEqual(release_version.parse_labels(''), set())

    def test_cli_prints_nothing_when_head_already_released(self):
        with TemporaryDirectory() as tmp:
            repo = Path(tmp)
            git(repo, 'init', '-q', '-b', 'main')
            (repo / 'a.txt').write_text('a\n', encoding='utf-8')
            git(repo, 'add', '-A')
            git(repo, 'commit', '-q', '-m', 'one')
            script = ROOT / 'scripts/release_version.py'
            run = lambda *a: subprocess.run([sys.executable, str(script), '--repo', str(repo), *a],
                                            capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(run(), 'v0.1.0')
            git(repo, 'tag', 'v0.1.0')
            self.assertEqual(run(), '')
            (repo / 'a.txt').write_text('b\n', encoding='utf-8')
            git(repo, 'commit', '-q', '-am', 'two')
            self.assertEqual(run('--labels', 'release:minor'), 'v0.2.0')
            self.assertEqual(run('--labels', 'release:skip'), '')


class HippoUpdateAcceptance(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.tmp = Path(temp.name)
        self.home = self.tmp / 'home'
        self.home.mkdir()
        self.env = dict(os.environ, HIPPO_CONFIG=str(self.tmp / 'config/hippo.json'))

        # Synthetic upstream: v0.1.0, v0.2.0, then one unreleased commit on main.
        work = self.tmp / 'work'
        work.mkdir()
        shutil.copy2(ROOT / 'hippo.py', work / 'hippo.py')
        ignore = shutil.ignore_patterns('__pycache__', '*.pyc')
        for name in CODE_DIRS:
            shutil.copytree(ROOT / name, work / name, ignore=ignore)
        git(work, 'init', '-q', '-b', 'main')
        git(work, 'add', '-A')
        git(work, 'commit', '-q', '-m', 'Initial release')
        git(work, 'tag', 'v0.1.0')
        (work / 'CHANGES.txt').write_text('second\n', encoding='utf-8')
        git(work, 'add', '-A')
        git(work, 'commit', '-q', '-m', 'Second release')
        git(work, 'tag', 'v0.2.0')
        (work / 'CHANGES.txt').write_text('unreleased\n', encoding='utf-8')
        git(work, 'commit', '-q', '-am', 'Unreleased work on main')
        self.work = work
        self.bare = self.tmp / 'upstream.git'
        git(self.tmp, 'clone', '-q', '--bare', str(work), str(self.bare))

        self.install = self.tmp / 'install'
        git(self.tmp, 'clone', '-q', str(self.bare), str(self.install))
        git(self.install, 'checkout', '-q', '--detach', 'v0.1.0')

        self.memory = self.tmp / 'memory'
        self.memory.mkdir()
        (self.memory / 'mine.md').write_text('# Mine\n\nkeep me\n', encoding='utf-8')
        claude_md = self.home / '.claude/CLAUDE.md'
        claude_md.parent.mkdir(parents=True)
        claude_md.write_text('# My own instructions\n', encoding='utf-8')
        self.claude_md = claude_md

    def hippo(self, *args):
        return subprocess.run([sys.executable, str(self.install / 'hippo.py'), *map(str, args)],
                              capture_output=True, text=True, env=self.env, timeout=60)

    def head(self):
        return git(self.install, 'rev-parse', 'HEAD').stdout.strip()

    def commit_of(self, ref):
        return git(self.work, 'rev-parse', f'{ref}^{{commit}}').stdout.strip()

    def _setup(self):
        res = self.hippo('setup', self.memory, '--harness', 'claude', '--home', self.home)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return res

    def test_setup_saves_config_and_links_command(self):
        res = self._setup()
        cfg = json.loads(Path(self.env['HIPPO_CONFIG']).read_text(encoding='utf-8'))
        self.assertEqual(cfg['memory_dir'], str(self.memory.resolve()))
        self.assertEqual(cfg['harnesses'], ['claude'])
        self.assertEqual(cfg['profile'], 'pointer')
        link = self.home / '.local/bin/hippo'
        self.assertTrue(link.is_symlink(), res.stdout)
        self.assertEqual(link.resolve(), (self.install / 'hippo.py').resolve())
        text = self.claude_md.read_text(encoding='utf-8')
        self.assertIn('<!-- hippo:start -->', text)
        self.assertIn('# My own instructions', text)
        # Setup leaves the install clean so later updates are not blocked.
        status = git(self.install, 'status', '--porcelain', '--untracked-files=no').stdout
        self.assertEqual(status.strip(), '')

    def test_update_moves_to_newest_release_and_reapplies_setup(self):
        self._setup()
        # Simulate a stale stanza: update must refresh the marked block only.
        stale = self.claude_md.read_text(encoding='utf-8').replace(
            str(self.memory.resolve()), '/stale/path')
        self.claude_md.write_text(stale, encoding='utf-8')

        check = self.hippo('update', '--check', '--home', self.home)
        self.assertEqual(check.returncode, 0, check.stderr)
        self.assertIn('Update available', check.stdout)
        self.assertIn('v0.2.0', check.stdout)
        self.assertEqual(self.head(), self.commit_of('v0.1.0'))

        res = self.hippo('update', '--home', self.home)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.head(), self.commit_of('v0.2.0'))
        self.assertIn('Second release', res.stdout)
        self.assertNotIn('Unreleased work', res.stdout)
        text = self.claude_md.read_text(encoding='utf-8')
        self.assertIn(str(self.memory.resolve()), text)
        self.assertNotIn('/stale/path', text)
        self.assertIn('# My own instructions', text)
        self.assertEqual((self.memory / 'mine.md').read_text(encoding='utf-8'),
                         '# Mine\n\nkeep me\n')

        again = self.hippo('update', '--home', self.home)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn('up to date', again.stdout)

        version = self.hippo('version')
        self.assertIn('v0.2.0', version.stdout)

    def test_update_picks_up_a_release_published_after_install(self):
        self._setup()
        self.assertEqual(self.hippo('update', '--home', self.home).returncode, 0)
        git(self.work, 'tag', 'v0.3.0', 'main')
        git(self.work, 'push', '-q', str(self.bare), 'v0.3.0')
        res = self.hippo('update', '--home', self.home)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(self.head(), self.commit_of('v0.3.0'))

    def test_update_refuses_local_changes(self):
        edited = self.install / 'examples/init_memory.py'
        edited.write_text(edited.read_text(encoding='utf-8') + '\n# local edit\n', encoding='utf-8')
        res = self.hippo('update')
        self.assertEqual(res.returncode, 1)
        self.assertIn('Local changes', res.stderr)
        self.assertEqual(self.head(), self.commit_of('v0.1.0'))

    def test_rollback_and_main_channel(self):
        self.assertEqual(self.hippo('update', '--no-reapply').returncode, 0)
        back = self.hippo('update', '--to', 'v0.1.0', '--no-reapply')
        self.assertEqual(back.returncode, 0, back.stderr)
        self.assertEqual(self.head(), self.commit_of('v0.1.0'))
        main = self.hippo('update', '--channel', 'main', '--no-reapply')
        self.assertEqual(main.returncode, 0, main.stderr)
        self.assertEqual(self.head(), self.commit_of('main'))
        # On main, the release channel would be a downgrade: leave the install alone.
        rel = self.hippo('update', '--no-reapply')
        self.assertEqual(rel.returncode, 0, rel.stderr)
        self.assertIn('newer than', rel.stdout)
        self.assertEqual(self.head(), self.commit_of('main'))

    def test_update_without_releases_explains_main_channel(self):
        git(self.bare, 'tag', '-d', 'v0.1.0')
        git(self.bare, 'tag', '-d', 'v0.2.0')
        git(self.install, 'tag', '-d', 'v0.1.0')
        git(self.install, 'tag', '-d', 'v0.2.0')
        res = self.hippo('update', '--no-reapply')
        self.assertEqual(res.returncode, 1)
        self.assertIn('--channel main', res.stderr)


@unittest.skipUnless((ROOT / 'scripts/export_public.py').is_file(), 'exporter not present')
class ExportIntoExistingRepo(unittest.TestCase):
    def test_into_keeps_history_removes_stale_files_and_ships_workflows(self):
        with TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            exporter = ROOT / 'scripts/export_public.py'
            first = tmp / 'first'
            res = subprocess.run([sys.executable, str(exporter), '--out', str(first), '--json'],
                                 capture_output=True, text=True, timeout=120)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            public = tmp / 'public'
            git(tmp, 'clone', '-q', str(first), str(public))
            (public / 'stale.txt').write_text('old\n', encoding='utf-8')
            git(public, 'add', '-A')
            git(public, 'commit', '-q', '-m', 'Stale file')

            res = subprocess.run(
                [sys.executable, str(exporter), '--into', str(public),
                 '--commit', 'Sync from archive', '--json'],
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            data = json.loads(res.stdout)
            self.assertTrue(data['scan']['passed'], data['scan']['hits'])
            self.assertFalse((public / 'stale.txt').exists())
            for rel in ('hippo.py', '.github/workflows/ci.yml', '.github/workflows/release.yml',
                        'scripts/release_version.py'):
                self.assertTrue((public / rel).is_file(), rel)
            self.assertTrue(os.access(public / 'hippo.py', os.X_OK))
            log = git(public, 'log', '--format=%s').stdout.splitlines()
            self.assertEqual(log, ['Sync from archive', 'Stale file', 'Initial public export of hippo'])
            author = git(public, 'log', '-1', '--format=%an <%ae>').stdout.strip()
            self.assertEqual(author, 'hippo-export <hippo@example.org>')

            # Re-running with nothing changed creates no empty commit.
            res = subprocess.run(
                [sys.executable, str(exporter), '--into', str(public),
                 '--commit', 'No-op', '--json'],
                capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            self.assertFalse(json.loads(res.stdout)['committed'])
            self.assertEqual(len(git(public, 'log', '--format=%s').stdout.splitlines()), 3)


if __name__ == '__main__':
    unittest.main()
