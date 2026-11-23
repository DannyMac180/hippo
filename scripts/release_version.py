"""Compute the next hippo release tag from existing git tags and merged-PR labels.

Versions come only from `vMAJOR.MINOR.PATCH` git tags (no version file is committed).
Labels on the merged pull request choose the bump:

- `release:major` -> next major
- `release:minor` -> next minor
- `release:skip`  -> no release
- anything else   -> next patch

With no release tags yet, the first release is `v0.1.0` (`v1.0.0` for `release:major`).
Prints the new tag, or nothing when no release should be made (skip label, or HEAD
already carries a release tag).
"""

import argparse
from pathlib import Path
import re
import subprocess
import sys


TAG_RE = re.compile(r'^v(\d+)\.(\d+)\.(\d+)$')


def parse_tag(tag):
    match = TAG_RE.match(tag.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def latest_version(tags):
    versions = [v for v in (parse_tag(t) for t in tags) if v is not None]
    return max(versions) if versions else None


def parse_labels(raw):
    if not raw:
        return set()
    return {label.strip().lower() for label in raw.split(',') if label.strip()}


def bump_kind(labels):
    labels = set(labels)
    if 'release:skip' in labels:
        return None
    if 'release:major' in labels:
        return 'major'
    if 'release:minor' in labels:
        return 'minor'
    return 'patch'


def next_tag(tags, labels):
    kind = bump_kind(labels)
    if kind is None:
        return None
    current = latest_version(tags)
    if current is None:
        return 'v1.0.0' if kind == 'major' else 'v0.1.0'
    major, minor, patch = current
    if kind == 'major':
        return f'v{major + 1}.0.0'
    if kind == 'minor':
        return f'v{major}.{minor + 1}.0'
    return f'v{major}.{minor}.{patch + 1}'


def _git_lines(repo, *args):
    proc = subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True, check=True)
    return [line for line in proc.stdout.splitlines() if line.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--labels', default='',
                        help='Comma-separated labels from the merged pull request.')
    parser.add_argument('--repo', type=Path, default=Path.cwd(),
                        help='Git repository to read tags from (default: current directory).')
    args = parser.parse_args(argv)

    head_tags = _git_lines(args.repo, 'tag', '--points-at', 'HEAD')
    if any(parse_tag(t) for t in head_tags):
        return 0  # HEAD is already released; print nothing.

    tag = next_tag(_git_lines(args.repo, 'tag', '-l', 'v*'), parse_labels(args.labels))
    if tag:
        print(tag)
    return 0


if __name__ == '__main__':
    sys.exit(main())
