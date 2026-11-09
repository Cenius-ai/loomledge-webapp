#!/usr/bin/env bash
# LoomLedge demo: the whole loop (operator, loom, band, tension, defect) against
# a scratch data file.  Non-interactive, no network, exits 0.
#
#   bash demo.sh                          # print the transcript and refresh demo-transcript.txt
#   LOOMLEDGE_DEMO_TRANSCRIPT=out.txt bash demo.sh
#
# Passphrases are piped in on stdin, so they are never echoed and never appear
# in the transcript.  LOOMLEDGE_NOW pins the clock so the output - and the
# checked-in transcript - are identical on every run.
set -euo pipefail
cd "$(dirname "$0")"

SCRATCH_DIR=".demo-scratch"
DATA="$SCRATCH_DIR/studio.json"
TRANSCRIPT="${LOOMLEDGE_DEMO_TRANSCRIPT:-demo-transcript.txt}"

export NO_COLOR=1

quote_args() {
    # render argv the way a shell would need it typed, for the transcript
    local rendered=""
    local arg
    for arg in "$@"; do
        case "$arg" in
            *[[:space:]]*) rendered="$rendered \"$arg\"" ;;
            *) rendered="$rendered $arg" ;;
        esac
    done
    printf '%s' "${rendered# }"
}

run_at() {
    # run_at <timestamp> <loomledge args...>
    local stamp="$1"
    shift
    export LOOMLEDGE_NOW="$stamp"
    printf '$ loomledge %s\n' "$(quote_args "$@")"
    local rc=0
    python3 loomledge.py "$@" || rc=$?
    if [ "$rc" -ne 0 ]; then
        printf 'exit %s\n' "$rc"
    fi
    printf '\n'
    return 0
}

run_with_stdin() {
    # run_with_stdin <timestamp> <stdin text> <loomledge args...>
    local stamp="$1"
    local input="$2"
    shift 2
    export LOOMLEDGE_NOW="$stamp"
    printf '$ loomledge %s\n' "$(quote_args "$@")"
    local rc=0
    printf '%s' "$input" | python3 loomledge.py "$@" || rc=$?
    if [ "$rc" -ne 0 ]; then
        printf 'exit %s\n' "$rc"
    fi
    printf '\n'
    return 0
}

demo() {
    printf 'LoomLedge demo - operator, loom, band, tension and defect loop\n'
    printf 'Data file: %s (recreated on every run, deleted at the end)\n' "$DATA"
    printf 'Commands are shown as `loomledge ...`; this script calls `python3 loomledge.py`.\n\n'

    run_at "2024-05-01T08:40:00Z" --version

    printf -- '-- operator sign-in ------------------------------------------------\n\n'
    printf '# the passphrase is piped on stdin, twice, and never echoed\n'
    run_with_stdin "2024-05-01T08:41:00Z" 'warp-and-weft
warp-and-weft
' operator register mira --data "$DATA"

    run_at "2024-05-01T08:42:00Z" loom list --data "$DATA"
    printf '# data commands refuse to run without a session (exit 1 above)\n\n'

    run_with_stdin "2024-05-01T08:43:00Z" 'warp-and-weft
' operator login mira --data "$DATA"

    printf -- '-- loom registry --------------------------------------------------\n\n'
    run_at "2024-05-01T08:45:00Z" loom add Rig --heddles 400 --shafts 8 --data "$DATA"
    run_at "2024-05-01T08:46:00Z" loom add Rig --heddles 200 --data "$DATA"
    printf '# a duplicate loom name is rejected with exit 1 and changes nothing\n\n'
    run_at "2024-05-01T08:47:00Z" loom show Rig --data "$DATA"

    printf -- '-- warp tension tolerance band ------------------------------------\n\n'
    run_at "2024-05-01T09:05:00Z" band set Rig --min 380 --max 460 --data "$DATA"
    run_at "2024-05-01T09:06:00Z" band show Rig --data "$DATA"

    printf -- '-- tension checks -------------------------------------------------\n\n'
    run_at "2024-05-01T09:10:00Z" tension check Rig --grams 420 --data "$DATA"
    run_at "2024-05-01T09:15:00Z" tension check Rig --grams 470 --data "$DATA"
    run_at "2024-05-01T09:20:00Z" tension check Rig --grams 520 --data "$DATA"
    run_at "2024-05-01T09:21:00Z" tension history Rig --data "$DATA"

    printf -- '-- selvedge defects -----------------------------------------------\n\n'
    run_at "2024-05-01T09:25:00Z" defect log Rig --type float --severity high --side left \
        -m "two floats at the left selvedge" --data "$DATA"
    run_at "2024-05-01T09:30:00Z" defect log Rig --type draw-in --severity med --side left --data "$DATA"
    run_at "2024-05-01T09:31:00Z" defect list --open --data "$DATA"
    run_at "2024-05-01T09:35:00Z" defect resolve 1 --data "$DATA"
    run_at "2024-05-01T09:36:00Z" defect list --loom Rig --data "$DATA"
    run_at "2024-05-01T09:37:00Z" loom list --data "$DATA"

    printf -- '-- sign out -------------------------------------------------------\n\n'
    run_at "2024-05-01T09:40:00Z" operator logout --data "$DATA"
    run_at "2024-05-01T09:41:00Z" loom list --data "$DATA"
    printf '# signed out, so the same command is refused again (exit 1)\n\n'
    printf 'demo complete\n'
}

rm -rf "$SCRATCH_DIR"
mkdir -p "$SCRATCH_DIR"
trap 'rm -rf "$SCRATCH_DIR"' EXIT

if [ -n "$TRANSCRIPT" ]; then
    demo 2>&1 | tee "$TRANSCRIPT"
else
    demo
fi
