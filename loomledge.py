#!/usr/bin/env python3
"""LoomLedge - warp tension tolerance bands, heddle counts and selvedge defects.

LoomLedge is a single-file, standard-library-only command line tool for hand
weaving studios.  It registers each loom with a heddle count and an optional
shaft count (deriving heddles-per-shaft from the two), stores a warp tension
tolerance band per loom and grades each tension reading as ok, warn or fail,
and logs selvedge defects that can be listed and resolved - all behind a local
passphrase sign-in, persisted in one JSON data file chosen with --data.

Exit codes
    0  the command succeeded
    1  a user or data error (unknown loom, bad value, not signed in, ...)
    2  a usage error (unknown flag, missing argument)

Run `loomledge --help` for the command groups, or
`loomledge <group> <command> --help` for one command's options.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

APP_NAME = "LoomLedge"
APP_VERSION = "1.0.0"

DEFAULT_DATA_PATH = Path.home() / ".loomledge" / "data.json"

PBKDF2_ITERATIONS = 120_000
SALT_BYTES = 16
SESSION_HOURS = 12

# The warn band sits just outside the tolerance band: a reading that misses the
# band by no more than WARN_MARGIN_RATIO of the band's upper bound is a warning,
# anything further out is a failure.  With a 380-460 g band the margin is 23 g,
# so 470 g warns and 520 g fails.
WARN_MARGIN_RATIO = 0.05

SEVERITIES = ("low", "med", "high")
SIDES = ("left", "right", "both")

DEMO_OPERATOR = "cenius"
DEMO_PASSPHRASE = "cenius"

STORE_LISTS = ("operators", "sessions", "looms", "readings", "defects")

EXAMPLES = """
examples
  loomledge seed --data ./studio.json
  loomledge operator login cenius --data ./studio.json
  loomledge loom add Rig --heddles 400 --shafts 8 --data ./studio.json
  loomledge band set Rig --min 380 --max 460 --data ./studio.json
  loomledge tension check Rig --grams 420 --data ./studio.json
  loomledge defect log Rig --type float --severity high --side left -m "two floats" --data ./studio.json
  loomledge defect list --open --data ./studio.json

