import hashlib, json, os, re, shutil, signal, subprocess, sys, tempfile, time, datetime
arm = sys.argv[1]; here = os.path.dirname(os.path.abspath(__file__)); stub = os.path.join(here, "stub.py")
script = os.path.join(arm, "handoff_test.py"); ok = 0; total = 0
def tree(d):
    out = {}
    for r, ds, fs in os.walk(d):
        for x in ds: out[os.path.relpath(os.path.join(r, x), d) + "/"] = "dir"
        for f in fs:
            p = os.path.join(r, f); out[os.path.relpath(p, d)] = hashlib.sha1(open(p, "rb").read()).hexdigest()
    return out
def mem(daily=True):
    d = tempfile.mkdtemp(prefix="hmem-")
    os.makedirs(os.path.join(d, "agent")); open(os.path.join(d, "AGENTS.md"), "w").write("# rules\n"); open(os.path.join(d, "agent", "index.md"), "w").write("# index\n")
    if daily: os.makedirs(os.path.join(d, "daily")); open(os.path.join(d, "daily", "existing.md"), "w").write("keep me\n")
    return d
def cfg(harn, timeout=None):
    c = {"harnesses": [{"name": n, "command": cmd} for n, cmd in harn]}
    if timeout is not None: c["timeout_seconds"] = timeout
    p = tempfile.mktemp(suffix=".json"); json.dump(c, open(p, "w")); return p
def st(mode, *extra): return [sys.executable, stub, mode, "{memory_dir}", "{prompt}", *extra]
def run(m, c, *flags, to=60):
    t = time.time(); r = subprocess.run([sys.executable, script, "--memory-dir", m, "--config", c, "--json", *flags], capture_output=True, text=True, timeout=to)
    try: j = json.loads(r.stdout)
    except Exception: j = None
    return r.returncode, j, time.time() - t, r
def check(name, cond, detail=""):
    global ok, total; total += 1; ok += bool(cond); print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else "  " + str(detail)[:300]))
