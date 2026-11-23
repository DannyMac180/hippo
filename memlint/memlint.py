"""Read-only checks for a folder of Markdown memory notes."""

import argparse
from collections import defaultdict
from datetime import date
import json
import os
from pathlib import Path
import posixpath
import re
import sys


CHECKS = ('D1', 'D2', 'L1', 'I1')
EXTRA_CHECKS = ('B1', 'B2', 'S1', 'C1')
ALL_CHECKS = CHECKS + EXTRA_CHECKS
ROOTS = {'README.md', 'agent/index.md'}
DECISIONS = 'agent/decisions.md'
INSTRUCTIONS = 'AGENTS.md'
INDEX = 'agent/index.md'
DREAM_LOG = 'agent/dream-log.md'
LOG_PREFIXES = ('daily/', 'runs/')
DEFAULT_EXEMPT_PREFIXES = ('templates/',)
PAGE_EXEMPT_PREFIXES = ('templates/', 'daily/', 'runs/', 'proposals/')
DEFAULT_MAX_LINES = 200
DEFAULT_MAX_BYTES = 25000
DEFAULT_MAX_STANZA_LINES = 10

FENCE = re.compile(r' {0,3}(`{3,}|~{3,})(.*)')
HEADING = re.compile(r' {0,3}#{1,6}(?:\s|$)')
DATE = re.compile(r'## ([0-9]{4}-[0-9]{2}-[0-9]{2}):')
LINK = re.compile(r'\[\[([^\[\]\n]+)\]\]')
URL = re.compile(r'https?://[^\s<>`]+', re.IGNORECASE)
CONFLICT = re.compile(r'(?:^|[\s>*\-(\[])CONFLICT\s*:')
REVIEW_AFTER = re.compile(
    r'^(?:review_after|review after)\s*:\s*["\']?([0-9]{4}-[0-9]{2}-[0-9]{2})["\']?(?:\s|$)',
    re.IGNORECASE,
)
READ_PAGES = re.compile(r'^(?:pages?\s+read|read)\s*:(.*)$', re.IGNORECASE)


def inline_spans(lines):
    """Pair equal-length backtick runs, including spans continued on another line."""
    runs = [(row, match) for row, line in enumerate(lines)
            for match in re.finditer(r'`+', line)]
    following = {}
    last = {}
    for i in reversed(range(len(runs))):
        width = len(runs[i][1][0])
        if width in last:
            following[i] = last[width]
        last[width] = i

    visible = [list(line) for line in lines]
    code = defaultdict(list)
    i = 0
    while i < len(runs):
        if i not in following:
            i += 1
            continue
        first, opening = runs[i]
        last_row, closing = runs[following[i]]
        if first == last_row:
            code[first].append(lines[first][opening.end():closing.start()].strip())
        for row in range(first, last_row + 1):
            start = opening.start() if row == first else 0
            end = closing.end() if row == last_row else len(lines[row])
            visible[row][start:end] = ' ' * (end - start)
        i = following[i] + 1
    for row, line in enumerate(visible):
        yield ''.join(line), code.get(row, ())


def markdown_lines(lines):
    """Yield line numbers, visible Markdown and single-line code spans."""
    paragraph = []
    fence = ''
    for number, line in enumerate(lines, 1):
        marker = FENCE.fullmatch(line)
        if fence:
            if marker and marker[1].startswith(fence) and not marker[2].strip():
                fence = ''
            continue
        opens = marker and (marker[1][0] == '~' or '`' not in marker[2])
        heading = HEADING.match(line)
        if paragraph and (opens or heading or not line.strip()):
            yield from enumerate(inline_spans(paragraph), number - len(paragraph))
            paragraph.clear()
        if opens:
            fence = marker[1]
        elif heading:
            yield number, next(inline_spans([line]))
        elif line.strip():
            paragraph.append(line)
    if paragraph:
        yield from enumerate(inline_spans(paragraph), number - len(paragraph) + 1)


def resolve(target, source, notes, by_name):
    if not target or posixpath.isabs(target):
        return ()
    name = target if target.endswith('.md') else target + '.md'
    for base in (posixpath.dirname(source), ''):
        candidate = posixpath.normpath(posixpath.join(base, name))
        if candidate in notes:
            return (candidate,)
    return by_name.get(name, ())


def dated(heading):
    match = DATE.match(heading)
    if match:
        try:
            date.fromisoformat(match[1])
            return True
        except ValueError:
            pass
    return False


