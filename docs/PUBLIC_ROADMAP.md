# hippo Roadmap

`hippo` is built in the open around three design principles grounded in Anthropic's ["The components of agent memory"](https://www.anthropic.com/research/components-of-agent-memory), adapted to stay **vendor-agnostic** and **human-inspectable**.

## Shipped (v0.1)

- **Plain-file harness contract (`docs/architecture.md`):** Works with any agent that reads UTF-8 Markdown — no database, no vector store, no vendor lock-in, zero third-party dependencies (Python 3 stdlib only).
- **Agent-driven setup (`examples/setup_hippo.py`, `docs/AGENT_SETUP.md`):** Accepts a folder path, `file://` URL, or `obsidian://open?vault=...` URI; merges missing scaffold files non-destructively; installs idempotent steering stanzas into Claude Code, Codex, and Antigravity.
- **Read-only memory linter (`memlint/`):** Checks decision dates & sources (`D1`/`D2`), broken wikilinks (`L1`, with allowlists), page reachability (`I1`, with dated logs exempt), injected-layer budget (`B1`/`B2`: 200 lines / 25 KB + 10-line stanza), page compression (`S1`: pages must be smaller than cited local sources), and `CONFLICT:` marks (`C1`).
- **Need-based dreaming (`dream/`):** Zero-token deterministic trigger checker (`T1` conflicts, `T2` backlog, `T3` lint regression, `T4` >80% budget, `T5` overdue `review_after`, `T6` 28-day backstop) plus a proposals-only pass runner that never edits live memory in place.
- **Empirical eval harness (`handoff/`):**
  - Steering profile measurement (`--mode no-hint` / `P1`, `pointer` → `read-first` → `inject`).
  - Write-on-request check (`--mode write` / `P1b`).
  - Correction propagation check (`--mode correction` / `F2`).
  - Concurrent two-writer collision test (`--mode concurrent` / `F1`).
  - Cross-note pages-vs-search benchmark (`pages_benchmark.py` / `P3b`).
- **Open-source safety scanner (`scripts/scan_public.py`):** Full working-tree and git-history scanner for secrets, local paths, emails, and custom canaries.

## Next (v0.2)

- **Empirical steering benchmarks across live models:** Publish measured `P1` / `P1b` / `F1` / `F2` / `P3b` scores across Claude Code, Codex, Antigravity, and other harnesses so users know which models hold `pointer` vs need `read-first`.
- **Transcript pointer resolvers:** Optional read-only adapters for checking claims against local Claude Code, Codex, and Antigravity session log files on the machine where a session ran.
- **Trigger threshold tuning:** Adjust `T2` backlog, `T3` unreachable delta, and `T6` backstop defaults using real `agent/dream-log.md` histories.
- **Scheduled runner templates:** Ready-to-use `launchd`, `cron`, and systemd examples that run `dream_check.py` cheaply and invoke `dream_pass.py` only when a trigger fires.

## Later (v0.3+)

- **Proposal review & apply helper:** Interactive CLI to inspect a generated `proposals/dream-*.md` file and apply accepted hunks cleanly into git.
- **Community harness configs & benchmarks:** Additional read-only/read-write harness templates (Cursor, Windsurf, Goose, OpenCode, etc.) and anonymized multi-document benchmark packs for `pages_benchmark.py`.
- **Sync-conflict diagnostics:** Tools to detect and surface sync-engine conflict copies (Obsidian Sync, iCloud, Syncthing) as `T1` dream triggers automatically.
