# Releasing and updating

## How users get updates

`hippo` is installed as a git clone, and git tags are the release channel:

```bash
git clone https://github.com/DannyMac180/hippo.git ~/.hippo
python3 ~/.hippo/hippo.py setup "<path or obsidian://open?vault=MyVault>" --harness claude
```

`hippo setup` saves its choices to `~/.config/hippo/config.json` (or `$XDG_CONFIG_HOME/hippo/config.json`, or `$HIPPO_CONFIG`) and links `~/.local/bin/hippo` to the clone. After that:

```bash
hippo update --check        # report whether a newer release exists
hippo update                # move to the newest vX.Y.Z release, then re-apply setup
hippo update --to v0.1.0    # move to a specific release (also rolls back)
hippo update --channel main # follow unreleased main instead of releases
hippo version
```

`hippo update`:

1. Refuses to run if tracked files in the clone have local edits.
2. Fetches tags from `origin` and checks out the newest `vX.Y.Z` tag (never moves backwards unless `--to` is given).
3. Prints the commits between the old and new version.
4. Re-runs the **new** version's setup from the saved config. Setup is non-destructive: it only rewrites the block between `<!-- hippo:start -->` and `<!-- hippo:end -->` in each harness file and creates scaffold files that are missing. Existing notes are never modified.

## How releases are made

Every push to `main` runs `.github/workflows/release.yml`:

1. Runs the full test suite. A failing suite publishes nothing.
2. Reads the labels on the merged pull request and computes the next tag with `scripts/release_version.py`:
   - `release:major` → next major, `release:minor` → next minor, `release:skip` → no release, otherwise → next patch.
   - The first release is `v0.1.0`.
3. Creates the tag and a GitHub release with generated notes.

Pull requests run the same suite in `.github/workflows/ci.yml` before merge. A release can also be cut by hand from the Actions tab (**Release → Run workflow**, choosing the bump).

The version lives only in git tags; no version file is committed back to `main`.