def is_path(span):
    # Allow directory paths (including spaces) and bare filenames.
    return bool(span and ('/' in span or re.fullmatch(r'[\w.~-]+(?: [\w~-]+)*\.\w+|[\w.~-]+', span)))


def normalize_relpath(value):
    return posixpath.normpath(value.strip().replace('\\', '/')).lstrip('./')


def normalize_prefix(value):
    cleaned = value.strip().replace('\\', '/').lstrip('./')
    if cleaned and not cleaned.endswith('/'):
        cleaned += '/'
    return cleaned


def collect_notes(folder):
    folder = Path(folder)
    notes = {}

    def raise_error(error):
        raise error

    for directory, dirs, files in os.walk(folder, onerror=raise_error):
        base = Path(directory)
        dirs[:] = [name for name in dirs if not (base / name).is_symlink()]
        for name in files:
            path = base / name
            if name.endswith('.md') and not path.is_symlink() and path.is_file():
                notes[path.relative_to(folder).as_posix()] = path
    return notes


def load_allowlist(path):
    targets = set()
    for line in Path(path).read_text(encoding='utf-8-sig').splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            targets.add(stripped)
    return targets


def _resolve_local_file(span, source, folder):
    if not span or posixpath.isabs(span) or URL.match(span):
        return None
    root = Path(folder).resolve()
    candidates = []
    for base in (posixpath.dirname(source), ''):
        rel = posixpath.normpath(posixpath.join(base, span))
        if rel.startswith('..') or rel == '.':
            continue
        candidates.append(rel)
    for rel in candidates:
        candidate = root / rel
        if candidate.is_symlink() or not candidate.is_file():
            continue
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        return rel, resolved
    return None


def measure_budget(folder, instructions=INSTRUCTIONS, index=INDEX,
                   max_lines=DEFAULT_MAX_LINES, max_bytes=DEFAULT_MAX_BYTES):
    root = Path(folder).resolve()
    targets = []
    for rel in (instructions, index):
        if not rel:
            continue
        norm = normalize_relpath(rel)
        if norm and norm not in targets:
            targets.append(norm)

    files = {}
    total_lines = 0
    total_bytes = 0
    for rel in targets:
        path = root / rel
        if path.is_symlink() or not path.is_file():
            continue
        raw = path.read_bytes()
        text = raw.decode('utf-8-sig')
        line_count = len(text.splitlines())
        byte_count = len(raw)
        files[rel] = {'lines': line_count, 'bytes': byte_count}
        total_lines += line_count
        total_bytes += byte_count

    line_ratio = (total_lines / max_lines) if max_lines > 0 else 0.0
    byte_ratio = (total_bytes / max_bytes) if max_bytes > 0 else 0.0
    return {
        'files': files,
        'lines': total_lines,
        'bytes': total_bytes,
        'max_lines': max_lines,
        'max_bytes': max_bytes,
        'line_ratio': round(line_ratio, 4),
        'byte_ratio': round(byte_ratio, 4),
        'exceeded': total_lines > max_lines or total_bytes > max_bytes,
    }


def measure_stanza(stanza_path, max_lines=DEFAULT_MAX_STANZA_LINES):
    raw = Path(stanza_path).read_bytes()
    lines = raw.decode('utf-8-sig').splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    return {
        'file': str(stanza_path),
        'lines': len(lines),
        'bytes': len(raw),
        'max_lines': max_lines,
        'exceeded': len(lines) > max_lines,
    }


def find_conflicts(folder, notes=None, exempt_prefixes=('templates/', 'proposals/')):
    notes = collect_notes(folder) if notes is None else notes
    hits = []
    for note, path in sorted(notes.items()):
        if any(note.startswith(prefix) for prefix in exempt_prefixes):
            continue
        lines = path.read_text(encoding='utf-8-sig').splitlines()
        for number, (line, _spans) in markdown_lines(lines):
            if CONFLICT.search(line):
                hits.append({'file': note, 'line': number, 'text': lines[number - 1].strip()})
    return hits


