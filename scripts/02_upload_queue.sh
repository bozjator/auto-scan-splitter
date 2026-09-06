#!/bin/bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QUEUE_DIR="$REPO_DIR/upload_queue"
CONFIG_FILE="$REPO_DIR/config/upload_destinations.conf"

mkdir -p "$QUEUE_DIR"

upload_file() {
    local file="$1"
    local filename
    filename=$(basename "$file")
    local all_success=true

    # Read destination paths, ignoring comments and empty lines
    while IFS= read -r dest || [ -n "$dest" ]; do
        [[ "$dest" =~ ^#.*$ || -z "$dest" ]] && continue

        # Verify destination directory is accessible/mounted
        if [ ! -d "$dest" ]; then
            echo "WARNING: Destination path '$dest' unavailable." >&2
            all_success=false
            continue
        fi

        # Copy file securely using rsync
        if rsync -a "$file" "$dest/$filename"; then
            echo "Successfully uploaded $filename -> $dest"
        else
            echo "ERROR: Failed uploading $filename -> $dest" >&2
            all_success=false
        fi
    done < "$CONFIG_FILE"

    # Only delete from queue if uploaded to ALL configured destinations
    if [ "$all_success" = true ]; then
        rm -f "$file"
        echo "File $filename delivered to all destinations and removed from queue."
        return 0
    else
        return 1
    fi
}

process_queue() {
    local has_errors=false

    shopt -s nullglob
    local files=("$QUEUE_DIR"/*)
    shopt -u nullglob

    if [ ${#files[@]} -eq 0 ]; then
        return 0
    fi

    for f in "${files[@]}"; do
        if [ -f "$f" ]; then
            if ! upload_file "$f"; then
                has_errors=true
            fi
        fi
    done

    if [ "$has_errors" = true ]; then
        return 1
    else
        return 0
    fi
}

echo "Started SMB Queue Manager (Hybrid Mode)..."

while true; do
    # 1 & 3. Process existing queue items (handles reboot backlog or network retries)
    if ! process_queue; then
        echo "Upload errors encountered or destination offline. Retrying in 10 seconds..."
        sleep 10
        continue
    fi

    # 2. Queue is clear! Block on inotifywait for the next new file event.
    # -t 300 acts as a 5-minute keep-alive check.
    echo "Queue clear. Waiting for new files..."
    inotifywait -q -t 300 -e close_write,moved_to "$QUEUE_DIR" || true
done