demo login  operator cenius / passphrase cenius (created by `loomledge seed`)
"""


# ---------------------------------------------------------------------------
# presentation
# ---------------------------------------------------------------------------

# Committed design direction for LoomLedge: terminal dark surface, mono type,
# aligned columns, box-drawing separators, one accent (oklch(0.58 0.12 192) ==
# #00908d) for the primary status plus amber/red for the two failure statuses.
# Colour is decoration only: every status is also spelled out in text.
ACCENT_HEX = "#00908d"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class Style:
    """ANSI decoration that switches itself off for pipes, files and NO_COLOR."""

    CODES = {
        "accent": "\x1b[38;2;0;144;141m",
        "ok": "\x1b[38;2;0;144;141m",
        "warn": "\x1b[38;2;217;164;65m",
        "fail": "\x1b[38;2;224;108;117m",
        "muted": "\x1b[38;2;128;138;145m",
        "bold": "\x1b[1m",
    }
    RESET = "\x1b[0m"

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def paint(self, token: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"{self.CODES[token]}{text}{self.RESET}"

    def header(self, text: str) -> str:
        return self.paint("bold", text)

    def muted(self, text: str) -> str:
        return self.paint("muted", text)

    def verdict(self, verdict: str) -> str:
        return self.paint(verdict, verdict)


def visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def _pad(text: str, width: int, align: str) -> str:
    fill = " " * max(0, width - visible_len(text))
    return fill + text if align == "right" else text + fill


def render_table(
    headers: list[str],
    rows: list[list[str]],
    aligns: list[str] | None = None,
    style: Style | None = None,
) -> list[str]:
    """Render an aligned, box-ruled table with one header rule."""
    aligns = aligns or ["left"] * len(headers)
    widths = [
        max([visible_len(head)] + [visible_len(row[i]) for row in rows])
        for i, head in enumerate(headers)
    ]
    head_line = "  ".join(_pad(h, w, a) for h, w, a in zip(headers, widths, aligns)).rstrip()
    rule = "  ".join("─" * w for w in widths)
    lines = [
        style.header(head_line) if style else head_line,
        style.muted(rule) if style else rule,
    ]
    for row in rows:
        lines.append(
            "  ".join(_pad(c, w, a) for c, w, a in zip(row, widths, aligns)).rstrip()
        )
    return lines


def render_kv(pairs: list[tuple[str, str]]) -> list[str]:
    """Render label/value pairs as one aligned block."""
    width = max(visible_len(label) for label, _ in pairs)
    return [f"{label.ljust(width)}  {value}".rstrip() for label, value in pairs]


def print_lines(lines: list[str]) -> None:
    for line in lines:
        print(line)


def print_hint(style: Style, text: str) -> None:
    print(f"{style.muted('hint')}  {text}")


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------


class UserError(Exception):
    """A user or data problem: reported on stderr, exit code 1."""


# ---------------------------------------------------------------------------
# time and number formatting
# ---------------------------------------------------------------------------


def now_utc() -> datetime:
    """Current UTC time, or the LOOMLEDGE_NOW override (used for transcripts)."""
    override = os.environ.get("LOOMLEDGE_NOW", "").strip()
    if override:
        try:
            return parse_timestamp(override)
        except ValueError:
            raise UserError(
                f"LOOMLEDGE_NOW must be an ISO-8601 timestamp (got {override!r})"
            )
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(raw: str) -> datetime:
    text = str(raw).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def fmt_moment(raw: str) -> str:
    """'2024-05-01T09:14:00Z' -> '2024-05-01 09:14 UTC'."""
    try:
        return parse_timestamp(raw).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return str(raw)


def fmt_day(raw: str) -> str:
    try:
        return parse_timestamp(raw).strftime("%Y-%m-%d")
    except ValueError:
        return str(raw)[:10]


def fmt_num(value: float) -> str:
    """Print whole numbers without a decimal tail: 380.0 -> '380', 380.5 -> '380.5'."""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def fmt_ratio(value: float) -> str:
    return f"{value:.2f}"


def fmt_band(band_min: float, band_max: float) -> str:
    return f"{fmt_num(band_min)}-{fmt_num(band_max)} g"


# ---------------------------------------------------------------------------
# validation helpers
# ---------------------------------------------------------------------------


def parse_positive_int(raw: str, label: str) -> int:
    problem = UserError(f"{label} must be a whole number greater than zero (got {raw!r})")
    try:
        value = int(str(raw).strip(), 10)
    except (TypeError, ValueError):
        raise problem
    if value <= 0:
        raise problem
    return value


def parse_positive_float(raw: str, label: str) -> float:
    problem = UserError(f"{label} must be a number greater than zero (got {raw!r})")
    try:
        value = float(str(raw).strip())
    except (TypeError, ValueError):
        raise problem
    if not value > 0 or value != value or value in (float("inf"), float("-inf")):
        raise problem
    return value


def parse_choice(raw: str, allowed: tuple[str, ...], label: str) -> str:
    text = str(raw).strip().lower()
    if text not in allowed:
        options = ", ".join(allowed)
        raise UserError(f"{label} must be one of {options} (got {raw!r})")
    return text


def normalise_name(raw: str, label: str, limit: int = 64) -> str:
    name = str(raw or "").strip()
    if not name:
        raise UserError(f"{label} is required")
    if len(name) > limit:
        raise UserError(f"{label} must be {limit} characters or fewer (got {len(name)})")
    if any(ch < " " or ch == "\x7f" for ch in name):
        raise UserError(f"{label} must not contain control characters")
    return name


# ---------------------------------------------------------------------------
# data store
# ---------------------------------------------------------------------------


def empty_store() -> dict:
    store: dict = {key: [] for key in STORE_LISTS}
    store["version"] = 1
    store["next_defect_id"] = 1
    return store


def load_store(path: Path) -> dict:
    if not path.exists():
        return empty_store()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise UserError(f"could not read data file {path}: {exc.strerror or exc}")
    if not text.strip():
        return empty_store()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UserError(f"data file {path} is not valid JSON (line {exc.lineno}: {exc.msg})")
    if not isinstance(raw, dict):
        raise UserError(f"data file {path} is not a LoomLedge data file")

    store = empty_store()
    for key in STORE_LISTS:
        value = raw.get(key)
        if isinstance(value, list):
            store[key] = [row for row in value if isinstance(row, dict)]
    next_id = raw.get("next_defect_id")
    if isinstance(next_id, int) and not isinstance(next_id, bool) and next_id > 0:
        store["next_defect_id"] = next_id
    else:
        highest = max([int(d.get("id", 0)) for d in store["defects"]] or [0])
        store["next_defect_id"] = highest + 1
    return store


def save_store(path: Path, store: dict) -> None:
    """Write the whole document to a temp file, then os.replace it into place."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise UserError(f"could not create data directory {path.parent}: {exc.strerror or exc}")

    payload = json.dumps(store, indent=2, ensure_ascii=False) + "\n"
    tmp_name = ""
    try:
        descriptor, tmp_name = tempfile.mkstemp(
            prefix=f"{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except OSError as exc:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
        raise UserError(f"could not write data file {path}: {exc.strerror or exc}")


# ---------------------------------------------------------------------------
# lookups
# ---------------------------------------------------------------------------


def find_operator(store: dict, name: str) -> dict | None:
    wanted = str(name or "").strip().casefold()
    for operator in store["operators"]:
        if str(operator.get("name", "")).casefold() == wanted:
            return operator
    return None


def find_operator_by_id(store: dict, operator_id: str) -> dict | None:
    for operator in store["operators"]:
        if operator.get("id") == operator_id:
            return operator
    return None


def find_loom(store: dict, name: str) -> dict:
    wanted = str(name or "").strip().casefold()
    for loom in store["looms"]:
        if str(loom.get("name", "")).casefold() == wanted:
            return loom
    raise UserError(f"no loom named {str(name).strip()} - run loom list")


def find_loom_or_none(store: dict, name: str) -> dict | None:
    try:
        return find_loom(store, name)
    except UserError:
        return None


def find_defect(store: dict, defect_id: int) -> dict:
    for defect in store["defects"]:
        if defect.get("id") == defect_id:
            return defect
    raise UserError(f"no defect {defect_id} - run defect list")


def loom_band(loom: dict) -> tuple[float, float]:
    band_min = loom.get("band_min_g")
    band_max = loom.get("band_max_g")
    if band_min is None or band_max is None:
        raise UserError(
            f"no tolerance band set for {loom['name']} - "
            f"run band set {loom['name']} --min G --max G"
        )
    return float(band_min), float(band_max)


def open_defect_count(store: dict, loom_id: str) -> int:
    return sum(
        1
        for defect in store["defects"]
        if defect.get("loom_id") == loom_id and defect.get("status") == "open"
    )


def heddles_per_shaft(loom: dict) -> float | None:
    shafts = loom.get("shaft_count")
    if not shafts:
        return None
    return int(loom["heddle_count"]) / int(shafts)


def warn_margin(band_max: float) -> float:
    """The warn margin measured from the band's upper bound."""
    return WARN_MARGIN_RATIO * float(band_max)


def classify_reading(grams: float, band_min: float, band_max: float) -> str:
    margin = warn_margin(band_max)
    if band_min <= grams <= band_max:
        return "ok"
    if band_min - margin <= grams < band_min or band_max < grams <= band_max + margin:
        return "warn"
    return "fail"


# ---------------------------------------------------------------------------
# sessions and passphrases
# ---------------------------------------------------------------------------


def current_operator(store: dict) -> dict | None:
    sessions = store.get("sessions") or []
    if not sessions:
        return None
    session = sessions[0]
    try:
        expires_at = parse_timestamp(session.get("expires_at", ""))
    except ValueError:
        return None
    if expires_at <= now_utc():
        return None
    return find_operator_by_id(store, str(session.get("operator_id", "")))


def require_session(store: dict) -> dict:
    operator = current_operator(store)
    if operator is not None:
        return operator
    if not store["operators"]:
        raise UserError("no operators yet - run operator register <name>")
    raise UserError("not signed in - run operator login <name>")


def hash_passphrase(passphrase: str, salt_hex: str, iterations: int) -> str:
    digest = hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
    )
    return digest.hex()


