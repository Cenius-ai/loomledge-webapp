# Using loomledge

## Overview

Warp tension tolerance bands, heddle counts and selvedge defects for hand-weaving studios.

## Quickstart

```bash
loomledge --help
# or, if installed from source without symlink:
python -m loomledge --help
```

## Commands

This CLI exposes **19** subcommands. Each was extracted from the source.

| Command | Source file |
|---------|-------------|
| `loomledge add` | `loomledge.py` |
| `loomledge band` | `loomledge.py` |
| `loomledge check` | `loomledge.py` |
| `loomledge defect` | `loomledge.py` |
| `loomledge history` | `loomledge.py` |
| `loomledge list` | `loomledge.py` |
| `loomledge log` | `loomledge.py` |
| `loomledge login` | `loomledge.py` |
| `loomledge logout` | `loomledge.py` |
| `loomledge loom` | `loomledge.py` |
| `loomledge operator` | `loomledge.py` |
| `loomledge register` | `loomledge.py` |
| `loomledge resolve` | `loomledge.py` |
| `loomledge seed` | `loomledge.py` |
| `loomledge set` | `loomledge.py` |
| `loomledge set-heddles` | `loomledge.py` |
| `loomledge set-shafts` | `loomledge.py` |
| `loomledge show` | `loomledge.py` |
| `loomledge tension` | `loomledge.py` |

### Per-command help

```bash
loomledge --help                # top-level help
loomledge add --help    # help for `add`
```

## Worked example

1. Verify the CLI is on your PATH: `which loomledge` (or run via `cargo run --`/`python -m`).
2. Show all subcommands: `loomledge --help`.
3. Try the first detected subcommand:

```bash
loomledge add --help
```

## Reference

### Entry points

| Kind | Name | Source |
|------|------|--------|
| Console script | `loomledge` | `loomledge:main` (pyproject.toml) |

### Surface index (auto-extracted)

| Kind | Title | Source file |
|------|-------|-------------|
| command | `add` | `loomledge.py` |
| command | `band` | `loomledge.py` |
| command | `check` | `loomledge.py` |
| command | `defect` | `loomledge.py` |
| command | `history` | `loomledge.py` |
| command | `list` | `loomledge.py` |
| command | `log` | `loomledge.py` |
| command | `login` | `loomledge.py` |
| command | `logout` | `loomledge.py` |
| command | `loom` | `loomledge.py` |
| command | `operator` | `loomledge.py` |
| command | `register` | `loomledge.py` |
| command | `resolve` | `loomledge.py` |
| command | `seed` | `loomledge.py` |
| command | `set` | `loomledge.py` |
| command | `set-heddles` | `loomledge.py` |
| command | `set-shafts` | `loomledge.py` |
| command | `show` | `loomledge.py` |
| command | `tension` | `loomledge.py` |

## Next steps

- Read [INSTALL.md](./INSTALL.md) if anything in the Quickstart didn't work.
- Inspect `.env.example` for the full list of environment variables this project understands.
- See [README.md](./README.md) for the architecture diagram + feature list.
