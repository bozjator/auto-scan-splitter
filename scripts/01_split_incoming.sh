#!/bin/bash
set -euo pipefail

# Stage 1: watch the Samba incoming share, split each multi-photo scan into
# individual crops, and drop them in the upload queue for stage 2.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INCOMING_DIR="/srv/auto-scan-splitter"
QUEUE_DIR="$REPO_DIR/upload_queue"
SPLITTER_SCRIPT="$REPO_DIR/scripts/scan_splitter.py"

# Backlog files younger than this are assumed to still being written by the
# scanner, and are left for the inotify watch to pick up when they close.
BACKLOG_STABLE_SECONDS=15

# Makes the [[ =~ ]] extension test case-insensitive.
shopt -s nocasematch

is_scannable_image() {
    local base
    base="$(basename -- "$1")"

    # Samba and scanner firmware both stage uploads as hidden temp files. Those
    # are skipped here and handled by the moved_to event on the final rename.
    [[ "$base" == .* ]] && return 1

    [[ "$base" =~ \.(jpe?g|png|tiff?)$ ]]
}

process_file() {
    local filepath="$1"

    [[ -f "$filepath" ]] || return 0
    is_scannable_image "$filepath" || return 0

    echo "Processing scan: $filepath"

    if python3 "$SPLITTER_SCRIPT" "$filepath" "$QUEUE_DIR"; then
        echo "Successfully split $filepath. Deleting original scan."
        if ! rm -f -- "$filepath"; then
            # Deliberately not fatal: the crops are already queued, and aborting
            # here would kill the watcher and stall every later scan.
            echo "WARN: split OK but could not delete $filepath as $(id -un); leaving it in place." >&2
            echo "WARN: $INCOMING_DIR must be writable by this user, or that scan is re-split on every restart." >&2
        fi
    else
        echo "ERROR: Failed to process $filepath. Retaining raw file for review." >&2
    fi
}

process_backlog() {
    local cutoff found=0 filepath
    cutoff="$(date -d "-${BACKLOG_STABLE_SECONDS} seconds" '+%Y-%m-%d %H:%M:%S')"

    # inotify only reports events from the moment the watch starts, so this is
    # the only pass that ever sees scans deposited while the service was down.
    while IFS= read -r -d '' filepath; do
        # Filtered here too, so the summary below counts real scans and not
        # whatever else is sitting in the share.
        is_scannable_image "$filepath" || continue
        found=$((found + 1))
        process_file "$filepath"
    done < <(find "$INCOMING_DIR" -maxdepth 1 -type f ! -newermt "$cutoff" -print0)

    if ((found > 0)); then
        echo "Cleared $found leftover scan(s) from $INCOMING_DIR."
    fi
}

preflight() {
    if ! command -v inotifywait >/dev/null 2>&1; then
        echo "ERROR: inotifywait not found. Install it with: sudo apt install inotify-tools" >&2
        exit 1
    fi

    if [[ ! -d "$INCOMING_DIR" ]]; then
        echo "ERROR: incoming directory '$INCOMING_DIR' does not exist." >&2
        echo "ERROR: create the Samba share first - see README section 1." >&2
        exit 1
    fi

    if [[ ! -r "$SPLITTER_SCRIPT" ]]; then
        echo "ERROR: splitter script not found at '$SPLITTER_SCRIPT'." >&2
        exit 1
    fi

    # Fails loudly at startup instead of after every single scan.
    if [[ ! -w "$INCOMING_DIR" ]]; then
        echo "WARN: '$INCOMING_DIR' is not writable by $(id -un); raw scans cannot be deleted after splitting." >&2
    fi

    mkdir -p "$QUEUE_DIR"
}

preflight

echo "Started Scan Splitter Watcher on: $INCOMING_DIR"
echo "Cropped output queue: $QUEUE_DIR"

# The watch is opened BEFORE the backlog is drained so no event can slip
# through the gap between the two; inotifywait buffers into the pipe meanwhile.
inotifywait -m -e close_write,moved_to --format '%w%f' "$INCOMING_DIR" | {
    process_backlog

    while IFS= read -r filepath; do
        process_file "$filepath"
    done
}