def verify_passphrase(operator: dict, passphrase: str) -> bool:
    try:
        expected = hash_passphrase(
            passphrase, str(operator["salt"]), int(operator["iterations"])
        )
    except (KeyError, ValueError):
        return False
    return secrets.compare_digest(expected, str(operator.get("passphrase_hash", "")))


def read_secret(label: str, args: argparse.Namespace) -> str:
    """Read a passphrase: getpass in a terminal, one stdin line when piped."""
    piped = bool(getattr(args, "passphrase_stdin", False)) or not sys.stdin.isatty()
    if piped:
        line = sys.stdin.readline()
        if not line:
            raise UserError(
                "no passphrase on stdin - pipe one line per prompt or run in a terminal"
            )
        return line.rstrip("\r\n")
    try:
        return getpass.getpass(label)
    except (EOFError, KeyboardInterrupt):
        raise UserError("passphrase entry cancelled - nothing was changed")


# ---------------------------------------------------------------------------
# command context
# ---------------------------------------------------------------------------


@dataclass
class Context:
    store: dict
    path: Path
    style: Style


# ---------------------------------------------------------------------------
# operator commands
# ---------------------------------------------------------------------------


def cmd_operator_register(ctx: Context, args: argparse.Namespace) -> bool:
    name = normalise_name(args.name, "operator name")
    if find_operator(ctx.store, name) is not None:
        raise UserError(f"an operator named {name} already exists - run operator login {name}")

    passphrase = read_secret(f"passphrase for {name}: ", args)
    repeated = read_secret("repeat passphrase: ", args)
    if passphrase != repeated:
        raise UserError("passphrases do not match - run operator register again")
    if len(passphrase) < 4:
        raise UserError("passphrase must be at least 4 characters")

    salt_hex = secrets.token_hex(SALT_BYTES)
    ctx.store["operators"].append(
        {
            "id": f"op_{secrets.token_hex(6)}",
            "name": name,
            "passphrase_hash": hash_passphrase(passphrase, salt_hex, PBKDF2_ITERATIONS),
            "salt": salt_hex,
            "iterations": PBKDF2_ITERATIONS,
            "created_at": iso(now_utc()),
        }
    )
    print(f"registered operator {name}")
    print_hint(ctx.style, f"run operator login {name} to sign in")
    return True


def cmd_operator_login(ctx: Context, args: argparse.Namespace) -> bool:
    name = str(args.name or "").strip()
    operator = find_operator(ctx.store, name)
    passphrase = read_secret(f"passphrase for {name}: ", args)
    if operator is None:
        # Do the same work as a real check so a wrong name and a wrong
        # passphrase stay indistinguishable.
        hashlib.pbkdf2_hmac(
            "sha256", passphrase.encode("utf-8"), b"\x00" * 16, PBKDF2_ITERATIONS
        )
        raise UserError("name or passphrase is incorrect")
    if not verify_passphrase(operator, passphrase):
        raise UserError("name or passphrase is incorrect")

    signed_in_at = now_utc()
    expires_at = signed_in_at + timedelta(hours=SESSION_HOURS)
    ctx.store["sessions"] = [
        {
            "token": secrets.token_urlsafe(32),
            "operator_id": operator["id"],
            "created_at": iso(signed_in_at),
            "expires_at": iso(expires_at),
        }
    ]
    print(f"signed in as {operator['name']} (session expires {fmt_moment(iso(expires_at))})")
    print_hint(ctx.style, "data commands now run without the passphrase")
    return True


def cmd_operator_logout(ctx: Context, args: argparse.Namespace) -> bool:
    del args
    operator = require_session(ctx.store)
    ctx.store["sessions"] = []
    print(f"signed out {operator['name']}")
    return True


# ---------------------------------------------------------------------------
# loom commands
# ---------------------------------------------------------------------------


def cmd_loom_add(ctx: Context, args: argparse.Namespace) -> bool:
    operator = require_session(ctx.store)
    name = normalise_name(args.name, "loom name")
    if find_loom_or_none(ctx.store, name) is not None:
        raise UserError(f"a loom named {name} already exists - run loom list")
    heddles = parse_positive_int(args.heddles, "heddles")
    shafts = parse_positive_int(args.shafts, "shafts") if args.shafts is not None else None

    loom = {
        "id": f"loom_{secrets.token_hex(6)}",
        "name": name,
        "heddle_count": heddles,
        "shaft_count": shafts,
        "band_min_g": None,
        "band_max_g": None,
        "created_at": iso(now_utc()),
        "created_by": operator["id"],
    }
    ctx.store["looms"].append(loom)

    if shafts is None:
        print(f"added loom {name} ({heddles} heddles)")
        print_hint(ctx.style, f"run loom set-shafts {name} S to record shafts")
    else:
        print(
            f"added loom {name} ({heddles} heddles, {shafts} shafts, "
            f"{fmt_ratio(heddles_per_shaft(loom) or 0.0)} heddles per shaft)"
        )
    return True


