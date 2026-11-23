"""Test whether fresh agent sessions can recover, write, update, or merge shared memory."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, timedelta
import errno
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import subprocess
import sys
import tempfile
import time


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import stanza as stanza_mod  # noqa: E402


MODES = ('direct', 'no-hint', 'write', 'correction', 'concurrent')


def load_config(path):
    def invalid_constant(value):
        raise ValueError(f"{value} is not a JSON number")

    with path.open(encoding="utf-8") as stream:
        config = json.load(stream, parse_constant=invalid_constant)
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    timeout = config.get("timeout_seconds", 300)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("timeout_seconds must be a positive finite number")
    harnesses = config.get("harnesses")
    if not isinstance(harnesses, list) or not harnesses:
        raise ValueError("harnesses must be a nonempty array")
    for index, harness in enumerate(harnesses):
        if not isinstance(harness, dict):
            raise ValueError(f"harnesses[{index}] must be an object")
        name = harness.get("name")
        if not isinstance(name, str) or not name.strip() or not name.isprintable():
            raise ValueError(f"harnesses[{index}] requires a printable name")
        command = harness.get("command")
        if (not isinstance(command, list) or not command
                or not all(isinstance(arg, str) and "\0" not in arg for arg in command)
                or not command[0]):
            raise ValueError(f"{name} requires a nonempty command argument array")
        for arg in command:
            os.fsencode(arg)
    return timeout, harnesses


@contextmanager
def defer_interrupt():
    """Finish a short filesystem transaction before delivering Ctrl-C."""
    interrupted = False

    def remember(signum, frame):
        nonlocal interrupted
        interrupted = True

    previous = signal.signal(signal.SIGINT, remember)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)
        if interrupted:
            raise KeyboardInterrupt


def _ensure_subdir(memory, subdir_name):
    subdir = memory / subdir_name
    created = None
    try:
        subdir.mkdir()
        created = subdir.lstat()
    except FileExistsError:
        if subdir.is_symlink() or not subdir.is_dir():
            raise ValueError(f"{subdir} must be an ordinary directory")
    return subdir, created


def _remove_created_dir(subdir, created_stat):
    if created_stat is None:
        return
    try:
        if os.path.samestat(subdir.lstat(), created_stat):
            subdir.rmdir()
    except OSError as error:
        if error.errno not in (errno.ENOTEMPTY, errno.EEXIST, errno.ENOENT):
            raise


@contextmanager
def planted_note(memory, keep, *, topic=None):
    daily = memory / "daily"
    created_daily = None
    stream = None
    complete = False
    try:
        with defer_interrupt():
            daily, created_daily = _ensure_subdir(memory, "daily")
            while True:
                note = daily / f"handoff-test-{secrets.token_hex(4)}.md"
                try:
                    stream = note.open("x", encoding="utf-8")
                    break
                except FileExistsError:
                    continue
            decision = "decision-" + secrets.token_hex(16)
            reason = "reason-" + secrets.token_hex(16)
            relative = note.relative_to(memory).as_posix()
            heading = f"# Handoff test {date.today().isoformat()}"
            topic_line = f"Topic: {topic}\n" if topic else ""
            stream.write(
                f"{heading}\n\n{topic_line}"
                f"Decision: {decision}\nRationale: {reason}\nSource: {relative}\n"
            )
            stream.flush()
            complete = True
        yield relative, {"decision": decision, "reason": reason, "source": note.name}
    finally:
        with defer_interrupt():
            if stream is not None:
                planted = os.fstat(stream.fileno())
                stream.close()
                if not (keep and complete):
                    try:
                        if os.path.samestat(note.lstat(), planted):
                            note.unlink()
                    except FileNotFoundError:
                        pass
            _remove_created_dir(daily, created_daily)


@contextmanager
def planted_correction_notes(memory, keep):
    daily = memory / "daily"
    created_daily = None
    streams = []
    complete = False
    try:
        with defer_interrupt():
            daily, created_daily = _ensure_subdir(memory, "daily")
            topic = "topic-" + secrets.token_hex(6)
            old_date = (date.today() - timedelta(days=14)).isoformat()
            new_date = date.today().isoformat()
            old_decision = "old-decision-" + secrets.token_hex(12)
            old_reason = "old-reason-" + secrets.token_hex(12)
            new_decision = "new-decision-" + secrets.token_hex(12)
            new_reason = "new-reason-" + secrets.token_hex(12)

            while True:
                init_note = daily / f"handoff-init-{secrets.token_hex(4)}.md"
                try:
                    s1 = init_note.open("x", encoding="utf-8")
                    break
                except FileExistsError:
                    continue
            streams.append((init_note, s1))

            while True:
                corr_note = daily / f"handoff-corr-{secrets.token_hex(4)}.md"
                try:
                    s2 = corr_note.open("x", encoding="utf-8")
                    break
                except FileExistsError:
                    continue
            streams.append((corr_note, s2))

            init_rel = init_note.relative_to(memory).as_posix()
            corr_rel = corr_note.relative_to(memory).as_posix()

            s1.write(
                f"# Decision {old_date}\n\n"
                f"Topic: {topic}\n"
                f"Decision: {old_decision}\n"
                f"Rationale: {old_reason}\n"
                f"Source: {init_rel}\n"
            )
            s1.flush()

            s2.write(
                f"# Correction {new_date}\n\n"
                f"Topic: {topic}\n"
                f"Supersedes: [[{init_rel[:-3]}]]\n"
                f"Decision: {new_decision}\n"
                f"Rationale: {new_reason}\n"
                f"Source: {corr_rel}\n"
            )
            s2.flush()
            complete = True
        yield {
            "topic": topic,
            "initial_note": init_rel,
            "correction_note": corr_rel,
            "old_decision": old_decision,
            "expected": {
                "decision": new_decision,
                "reason": new_reason,
                "initial_source": init_note.name,
                "correction_source": corr_note.name,
            },
        }
    finally:
        with defer_interrupt():
            for path, st in streams:
                planted = os.fstat(st.fileno())
                st.close()
                if not (keep and complete):
                    try:
                        if os.path.samestat(path.lstat(), planted):
                            path.unlink()
                    except FileNotFoundError:
                        pass
            _remove_created_dir(daily, created_daily)


def _snapshot_tree(memory):
    files = {}
    dirs = set()
    for root, dirnames, filenames in os.walk(memory):
        base = Path(root)
        for d in dirnames:
            dirs.add((base / d).relative_to(memory).as_posix())
        for f in filenames:
            p = base / f
            if not p.is_symlink() and p.is_file():
                files[p.relative_to(memory).as_posix()] = p.read_bytes()
    return files, dirs


def _restore_added_entries(memory, before_files, before_dirs):
    after_files, after_dirs = _snapshot_tree(memory)
    for rel in sorted(set(after_files) - set(before_files)):
        try:
            (memory / rel).unlink()
        except FileNotFoundError:
            pass
    for rel in sorted(set(after_dirs) - set(before_dirs), key=len, reverse=True):
        try:
            (memory / rel).rmdir()
        except OSError:
            pass


def _exec_harness(harness, substitutions, timeout):
    # Replace once so paths that contain literal braces survive unchanged.
    command = [re.sub(r"\{(?:prompt|memory_dir)\}", lambda m: substitutions[m[0]], arg)
               for arg in harness["command"]]
    start = time.monotonic()
    output = b""
    exit_code = None
    timed_out = False
    with tempfile.TemporaryDirectory(prefix="handoff-") as cwd:
        try:
            child = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     start_new_session=(os.name == "posix"))
        except OSError:
            pass  # A missing or unlaunchable command is a failed harness.
        else:
            with child:
                try:
                    output, _ = child.communicate(timeout=timeout)
                    exit_code = child.returncode
                except subprocess.TimeoutExpired as error:
                    timed_out = True
                    output = error.output or b""
                finally:
                    if exit_code is None:
                        try:
                            if os.name == "posix":
                                os.killpg(child.pid, signal.SIGKILL)
                            else:
                                child.kill()
                        except ProcessLookupError:
                            pass
    return output, exit_code, timed_out, round(time.monotonic() - start, 3)


def run_harness(harness, substitutions, expected, timeout):
    output, exit_code, timed_out, seconds = _exec_harness(harness, substitutions, timeout)
    found = {key: value.encode() in output for key, value in expected.items()}
    return {"harness": harness["name"], "passed": exit_code is not None and all(found.values()),
            "found": found, "exit_code": exit_code, "timed_out": timed_out,
            "seconds": seconds}


def _run_write_trial(harness, memory, profile, timeout, keep):
    before_files, before_dirs = _snapshot_tree(memory)
    decision = "decision-" + secrets.token_hex(12)
    reason = "reason-" + secrets.token_hex(12)
    source_ref = f"session-{secrets.token_hex(6)}"
    today_str = date.today().isoformat()

    stanza_text = stanza_mod.render_stanza(memory, profile=profile)
    prompt = (
        f"{stanza_text}\n\n"
        f"Please remember this decision in shared memory: "
        f"Decision: {decision}. Rationale: {reason}. Source: {source_ref}. "
        f"Write a new dated session note in daily/ with today's date ({today_str}) and the source."
    )
    substitutions = {"{memory_dir}": str(memory), "{prompt}": prompt}
    created_files = []
    try:
        _output, exit_code, timed_out, seconds = _exec_harness(harness, substitutions, timeout)
        after_files, _after_dirs = _snapshot_tree(memory)
        created_files = sorted(
            rel for rel in set(after_files) - set(before_files) if rel.endswith(".md")
        )
        has_note = bool(created_files)
        has_decision = False
        has_date = False
        has_source = False
        for rel in created_files:
            text = after_files[rel].decode("utf-8", errors="replace")
            if decision in text:
                has_decision = True
            if today_str in text or today_str in rel:
                has_date = True
            if (source_ref in text
                    or re.search(r"^(?:Source|Related)\s*:", text, re.MULTILINE)):
                has_source = True
        found = {
            "note_created": has_note,
            "decision": has_decision,
            "date": has_date,
            "source": has_source,
        }
        passed = exit_code == 0 and all(found.values())
    finally:
        if not keep:
            with defer_interrupt():
                _restore_added_entries(memory, before_files, before_dirs)
    return {
        "harness": harness["name"],
        "passed": passed,
        "found": found,
        "created_files": created_files,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "seconds": seconds,
    }


def _run_concurrent_trial(harness, memory, timeout, keep):
    before_files, before_dirs = _snapshot_tree(memory)
    notes_dir = memory / "notes"
    created_notes_dir = None
    shared_path = None
    try:
        with defer_interrupt():
            notes_dir, created_notes_dir = _ensure_subdir(memory, "notes")
            while True:
                shared_path = notes_dir / f"handoff-concurrent-{secrets.token_hex(4)}.md"
                try:
                    with shared_path.open("x", encoding="utf-8") as stream:
                        stream.write("# Concurrent write test\n\n## Entries\n")
                    break
                except FileExistsError:
                    continue
        shared_rel = shared_path.relative_to(memory).as_posix()
        today_str = date.today().isoformat()
        token_a = "line-a-" + secrets.token_hex(12)
        token_b = "line-b-" + secrets.token_hex(12)

        def make_prompt(writer_id, token):
            return (
                f"Shared memory is at {memory}. Following contract rules 6 and 7: "
                f"1) Create a new session note in {memory / 'daily'} named with today's date "
                f"({today_str}) and writer id '{writer_id}' containing '{token}'. "
                f"2) Re-read {shared_path} immediately before editing, append ONLY this line: "
                f"'- [{today_str}] {token} (writer: {writer_id})' without removing any other lines, "
                f"and read the file back to confirm your line landed."
            )

        subs_a = {"{memory_dir}": str(memory), "{prompt}": make_prompt("writer-a", token_a)}
        subs_b = {"{memory_dir}": str(memory), "{prompt}": make_prompt("writer-b", token_b)}

        start = time.monotonic()
        with ThreadPoolExecutor(max_workers=2) as pool:
            fut_a = pool.submit(_exec_harness, harness, subs_a, timeout)
            fut_b = pool.submit(_exec_harness, harness, subs_b, timeout)
            res_a = fut_a.result()
            res_b = fut_b.result()
        seconds = round(time.monotonic() - start, 3)

        shared_text = shared_path.read_text(encoding="utf-8", errors="replace") if shared_path.exists() else ""
        after_files, _ = _snapshot_tree(memory)
        new_daily = sorted(
            rel for rel in set(after_files) - set(before_files)
            if rel.startswith("daily/") and rel.endswith(".md")
        )
        found = {
            "line_a": token_a in shared_text,
            "line_b": token_b in shared_text,
            "both_session_notes": len(new_daily) >= 2,
        }
        exit_codes = [res_a[1], res_b[1]]
        timed_out = res_a[2] or res_b[2]
        passed = all(code == 0 for code in exit_codes) and not timed_out and all(found.values())
    finally:
        if not keep:
            with defer_interrupt():
                if shared_path is not None:
                    try:
                        shared_path.unlink()
                    except FileNotFoundError:
                        pass
                _restore_added_entries(memory, before_files, before_dirs)
                _remove_created_dir(notes_dir, created_notes_dir)

    return {
        "harness": harness["name"],
        "passed": passed,
        "found": found,
        "shared_page": shared_rel,
        "exit_codes": exit_codes,
        "timed_out": timed_out,
        "seconds": seconds,
    }


def _profile_recommendation(profile, pass_rate):
    if pass_rate >= 0.9:
        return {"action": "stay", "next_profile": profile}
    if profile == "pointer":
        return {"action": "step_up", "next_profile": "read-first"}
    if profile == "read-first":
        return {"action": "step_up", "next_profile": "inject"}
    return {"action": "investigate", "next_profile": "inject"}


def print_report(report):
    results = report["results"]
    for result in results:
        missing = [key for key, found in result["found"].items() if not found]
        details = []
        if result.get("timed_out"):
            details.append("timed out")
        elif result.get("exit_code") is None and "exit_codes" not in result:
            details.append("could not start")
        if missing:
            details.append("missing " + ", ".join(missing))
        status = "PASS" if result["passed"] else "FAIL"
        print(" — ".join([result["harness"], status, *details]))
    passed = sum(result["passed"] for result in results)
    state = "removed" if report["cleaned_up"] else "kept"
    note_label = report.get("note") or report.get("mode", "handoff")
    print(f"{passed}/{len(results)} passed — {note_label} ({state})")
    for item in report.get("summary", []):
        rec = item.get("recommendation")
        rec_text = f" -> {rec['action']} ({rec['next_profile']})" if rec else ""
        print(f"  {item['harness']}: {item['passed_runs']}/{item['total_runs']} "
              f"({item['pass_rate']:.0%}){rec_text}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--mode", choices=MODES, default="direct",
                        help="Test mode: direct (default), no-hint (P1), write (P1b), correction (F2), concurrent (F1).")
    parser.add_argument("--profile", choices=stanza_mod.PROFILES, default="pointer",
                        help="Steering profile for no-hint and write modes (default: pointer).")
    parser.add_argument("--runs", type=int, default=1,
                        help="Number of trials per harness (e.g. 10 for P1/F1, 5 for P1b).")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()

    if args.runs <= 0:
        parser.error("--runs must be a positive integer")

    try:
        memory = args.memory_dir.resolve(strict=True)
        if not memory.is_dir():
            raise ValueError(f"{memory} must be a directory")
        timeout, harnesses = load_config(args.config)
    except (OSError, ValueError, OverflowError, RecursionError) as error:
        parser.error(str(error))

    # Preserve exact original behavior & JSON schema for default single-run direct mode.
    if args.mode == "direct" and args.runs == 1:
        prompt = (f"Read the shared memory folder at {memory}. What was decided in the "
                  "most recent handoff test, why, and which file records it? Quote the "
                  "decision and rationale exactly and give the source filename.")
        substitutions = {"{memory_dir}": str(memory), "{prompt}": prompt}
        try:
            with planted_note(memory, args.keep) as (note, expected):
                results = [run_harness(harness, substitutions, expected, timeout)
                           for harness in harnesses]
        except ValueError as error:
            parser.error(str(error))
        except OSError as error:
            parser.exit(1, f"handoff test failed: {error}\n")
        report = {"note": note, "results": results, "cleaned_up": not args.keep}
        if args.json:
            print(json.dumps(report))
        else:
            print_report(report)
        return 0 if all(result["passed"] for result in results) else 1

    # Extended modes / multi-run trials.
    results = []
    notes_planted = []
    try:
        for run_idx in range(1, args.runs + 1):
            if args.mode == "direct":
                prompt = (f"Read the shared memory folder at {memory}. What was decided in the "
                          "most recent handoff test, why, and which file records it? Quote the "
                          "decision and rationale exactly and give the source filename.")
                substitutions = {"{memory_dir}": str(memory), "{prompt}": prompt}
                with planted_note(memory, args.keep) as (note, expected):
                    notes_planted.append(note)
                    for harness in harnesses:
                        row = run_harness(harness, substitutions, expected, timeout)
                        row["run"] = run_idx
                        results.append(row)
            elif args.mode == "no-hint":
                topic = "codename-" + secrets.token_hex(6)
                stanza_text = stanza_mod.render_stanza(memory, profile=args.profile)
                prompt = (
                    f"{stanza_text}\n\n"
                    f"Question:\n"
                    f"What was decided for {topic}, why, and which file records it? "
                    f"Quote the decision and rationale exactly and give the source filename."
                )
                substitutions = {"{memory_dir}": str(memory), "{prompt}": prompt}
                with planted_note(memory, args.keep, topic=topic) as (note, expected):
                    notes_planted.append(note)
                    for harness in harnesses:
                        row = run_harness(harness, substitutions, expected, timeout)
                        row["run"] = run_idx
                        row["profile"] = args.profile
                        results.append(row)
            elif args.mode == "write":
                for harness in harnesses:
                    row = _run_write_trial(harness, memory, args.profile, timeout, args.keep)
                    row["run"] = run_idx
                    row["profile"] = args.profile
                    results.append(row)
            elif args.mode == "correction":
                with planted_correction_notes(memory, args.keep) as info:
                    notes_planted.extend([info["initial_note"], info["correction_note"]])
                    prompt = (
                        f"Read the shared memory folder at {memory}. What is the current decision "
                        f"for {info['topic']}, why did it change, and which two files record the "
                        f"initial decision and the correction? Quote the current decision and "
                        f"rationale exactly and name both source filenames."
                    )
                    substitutions = {"{memory_dir}": str(memory), "{prompt}": prompt}
                    for harness in harnesses:
                        output, exit_code, timed_out, seconds = _exec_harness(
                            harness, substitutions, timeout
                        )
                        found = {k: v.encode() in output for k, v in info["expected"].items()}
                        reported_stale_only = (
                            info["old_decision"].encode() in output and not found["decision"]
                        )
                        results.append({
                            "harness": harness["name"],
                            "run": run_idx,
                            "passed": exit_code is not None and all(found.values()),
                            "found": found,
                            "reported_stale_only": reported_stale_only,
                            "exit_code": exit_code,
                            "timed_out": timed_out,
                            "seconds": seconds,
                        })
            elif args.mode == "concurrent":
                for harness in harnesses:
                    row = _run_concurrent_trial(harness, memory, timeout, args.keep)
                    row["run"] = run_idx
                    results.append(row)
    except (ValueError, FileNotFoundError) as error:
        parser.error(str(error))
    except OSError as error:
        parser.exit(1, f"handoff test failed: {error}\n")

    summary = []
    all_ok = True
    for harness in harnesses:
        h_rows = [r for r in results if r["harness"] == harness["name"]]
        passed_runs = sum(1 for r in h_rows if r["passed"])
        total_runs = len(h_rows)
        pass_rate = round(passed_runs / total_runs, 4) if total_runs else 0.0
        entry = {
            "harness": harness["name"],
            "passed_runs": passed_runs,
            "total_runs": total_runs,
            "pass_rate": pass_rate,
        }
        if args.mode == "no-hint":
            rec = _profile_recommendation(args.profile, pass_rate)
            entry["recommendation"] = rec
            if pass_rate < 0.9:
                all_ok = False
        else:
            if passed_runs != total_runs:
                all_ok = False
        summary.append(entry)

    report = {
        "mode": args.mode,
        "profile": args.profile if args.mode in ("no-hint", "write") else None,
        "runs": args.runs,
        "note": notes_planted[-1] if notes_planted else None,
        "results": results,
        "summary": summary,
        "cleaned_up": not args.keep,
    }
    if args.json:
        print(json.dumps(report))
    else:
        print_report(report)
    return 0 if all_ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
