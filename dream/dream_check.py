"""Deterministic, read-only trigger checker for dreaming on need.

Evaluates triggers T1-T6 from docs/design-2026-11-21.md §4.5 without making
any model call or modifying any files in the memory folder.
"""

import argparse
from datetime import date, datetime, timezone
import json
from pathlib import Path
import posixpath
import re
import sys


REPO_ROOT = Path(__file__).resolve().parent.parent
MEMLINT_DIR = REPO_ROOT / 'memlint'
if str(MEMLINT_DIR) not in sys.path:
    sys.path.insert(0, str(MEMLINT_DIR))

import memlint  # noqa: E402


DEFAULT_LOG = 'agent/dream-log.md'
DEFAULT_BACKLOG = 10
DEFAULT_UNREACHABLE_DELTA = 5
DEFAULT_BUDGET_RATIO = 0.80
DEFAULT_BACKSTOP_DAYS = 28
DEFAULT_OVERDUE_DAYS = 7

LOG_LINE_RE = re.compile(
    r'^\s*-\s*([0-9]{4}-[0-9]{2}-[0-9]{2}(?:T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-][0-9]{2}:[0-9]{2}))?)\s*\|\s*(.+)$'
)
FILENAME_DATE_RE = re.compile(r'([0-9]{4}-[0-9]{2}-[0-9]{2})')


def _parse_iso_dt(raw):
    text = raw.strip()
    if len(text) == 10:
        d = date.fromisoformat(text)
        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_dream_log(log_path):
    """Parse dream log entries from Markdown bullet lines or JSONL."""
    path = Path(log_path)
    if not path.is_file():
        return []
    entries = []
    for line_no, raw in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            ts_raw = obj.get('timestamp') or obj.get('date')
            if not ts_raw:
                continue
            try:
                ts = _parse_iso_dt(str(ts_raw))
            except ValueError:
                continue
            triggers = [str(t).strip() for t in obj.get('triggers', []) if str(t).strip()]
            unreachable = obj.get('unreachable')
            entries.append({
                'line': line_no,
                'timestamp': ts.isoformat().replace('+00:00', 'Z'),
                'epoch': ts.timestamp(),
                'date': ts.date().isoformat(),
                'triggers': triggers,
                'unreachable': int(unreachable) if isinstance(unreachable, int) else None,
                'proposal': obj.get('proposal'),
                'status': obj.get('status', 'proposed'),
                'valid': bool(triggers),
            })
            continue

        match = LOG_LINE_RE.match(stripped)
        if not match:
            continue
        try:
            ts = _parse_iso_dt(match[1])
        except ValueError:
            continue
        fields = {}
        for part in match[2].split('|'):
            if '=' in part:
                k, v = part.split('=', 1)
                fields[k.strip().lower()] = v.strip()
        raw_triggers = fields.get('triggers', '')
        triggers = [t.strip() for t in raw_triggers.split(',') if t.strip() and t.strip().lower() != 'none']
        unreachable = None
        if 'unreachable' in fields:
            try:
                unreachable = int(fields['unreachable'])
            except ValueError:
                unreachable = None
        entries.append({
            'line': line_no,
            'timestamp': ts.isoformat().replace('+00:00', 'Z'),
            'epoch': ts.timestamp(),
            'date': ts.date().isoformat(),
            'triggers': triggers,
            'unreachable': unreachable,
            'proposal': fields.get('proposal'),
            'status': fields.get('status', 'proposed'),
            'valid': bool(triggers),
        })
    return entries


def validate_dream_log(log_path):
    """Return any log entries that fail the P2 invariant (missing trigger name)."""
    entries = parse_dream_log(log_path)
    invalid = [entry for entry in entries if not entry['valid']]
    return {'total_entries': len(entries), 'invalid_entries': invalid, 'valid': not invalid}


def _resolve_log_path(folder, log_arg):
    path = Path(log_arg)
    if path.is_absolute():
        return path
    return Path(folder) / path


def _log_notes(notes, log_prefixes=memlint.LOG_PREFIXES):
    result = {}
    for rel, path in notes.items():
        if any(rel.startswith(prefix) for prefix in log_prefixes):
            if posixpath.basename(rel).lower() == 'readme.md':
                continue
            result[rel] = path
    return result


def _is_note_newer(rel, path, last_entry):
    if last_entry is None:
        return True
    if path.stat().st_mtime > last_entry['epoch']:
        return True
    # Also respect an explicit filename date strictly after the last pass date.
    match = FILENAME_DATE_RE.search(posixpath.basename(rel))
    if match:
        try:
            note_date = date.fromisoformat(match[1])
            if note_date > date.fromisoformat(last_entry['date']):
                return True
        except ValueError:
            pass
    return False