def cmd_loom_list(ctx: Context, args: argparse.Namespace) -> bool:
    del args
    require_session(ctx.store)
    looms = sorted(ctx.store["looms"], key=lambda row: str(row.get("name", "")).casefold())
    if not looms:
        print("no looms yet - run loom add <name> --heddles N [--shafts S]")
        return False

    rows = []
    for loom in looms:
        band_min = loom.get("band_min_g")
        band_max = loom.get("band_max_g")
        band = (
            fmt_band(float(band_min), float(band_max))
            if band_min is not None and band_max is not None
            else "-"
        )
        shafts = loom.get("shaft_count")
        rows.append(
            [
                str(loom.get("name", "")),
                str(loom.get("heddle_count", 0)),
                str(shafts) if shafts else "-",
                band,
                str(open_defect_count(ctx.store, str(loom.get("id", "")))),
            ]
        )
    print_lines(
        render_table(
            ["LOOM", "HEDDLES", "SHAFTS", "BAND", "OPEN"],
            rows,
            ["left", "right", "right", "left", "right"],
            ctx.style,
        )
    )
    return False


def cmd_loom_show(ctx: Context, args: argparse.Namespace) -> bool:
    require_session(ctx.store)
    loom = find_loom(ctx.store, args.name)
    pairs: list[tuple[str, str]] = [
        ("loom", str(loom.get("name", ""))),
        ("heddles", str(loom.get("heddle_count", 0))),
    ]
    ratio = heddles_per_shaft(loom)
    if ratio is not None:
        pairs.append(("shafts", str(loom.get("shaft_count"))))
        pairs.append(("heddles/shaft", fmt_ratio(ratio)))
    band_min = loom.get("band_min_g")
    band_max = loom.get("band_max_g")
    pairs.append(
        (
            "band",
            fmt_band(float(band_min), float(band_max))
            if band_min is not None and band_max is not None
            else "-",
        )
    )
    pairs.append(("open defects", str(open_defect_count(ctx.store, str(loom.get("id", ""))))))
    pairs.append(("added", fmt_moment(str(loom.get("created_at", "")))))
    print_lines(render_kv(pairs))
    if ratio is None:
        print_hint(ctx.style, f"run loom set-shafts {loom.get('name')} S to record shafts")
    return False


def cmd_loom_set_heddles(ctx: Context, args: argparse.Namespace) -> bool:
    require_session(ctx.store)
    loom = find_loom(ctx.store, args.name)
    heddles = parse_positive_int(args.heddles, "heddles")
    loom["heddle_count"] = heddles
    ratio = heddles_per_shaft(loom)
    if ratio is None:
        print(f"loom {loom.get('name')} now has {heddles} heddles")
    else:
        print(
            f"loom {loom.get('name')} now has {heddles} heddles "
            f"({fmt_ratio(ratio)} heddles per shaft)"
        )
    return True


def cmd_loom_set_shafts(ctx: Context, args: argparse.Namespace) -> bool:
    require_session(ctx.store)
    loom = find_loom(ctx.store, args.name)
    if args.clear:
        loom["shaft_count"] = None
        print(f"cleared the shaft count for {loom.get('name')}")
        print_hint(ctx.style, f"run loom set-shafts {loom.get('name')} S to set it again")
        return True
    shafts = parse_positive_int(args.shafts, "shafts")
    loom["shaft_count"] = shafts
    ratio = heddles_per_shaft(loom) or 0.0
    print(
        f"loom {loom.get('name')} now has {shafts} shafts "
        f"({fmt_ratio(ratio)} heddles per shaft)"
    )
    return True


# ---------------------------------------------------------------------------
# band commands
# ---------------------------------------------------------------------------


def cmd_band_set(ctx: Context, args: argparse.Namespace) -> bool:
    require_session(ctx.store)
    loom = find_loom(ctx.store, args.loom)
    band_min = parse_positive_float(args.min, "min")
    band_max = parse_positive_float(args.max, "max")
    if band_min >= band_max:
        raise UserError(
            f"min must be less than max (got {fmt_num(band_min)} and {fmt_num(band_max)})"
        )
    loom["band_min_g"] = band_min
    loom["band_max_g"] = band_max
    print(f"tolerance band for {loom.get('name')} set to {fmt_band(band_min, band_max)}")
    return True


def cmd_band_show(ctx: Context, args: argparse.Namespace) -> bool:
    require_session(ctx.store)
    loom = find_loom(ctx.store, args.loom)
    band_min, band_max = loom_band(loom)
    print(f"tolerance band for {loom.get('name')}: {fmt_band(band_min, band_max)}")
    print_hint(
        ctx.style, f"run tension check {loom.get('name')} --grams G to grade a reading"
    )
    return False


# ---------------------------------------------------------------------------
# tension commands
# ---------------------------------------------------------------------------


def cmd_tension_check(ctx: Context, args: argparse.Namespace) -> bool:
    operator = require_session(ctx.store)
    loom = find_loom(ctx.store, args.loom)
    band_min, band_max = loom_band(loom)
    grams = parse_positive_float(args.grams, "grams")
    verdict = classify_reading(grams, band_min, band_max)
    margin = warn_margin(band_max)

    ctx.store["readings"].append(
        {
            "id": f"rd_{secrets.token_hex(6)}",
            "loom_id": loom.get("id"),
            "grams": grams,
            "verdict": verdict,
            "checked_at": iso(now_utc()),
            "operator_id": operator["id"],
        }
    )

    pairs = [
        ("reading", f"{fmt_num(grams)} g"),
        ("band", fmt_band(band_min, band_max)),
        ("margin", f"{fmt_num(margin)} g either side of the band warns"),
        ("verdict", ctx.style.verdict(verdict)),
    ]
    print_lines(render_kv(pairs))
    print_hint(ctx.style, f"run tension history {loom.get('name')} to see stored readings")
    return True


