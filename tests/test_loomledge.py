"""End-to-end tests for the LoomLedge CLI.

Every test drives the real tool through a subprocess against a scratch data
file, so the suite exercises the shipped argument parser, the JSON store and
the exit-code contract exactly as a user would.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "loomledge.py"
NOW = "2024-05-01T09:00:00Z"
PASSPHRASE = "warpweft"


@dataclass
class Result:
    rc: int
    out: str
    err: str

    @property
    def text(self) -> str:
        return self.out + self.err


def run_cli(data: Path, *args: str, stdin: str = "", now: str | None = NOW) -> Result:
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("LOOMLEDGE_NOW", "NO_COLOR")
    }
    env["NO_COLOR"] = "1"
    if now is not None:
        env["LOOMLEDGE_NOW"] = now
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *[str(a) for a in args], "--data", str(data)],
        input=stdin,
        text=True,
        capture_output=True,
        env=env,
        cwd=str(REPO),
        timeout=120,
    )
    return Result(proc.returncode, proc.stdout, proc.stderr)


@pytest.fixture()
def data(tmp_path: Path) -> Path:
    return tmp_path / "studio.json"


@pytest.fixture()
def signed_in(data: Path) -> Path:
    assert run_cli(data, "operator", "register", "mira", stdin=f"{PASSPHRASE}\n{PASSPHRASE}\n").rc == 0
    login = run_cli(data, "operator", "login", "mira", stdin=f"{PASSPHRASE}\n")
    assert login.rc == 0, login.text
    return data


@pytest.fixture()
def rig(signed_in: Path) -> Path:
    added = run_cli(signed_in, "loom", "add", "Rig", "--heddles", "400", "--shafts", "8")
    assert added.rc == 0, added.text
    band = run_cli(signed_in, "band", "set", "Rig", "--min", "380", "--max", "460")
    assert band.rc == 0, band.text
    return signed_in


# ---------------------------------------------------------------------------
# command surface, usage errors and the data file
# ---------------------------------------------------------------------------


def test_help_lists_every_command_group():
    result = run_cli(Path("/tmp/unused.json"), "--help")
    assert result.rc == 0
    for group in ("operator", "loom", "band", "tension", "defect"):
        assert group in result.out
    assert "seed" in result.out


def test_bare_invocation_prints_an_overview_and_exits_zero():
    result = run_cli(Path("/tmp/unused.json"))
    assert result.rc == 0
    assert "loomledge" in result.out.lower()
    assert "quick start" in result.out
    assert "operator" in result.out


def test_version_flag():
    result = run_cli(Path("/tmp/unused.json"), "--version")
    assert result.rc == 0
    assert "LoomLedge" in result.out


def test_subcommand_help_exits_zero():
    result = run_cli(Path("/tmp/unused.json"), "defect", "log", "--help")
    assert result.rc == 0
    assert "--severity" in result.out
    assert "--side" in result.out


def test_data_file_is_created_on_first_mutating_run(data: Path):
    assert not data.exists()
    result = run_cli(data, "operator", "register", "mira", stdin=f"{PASSPHRASE}\n{PASSPHRASE}\n")
    assert result.rc == 0
    assert data.exists()
    store = json.loads(data.read_text())
    for key in ("operators", "sessions", "looms", "readings", "defects"):
        assert isinstance(store[key], list)


def test_read_only_command_does_not_create_the_data_file(data: Path):
    result = run_cli(data, "loom", "list")
    assert result.rc == 1
    assert not data.exists()


def test_atomic_write_leaves_no_temp_files(data: Path):
    run_cli(data, "operator", "register", "mira", stdin=f"{PASSPHRASE}\n{PASSPHRASE}\n")
    leftovers = [p.name for p in data.parent.iterdir() if p.name != data.name]
    assert leftovers == []


def test_unknown_flag_prints_that_one_command_usage_and_exits_two(signed_in: Path):
    result = run_cli(signed_in, "loom", "list", "--bogus")
    assert result.rc == 2
    assert "loom list" in result.err
    assert "unrecognized arguments: --bogus" in result.err


def test_missing_required_option_is_a_usage_error(signed_in: Path):
    result = run_cli(signed_in, "loom", "add", "Rig")
    assert result.rc == 2
    assert "--heddles" in result.err


# ---------------------------------------------------------------------------
# operator accounts and sessions
# ---------------------------------------------------------------------------


def test_register_stores_a_salted_hash_and_never_the_passphrase(data: Path):
    result = run_cli(data, "operator", "register", "mira", stdin=f"{PASSPHRASE}\n{PASSPHRASE}\n")
    assert result.rc == 0
    assert "registered operator mira" in result.out
    raw = data.read_text()
    assert PASSPHRASE not in raw
    operator = json.loads(raw)["operators"][0]
    assert operator["name"] == "mira"
    assert len(operator["salt"]) == 32
    assert len(operator["passphrase_hash"]) == 64
    assert operator["iterations"] >= 100_000


def test_register_rejects_mismatched_passphrases(data: Path):
    result = run_cli(data, "operator", "register", "mira", stdin="warpweft\notherpass\n")
    assert result.rc == 1
    assert "passphrases do not match" in result.err


def test_duplicate_operator_is_rejected(data: Path):
    run_cli(data, "operator", "register", "mira", stdin=f"{PASSPHRASE}\n{PASSPHRASE}\n")
    again = run_cli(data, "operator", "register", "mira", stdin=f"{PASSPHRASE}\n{PASSPHRASE}\n")
    assert again.rc == 1
    assert "already exists" in again.err


def test_login_then_data_command_then_logout(signed_in: Path):
    listed = run_cli(signed_in, "loom", "list")
    assert listed.rc == 0
    assert "no looms yet" in listed.out

    assert run_cli(signed_in, "operator", "logout").rc == 0
    refused = run_cli(signed_in, "loom", "add", "Rig", "--heddles", "400")
    assert refused.rc == 1
    assert "operator login" in refused.err


def test_data_command_without_a_session_is_refused(data: Path):
    result = run_cli(data, "loom", "add", "Rig", "--heddles", "400")
    assert result.rc == 1
    assert "operator register" in result.err

    run_cli(data, "operator", "register", "mira", stdin=f"{PASSPHRASE}\n{PASSPHRASE}\n")
    again = run_cli(data, "loom", "add", "Rig", "--heddles", "400")
    assert again.rc == 1
    assert "run operator login" in again.err


def test_wrong_name_and_wrong_passphrase_share_one_message(data: Path):
    run_cli(data, "operator", "register", "mira", stdin=f"{PASSPHRASE}\n{PASSPHRASE}\n")
    wrong_pass = run_cli(data, "operator", "login", "mira", stdin="nope\n")
    unknown = run_cli(data, "operator", "login", "nobody", stdin=f"{PASSPHRASE}\n")
    assert wrong_pass.rc == 1 and unknown.rc == 1
    assert wrong_pass.err.strip() == unknown.err.strip()
    assert "name or passphrase is incorrect" in wrong_pass.err


def test_passphrase_stdin_flag_is_honoured(data: Path):
    result = run_cli(
        data,
        "operator",
        "register",
        "mira",
        "--passphrase-stdin",
        stdin=f"{PASSPHRASE}\n{PASSPHRASE}\n",
    )
    assert result.rc == 0
    login = run_cli(data, "operator", "login", "mira", "--passphrase-stdin", stdin=f"{PASSPHRASE}\n")
    assert login.rc == 0
    assert "signed in as mira" in login.out


# ---------------------------------------------------------------------------
# loom registry
# ---------------------------------------------------------------------------


def test_loom_show_derives_heddles_per_shaft(signed_in: Path):
    run_cli(signed_in, "loom", "add", "Rig", "--heddles", "400", "--shafts", "8")
    shown = run_cli(signed_in, "loom", "show", "Rig")
    assert shown.rc == 0
    assert "heddles" in shown.out and "400" in shown.out
    assert "shafts" in shown.out and "8" in shown.out
    assert "50.00" in shown.out


def test_loom_without_shafts_omits_shaft_and_ratio_lines(signed_in: Path):
    run_cli(signed_in, "loom", "add", "Table", "--heddles", "160")
    shown = run_cli(signed_in, "loom", "show", "Table")
    assert shown.rc == 0
    lines = [line for line in shown.out.splitlines() if line.strip()]
    labels = [line.split()[0] for line in lines]
    assert "shafts" not in labels
    assert "heddles/shaft" not in labels
    assert any("to record shafts" in line for line in lines)


def test_duplicate_loom_name_is_rejected(signed_in: Path):
    run_cli(signed_in, "loom", "add", "Rig", "--heddles", "400", "--shafts", "8")
    again = run_cli(signed_in, "loom", "add", "Rig", "--heddles", "200")
    assert again.rc == 1
    assert "already exists" in again.err


def test_loom_list_shows_a_dash_for_missing_shafts(signed_in: Path):
    run_cli(signed_in, "loom", "add", "Rig", "--heddles", "400", "--shafts", "8")
    run_cli(signed_in, "loom", "add", "Table", "--heddles", "160")
    listed = run_cli(signed_in, "loom", "list")
    assert listed.rc == 0
    assert "Rig" in listed.out and "400" in listed.out
    assert "Table" in listed.out and "160" in listed.out
    table_row = next(line for line in listed.out.splitlines() if line.startswith("Table"))
    assert table_row.split()[2] == "-"


def test_set_shafts_then_clear(signed_in: Path):
    run_cli(signed_in, "loom", "add", "Rig", "--heddles", "400")
    set_result = run_cli(signed_in, "loom", "set-shafts", "Rig", "6")
    assert set_result.rc == 0
    shown = run_cli(signed_in, "loom", "show", "Rig")
    assert "shafts" in shown.out and "66.67" in shown.out

    cleared = run_cli(signed_in, "loom", "set-shafts", "Rig", "--clear")
    assert cleared.rc == 0
    after = run_cli(signed_in, "loom", "show", "Rig")
    labels = [line.split()[0] for line in after.out.splitlines() if line.strip()]
    assert "shafts" not in labels
    assert "heddles/shaft" not in labels


def test_set_heddles_recomputes_heddles_per_shaft(signed_in: Path):
    run_cli(signed_in, "loom", "add", "Rig", "--heddles", "400", "--shafts", "6")
    changed = run_cli(signed_in, "loom", "set-heddles", "Rig", "300")
    assert changed.rc == 0
    shown = run_cli(signed_in, "loom", "show", "Rig")
    assert "50.00" in shown.out


def test_non_numeric_heddle_count_is_quoted_back(signed_in: Path):
    result = run_cli(signed_in, "loom", "add", "Rig", "--heddles", "abc")
    assert result.rc == 1
    assert "'abc'" in result.err
    assert "whole number" in result.err


def test_unknown_loom_names_the_loom_and_points_at_loom_list(signed_in: Path):
    result = run_cli(signed_in, "band", "show", "Ghost")
    assert result.rc == 1
    assert "no loom named Ghost" in result.err
    assert "loom list" in result.err


# ---------------------------------------------------------------------------
# tolerance bands and tension checks
# ---------------------------------------------------------------------------


def test_band_set_and_show(rig: Path):
    shown = run_cli(rig, "band", "show", "Rig")
    assert shown.rc == 0
    assert "380-460 g" in shown.out


def test_band_rejects_min_not_below_max(rig: Path):
    result = run_cli(rig, "band", "set", "Rig", "--min", "40", "--max", "30")
    assert result.rc == 1
    assert "min must be less than max" in result.err


def test_band_rejects_non_positive_bounds(rig: Path):
    negative = run_cli(rig, "band", "set", "Rig", "--min", "-5", "--max", "30")
    assert negative.rc == 1
    assert "'-5'" in negative.err
    zero = run_cli(rig, "band", "set", "Rig", "--min", "0", "--max", "30")
    assert zero.rc == 1
    assert "'0'" in zero.err


def test_band_show_without_a_band_points_at_band_set(signed_in: Path):
    run_cli(signed_in, "loom", "add", "Rig", "--heddles", "400")
    result = run_cli(signed_in, "band", "show", "Rig")
    assert result.rc == 1
    assert "no tolerance band set for Rig" in result.err
    assert "band set Rig --min G --max G" in result.err


def test_tension_check_grades_ok(rig: Path):
    result = run_cli(rig, "tension", "check", "Rig", "--grams", "420")
    assert result.rc == 0
    assert "420 g" in result.out
    assert "380-460 g" in result.out
    assert "verdict  ok" in result.out


def test_tension_check_grades_warn_and_fail(rig: Path):
    warn = run_cli(rig, "tension", "check", "Rig", "--grams", "470")
    fail = run_cli(rig, "tension", "check", "Rig", "--grams", "520")
    assert warn.rc == 0 and "verdict  warn" in warn.out
    assert fail.rc == 0 and "verdict  fail" in fail.out


def test_tension_check_without_a_band_is_refused(signed_in: Path):
    run_cli(signed_in, "loom", "add", "Rig", "--heddles", "400")
    result = run_cli(signed_in, "tension", "check", "Rig", "--grams", "400")
    assert result.rc == 1
    assert "no tolerance band set for Rig" in result.err


def test_non_numeric_grams_are_quoted_back(rig: Path):
    result = run_cli(rig, "tension", "check", "Rig", "--grams", "tight")
    assert result.rc == 1
    assert "'tight'" in result.err


def test_tension_check_stores_a_reading(rig: Path):
    run_cli(rig, "tension", "check", "Rig", "--grams", "420")
    history = run_cli(rig, "tension", "history", "Rig")
    assert history.rc == 0
    assert "420" in history.out
    assert "ok" in history.out


# ---------------------------------------------------------------------------
# tension history
# ---------------------------------------------------------------------------


def test_history_lists_readings_newest_first(rig: Path):
    run_cli(rig, "tension", "check", "Rig", "--grams", "420", now="2024-05-01T09:00:00Z")
    run_cli(rig, "tension", "check", "Rig", "--grams", "520", now="2024-05-01T10:00:00Z")
    history = run_cli(rig, "tension", "history", "Rig")
    assert history.rc == 0
    body = [line for line in history.out.splitlines()[2:] if line.strip()]
    assert len(body) == 2
    assert "520" in body[0] and "fail" in body[0]
    assert "420" in body[1] and "ok" in body[1]
    assert "2024-05-01 10:00" in body[0]


def test_history_limit_trims_to_the_newest(rig: Path):
    for index, grams in enumerate(("420", "470", "520")):
        run_cli(rig, "tension", "check", "Rig", "--grams", grams, now=f"2024-05-01T1{index}:00:00Z")
    limited = run_cli(rig, "tension", "history", "Rig", "--limit", "1")
    assert limited.rc == 0
    body = [line for line in limited.out.splitlines()[2:] if line.strip()]
    assert len(body) == 1
    assert "520" in body[0]


def test_history_without_readings_explains_what_to_run(rig: Path):
    history = run_cli(rig, "tension", "history", "Rig")
    assert history.rc == 0
    assert "no readings for Rig yet" in history.out
    assert "tension check Rig --grams G" in history.out


def test_history_rejects_a_bad_limit(rig: Path):
    result = run_cli(rig, "tension", "history", "Rig", "--limit", "zero")
    assert result.rc == 1
    assert "'zero'" in result.err


# ---------------------------------------------------------------------------
# selvedge defects
# ---------------------------------------------------------------------------


def test_defect_log_then_list_open(rig: Path):
    logged = run_cli(
        rig,
        "defect",
        "log",
        "Rig",
        "--type",
        "float",
        "--severity",
        "high",
        "--side",
        "left",
        "-m",
        "two floats",
    )
    assert logged.rc == 0
    listed = run_cli(rig, "defect", "list", "--loom", "Rig", "--open")
    assert listed.rc == 0
    assert "float" in listed.out
    assert "high" in listed.out
    assert "left" in listed.out
    assert "open" in listed.out
    row = next(line for line in listed.out.splitlines() if "float" in line)
    assert row.split()[0].isdigit()


def test_defect_list_open_header_counts_open_only(rig: Path):
    run_cli(rig, "defect", "log", "Rig", "--type", "float", "--severity", "high", "--side", "left")
    run_cli(rig, "defect", "log", "Rig", "--type", "draw-in", "--severity", "med", "--side", "right")
    run_cli(rig, "defect", "log", "Rig", "--type", "loop", "--severity", "low", "--side", "both")
    run_cli(rig, "defect", "resolve", "1")

    listed = run_cli(rig, "defect", "list", "--open")
    assert listed.rc == 0
    assert listed.out.splitlines()[0].startswith("2 open defects")
    assert "loop" in listed.out
    assert "draw-in" in listed.out
    float_rows = [line for line in listed.out.splitlines() if " float " in line]
    assert float_rows == []


def test_defect_resolve_updates_loom_open_count(rig: Path):
    run_cli(rig, "loom", "add", "Table", "--heddles", "160")
    run_cli(rig, "defect", "log", "Rig", "--type", "float", "--severity", "high", "--side", "left")
    run_cli(rig, "defect", "log", "Rig", "--type", "loop", "--severity", "low", "--side", "both")
    second_id = json.loads(rig.read_text())["defects"][1]["id"]

    resolved = run_cli(rig, "defect", "resolve", str(second_id))
    assert resolved.rc == 0

    listed = run_cli(rig, "loom", "list")
    rig_row = next(line for line in listed.out.splitlines() if line.startswith("Rig"))
    assert rig_row.split()[-1] == "1"


def test_defect_resolve_all_leaves_zero_open(rig: Path):
    run_cli(rig, "defect", "log", "Rig", "--type", "float", "--severity", "high", "--side", "left")
    defect_id = json.loads(rig.read_text())["defects"][0]["id"]
    assert run_cli(rig, "defect", "resolve", str(defect_id)).rc == 0
    listed = run_cli(rig, "loom", "list")
    rig_row = next(line for line in listed.out.splitlines() if line.startswith("Rig"))
    assert rig_row.split()[-1] == "0"


def test_defect_rejects_unknown_severity_and_prints_allowed_values(rig: Path):
    result = run_cli(
        rig, "defect", "log", "Rig", "--type", "float", "--severity", "urgent", "--side", "left"
    )
    assert result.rc == 1
    assert "low, med, high" in result.err


def test_defect_rejects_unknown_side_and_prints_allowed_values(rig: Path):
    result = run_cli(
        rig, "defect", "log", "Rig", "--type", "float", "--severity", "high", "--side", "middle"
    )
    assert result.rc == 1
    assert "left, right, both" in result.err


def test_defect_resolve_unknown_id(rig: Path):
    result = run_cli(rig, "defect", "resolve", "7")
    assert result.rc == 1
    assert "no defect 7" in result.err


def test_defect_list_filters_by_type(rig: Path):
    run_cli(rig, "defect", "log", "Rig", "--type", "float", "--severity", "high", "--side", "left")
    run_cli(rig, "defect", "log", "Rig", "--type", "loop", "--severity", "low", "--side", "both")
    filtered = run_cli(rig, "defect", "list", "--type", "float")
    assert filtered.rc == 0
    assert "float" in filtered.out
    assert "loop" not in filtered.out


def test_defect_list_with_no_matches_distinguishes_from_no_defects(rig: Path):
    empty = run_cli(rig, "defect", "list")
    assert empty.rc == 0
    assert "no defects logged" in empty.out

    run_cli(rig, "defect", "log", "Rig", "--type", "float", "--severity", "high", "--side", "left")
    no_match = run_cli(rig, "defect", "list", "--type", "nosuch")
    assert no_match.rc == 0
    assert "no defects match" in no_match.out


# ---------------------------------------------------------------------------
# the demo seed and the demo transcript
# ---------------------------------------------------------------------------


def test_seed_creates_the_demo_operator_and_can_sign_in(data: Path):
    seeded = run_cli(data, "seed")
    assert seeded.rc == 0
    assert "cenius" in seeded.out

    login = run_cli(data, "operator", "login", "cenius", stdin="cenius\n")
    assert login.rc == 0, login.text
    listed = run_cli(data, "loom", "list")
    assert listed.rc == 0
    assert "Rig" not in listed.out
    assert "Ashford Jack" in listed.out
    assert "Glimakra Standard" in listed.out

    defects = run_cli(data, "defect", "list", "--open")
    assert defects.rc == 0
    assert "draw-in" in defects.out

    history = run_cli(data, "tension", "history", "Ashford Jack")
    assert history.rc == 0
    assert "fail" in history.out


def test_seed_is_idempotent(data: Path):
    run_cli(data, "seed")
    first = json.loads(data.read_text())
    run_cli(data, "seed")
    second = json.loads(data.read_text())
    assert len(first["looms"]) == len(second["looms"])
    assert len(first["defects"]) == len(second["defects"])
    assert len(first["readings"]) == len(second["readings"])


def test_demo_transcript_matches_a_fresh_run(tmp_path: Path):
    transcript = tmp_path / "fresh-transcript.txt"
    env = dict(os.environ, NO_COLOR="1", LOOMLEDGE_DEMO_TRANSCRIPT=str(transcript))
    env.pop("LOOMLEDGE_NOW", None)
    proc = subprocess.run(
        ["bash", "demo.sh"],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        env=env,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr
    checked_in = (REPO / "demo-transcript.txt").read_text()
    assert transcript.read_text() == checked_in
    assert proc.stdout == checked_in


# ---------------------------------------------------------------------------
# performance
# ---------------------------------------------------------------------------


def test_commands_stay_fast_with_ten_thousand_defects(tmp_path: Path):
    data = tmp_path / "big.json"
    store = {
        "version": 1,
        "operators": [
            {
                "id": "op_1",
                "name": "mira",
                "passphrase_hash": "0" * 64,
                "salt": "0" * 32,
                "iterations": 1,
                "created_at": NOW,
            }
        ],
        "sessions": [
            {
                "token": "t" * 32,
                "operator_id": "op_1",
                "created_at": NOW,
                "expires_at": "2099-01-01T00:00:00Z",
            }
        ],
        "looms": [
            {
                "id": "loom_1",
                "name": "Rig",
                "heddle_count": 400,
                "shaft_count": 8,
                "band_min_g": 380.0,
                "band_max_g": 460.0,
                "created_at": NOW,
                "created_by": "op_1",
            }
        ],
        "readings": [],
        "defects": [
            {
                "id": index,
                "loom_id": "loom_1",
                "type": "float",
                "severity": "high",
                "side": "left",
                "note": "",
                "status": "open" if index % 20 else "resolved",
                "logged_at": NOW,
                "resolved_at": None,
                "operator_id": "op_1",
            }
            for index in range(1, 10_001)
        ],
        "next_defect_id": 10_001,
    }
    data.write_text(json.dumps(store))

    started = time.perf_counter()
    listed = run_cli(data, "defect", "list", "--open", "--loom", "Rig")
    elapsed = time.perf_counter() - started
    assert listed.rc == 0
    assert elapsed < 2.0, f"defect list took {elapsed:.2f}s with 10,000 defects"

    started = time.perf_counter()
    shown = run_cli(data, "loom", "show", "Rig")
    elapsed = time.perf_counter() - started
    assert shown.rc == 0
    assert elapsed < 2.0, f"loom show took {elapsed:.2f}s with 10,000 defects"