def _touched_pages(folder, notes, new_note_names):
    by_name = {}
    for note in notes:
        by_name.setdefault(posixpath.basename(note), []).append(note)
    touched = set()
    for rel in new_note_names:
        path = notes.get(rel)
        if path is None:
            continue
        lines = path.read_text(encoding='utf-8-sig').splitlines()
        for _num, (line, spans) in memlint.markdown_lines(lines):
            for link in memlint.LINK.finditer(line):
                destination = link[1].split('|', 1)[0].strip()
                target = destination.split('#', 1)[0].strip()
                if destination.startswith('#'):
                    continue
                for resolved in memlint.resolve(target, rel, notes, by_name):
                    if not any(resolved.startswith(p) for p in memlint.PAGE_EXEMPT_PREFIXES):
                        touched.add(resolved)
            for span in spans:
                resolved = memlint._resolve_local_file(span, rel, folder)
                if resolved is not None and resolved[0] in notes:
                    if not any(resolved[0].startswith(p) for p in memlint.PAGE_EXEMPT_PREFIXES):
                        touched.add(resolved[0])
    return sorted(touched)


def evaluate(
    folder,
    *,
    log_path=DEFAULT_LOG,
    now=None,
    allow_broken_links=None,
    backlog_threshold=DEFAULT_BACKLOG,
    unreachable_delta=DEFAULT_UNREACHABLE_DELTA,
    baseline_unreachable=None,
    budget_ratio=DEFAULT_BUDGET_RATIO,
    max_lines=memlint.DEFAULT_MAX_LINES,
    max_bytes=memlint.DEFAULT_MAX_BYTES,
    backstop_days=DEFAULT_BACKSTOP_DAYS,
    pending_since=None,
    overdue_days=DEFAULT_OVERDUE_DAYS,
    roots=None,
    decisions=memlint.DECISIONS,
    instructions=memlint.INSTRUCTIONS,
    index=memlint.INDEX,
):
    folder = Path(folder)
    if now is None:
        now_dt = datetime.now(timezone.utc)
    elif isinstance(now, date) and not isinstance(now, datetime):
        now_dt = datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=timezone.utc)
    else:
        now_dt = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    today = now_dt.date()

    notes = memlint.collect_notes(folder)
    resolved_log = _resolve_log_path(folder, log_path)
    entries = parse_dream_log(resolved_log)
    last_entry = entries[-1] if entries else None

    triggers = []

    # T1: Conflict
    conflicts = memlint.find_conflicts(folder, notes=notes)
    if conflicts:
        triggers.append({
            'id': 'T1',
            'name': 'Conflict',
            'detail': f"{len(conflicts)} CONFLICT: mark(s) found.",
            'files': sorted({hit['file'] for hit in conflicts}),
        })

    # T2: Backlog
    logs = _log_notes(notes)
    new_notes = sorted(rel for rel, path in logs.items() if _is_note_newer(rel, path, last_entry))
    if len(new_notes) >= backlog_threshold:
        triggers.append({
            'id': 'T2',
            'name': 'Backlog',
            'detail': f"{len(new_notes)} new session/run note(s) >= threshold {backlog_threshold}.",
            'count': len(new_notes),
            'threshold': backlog_threshold,
        })

    # T3: Lint regression (pages only; daily/, runs/, templates/, proposals/ exempt)
    exempt = list(memlint.PAGE_EXEMPT_PREFIXES)
    lint_findings = memlint.lint(
        folder,
        roots=roots,
        decisions=decisions,
        exempt_prefixes=exempt,
        allow_broken_links=allow_broken_links,
    )
    lint_counts = dict.fromkeys(memlint.CHECKS, 0)
    for f in lint_findings:
        lint_counts[f['check']] += 1
    decision_problems = lint_counts['D1'] + lint_counts['D2']
    broken_links = lint_counts['L1']
    unreachable_pages = lint_counts['I1']
    if baseline_unreachable is not None:
        baseline = baseline_unreachable
    elif last_entry is not None and last_entry['unreachable'] is not None:
        baseline = last_entry['unreachable']
    else:
        baseline = 0
    unreachable_increase = unreachable_pages - baseline
    t3_reasons = []
    if decision_problems > 0:
        t3_reasons.append(f"{decision_problems} decision problem(s)")
    if broken_links > 0:
        t3_reasons.append(f"{broken_links} broken link(s)")
    if unreachable_increase >= unreachable_delta:
        t3_reasons.append(
            f"{unreachable_pages} unreachable page(s) (+{unreachable_increase} vs baseline {baseline})"
        )
    if t3_reasons:
        triggers.append({
            'id': 'T3',
            'name': 'Lint regression',
            'detail': '; '.join(t3_reasons) + '.',
            'counts': lint_counts,
            'baseline_unreachable': baseline,
        })

    # T4: Injected size (> 80% of budget)
    budget = memlint.measure_budget(
        folder, instructions=instructions, index=index,
        max_lines=max_lines, max_bytes=max_bytes,
    )
    if budget['line_ratio'] > budget_ratio or budget['byte_ratio'] > budget_ratio:
        triggers.append({
            'id': 'T4',
            'name': 'Injected size',
            'detail': (
                f"Injected layer at {budget['lines']}/{budget['max_lines']} lines "
                f"({budget['line_ratio']:.0%}) and {budget['bytes']}/{budget['max_bytes']} bytes "
                f"({budget['byte_ratio']:.0%}), threshold {budget_ratio:.0%}."
            ),
            'budget': budget,
        })

    # T5: Review date passed
    review_dates = memlint.find_review_dates(folder, notes=notes)
    overdue_reviews = [
        item for item in review_dates
        if date.fromisoformat(item['review_after']) < today
    ]
    if overdue_reviews:
        triggers.append({
            'id': 'T5',
            'name': 'Review date',
            'detail': f"{len(overdue_reviews)} page(s) past review_after date.",
            'pages': overdue_reviews,
        })

    # T6: Backstop (28 days with no pass)
    days_since_pass = None
    if last_entry is not None:
        last_date = date.fromisoformat(last_entry['date'])
        days_since_pass = (today - last_date).days
    elif notes:
        oldest_mtime = min(p.stat().st_mtime for p in notes.values())
        oldest_date = datetime.fromtimestamp(oldest_mtime, tz=timezone.utc).date()
        days_since_pass = (today - oldest_date).days

    if days_since_pass is not None and days_since_pass >= backstop_days:
        triggers.append({
            'id': 'T6',
            'name': 'Backstop',
            'detail': f"{days_since_pass} day(s) without a pass >= {backstop_days}-day backstop.",
            'days_since_pass': days_since_pass,
            'backstop_days': backstop_days,
        })

    read_frequency = memlint.extract_read_pages(folder, notes=notes)
    touched = _touched_pages(folder, notes, new_notes)
    priority_pages = sorted(
        set(touched)
        | {hit['file'] for hit in conflicts}
        | {item['file'] for item in overdue_reviews},
        key=lambda page: (-read_frequency.get(page, 0), page),
    )

    trigger_ids = [t['id'] for t in triggers]
    if trigger_ids == ['T6']:
        # §4.5 Drift: T6 backstop scopes to pages with review_after and most-read pages.
        review_files = {item['file'] for item in review_dates}
        top_read = list(read_frequency.keys())[:10]
        priority_pages = sorted(
            review_files | set(top_read),
            key=lambda page: (-read_frequency.get(page, 0), page),
        )

    overdue_trigger = False
    if triggers and pending_since is not None:
        pending_date = pending_since if isinstance(pending_since, date) else date.fromisoformat(pending_since)
        if (today - pending_date).days >= overdue_days:
            overdue_trigger = True

    invalid_log_entries = [e for e in entries if not e['valid']]

    return {
        'memory_dir': str(folder),
        'checked_at': today.isoformat(),
        'should_dream': bool(triggers),
        'trigger_ids': trigger_ids,
        'triggers': triggers,
        'overdue_trigger': overdue_trigger,
        'last_pass': last_entry,
        'log_valid': not invalid_log_entries,
        'invalid_log_entries': invalid_log_entries,
        'metrics': {
            'conflicts': len(conflicts),
            'new_notes': len(new_notes),
            'lint_counts': lint_counts,
            'baseline_unreachable': baseline,
            'budget': budget,
            'overdue_reviews': len(overdue_reviews),
            'days_since_pass': days_since_pass,
        },
        'scope': {
            'new_notes': new_notes,
            'touched_pages': touched,
            'priority_pages': priority_pages,
            'conflict_hits': conflicts,
            'overdue_review_pages': overdue_reviews,
            'read_frequency': read_frequency,
        },
    }