def cmd_tension_history(ctx: Context, args: argparse.Namespace) -> bool:
    require_session(ctx.store)
    loom = find_loom(ctx.store, args.loom)
    limit = parse_positive_int(args.limit, "limit") if args.limit is not None else None

    readings = [row for row in ctx.store["readings"] if row.get("loom_id") == loom.get("id")]
    readings.reverse()  # stored oldest first, listed newest first
    if limit is not None:
        readings = readings[:limit]

    if not readings:
        print(
            f"no readings for {loom.get('name')} yet - "
            f"run tension check {loom.get('name')} --grams G"
        )
        return False

    rows = [
        [
            fmt_moment(str(row.get("checked_at", ""))),
            fmt_num(float(row.get("grams", 0))),
            ctx.style.verdict(str(row.get("verdict", ""))),
        ]
        for row in readings
    ]
    print_lines(
        render_table(["CHECKED", "GRAMS", "VERDICT"], rows, ["left", "right", "left"], ctx.style)
    )
    return False


# ---------------------------------------------------------------------------
# defect commands
# ---------------------------------------------------------------------------


def cmd_defect_log(ctx: Context, args: argparse.Namespace) -> bool:
    operator = require_session(ctx.store)
    loom = find_loom(ctx.store, args.loom)
    severity = parse_choice(args.severity, SEVERITIES, "severity")
    side = parse_choice(args.side, SIDES, "side")
    defect_type = normalise_name(args.type, "defect type", limit=40)
    note = str(args.message or "").strip()
    if len(note) > 500:
        raise UserError(f"note must be 500 characters or fewer (got {len(note)})")

    defect_id = int(ctx.store["next_defect_id"])
    ctx.store["next_defect_id"] = defect_id + 1
    ctx.store["defects"].append(
        {
            "id": defect_id,
            "loom_id": loom.get("id"),
            "type": defect_type,
            "severity": severity,
            "side": side,
            "note": note,
            "status": "open",
            "logged_at": iso(now_utc()),
            "resolved_at": None,
            "operator_id": operator["id"],
        }
    )
    print(f"logged defect #{defect_id} ({defect_type}, {severity}, {side}) on {loom.get('name')}")
    print_hint(ctx.style, "run defect list --open to see every open defect")
    return True


def cmd_defect_list(ctx: Context, args: argparse.Namespace) -> bool:
    require_session(ctx.store)
    loom_id = None
    loom_name = ""
    if args.loom:
        loom = find_loom(ctx.store, args.loom)
        loom_id = loom.get("id")
        loom_name = str(loom.get("name"))

    defects = list(ctx.store["defects"])
    if loom_id is not None:
        defects = [row for row in defects if row.get("loom_id") == loom_id]
    type_filter = str(args.type).strip() if args.type else ""
    if type_filter:
        wanted = type_filter.casefold()
        defects = [row for row in defects if str(row.get("type", "")).casefold() == wanted]

    open_count = sum(1 for row in defects if row.get("status") == "open")
    if args.open:
        defects = [row for row in defects if row.get("status") == "open"]

    if not ctx.store["defects"]:
        print(
            "no defects logged - run defect log <loom> --type T "
            "--severity low|med|high --side left|right|both"
        )
        return False
    if not defects:
        filters = []
        if loom_name:
            filters.append(f"loom {loom_name}")
        if type_filter:
            filters.append(f"type {type_filter}")
        if args.open:
            filters.append("open only")
        described = ", ".join(filters) if filters else "the current filters"
        print(f"no defects match {described} - drop --loom/--type or --open to widen the list")
        return False

    defects.sort(
        key=lambda row: (str(row.get("logged_at", "")), int(row.get("id", 0))), reverse=True
    )
    loom_names = {str(loom.get("id")): str(loom.get("name")) for loom in ctx.store["looms"]}
    rows = []
    for row in defects:
        status = str(row.get("status", ""))
        rows.append(
            [
                str(row.get("id", "")),
                fmt_day(str(row.get("logged_at", ""))),
                loom_names.get(str(row.get("loom_id", "")), "-"),
                str(row.get("type", "")),
                str(row.get("severity", "")),
                str(row.get("side", "")),
                ctx.style.verdict(status) if status == "resolved" else status,
            ]
        )
    plural = "" if open_count == 1 else "s"
    print(f"{open_count} open defect{plural}")
    print_lines(
        render_table(
            ["ID", "DATE", "LOOM", "TYPE", "SEVERITY", "SIDE", "STATUS"],
            rows,
            ["right", "left", "left", "left", "left", "left", "left"],
            ctx.style,
        )
    )
    return False


def cmd_defect_resolve(ctx: Context, args: argparse.Namespace) -> bool:
    require_session(ctx.store)
    defect_id = parse_positive_int(args.id, "defect id")
    defect = find_defect(ctx.store, defect_id)
    if defect.get("status") == "resolved":
        resolved_on = fmt_day(str(defect.get("resolved_at") or ""))
        raise UserError(
            f"defect {defect_id} is already resolved (on {resolved_on}) - run defect list"
        )
    defect["status"] = "resolved"
    defect["resolved_at"] = iso(now_utc())
    loom = next(
        (row for row in ctx.store["looms"] if row.get("id") == defect.get("loom_id")), None
    )
    loom_label = str(loom.get("name")) if loom else "-"
    remaining = open_defect_count(ctx.store, str(defect.get("loom_id")))
    print(f"resolved defect #{defect_id} on {loom_label} ({remaining} still open)")
    return True


