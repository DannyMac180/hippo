"""V3 safety scanner: inspect working tree and full git history for secrets and canaries."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys


# Construct sensitive prefix regexes without embedding literal token prefixes in source.
SECRET_PATTERNS = [
    ('anthropic_key', re.compile(r'\bsk' + r'-ant-[A-Za-z0-9_\-]{16,}')),
    ('openai_key', re.compile(r'\bsk' + r'-(?:proj-|live-|test-)?[A-Za-z0-9_\-]{20,}')),
    ('github_pat', re.compile(r'\b(?:gh' + r'p_|gh' + r'o_|github_' + r'pat_)[A-Za-z0-9_]{20,}')),
    ('aws_access_key', re.compile(r'\bAK' + r'IA[0-9A-Z]{16}\b')),
    ('private_key', re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----')),
    ('slack_token', re.compile(r'\bxox' + r'[baprs]-[0-9A-Za-z\-]{10,}')),
]

PERSONAL_PATH_PATTERNS = [
    ('macos_home_path', re.compile(r'/' + r'Users/[A-Za-z0-9._-]+(?:/|\b)')),
    ('linux_home_path', re.compile(r'/' + r'home/[A-Za-z0-9._-]+(?:/|\b)')),
    ('windows_home_path', re.compile(r'[A-Za-z]:\\' + r'Users\\[A-Za-z0-9._-]+\\')),
]

EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,})\b')
ALLOWED_EMAIL_DOMAINS = frozenset({
    'example.com',
    'example.org',
    'example.net',
    'users.noreply.github.com',
})


def load_canaries(path):
    items = []
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            items.append(stripped)
    return items


def _scan_text(text, source_label, canaries, ignore_case=True):
    hits = []
    for line_no, line in enumerate(text.splitlines(), 1):
        for name, pattern in SECRET_PATTERNS:
            if pattern.search(line):
                hits.append({
                    'kind': 'secret',
                    'rule': name,
                    'location': source_label,
                    'line': line_no,
                })
        for name, pattern in PERSONAL_PATH_PATTERNS:
            if pattern.search(line):
                hits.append({
                    'kind': 'personal_path',
                    'rule': name,
                    'location': source_label,
                    'line': line_no,
                })
        for match in EMAIL_RE.finditer(line):
            domain = match.group(1).lower()
            if domain not in ALLOWED_EMAIL_DOMAINS:
                hits.append({
                    'kind': 'email',
                    'rule': 'non_example_email',
                    'location': source_label,
                    'line': line_no,
                })
        haystack = line.lower() if ignore_case else line
        for canary in canaries:
            needle = canary.lower() if ignore_case else canary
            if needle and needle in haystack:
                hits.append({
                    'kind': 'canary',
                    'rule': 'canary_match',
                    'location': source_label,
                    'line': line_no,
                })
    return hits


def _is_binary(raw):
    return b'\x00' in raw[:8192]


def scan_working_tree(repo_dir, canaries, ignore_case=True):
    root = Path(repo_dir).resolve()
    hits = []
    files_scanned = 0
    for directory, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ('.git', '__pycache__')]
        base = Path(directory)
        for name in sorted(files):
            path = base / name
            if path.is_symlink() or not path.is_file():
                continue
            raw = path.read_bytes()
            if _is_binary(raw):
                continue
            files_scanned += 1
            rel = path.relative_to(root).as_posix()
            text = raw.decode('utf-8', errors='replace')
            hits.extend(_scan_text(text, f'worktree:{rel}', canaries, ignore_case=ignore_case))
    return hits, files_scanned


def scan_git_history(repo_dir, canaries, ignore_case=True):
    root = Path(repo_dir).resolve()
    if not (root / '.git').exists():
        return [], 0, 0

    hits = []
    # Scan commit messages and author metadata across all refs
    log_proc = subprocess.run(
        ['git', 'log', '--all', '--format=%H %an <%ae> %s%n%b%n---COMMIT-END---'],
        cwd=root, capture_output=True, text=True, check=True,
    )
    commits_scanned = log_proc.stdout.count('---COMMIT-END---')
    hits.extend(_scan_text(log_proc.stdout, 'git:commit-metadata', canaries, ignore_case=ignore_case))

    # Scan every blob reachable from every ref
    obj_proc = subprocess.run(
        ['git', 'rev-list', '--all', '--objects'],
        cwd=root, capture_output=True, text=True, check=True,
    )
    blobs_scanned = 0
    seen_oids = set()
    for line in obj_proc.stdout.splitlines():
        parts = line.split(' ', 1)
        oid = parts[0].strip()
        label = parts[1].strip() if len(parts) > 1 else oid
        if not oid or oid in seen_oids:
            continue
        seen_oids.add(oid)
        type_proc = subprocess.run(
            ['git', 'cat-file', '-t', oid],
            cwd=root, capture_output=True, text=True, check=True,
        )
        if type_proc.stdout.strip() != 'blob':
            continue
        blob_proc = subprocess.run(
            ['git', 'cat-file', '-p', oid],
            cwd=root, capture_output=True, check=True,
        )
        raw = blob_proc.stdout
        if _is_binary(raw):
            continue
        blobs_scanned += 1
        text = raw.decode('utf-8', errors='replace')
        hits.extend(_scan_text(text, f'git:{oid[:10]}:{label}', canaries, ignore_case=ignore_case))

    return hits, commits_scanned, blobs_scanned


def scan_repo(repo_dir, *, canaries=(), ignore_case=True):
    wt_hits, files_scanned = scan_working_tree(repo_dir, canaries, ignore_case=ignore_case)
    git_hits, commits_scanned, blobs_scanned = scan_git_history(
        repo_dir, canaries, ignore_case=ignore_case
    )
    all_hits = wt_hits + git_hits
    return {
        'repo_dir': str(Path(repo_dir).resolve()),
        'passed': len(all_hits) == 0,
        'files_scanned': files_scanned,
        'commits_scanned': commits_scanned,
        'blobs_scanned': blobs_scanned,
        'hit_count': len(all_hits),
        'hits': all_hits,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repo_dir', help='Directory or git repository to scan.')
    parser.add_argument('--canaries', type=Path, default=None,
                        help='File containing canary strings (one per line).')
    parser.add_argument('--canary', action='append', default=[],
                        help='Literal canary string to flag (repeatable).')
    parser.add_argument('--case-sensitive', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()

    repo = Path(args.repo_dir)
    if not repo.is_dir():
        parser.error(f'Expected a directory: {repo}')

    canaries = list(args.canary)
    if args.canaries is not None:
        try:
            canaries.extend(load_canaries(args.canaries))
        except (OSError, UnicodeError) as error:
            parser.error(str(error))

    try:
        report = scan_repo(repo, canaries=canaries, ignore_case=not args.case_sensitive)
    except (OSError, subprocess.CalledProcessError) as error:
        parser.error(str(error))

    if args.json:
        print(json.dumps(report))
    else:
        status = 'PASS' if report['passed'] else 'FAIL'
        print(f"V3 scan {status}: {report['hit_count']} hit(s) across "
              f"{report['files_scanned']} files, {report['commits_scanned']} commits, "
              f"{report['blobs_scanned']} git blobs.")
        for hit in report['hits']:
            print(f"  - [{hit['kind']}/{hit['rule']}] {hit['location']}:{hit['line']}")
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