def find_review_dates(folder, notes=None, exempt_prefixes=('templates/', 'proposals/')):
    notes = collect_notes(folder) if notes is None else notes
    results = []
    for note, path in sorted(notes.items()):
        if any(note.startswith(prefix) for prefix in exempt_prefixes):
            continue
        lines = path.read_text(encoding='utf-8-sig').splitlines()
        seen_lines = set()
        # Check optional YAML frontmatter first.
        if lines and lines[0].strip() == '---':
            for idx in range(1, len(lines)):
                stripped = lines[idx].strip()
                if stripped in ('---', '...'):
                    break
                match = REVIEW_AFTER.match(stripped)
                if match:
                    try:
                        parsed = date.fromisoformat(match[1])
                    except ValueError:
                        continue
                    seen_lines.add(idx + 1)
                    results.append({'file': note, 'line': idx + 1, 'review_after': parsed.isoformat()})
        # Check visible Markdown lines outside code fences/spans.
        for number, (line, _spans) in markdown_lines(lines):
            if number in seen_lines:
                continue
            match = REVIEW_AFTER.match(line.strip())
            if match:
                try:
                    parsed = date.fromisoformat(match[1])
                except ValueError:
                    continue
                results.append({'file': note, 'line': number, 'review_after': parsed.isoformat()})
    return results


def extract_read_pages(folder, notes=None, log_prefixes=LOG_PREFIXES):
    """Count self-reported pages read from session/run notes (Read: / Pages read:)."""
    notes = collect_notes(folder) if notes is None else notes
    by_name = defaultdict(list)
    for note in notes:
        by_name[posixpath.basename(note)].append(note)
    counts = defaultdict(int)
    for note, path in notes.items():
        if not any(note.startswith(prefix) for prefix in log_prefixes):
            continue
        lines = path.read_text(encoding='utf-8-sig').splitlines()
        for _number, (line, spans) in markdown_lines(lines):
            match = READ_PAGES.match(line.strip())
            if not match:
                continue
            rest = match[1]
            for link in LINK.finditer(rest):
                destination = link[1].split('|', 1)[0].strip()
                target = destination.split('#', 1)[0].strip()
                for resolved in resolve(target, note, notes, by_name):
                    counts[resolved] += 1
            for span in spans:
                resolved = _resolve_local_file(span, note, folder)
                if resolved is not None and resolved[0] in notes:
                    counts[resolved[0]] += 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def check_page_sizes(folder, notes=None, roots=ROOTS, instructions=INSTRUCTIONS,
                     index=INDEX, decisions=DECISIONS, dream_log=DREAM_LOG,
                     exempt_prefixes=PAGE_EXEMPT_PREFIXES):
    folder = Path(folder)
    notes = collect_notes(folder) if notes is None else notes
    by_name = defaultdict(list)
    for note in notes:
        by_name[posixpath.basename(note)].append(note)

    structural = {normalize_relpath(p) for p in roots if p}
    for extra in (instructions, index, decisions, dream_log):
        if extra:
            structural.add(normalize_relpath(extra))

    findings = []
    for note, path in sorted(notes.items()):
        if note in structural or any(note.startswith(prefix) for prefix in exempt_prefixes):
            continue
        lines = path.read_text(encoding='utf-8-sig').splitlines()
        sources = {}
        for _number, (line, spans) in markdown_lines(lines):
            for link in LINK.finditer(line):
                destination = link[1].split('|', 1)[0].strip()
                target = destination.split('#', 1)[0].strip()
                if destination.startswith('#'):
                    continue
                for match in resolve(target, note, notes, by_name):
                    if match != note:
                        sources[match] = notes[match]
            for span in spans:
                resolved = _resolve_local_file(span, note, folder)
                if resolved is not None and resolved[0] != note:
                    sources[resolved[0]] = resolved[1]
        if not sources:
            continue
        page_bytes = path.stat().st_size
        source_bytes = sum(src.stat().st_size for src in sources.values())
        if page_bytes >= source_bytes:
            findings.append({
                'check': 'S1',
                'file': note,
                'line': None,
                'message': (
                    f'Page ({page_bytes} B) is not smaller than its cited local '
                    f'sources ({source_bytes} B across {len(sources)} file(s)).'
                ),
                'page_bytes': page_bytes,
                'source_bytes': source_bytes,
                'sources': sorted(sources),
            })
    return findings