# ---------------------------------------------------------------------------
# seed: the demo studio
# ---------------------------------------------------------------------------

DEMO_LOOMS = [
    # name, heddles, shafts, band_min, band_max
    ("Ashford Jack", 400, 8, 380.0, 460.0),
    ("Glimakra Standard", 720, 6, 520.0, 600.0),
    ("Louet Erica", 160, 4, 180.0, 240.0),
    ("Macomber B5", 600, None, None, None),
]

DEMO_READINGS = [
    # loom, grams, hours before now
    ("Louet Erica", 205.0, 52),
    ("Glimakra Standard", 560.0, 45),
    ("Ashford Jack", 528.0, 40),
    ("Ashford Jack", 471.0, 27),
    ("Glimakra Standard", 505.0, 18),
    ("Louet Erica", 176.0, 11),
    ("Ashford Jack", 441.0, 6),
    ("Ashford Jack", 421.0, 2),
]

DEMO_DEFECTS = [
    # loom, type, severity, side, note, hours before now, resolved hours before now
    ("Glimakra Standard", "slack selvedge", "low", "right",
     "right edge loops on every fourth pick", 96, None),
    ("Macomber B5", "weft crimp", "med", "left", "temple will not hold the fell", 78, None),
    ("Louet Erica", "draw-in", "med", "left", "beaten too hard past the reed", 70, 60),
    ("Glimakra Standard", "broken end", "high", "right", "end 214 snapped, tied in", 26, 20),
    ("Louet Erica", "loop", "low", "both", "", 14, None),
    ("Ashford Jack", "float", "high", "left", "two floats at the left selvedge", 30, 4),
    ("Ashford Jack", "draw-in", "med", "left", "pulled in 3 mm over twenty picks", 8, None),
]


def cmd_seed(ctx: Context, args: argparse.Namespace) -> bool:
    del args
    created = {"operators": 0, "looms": 0, "readings": 0, "defects": 0}

    operator = find_operator(ctx.store, DEMO_OPERATOR)
    if operator is None:
        salt_hex = secrets.token_hex(SALT_BYTES)
        operator = {
            "id": f"op_{secrets.token_hex(6)}",
            "name": DEMO_OPERATOR,
            "passphrase_hash": hash_passphrase(DEMO_PASSPHRASE, salt_hex, PBKDF2_ITERATIONS),
            "salt": salt_hex,
            "iterations": PBKDF2_ITERATIONS,
            "created_at": iso(now_utc()),
        }
        ctx.store["operators"].append(operator)
        created["operators"] += 1

    base = now_utc()
    for name, heddles, shafts, band_min, band_max in DEMO_LOOMS:
        if find_loom_or_none(ctx.store, name) is not None:
            continue
        ctx.store["looms"].append(
            {
                "id": f"loom_{secrets.token_hex(6)}",
                "name": name,
                "heddle_count": heddles,
                "shaft_count": shafts,
                "band_min_g": band_min,
                "band_max_g": band_max,
                "created_at": iso(base - timedelta(hours=120)),
                "created_by": operator["id"],
            }
        )
        created["looms"] += 1

    for loom_name, grams, hours in DEMO_READINGS:
        loom = find_loom_or_none(ctx.store, loom_name)
        if loom is None or loom.get("band_min_g") is None or loom.get("band_max_g") is None:
            continue
        checked_at = iso(base - timedelta(hours=hours))
        already = any(
            row.get("loom_id") == loom["id"] and row.get("checked_at") == checked_at
            for row in ctx.store["readings"]
        )
        if already:
            continue
        ctx.store["readings"].append(
            {
                "id": f"rd_{secrets.token_hex(6)}",
                "loom_id": loom["id"],
                "grams": grams,
                "verdict": classify_reading(grams, loom["band_min_g"], loom["band_max_g"]),
                "checked_at": checked_at,
                "operator_id": operator["id"],
            }
        )
        created["readings"] += 1

    for loom_name, defect_type, severity, side, note, hours, resolved_hours in DEMO_DEFECTS:
        loom = find_loom_or_none(ctx.store, loom_name)
        if loom is None:
            continue
        logged_at = iso(base - timedelta(hours=hours))
        already = any(
            row.get("loom_id") == loom["id"]
            and row.get("type") == defect_type
            and row.get("logged_at") == logged_at
            for row in ctx.store["defects"]
        )
        if already:
            continue
        defect_id = int(ctx.store["next_defect_id"])
        ctx.store["next_defect_id"] = defect_id + 1
        ctx.store["defects"].append(
            {
                "id": defect_id,
                "loom_id": loom["id"],
                "type": defect_type,
                "severity": severity,
                "side": side,
                "note": note,
                "status": "resolved" if resolved_hours is not None else "open",
                "logged_at": logged_at,
                "resolved_at": (
                    iso(base - timedelta(hours=resolved_hours))
                    if resolved_hours is not None
                    else None
                ),
                "operator_id": operator["id"],
            }
        )
        created["defects"] += 1

    if sum(created.values()) == 0:
        print(f"demo studio already present in {ctx.path} - nothing to seed")
    else:
        print(
            "seeded demo studio: "
            f"{created['operators']} operator, {created['looms']} looms, "
            f"{created['readings']} readings, {created['defects']} defects in {ctx.path}"
        )
    print(f"demo login: operator {DEMO_OPERATOR} / passphrase {DEMO_PASSPHRASE}")
    print_hint(ctx.style, f"run operator login {DEMO_OPERATOR} then loom list")
    return True


# ---------------------------------------------------------------------------
# command surface
# ---------------------------------------------------------------------------

