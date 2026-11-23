#!/usr/bin/env python3
"""hippo command-line launcher.

Commands:
  setup TARGET   Set up a memory folder and harness stanzas, and remember the choices.
  update         Move this install to the newest release and re-apply the saved setup.
  version        Print the installed version.
  lint           Run memlint --all on the configured memory folder.
  dream-check    Run the zero-token dream trigger check on the configured memory folder.

Install by cloning the repository (git is the update channel):

  git clone https://github.com/DannyMac180/hippo.git ~/.hippo
  python3 ~/.hippo/hippo.py setup "obsidian://open?vault=MyVault" --harness claude

`hippo update` never touches your notes: it re-runs the non-destructive setup, which only
rewrites the block between the hippo markers in each harness file and adds missing
scaffold files.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parent
SETUP_SCRIPT = REPO_ROOT / 'examples/setup_hippo.py'
MEMLINT_SCRIPT = REPO_ROOT / 'memlint/memlint.py'
DREAM_CHECK_SCRIPT = REPO_ROOT / 'dream/dream_check.py'
CHANNELS = ('release', 'main')


# ---------------------------------------------------------------- config

def config_path(home=None):
    override = os.environ.get('HIPPO_CONFIG')
    if override:
        return Path(override).expanduser()
    if home is None:
        xdg = os.environ.get('XDG_CONFIG_HOME')
        if xdg:
            return Path(xdg).expanduser() / 'hippo/config.json'
    base = Path(home).expanduser() if home is not None else Path.home()
    return base / '.config/hippo/config.json'


def load_config(home=None):
    path = config_path(home)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding='utf-8'))


def save_config(data, home=None):
    path = config_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return path


# ---------------------------------------------------------------- git helpers

def git(*args, check=True):
    proc = subprocess.run(['git', '-C', str(REPO_ROOT), *args],
                          capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


def rev(ref):
    return git('rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}').stdout.strip()


def is_ancestor(older, newer):
    return git('merge-base', '--is-ancestor', older, newer, check=False).returncode == 0


def describe(ref='HEAD'):
    proc = git('describe', '--tags', '--always', ref, check=False)
    return proc.stdout.strip() or 'unknown'


def parse_tag(tag):
    parts = tag[1:].split('.') if tag.startswith('v') else []
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def latest_release_tag():
    tags = git('tag', '-l', 'v*').stdout.split()
    versions = [(parse_tag(t), t) for t in tags if parse_tag(t) is not None]
    return max(versions)[1] if versions else None


# ---------------------------------------------------------------- commands

def _link_bin(home):
    if os.name == 'nt':
        return None, 'skipped (Windows: run python hippo.py directly)'
    bin_dir = (Path(home).expanduser() if home is not None else Path.home()) / '.local/bin'
    link = bin_dir / 'hippo'
    target = REPO_ROOT / 'hippo.py'
    if link.is_symlink() and link.resolve() == target:
        return link, 'unchanged'
    if link.exists() or link.is_symlink():
        return link, 'skipped (a different file already exists there)'
    bin_dir.mkdir(parents=True, exist_ok=True)
    try:
        target.chmod(target.stat().st_mode | 0o111)
    except OSError:
        pass
    link.symlink_to(target)
    return link, 'created'


def cmd_setup(args):
    if args.from_config:
        cfg = load_config(args.home)
        if cfg is None:
            print(f'No saved setup at {config_path(args.home)}; run `hippo setup TARGET` first.',
                  file=sys.stderr)
            return 1
        memory_dir = cfg['memory_dir']
        harnesses = cfg.get('harnesses', [])
        profile = cfg.get('profile', 'pointer')
        home = cfg.get('home')
    else:
        if not args.target:
            print('hippo setup: TARGET is required (or pass --from-config).', file=sys.stderr)
            return 2
        sys.path.insert(0, str(REPO_ROOT / 'examples'))
        import setup_hippo  # noqa: E402
        try:
            resolved = setup_hippo.resolve_memory_target(
                args.target, obsidian_config=args.obsidian_config,
                default_subdir=args.subdir, home=args.home,
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(f'hippo setup: {error}', file=sys.stderr)
            return 2
        memory_dir = str(resolved)
        harnesses = list(dict.fromkeys(args.harness))
        profile = args.profile
        home = str(Path(args.home).expanduser()) if args.home is not None else None

    cmd = [sys.executable, str(SETUP_SCRIPT), memory_dir, '--profile', profile]
    for name in harnesses:
        cmd += ['--harness', name]
    if home is not None:
        cmd += ['--home', home]
    if args.dry_run:
        cmd.append('--dry-run')
    proc = subprocess.run(cmd)
    if proc.returncode != 0 or args.dry_run:
        return proc.returncode

    existing = load_config(home) or {}
    existing.update({'memory_dir': memory_dir, 'harnesses': harnesses,
                     'profile': profile, 'home': home})
    path = save_config(existing, home)
    print(f'Saved setup to {path} (used by `hippo update`).')

    if not args.no_bin:
        link, status = _link_bin(home)
        if link is not None:
            print(f'Command link {link}: {status}')
            path_dirs = os.environ.get('PATH', '').split(os.pathsep)
            if status == 'created' and str(link.parent) not in path_dirs:
                print(f'Add {link.parent} to your PATH to run `hippo` directly.')
        else:
            print(f'Command link: {status}')
    return 0


def cmd_version(_args):
    print(f'hippo {describe()}')
    return 0


def cmd_update(args):
    if not (REPO_ROOT / '.git').exists():
        print(f'{REPO_ROOT} is not a git clone, so it cannot self-update.\n'
              'Reinstall with: git clone https://github.com/DannyMac180/hippo.git', file=sys.stderr)
        return 1
    try:
        dirty = git('status', '--porcelain', '--untracked-files=no').stdout.strip()
        if dirty and not args.check:
            print('Local changes to hippo itself would be overwritten; commit or stash them first:\n'
                  + dirty, file=sys.stderr)
            return 1

        git('fetch', '--quiet', '--tags', '--force', 'origin')
        old = rev('HEAD')
        old_label = describe()

        if args.to:
            target_ref = args.to
        elif args.channel == 'main':
            target_ref = 'origin/main'
        else:
            target_ref = latest_release_tag()
            if target_ref is None:
                print('No releases published yet. Use `hippo update --channel main` to follow main.',
                      file=sys.stderr)
                return 1
        new = rev(target_ref)
        if not new:
            print(f'Unknown version: {target_ref}', file=sys.stderr)
            return 1

        if new == old:
            print(f'hippo is up to date ({old_label}).')
            return 0
        if not args.to and not is_ancestor(old, new):
            if is_ancestor(new, old):
                print(f'Installed {old_label} is newer than {target_ref}; nothing to do. '
                      f'Use --to {target_ref} to switch anyway.')
                return 0
            print(f'Installed {old_label} has diverged from {target_ref}. '
                  f'Use --to {target_ref} to switch anyway.', file=sys.stderr)
            return 1

        if args.check:
            print(f'Update available: {old_label} -> {target_ref}. Run `hippo update`.')
            return 0

        git('checkout', '--quiet', '--detach', new)
        print(f'Updated hippo {old_label} -> {describe()}.')
        log = git('log', '--oneline', '--no-decorate', '-n', '20', f'{old}..{new}', check=False)
        if log.stdout.strip():
            print('Changes:')
            for line in log.stdout.strip().splitlines():
                print(f'  {line}')
    except RuntimeError as error:
        print(f'hippo update: {error}', file=sys.stderr)
        return 1

    if args.no_reapply:
        return 0
    if load_config(args.home) is None:
        print('No saved setup found; run `hippo setup TARGET` to install harness stanzas.')
        return 0
    # Run the *new* code, not the already-imported old module.
    reapply = [sys.executable, str(REPO_ROOT / 'hippo.py'), 'setup', '--from-config', '--no-bin']
    if args.home is not None:
        reapply += ['--home', str(args.home)]
    return subprocess.run(reapply).returncode


def _memory_dir_or_error(args):
    if args.memory_dir:
        return args.memory_dir
    cfg = load_config(args.home)
    if cfg is None:
        print('No memory folder given and no saved setup; pass a folder path.', file=sys.stderr)
        return None
    return cfg['memory_dir']


def cmd_lint(args):
    memory_dir = _memory_dir_or_error(args)
    if memory_dir is None:
        return 2
    return subprocess.run([sys.executable, str(MEMLINT_SCRIPT), memory_dir, '--all']).returncode


def cmd_dream_check(args):
    memory_dir = _memory_dir_or_error(args)
    if memory_dir is None:
        return 2
    return subprocess.run([sys.executable, str(DREAM_CHECK_SCRIPT), memory_dir]).returncode


def build_parser():
    parser = argparse.ArgumentParser(prog='hippo', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)

    def add_home(p):
        p.add_argument('--home', type=Path, default=None,
                       help='Override the home directory (for testing).')

    p = sub.add_parser('setup', help='Set up a memory folder and harness stanzas.')
    p.add_argument('target', nargs='?',
                   help='Memory folder path, file:// URL, or obsidian://open?vault=... URI.')
    p.add_argument('--harness', action='append', default=[],
                   choices=['claude', 'codex', 'antigravity', 'amp', 'all'])
    p.add_argument('--profile', choices=['pointer', 'read-first', 'inject'], default='pointer')
    p.add_argument('--subdir', default='Agents')
    p.add_argument('--obsidian-config', type=Path, default=None)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--no-bin', action='store_true',
                   help='Do not link the hippo command into ~/.local/bin.')
    p.add_argument('--from-config', action='store_true',
                   help='Re-apply the saved setup (used by `hippo update`).')
    add_home(p)
    p.set_defaults(func=cmd_setup)

    p = sub.add_parser('update', help='Update to the newest release and re-apply setup.')
    p.add_argument('--check', action='store_true', help='Only report whether an update exists.')
    p.add_argument('--to', metavar='TAG', help='Move to a specific version (also rolls back).')
    p.add_argument('--channel', choices=CHANNELS, default='release',
                   help='release = newest vX.Y.Z tag (default); main = latest main.')
    p.add_argument('--no-reapply', action='store_true',
                   help='Update the code only; do not re-run setup.')
    add_home(p)
    p.set_defaults(func=cmd_update)

    p = sub.add_parser('version', help='Print the installed version.')
    p.set_defaults(func=cmd_version)

    for name, func, help_text in (('lint', cmd_lint, 'Run memlint --all.'),
                                  ('dream-check', cmd_dream_check, 'Run the dream trigger check.')):
        p = sub.add_parser(name, help=help_text)
        p.add_argument('memory_dir', nargs='?')
        add_home(p)
        p.set_defaults(func=func)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
