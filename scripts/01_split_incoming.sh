#!/bin/bash
set -euo pipefail

# Define relative paths based on repo root
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INCOMING_DIR="$REPO_DIR/incoming" # TODO this should be configurable via env variable or config file
QUEUE_DIR="$REPO_DIR/upload_queue"
SPLITTER_SCRIPT="$REPO_DIR/scripts/scan_splitter.py"

mkdir -p "$INCOMING_DIR" "$QUEUE_DIR"

echo "Started Scan Splitter Watcher on: $INCOMING_DIR"

# Watch for closed-after-write events (prevents processing half-written files)
inotifywait -m -e close_write --format '%w%f' "$INCOMING_DIR" | while read -r FILEPATH; do
    # Filter by valid image extensions (case-insensitive)
    if [[ "$FILEPATH" =~ \.(jpg|jpeg|png|tif|tiff|JPG|JPEG|PNG|TIF|TIFF)$ ]]; then
        echo "New scan detected: $FILEPATH"

        # Execute python splitter
        if python3 "$SPLITTER_SCRIPT" "$FILEPATH" "$QUEUE_DIR"; then
            echo "Successfully split $FILEPATH. Deleting original scan."
            rm -f "$FILEPATH"
        else
            echo "ERROR: Failed to process $FILEPATH. Retaining raw file for review." >&2
        fi
    fi
done