def lint(folder, *, roots=None, decisions=DECISIONS, exempt_prefixes=None,
         allow_broken_links=None, check_budget=False, instructions=INSTRUCTIONS,
         index=INDEX, max_lines=DEFAULT_MAX_LINES, max_bytes=DEFAULT_MAX_BYTES,
         stanza_path=None, max_stanza_lines=DEFAULT_MAX_STANZA_LINES,
         check_pages=False, check_conflicts=False):
    folder = Path(folder)
    notes = collect_notes(folder)
    active_roots = set(ROOTS) if roots is None else {normalize_relpath(r) for r in roots if r}
    decisions_path = normalize_relpath(decisions) if decisions else ''
    prefixes = tuple(DEFAULT_EXEMPT_PREFIXES if exempt_prefixes is None
                     else (normalize_prefix(p) for p in exempt_prefixes if p))
    allowed_broken = set(allow_broken_links or ())

    by_name = defaultdict(list)
    for note in notes:
        by_name[posixpath.basename(note)].append(note)
    edges = defaultdict(set)
    findings = []

    def report(check, note, line, message, **extra):
        findings.append(dict(check=check, file=note, line=line, message=message, **extra))

    for note, path in notes.items():
        pending_source = None
        lines = path.read_text(encoding='utf-8-sig').splitlines()
        for number, (line, spans) in markdown_lines(lines):
            links = list(LINK.finditer(line))
            for link in links:
                destination = link[1].split('|', 1)[0].strip()
                target = destination.split('#', 1)[0].strip()
                matches = (note,) if destination.startswith('#') else resolve(target, note, notes, by_name)
                if matches:
                    edges[note].update(matches)
                elif target not in allowed_broken:
                    report('L1', note, number, f'Unresolved wikilink [[{target}]].', target=target)

            if note == decisions_path:
                if lines[number - 1].startswith('## '):
                    if pending_source is not None:
                        report('D2', note, pending_source, 'Decision needs a Related: or Source: reference.')
                    pending_source = number
                    if not dated(line):
                        report('D1', note, number, 'Decision heading needs a YYYY-MM-DD: date.')
                elif line.startswith(('Related:', 'Source:')):
                    if links or URL.search(line) or any(map(is_path, spans)):
                        pending_source = None
        if pending_source is not None:
            report('D2', note, pending_source, 'Decision needs a Related: or Source: reference.')

    reached = active_roots.intersection(notes)
    pending = list(reached)
    while pending:
        new = edges[pending.pop()] - reached
        reached.update(new)
        pending.extend(new)
    root_label = ' and '.join(sorted(active_roots)) if active_roots else 'roots'
    for note in notes.keys() - reached:
        if not any(note.startswith(prefix) for prefix in prefixes):
            report('I1', note, None, f'Unreachable from {root_label}.')

    if check_budget:
        budget = measure_budget(folder, instructions=instructions, index=index,
                                max_lines=max_lines, max_bytes=max_bytes)
        if budget['exceeded']:
            parts = []
            if budget['lines'] > budget['max_lines']:
                parts.append(f"{budget['lines']} lines > {budget['max_lines']}")
            if budget['bytes'] > budget['max_bytes']:
                parts.append(f"{budget['bytes']} bytes > {budget['max_bytes']}")
            target_file = normalize_relpath(instructions or index or 'AGENTS.md')
            report('B1', target_file, None,
                   f"Injected layer exceeds budget ({', '.join(parts)}).",
                   lines=budget['lines'], bytes=budget['bytes'])

    if stanza_path is not None:
        stanza = measure_stanza(stanza_path, max_lines=max_stanza_lines)
        if stanza['exceeded']:
            report('B2', str(stanza_path), None,
                   f"Pointer stanza exceeds budget ({stanza['lines']} lines > {stanza['max_lines']}).",
                   lines=stanza['lines'])

    if check_pages:
        findings.extend(check_page_sizes(
            folder, notes=notes, roots=active_roots, instructions=instructions,
            index=index, decisions=decisions_path,
        ))

    if check_conflicts:
        for hit in find_conflicts(folder, notes=notes):
            report('C1', hit['file'], hit['line'], f"Unresolved conflict mark: {hit['text']}")

    findings.sort(key=lambda f: (f['file'], f['line'] or 0, f['check']))
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('memory_dir')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--roots', action='append', default=None,
                        help='Root file(s) for reachability (comma-separated or repeatable).')
    parser.add_argument('--decisions', default=DECISIONS,
                        help=f'Decision log path relative to memory_dir (default: {DECISIONS}).')
    parser.add_argument('--instructions', default=INSTRUCTIONS,
                        help=f'Instruction file path relative to memory_dir (default: {INSTRUCTIONS}).')
    parser.add_argument('--index', default=INDEX,
                        help=f'Index file path relative to memory_dir (default: {INDEX}).')
    parser.add_argument('--exempt-prefix', action='append', default=None,
                        help='Directory prefix exempt from reachability (repeatable).')
    parser.add_argument('--exempt-logs', action='store_true',
                        help='Exempt daily/ and runs/ dated logs from reachability (I1).')
    parser.add_argument('--allow-broken-link', action='append', default=[],
                        help='Known benign broken wikilink target to ignore (repeatable).')
    parser.add_argument('--allowlist', type=Path, default=None,
                        help='File listing benign broken wikilink targets, one per line.')
    parser.add_argument('--budget', action='store_true',
                        help='Check injected-layer line and byte budget (B1).')
    parser.add_argument('--max-lines', type=int, default=DEFAULT_MAX_LINES)
    parser.add_argument('--max-bytes', type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument('--stanza', type=Path, default=None,
                        help='Check a pointer stanza file against the stanza line budget (B2).')
    parser.add_argument('--max-stanza-lines', type=int, default=DEFAULT_MAX_STANZA_LINES)
    parser.add_argument('--page-size', action='store_true',
                        help='Check that synthesized pages are smaller than cited local sources (S1).')
    parser.add_argument('--conflicts', action='store_true',
                        help='Report visible CONFLICT: markers (C1).')
    parser.add_argument('--all', action='store_true',
                        help='Enable --exempt-logs, --budget, --page-size, and --conflicts.')
    args = parser.parse_args()

    folder = Path(args.memory_dir)
    if not folder.is_dir():
        parser.error(f'Expected a memory directory: {folder}')
    if args.max_lines <= 0 or args.max_bytes <= 0 or args.max_stanza_lines <= 0:
        parser.error('Budget limits must be positive integers.')

    roots = None
    if args.roots is not None:
        roots = [item.strip() for raw in args.roots for item in raw.split(',') if item.strip()]

    exempt = list(DEFAULT_EXEMPT_PREFIXES) if args.exempt_prefix is None else list(args.exempt_prefix)
    if args.exempt_logs or args.all:
        for prefix in LOG_PREFIXES:
            if prefix not in exempt:
                exempt.append(prefix)

    allow_broken = set(args.allow_broken_link)
    if args.allowlist is not None:
        try:
            allow_broken.update(load_allowlist(args.allowlist))
        except (OSError, UnicodeError) as error:
            parser.error(str(error))

    enable_budget = args.budget or args.all
    enable_pages = args.page_size or args.all
    enable_conflicts = args.conflicts or args.all

    active_checks = list(CHECKS)
    if enable_budget:
        active_checks.append('B1')
    if args.stanza is not None:
        active_checks.append('B2')
    if enable_pages:
        active_checks.append('S1')
    if enable_conflicts:
        active_checks.append('C1')

    try:
        findings = lint(
            folder,
            roots=roots,
            decisions=args.decisions,
            exempt_prefixes=exempt,
            allow_broken_links=allow_broken,
            check_budget=enable_budget,
            instructions=args.instructions,
            index=args.index,
            max_lines=args.max_lines,
            max_bytes=args.max_bytes,
            stanza_path=args.stanza,
            max_stanza_lines=args.max_stanza_lines,
            check_pages=enable_pages,
            check_conflicts=enable_conflicts,
        )
        metrics = {}
        if enable_budget:
            metrics['budget'] = measure_budget(
                folder, instructions=args.instructions, index=args.index,
                max_lines=args.max_lines, max_bytes=args.max_bytes,
            )
        if args.stanza is not None:
            metrics['stanza'] = measure_stanza(args.stanza, max_lines=args.max_stanza_lines)
    except (OSError, UnicodeError) as error:
        parser.error(str(error))

    counts = dict.fromkeys(active_checks, 0)
    for finding in findings:
        counts[finding['check']] += 1

    if args.json:
        payload = dict(memory_dir=args.memory_dir, findings=findings, counts=counts)
        if metrics:
            payload['metrics'] = metrics
        print(json.dumps(payload))
    else:
        for finding in findings:
            location = finding['file']
            if finding['line'] is not None:
                location += f":{finding['line']}"
            print(f"{location} {finding['check']} {finding['message']}")
        totals = ', '.join(f'{check}={n}' for check, n in counts.items())
        print(f'{len(findings)} findings ({totals})')
    return int(bool(findings))


if __name__ == '__main__':
    sys.exit(main())
