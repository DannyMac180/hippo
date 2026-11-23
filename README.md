# hippo

**One plain Markdown memory folder shared across every AI agent you use** — Claude Code, Codex, Antigravity, or whatever comes next.

No database. No vendor lock-in. No third-party dependencies (Python 3 standard library only). Your memory stays in your own Obsidian vault or git folder where you can read, edit, diff, and own every word.

## Why hippo?

Every coding agent now ships its own private memory store. That traps your decisions, project context, and working preferences inside one vendor's silo.

`hippo` implements the architecture from Anthropic's ["The components of agent memory"](https://www.anthropic.com/research/components-of-agent-memory) as a **vendor-agnostic plain-file system**:

1. **Give agents freedom to decide when to use memory** — Start every harness on a tiny 7-line `pointer` stanza with no rigid rules about what to record. Step up (`read-first` → `inject`) only when the empirical `P1` check proves a model actually needs stronger steering.
2. **Dream on necessity, not on a blind timer** — A deterministic, zero-token trigger check (`dream/dream_check.py`) watches for conflicts, note backlogs, lint regressions, budget pressure, overdue review dates, or a 28-day drift backstop. When nothing fired, zero tokens are spent. When a trigger fires, `dream/dream_pass.py` writes a **proposal** for human review — never silently rewriting live memory.
3. **Four-layer hierarchy where pages earn their keep** — Raw transcripts stay in each harness's own logs (referenced by pointer), session notes live in `daily/` (one writer per file), synthesized pages live in `projects/`/`people/`/`notes/` only when they combine multiple sources and stay smaller than what they summarize, and the injected layer stays strictly budgeted (`<= 200` lines / `25 KB`, stanza `<= 10` lines).

## Set up with your agent in 60 seconds

Point your coding agent (Claude Code, Codex, Antigravity, etc.) at `https://github.com/DannyMac180/hippo` and your Obsidian URI or notes path:

> Set up hippo from https://github.com/DannyMac180/hippo using my notes folder `<path or obsidian://open?vault=MyVault>`. Follow `docs/AGENT_SETUP.md`, ask me before modifying global instruction files, and verify with `memlint --all` and `dream_check`.

Or clone and run the setup helper directly:

```bash
git clone https://github.com/DannyMac180/hippo.git
cd hippo

# Works with an Obsidian URI, file:// URL, or any plain folder path
python3 examples/setup_hippo.py "obsidian://open?vault=MyVault"   --harness claude --harness codex --harness antigravity --dry-run

# Or initialize a fresh git-backed starter folder
python3 examples/init_memory.py ./my-memory
python3 memlint/memlint.py ./my-memory --all
python3 dream/dream_check.py ./my-memory
```

See [docs/AGENT_SETUP.md](docs/AGENT_SETUP.md) for the agent runbook and [docs/quickstart.md](docs/quickstart.md) for manual setup.

## What is in this repo

| Path | What it is |
|---|---|
| `docs/architecture.md` | Four-layer memory model, MUST/SHOULD harness contract, steering profiles, dreaming triggers, privacy/poisoning controls, and acceptance bars. |
| `docs/AGENT_SETUP.md` | Step-by-step runbook for agents setting up `hippo` for a user from an Obsidian URI or folder path. |
| `docs/PUBLIC_ROADMAP.md` | Public roadmap (v0.1 shipped, v0.2 next, v0.3+ later). |
| `docs/quickstart.md` | Manual first-run walkthrough from a clean clone. |
| `examples/setup_hippo.py` | Non-destructive setup helper: resolves Obsidian URIs or folder paths, merges missing scaffold files, and idempotently installs harness stanzas. |
| `examples/init_memory.py` | Creates a clean, git-backed starter memory folder for testing or starting fresh. |
| `memlint/` | Read-only linter for decision dates/sources (`D1`/`D2`), broken wikilinks (`L1`), reachability (`I1`), injected budget (`B1`/`B2`), page compression (`S1`), and `CONFLICT:` marks (`C1`). |
| `dream/` | Zero-token trigger checker (`dream_check.py`, triggers `T1`–`T6`) and proposals-only pass runner (`dream_pass.py`). |
| `handoff/` | Steering stanza generator (`stanza.py`), empirical eval suite (`handoff_test.py`: `direct`, `no-hint` P1, `write` P1b, `correction` F2, `concurrent` F1), and 10-question cross-note benchmark (`pages_benchmark.py` P3b). |
| `scripts/scan_public.py` | Full git-history and working-tree scanner for secrets and personal canaries. |
| `acceptance/` | End-to-end acceptance tests for all tools. |

## Roadmap

- **v0.1 (shipped):** Plain-file contract, agent-driven setup (`obsidian://` + folder paths), read-only linter with budget & compression checks, need-based dreaming triggers + proposals-only runner, full `P1`/`P1b`/`P2`/`P3a`/`P3b`/`F1`/`F2`/`V1`–`V3` eval suite.
- **v0.2 (next):** Published live model steering scores across Claude Code, Codex, Antigravity, and other harnesses; local transcript-pointer resolvers; `launchd`/`cron` trigger templates; tuned trigger thresholds from real dream logs.
- **v0.3+:** Interactive proposal-apply CLI, community harness adapters, and sync-conflict diagnostics.

Full details in [docs/PUBLIC_ROADMAP.md](docs/PUBLIC_ROADMAP.md).

## Run the tests

```bash
(cd memlint && python3 -m unittest -q) && \
(cd handoff && python3 -m unittest -q) && \
(cd dream && python3 -m unittest -q) && \
(cd examples && python3 -m unittest -q) && \
python3 -m unittest discover -s acceptance -q
```

## License

MIT — see [LICENSE](LICENSE).