def res(j, i=0): return (j or {}).get("results", [{}] * (i + 1))[i] if j and len(j.get("results", [])) > i else {}
# 1 correct
m = mem(); b = tree(m); rc, j, s, r = run(m, cfg([("a", st("correct"))], 20)); x = res(j)
check("correct answer passes, exit 0", rc == 0 and x.get("passed") is True and x.get("found") == {"decision": True, "reason": True, "source": True}, (rc, j, r.stderr))
check("note field has the specified name", j and re.fullmatch(r"daily/handoff-test-[0-9a-f]{8}\.md", j.get("note", "")), j)
check("result fields: exit_code 0, timed_out false, seconds number, cleaned_up true", x.get("exit_code") == 0 and x.get("timed_out") is False and isinstance(x.get("seconds"), (int, float)) and j and j.get("cleaned_up") is True, j)
check("folder unchanged after pass", tree(m) == b)
# 2 wrong
m = mem(); b = tree(m); rc, j, s, r = run(m, cfg([("a", st("wrong"))], 20)); x = res(j)
check("wrong answer fails, exit 1", rc == 1 and x.get("passed") is False and x.get("found") == {"decision": False, "reason": False, "source": False}, (rc, j))
check("folder unchanged after fail", tree(m) == b)
# 3, 4 partial
m = mem(); rc, j, s, r = run(m, cfg([("a", st("decision_only"))], 20)); x = res(j)
check("decision only: decision true, reason false, source false", rc == 1 and x.get("found") == {"decision": True, "reason": False, "source": False}, (rc, j))
m = mem(); rc, j, s, r = run(m, cfg([("a", st("no_filename"))], 20)); x = res(j)
print("  n/a  answer without file name (note text contains its own name)") if x.get("found", {}).get("source") and rc == 0 else check("answer without file name: source false, fails", rc == 1 and x.get("found") == {"decision": True, "reason": True, "source": False}, (rc, j))
# 5 hang
m = mem(); b = tree(m); rc, j, s, r = run(m, cfg([("a", st("hang"))], 2)); x = res(j)
check("timeout: timed_out true, exit_code null, exit 1, returns promptly", rc == 1 and x.get("timed_out") is True and x.get("exit_code") is None and x.get("passed") is False and s < 15, (rc, j, s))
check("folder unchanged after timeout", tree(m) == b)
# 6 missing command
m = mem(); b = tree(m); rc, j, s, r = run(m, cfg([("a", ["/no/such/command-xyz", "{prompt}"])], 5)); x = res(j)
check("command that cannot start: fails, exit_code null, timed_out false", rc == 1 and x.get("passed") is False and x.get("exit_code") is None and x.get("timed_out") is False, (rc, j, r.stderr))
check("folder unchanged after missing command", tree(m) == b)
# 7 record
m = mem(); rec = tempfile.mktemp(); rc, j, s, r = run(m, cfg([("a", st("record", rec))], 20))
d = json.load(open(rec)) if os.path.exists(rec) else {}
p = d.get("prompt", ""); note = d.get("note", "")
words = {w.strip(".,;:`'\"()[]") for l in note.splitlines() if l.startswith(("Decision:", "Rationale:")) for w in l.split()[1:]}
leaked = [w for w in words if len(w) >= 8 and w in p]
check("prompt gives the memory folder path", os.path.realpath(m) in p or m in p, p)
check("prompt does not leak the note name or tokens", d and d["name"] and d["name"] not in p and "handoff-test-" + d["name"][13:21] not in p and not leaked, (leaked, p))
check("harness runs in an empty directory that is not the memory folder", d and d.get("listing") == [] and os.path.realpath(d["cwd"]) != os.path.realpath(m), d.get("cwd"))
today = datetime.date.today().isoformat()
check("note has today's date and Decision, Rationale, Source lines", today in note and all(any(l.startswith(k) for l in note.splitlines()) for k in ("Decision:", "Rationale:", "Source:")), note)
# 8 order
m = mem(); rc, j, s, r = run(m, cfg([("first", st("wrong")), ("second", st("correct"))], 20))
check("two harnesses, in order, one failing gives exit 1", rc == 1 and j and [x.get("harness") for x in j["results"]] == ["first", "second"] and [x.get("passed") for x in j["results"]] == [False, True], (rc, j))
# 9 keep
m = mem(); rc, j, s, r = run(m, cfg([("a", st("correct"))], 20), "--keep")
kept = j and os.path.exists(os.path.join(m, j.get("note", "x")))
check("--keep leaves the note and reports cleaned_up false", kept and j.get("cleaned_up") is False, j)
m2 = mem(); rc2, j2, s, r = run(m2, cfg([("a", st("correct"))], 20), "--keep")
try: n1 = open(os.path.join(m, j["note"])).read(); n2 = open(os.path.join(m2, j2["note"])).read(); diff = n1 != n2 and j["note"] != j2["note"]
except Exception: diff = False
check("tokens and note name differ between runs", diff)
# 10 config errors
m = mem(); b = tree(m)
rc, j, s, r = run(os.path.join(m, "nope"), cfg([("a", st("correct"))])); check("missing folder: exit 2", rc == 2, rc)
bad = tempfile.mktemp(); open(bad, "w").write("{not json"); rc, j, s, r = run(m, bad); check("invalid config: exit 2, nothing planted", rc == 2 and tree(m) == b, rc)
rc, j, s, r = run(m, cfg([])); check("no harnesses: exit 2, nothing planted", rc == 2 and tree(m) == b, rc)
rc, j, s, r = run(m, tempfile.mktemp()); check("unreadable config: exit 2", rc == 2, rc)
# 11 daily created then removed
m = mem(daily=False); b = tree(m); rc, j, s, r = run(m, cfg([("a", st("correct"))], 20)); check("daily/ created by the run is removed again", rc == 0 and tree(m) == b, (rc, tree(m)))
# 12 default timeout
m = mem(); rc, j, s, r = run(m, cfg([("a", st("correct"))])); check("config without timeout_seconds works", rc == 0, (rc, r.stderr))
# 13 Ctrl-C
m = mem(); b = tree(m); pr = subprocess.Popen([sys.executable, script, "--memory-dir", m, "--config", cfg([("a", st("hang"))], 30), "--json"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
time.sleep(2); planted = tree(m) != b; pr.send_signal(signal.SIGINT)
try: pr.wait(15)
except Exception: pr.kill()
time.sleep(0.5); check("Ctrl-C mid-run: note was planted, then removed", planted and tree(m) == b, (planted, tree(m)))
# 14 plain output
m = mem(); r = subprocess.run([sys.executable, script, "--memory-dir", m, "--config", cfg([("a", st("wrong"))], 20)], capture_output=True, text=True, timeout=60)
check("plain output: a line per harness and a summary", r.returncode == 1 and len(r.stdout.strip().splitlines()) >= 2, r.stdout)
print(f"{arm}: {ok}/{total}")
