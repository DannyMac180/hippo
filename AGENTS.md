# AGENTS.md

Instructions for coding agents working in `hippo`.

## Setting up hippo for a user

If the user asked you to set up `hippo` for their Obsidian vault or notes folder, follow `docs/AGENT_SETUP.md` and use `examples/setup_hippo.py`.

## Code and test conventions

- Keep all tools Python 3 standard library only (no third-party dependencies).
- Never commit personal memory contents, tokens, or secrets.
- Do not weaken `acceptance/` tests to make a change pass; add unit or acceptance tests for new behavior.
- Run the full suite before reporting any code change:

```bash
(cd memlint && python3 -m unittest -q) && \
(cd handoff && python3 -m unittest -q) && \
(cd dream && python3 -m unittest -q) && \
(cd examples && python3 -m unittest -q) && \
python3 -m unittest discover -s acceptance -q
```
