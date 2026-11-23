# Quickstart

`hippo` uses only the Python 3 standard library (no `pip install`).

## 1. Create a starter memory folder

```bash
python3 examples/init_memory.py ./my-memory
```

This creates a git-initialized Markdown memory folder with `AGENTS.md`, `agent/index.md`, `agent/decisions.md`, `agent/dream-log.md`, sample pages, and dated session notes.

## 2. Lint the memory folder

```bash
python3 memlint/memlint.py ./my-memory --all
```

This read-only check verifies:
- `D1`/`D2`: dated headings and sources in `agent/decisions.md`
- `L1`: broken wikilinks (use `--allowlist <file>` for intentional placeholders)
- `I1`: unreachable pages (with `daily/`, `runs/`, and `templates/` exempt)
- `B1`: injected-layer budget (`AGENTS.md` + `agent/index.md` <= 200 lines and 25 KB)
- `S1`: synthesized pages stay smaller than their cited local sources
- `C1`: unresolved `CONFLICT:` marks

## 3. Check whether a dream pass is needed

```bash
python3 dream/dream_check.py ./my-memory
```

Exit code `0` means no triggers fired (zero tokens spent). Exit code `1` means one or more triggers (`T1`–`T6`) fired. To inspect the scoped dream prompt without calling a model:

```bash
python3 dream/dream_pass.py --memory-dir ./my-memory --dry-run
```

## 4. Render a steering stanza for your harness

Start every harness on the lightest `pointer` profile and paste the output into its global instruction file (`~/.claude/CLAUDE.md`, `~/.codex/AGENTS.md`, or `~/.gemini/GEMINI.md`):

```bash
python3 handoff/stanza.py --memory-dir "$(cd ./my-memory && pwd)" --profile pointer
```

## 5. Run a handoff test

Using a local harness configuration (see `handoff/harnesses.example.json` or `handoff/harnesses.third-party.example.json`):

```bash
# Direct retrieval check
python3 handoff/handoff_test.py --memory-dir ./my-memory --config handoff/harnesses.example.json

# P1: measure whether a harness chooses to read memory on the pointer profile (10 runs)
python3 handoff/handoff_test.py --memory-dir ./my-memory --config handoff/harnesses.example.json \
  --mode no-hint --profile pointer --runs 10

# F2: verify corrections supersede older decisions
python3 handoff/handoff_test.py --memory-dir ./my-memory --config handoff/harnesses.example.json \
  --mode correction
```

## 6. Run the full test suite

```bash
(cd memlint && python3 -m unittest -q) && \
(cd handoff && python3 -m unittest -q) && \
(cd dream && python3 -m unittest -q) && \
python3 -m unittest discover -s acceptance -q
```