def format_report(result):
    lines = []
    if not result['should_dream']:
        lines.append(f"No dream triggers fired ({result['memory_dir']}).")
    else:
        lines.append(
            f"Dream pass needed ({', '.join(result['trigger_ids'])}) for {result['memory_dir']}:"
        )
        for trig in result['triggers']:
            lines.append(f"  - {trig['id']} {trig['name']}: {trig['detail']}")
    if result['overdue_trigger']:
        lines.append("WARNING: A trigger has remained pending for >= 7 days without a pass.")
    if not result['log_valid']:
        lines.append(
            f"WARNING: {len(result['invalid_log_entries'])} dream log entry(s) missing a trigger."
        )
    metrics = result['metrics']
    budget = metrics['budget']
    lines.append(
        f"Summary: new_notes={metrics['new_notes']}, conflicts={metrics['conflicts']}, "
        f"unreachable={metrics['lint_counts']['I1']} (baseline={metrics['baseline_unreachable']}), "
        f"budget={budget['lines']}/{budget['max_lines']}L {budget['bytes']}/{budget['max_bytes']}B, "
        f"overdue_reviews={metrics['overdue_reviews']}, days_since_pass={metrics['days_since_pass']}"
    )
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('memory_dir')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--log', default=DEFAULT_LOG,
                        help=f'Dream log path (relative to memory_dir or absolute, default: {DEFAULT_LOG}).')
    parser.add_argument('--now', default=None,
                        help='Override current date (YYYY-MM-DD) for deterministic checks.')
    parser.add_argument('--allow-broken-link', action='append', default=[])
    parser.add_argument('--allowlist', type=Path, default=None)
    parser.add_argument('--backlog', type=int, default=DEFAULT_BACKLOG)
    parser.add_argument('--unreachable-delta', type=int, default=DEFAULT_UNREACHABLE_DELTA)
    parser.add_argument('--baseline-unreachable', type=int, default=None)
    parser.add_argument('--budget-ratio', type=float, default=DEFAULT_BUDGET_RATIO)
    parser.add_argument('--max-lines', type=int, default=memlint.DEFAULT_MAX_LINES)
    parser.add_argument('--max-bytes', type=int, default=memlint.DEFAULT_MAX_BYTES)
    parser.add_argument('--backstop-days', type=int, default=DEFAULT_BACKSTOP_DAYS)
    parser.add_argument('--pending-since', default=None,
                        help='ISO date (YYYY-MM-DD) when triggers first fired without a pass.')
    parser.add_argument('--roots', action='append', default=None)
    parser.add_argument('--decisions', default=memlint.DECISIONS)
    parser.add_argument('--instructions', default=memlint.INSTRUCTIONS)
    parser.add_argument('--index', default=memlint.INDEX)
    parser.add_argument('--validate-log', action='store_true',
                        help='Only validate dream log entries (every entry must name a trigger).')
    args = parser.parse_args()

    folder = Path(args.memory_dir)
    if not folder.is_dir():
        parser.error(f'Expected a memory directory: {folder}')

    if args.validate_log:
        log_path = _resolve_log_path(folder, args.log)
        validation = validate_dream_log(log_path)
        if args.json:
            print(json.dumps(validation))
        else:
            status = 'OK' if validation['valid'] else 'INVALID'
            print(f"Dream log {status}: {validation['total_entries']} entries, "
                  f"{len(validation['invalid_entries'])} invalid.")
        return 0 if validation['valid'] else 1

    now_val = None
    if args.now is not None:
        try:
            now_val = date.fromisoformat(args.now)
        except ValueError:
            parser.error(f'Invalid --now date: {args.now}')

    pending_val = None
    if args.pending_since is not None:
        try:
            pending_val = date.fromisoformat(args.pending_since)
        except ValueError:
            parser.error(f'Invalid --pending-since date: {args.pending_since}')

    allow_broken = set(args.allow_broken_link)
    if args.allowlist is not None:
        try:
            allow_broken.update(memlint.load_allowlist(args.allowlist))
        except (OSError, UnicodeError) as error:
            parser.error(str(error))

    roots = None
    if args.roots is not None:
        roots = [item.strip() for raw in args.roots for item in raw.split(',') if item.strip()]

    try:
        result = evaluate(
            folder,
            log_path=args.log,
            now=now_val,
            allow_broken_links=allow_broken,
            backlog_threshold=args.backlog,
            unreachable_delta=args.unreachable_delta,
            baseline_unreachable=args.baseline_unreachable,
            budget_ratio=args.budget_ratio,
            max_lines=args.max_lines,
            max_bytes=args.max_bytes,
            backstop_days=args.backstop_days,
            pending_since=pending_val,
            roots=roots,
            decisions=args.decisions,
            instructions=args.instructions,
            index=args.index,
        )
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))

    if args.json:
        print(json.dumps(result))
    else:
        print(format_report(result))
    return 1 if result['should_dream'] else 0


if __name__ == '__main__':
    sys.exit(main())
