# Installing LoomLedge

LoomLedge is a Python command line tool. It has no runtime dependencies - the
standard library is enough - so "installing" means putting the `loomledge`
command on your `PATH`.

## Prerequisites

| Requirement | Why | How to check |
|-------------|-----|--------------|
| Python 3.9 or newer | runs the tool | `python3 --version` |
| pip (ships with Python) | installs the command and the test runner | `python3 -m pip --version` |
| bash | runs `install.sh` and `demo.sh` | `bash --version` |

Nothing else is needed: no database server, no network service, no API key.
Windows users can run every command as `python3 loomledge.py ...` without the
install step.

## 1. Install the package and its dev dependencies

```bash
bash install.sh
```

That runs, in order:

1. `python3 -m pip install --upgrade pip setuptools wheel`
2. `python3 -m pip install -r requirements.txt` (the test runner, `pytest`)
3. `python3 -m pip install -e .` (adds the `loomledge` command)
4. a self-check: `python3 -m loomledge --version`

The script installs project-level dependencies only (no `sudo`, no system
packages) and **exits** - it never starts anything. Running it twice is safe.

## 2. Create the demo studio (optional, recommended for a first look)

```bash
python3 -m loomledge seed --data ./studio.json
```

Adds the demo operator `cenius` (passphrase `cenius`) and a studio of four looms,
eight tension readings and seven selvedge defects. Safe to re-run - it inserts
nothing twice.

## 3. Run it

```bash
printf 'cenius\n' | python3 -m loomledge operator login cenius --data ./studio.json
python3 -m loomledge loom list --data ./studio.json
python3 -m loomledge tension check "Ashford Jack" --grams 470 --data ./studio.json
python3 -m loomledge defect list --open --data ./studio.json
```

Use your own data file per studio with `--data PATH`; without it LoomLedge uses
`~/.loomledge/data.json`. There is no server and no port: each command runs,
prints and exits.

## 4. Run the tests

```bash
python3 -m pytest
```

All tests work offline against scratch data files.

## 5. Regenerate the demo transcript

```bash
bash demo.sh
```

Runs the full loop against a scratch data file and rewrites
`demo-transcript.txt` in place.

## Troubleshooting

- `python3: command not found` - install Python 3.9+ from your platform's
  package manager; that is the only system prerequisite.
- `loomledge: command not found` after installing - use
  `python3 -m loomledge ...` (works without any install) or re-run
  `bash install.sh` and check that pip's script directory is on `PATH`.
- `not signed in - run operator login <name>` - every data command needs a
  session; run `operator login` first.
- `name or passphrase is incorrect` - exactly that; a wrong name and a wrong
  passphrase are deliberately indistinguishable.
- Forgot the passphrase of a real studio file - there is no reset path by
  design (no email, no network). Start a new data file.
