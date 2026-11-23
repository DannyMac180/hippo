# Agent Setup Guide

If a human pointed you at `https://github.com/DannyMac180/hippo` and asked you to set up `hippo` for their notes or Obsidian vault, follow this runbook. Everything uses the Python 3 standard library — no packages to install.

## Step 1: Confirm three things with the human (one short message)

Before modifying any global instruction file or existing notes folder, confirm:

1. **Memory folder location:** Accepts a plain folder path (e.g. `~/Notes/Agents`), a `file://` URL, or an Obsidian URI (`obsidian://open?vault=MyVault`). By default, an Obsidian vault URI maps to `<Vault>/Agents/` so existing personal notes stay untouched.
2. **Which harnesses to connect:** Any combination of Claude Code (`~/.claude/CLAUDE.md`), Codex (`~/.codex/AGENTS.md`), and Antigravity (`~/.gemini/GEMINI.md`) — or any other Markdown-capable agent's global instruction file. All start on the lightest `pointer` profile (7 lines).
3. **Personal context boundary:** What non-sensitive personal context may live in `notes/personal-context.md` (readable by every connected vendor), and confirm that secrets and sensitive PII (SSN, bank accounts, exact home address, passwords, tokens) are always excluded.

## Step 2: Install by cloning, then preview the setup (read-only)

Clone the repository to a stable location. The clone is the install, and it is what `hippo update` updates later:

```bash
git clone https://github.com/DannyMac180/hippo.git ~/.hippo
```

Run `hippo.py setup` with `--dry-run` first to verify path resolution and see exactly which files will be created or updated:

```bash
python3 ~/.hippo/hippo.py setup "<path-or-obsidian-uri>" \
  --harness claude --harness codex --harness antigravity \
  --dry-run
```

## Step 3: Apply the non-destructive setup

Remove `--dry-run` once confirmed. `hippo.py setup` runs `examples/setup_hippo.py`, which:
- Preserves every existing file in the target folder and creates only missing scaffold files (`AGENTS.md`, `README.md`, `agent/index.md`, `agent/decisions.md`, `agent/dream-log.md`, `notes/personal-context.md`, `templates/session-note.md`).
- Inserts or updates the 7-line `pointer` stanza wrapped in `<!-- hippo:start -->` ... `<!-- hippo:end -->` in each selected harness's global instruction file without clobbering existing instructions.
- Runs `memlint --all` and `dream_check` to verify the folder is clean.

```bash
python3 ~/.hippo/hippo.py setup "<path-or-obsidian-uri>" \
  --harness claude --harness codex --harness antigravity
```

It then saves these choices to `~/.config/hippo/config.json` and links `~/.local/bin/hippo` to the clone (pass `--no-bin` to skip the link). From then on, `hippo update` moves the install to the newest release and re-applies the same setup; see `docs/RELEASING.md`.

If the user is starting from scratch (no existing vault), initialize a git-backed starter folder instead:

```bash
python3 examples/init_memory.py ~/agent-memory
```

## Step 4: Verify without touching live personal notes

Test the installed harnesses against a **synthetic** folder first (never run live handoff tests against the user's real vault without explicit approval):

```bash
python3 examples/init_memory.py /tmp/hippo-verify
python3 memlint/memlint.py /tmp/hippo-verify --all
python3 dream/dream_check.py /tmp/hippo-verify
python3 handoff/handoff_test.py --memory-dir /tmp/hippo-verify --config handoff/harnesses.example.json
```

To measure whether a harness needs stronger read steering than `pointer` (`P1`):

```bash
python3 handoff/handoff_test.py --memory-dir /tmp/hippo-verify \
  --config handoff/harnesses.example.json --mode no-hint --profile pointer --runs 10
```

If a harness passes `< 9/10`, re-run `hippo.py setup` for that harness with `--profile read-first` and re-measure.

## Step 5: Report back

Report concisely:
- Resolved memory folder path and which scaffold files were created vs preserved.
- Which harness global instruction files were updated and on which profile (`pointer`).
- Verification results (`memlint --all`, `dream_check`, and any synthetic handoff checks run).
- How to get updates: `hippo update --check` and `hippo update` (see `docs/RELEASING.md`).
- How to run periodic zero-token dream checks (`python3 dream/dream_check.py "<memory_dir>"`) and proposals-only passes (`python3 dream/dream_pass.py --memory-dir "<memory_dir>" --dry-run`).