DISPATCH = {
    ("operator", "register"): cmd_operator_register,
    ("operator", "login"): cmd_operator_login,
    ("operator", "logout"): cmd_operator_logout,
    ("loom", "add"): cmd_loom_add,
    ("loom", "list"): cmd_loom_list,
    ("loom", "show"): cmd_loom_show,
    ("loom", "set-heddles"): cmd_loom_set_heddles,
    ("loom", "set-shafts"): cmd_loom_set_shafts,
    ("band", "set"): cmd_band_set,
    ("band", "show"): cmd_band_show,
    ("tension", "check"): cmd_tension_check,
    ("tension", "history"): cmd_tension_history,
    ("defect", "log"): cmd_defect_log,
    ("defect", "list"): cmd_defect_list,
    ("defect", "resolve"): cmd_defect_resolve,
    ("seed", None): cmd_seed,
}

GROUP_PARSERS: dict[str, argparse.ArgumentParser] = {}
GROUP_COMMANDS: dict[str, argparse.Action] = {}


def add_common_options(parser: argparse.ArgumentParser, top: bool = False) -> None:
    """--data and --no-color work before or after the command, on every parser."""
    parser.add_argument(
        "--data",
        metavar="PATH",
        default=None if top else argparse.SUPPRESS,
        help=f"JSON data file to use (default: {DEFAULT_DATA_PATH})",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False if top else argparse.SUPPRESS,
        help="never emit ANSI colour, even on a terminal",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loomledge",
        description=(
            "Warp tension tolerance bands, heddle counts and selvedge defects "
            "for hand-weaving studios."
        ),
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_common_options(parser, top=True)
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {APP_VERSION}")
    groups = parser.add_subparsers(dest="group", metavar="<group>")

    # operator -------------------------------------------------------------
    operator = groups.add_parser(
        "operator", help="register, sign in, sign out", description="Local operator accounts."
    )
    add_common_options(operator)
    operator_cmds = operator.add_subparsers(dest="command", metavar="<command>")
    GROUP_PARSERS["operator"] = operator
    GROUP_COMMANDS["operator"] = operator_cmds

    register = operator_cmds.add_parser(
        "register", help="create an operator with a passphrase", description="Create an operator."
    )
    add_common_options(register)
    register.add_argument("name", help="operator name, for example mira")
    register.add_argument(
        "--passphrase-stdin",
        action="store_true",
        help="read the passphrase and its repeat from stdin, one line each",
    )

    login = operator_cmds.add_parser(
        "login", help="sign in and store a session", description="Sign in with a passphrase."
    )
    add_common_options(login)
    login.add_argument("name", help="operator name")
    login.add_argument(
        "--passphrase-stdin", action="store_true", help="read the passphrase from stdin"
    )

    logout = operator_cmds.add_parser(
        "logout", help="clear the stored session", description="Sign the current operator out."
    )
    add_common_options(logout)

    # loom -----------------------------------------------------------------
    loom = groups.add_parser(
        "loom", help="register looms, set shafts and heddle counts", description="Loom registry."
    )
    add_common_options(loom)
    loom_cmds = loom.add_subparsers(dest="command", metavar="<command>")
    GROUP_PARSERS["loom"] = loom
    GROUP_COMMANDS["loom"] = loom_cmds

    loom_add = loom_cmds.add_parser(
        "add", help="register a loom", description="Register a loom by name."
    )
    add_common_options(loom_add)
    loom_add.add_argument("name", help="loom name, for example Rig")
    loom_add.add_argument(
        "--heddles", required=True, metavar="N", help="heddle count, a whole number"
    )
    loom_add.add_argument("--shafts", metavar="S", help="optional shaft count, a whole number")

    loom_list = loom_cmds.add_parser(
        "list", help="list looms with band and open defects", description="List every loom."
    )
    add_common_options(loom_list)

    loom_show = loom_cmds.add_parser(
        "show", help="show one loom, with heddles per shaft", description="Show one loom."
    )
    add_common_options(loom_show)
    loom_show.add_argument("name", help="loom name")

    loom_heddles = loom_cmds.add_parser(
        "set-heddles", help="change a loom heddle count", description="Change a heddle count."
    )
    add_common_options(loom_heddles)
    loom_heddles.add_argument("name", help="loom name")
    loom_heddles.add_argument("heddles", metavar="N", help="new heddle count")

    loom_shafts = loom_cmds.add_parser(
        "set-shafts",
        help="set, change or clear a loom shaft count",
        description="Set or clear the shaft count.",
    )
    add_common_options(loom_shafts)
    loom_shafts.add_argument("name", help="loom name")
    shafts_group = loom_shafts.add_mutually_exclusive_group(required=True)
    shafts_group.add_argument("shafts", nargs="?", metavar="S", help="new shaft count")
    shafts_group.add_argument("--clear", action="store_true", help="remove the shaft count")

    # band -----------------------------------------------------------------
    band = groups.add_parser(
        "band", help="set and show warp tension tolerance bands", description="Tolerance bands."
    )
    add_common_options(band)
    band_cmds = band.add_subparsers(dest="command", metavar="<command>")
    GROUP_PARSERS["band"] = band
    GROUP_COMMANDS["band"] = band_cmds

    band_set = band_cmds.add_parser(
        "set", help="set the band in grams", description="Set a loom's tolerance band."
    )
    add_common_options(band_set)
    band_set.add_argument("loom", help="loom name")
    band_set.add_argument("--min", required=True, metavar="G", help="lower bound in grams")
    band_set.add_argument("--max", required=True, metavar="G", help="upper bound in grams")

    band_show = band_cmds.add_parser(
        "show", help="print the band", description="Print a loom's tolerance band."
    )
    add_common_options(band_show)
    band_show.add_argument("loom", help="loom name")

    # tension --------------------------------------------------------------
    tension = groups.add_parser(
        "tension",
        help="grade a warp tension reading and list readings",
        description="Tension checks and history.",
    )
    add_common_options(tension)
    tension_cmds = tension.add_subparsers(dest="command", metavar="<command>")
    GROUP_PARSERS["tension"] = tension
    GROUP_COMMANDS["tension"] = tension_cmds

    tension_check = tension_cmds.add_parser(
        "check", help="grade a reading against the band", description="Grade a tension reading."
    )
    add_common_options(tension_check)
    tension_check.add_argument("loom", help="loom name")
    tension_check.add_argument("--grams", required=True, metavar="G", help="tension in grams")

    tension_history = tension_cmds.add_parser(
        "history", help="list stored readings, newest first", description="Reading history."
    )
    add_common_options(tension_history)
    tension_history.add_argument("loom", help="loom name")
    tension_history.add_argument("--limit", metavar="N", help="show at most N readings")

    # defect ---------------------------------------------------------------
    defect = groups.add_parser(
        "defect", help="log, list and resolve selvedge defects", description="Selvedge defects."
    )
    add_common_options(defect)
    defect_cmds = defect.add_subparsers(dest="command", metavar="<command>")
    GROUP_PARSERS["defect"] = defect
    GROUP_COMMANDS["defect"] = defect_cmds

    defect_log = defect_cmds.add_parser(
        "log", help="log a selvedge defect", description="Log a selvedge defect."
    )
    add_common_options(defect_log)
    defect_log.add_argument("loom", help="loom name")
    defect_log.add_argument("--type", required=True, metavar="T", help="defect type, for example float")
    defect_log.add_argument(
        "--severity", required=True, metavar="S", help="low, med or high"
    )
    defect_log.add_argument("--side", required=True, metavar="S", help="left, right or both")
    defect_log.add_argument("-m", "--message", metavar="NOTE", help="optional note")

    defect_list = defect_cmds.add_parser(
        "list", help="list defects with an open count", description="List defects."
    )
    add_common_options(defect_list)
    defect_list.add_argument("--loom", metavar="LOOM", help="only defects on this loom")
    defect_list.add_argument("--open", action="store_true", help="only open defects")
    defect_list.add_argument("--type", metavar="T", help="only defects of this type")

    defect_resolve = defect_cmds.add_parser(
        "resolve", help="mark a defect resolved", description="Resolve a defect."
    )
    add_common_options(defect_resolve)
    defect_resolve.add_argument("id", help="defect id shown by defect list")

    # seed -----------------------------------------------------------------
    seed = groups.add_parser(
        "seed",
        help="load the demo studio into the data file",
        description="Create the demo operator and a studio of looms, readings and defects.",
    )
    add_common_options(seed)

    return parser


def leaf_parser(group: str | None, command: str | None) -> argparse.ArgumentParser | None:
    action = GROUP_COMMANDS.get(str(group))
    if action is None or command is None:
        return None
    return action.choices.get(command)


def colour_enabled(args: argparse.Namespace) -> bool:
    if getattr(args, "no_color", False):
        return False
    if "NO_COLOR" in os.environ:
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    return sys.stdout.isatty()


def print_home(parser: argparse.ArgumentParser, style: Style) -> None:
    print(f"{APP_NAME} {APP_VERSION} - {parser.description}")
    print()
    print(f"{style.header('usage')}  loomledge [--data PATH] <group> <command> [options]")
    print()
    print(style.header("groups"))
    print("  operator   register, login, logout")
    print("  loom       add, list, show, set-heddles, set-shafts")
    print("  band       set, show")
    print("  tension    check, history")
    print("  defect     log, list, resolve")
    print("  seed       load the demo studio into the data file")
    print()
    print(style.header("quick start"))
    print("  loomledge seed --data ./studio.json")
    print("  loomledge operator login cenius --data ./studio.json")
    print("  loomledge loom list --data ./studio.json")
    print("  loomledge loom add Rig --heddles 400 --shafts 8 --data ./studio.json")
    print("  loomledge band set Rig --min 380 --max 460 --data ./studio.json")
    print("  loomledge tension check Rig --grams 420 --data ./studio.json")
    print("  loomledge defect list --open --data ./studio.json")
    print()
    print(f"{style.header('try')}    loomledge --help   or   loomledge <group> <command> --help")
    print(f"{style.header('demo')}   operator {DEMO_OPERATOR} / passphrase {DEMO_PASSPHRASE}")
    print(f"{style.header('data')}   {DEFAULT_DATA_PATH} (override with --data PATH)")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    parser = build_parser()
    args, extra = parser.parse_known_args(argv)
    style = Style(colour_enabled(args))
    if extra:
        # Report an unknown flag against the one command it belongs to, not the
        # whole tool, so `loom list --bogus` prints usage for `loom list` only.
        leaf = leaf_parser(getattr(args, "group", None), getattr(args, "command", None))
        (leaf or parser).error(f"unrecognized arguments: {' '.join(extra)}")

    group = getattr(args, "group", None)
    command = getattr(args, "command", None)
    if group is None:
        print_home(parser, style)
        return 0
    if command is None and group in GROUP_PARSERS:
        GROUP_PARSERS[group].print_help()
        return 0

    handler = DISPATCH.get((group, command))
    if handler is None:
        parser.error(f"unknown command {group} {command}")

    path = Path(getattr(args, "data", None) or DEFAULT_DATA_PATH).expanduser()
    try:
        store = load_store(path)
        ctx = Context(store=store, path=path, style=style)
        changed = handler(ctx, args)
        if changed:
            save_store(path, store)
    except UserError as exc:
        print(f"{style.paint('fail', 'error:')} {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
