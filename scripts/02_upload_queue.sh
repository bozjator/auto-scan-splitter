#!/bin/bash
set -euo pipefail

# Stage 2: drain the upload queue to every configured destination, retrying
# until each file has reached all of them.

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QUEUE_DIR="$REPO_DIR/upload_queue"
CONFIG_FILE="$REPO_DIR/config/upload_destinations.conf"
LOCK_FILE="$QUEUE_DIR/.upload_queue.lock"

# A queued file must be this old before we touch it, so we never upload a crop
# the splitter is still writing.
MIN_AGE_SECONDS=3

# Bounds how long one destination may stall the loop. An offline NAS behind a
# systemd automount otherwise wedges rsync inside the kernel for minutes.
RSYNC_TIMEOUT=120

BACKOFF_INITIAL=10
BACKOFF_MAX=300

# Set by process_queue so the retry message can report how deep the queue is.
QUEUE_DEPTH=0

upload_to() {
    local file="$1" dest="$2"

    # Deliberately NOT -a: preserving perms/owner/group is unsupported on CIFS,
    # so rsync exits 23 *after* the data has already landed, and the retry loop
    # then mistakes every successful upload for a failure forever. --times
    # keeps the photo's date and lets rsync skip a file that already arrived.
    timeout "$RSYNC_TIMEOUT" rsync --times --no-perms --no-owner --no-group \
        --whole-file "$file" "$dest/"
}

destination_usable() {
    local dest="$1" fstype

    # findmnt reads /proc/mountinfo and never stats the path, so unlike [ -d ]
    # it cannot itself trigger (and hang on) a systemd automount.
    fstype="$(findmnt -no FSTYPE -- "$dest" 2>/dev/null)" || fstype=""

    case "$fstype" in
        autofs) return 0 ;;        # let the bounded rsync trigger the mount
        "")     [ -d "$dest" ] ;;  # plain directory, no automount involved
        *)      return 0 ;;        # a real filesystem is mounted here
    esac
}

upload_file() {
    local file="$1"
    local filename
    filename=$(basename "$file")
    local all_success=true
    local attempted=0
    local dest

    # Read destination paths, ignoring comments and empty lines
    while IFS= read -r dest || [ -n "$dest" ]; do
        [[ "$dest" =~ ^#.*$ || -z "$dest" ]] && continue
        dest="${dest%/}"
        attempted=$((attempted + 1))

        # Verify destination directory is accessible/mounted
        if ! destination_usable "$dest"; then
            echo "WARNING: destination '$dest' is not present; skipping it this round." >&2
            all_success=false
            continue
        fi

        # Copy file securely using rsync
        if upload_to "$file" "$dest"; then
            echo "Successfully uploaded $filename -> $dest"
        else
            echo "ERROR: failed uploading $filename -> $dest" >&2
            all_success=false
        fi
    done < "$CONFIG_FILE"

    # Without this guard an empty or all-comment config counts as "everything
    # succeeded" and the file is deleted having gone nowhere.
    if [ "$attempted" -eq 0 ]; then
        echo "ERROR: no usable destinations in $CONFIG_FILE; keeping $filename." >&2
        return 1
    fi

    # Only delete from queue if uploaded to ALL configured destinations
    if [ "$all_success" = true ]; then
        rm -f "$file"
        echo "File $filename delivered to all destinations and removed from queue."
        return 0
    fi
    return 1
}

process_queue() {
    local cutoff filepath
    cutoff="$(date -d "-${MIN_AGE_SECONDS} seconds" '+%Y-%m-%d %H:%M:%S')"
    local has_errors=false
    local count=0

    # Dotfiles are excluded so the lock file is never treated as a photo. The
    # age filter is what stops us uploading a crop mid-write.
    while IFS= read -r -d '' filepath; do
        count=$((count + 1))
        if ! upload_file "$filepath"; then
            has_errors=true
        fi
    done < <(find "$QUEUE_DIR" -maxdepth 1 -type f ! -name '.*' \
                  ! -newermt "$cutoff" -print0)

    QUEUE_DEPTH="$count"

    if [ "$has_errors" = true ]; then
        return 1
    fi
    return 0
}

preflight() {
    local tool
    for tool in rsync inotifywait findmnt flock timeout; do
        if ! command -v "$tool" >/dev/null 2>&1; then
            echo "ERROR: '$tool' not found. On Ubuntu: sudo apt install rsync inotify-tools util-linux" >&2
            exit 1
        fi
    done

    if [ ! -f "$CONFIG_FILE" ]; then
        echo "ERROR: destinations config not found at $CONFIG_FILE" >&2
        exit 1
    fi

    mkdir -p "$QUEUE_DIR"

    # Single instance: a second copy walking the same queue would double-upload
    # and race the deletes.
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        echo "ERROR: another uploader already holds $LOCK_FILE; exiting." >&2
        exit 1
    fi
}

preflight

echo "Started SMB Queue Manager (Hybrid Mode)..."

backoff="$BACKOFF_INITIAL"

while true; do
    # 1 & 3. Process existing queue items (handles reboot backlog or network retries)
    if ! process_queue; then
        echo "Upload errors encountered or destination offline. Queue depth: $QUEUE_DEPTH. Retrying in ${backoff}s..."
        sleep "$backoff"
        backoff=$((backoff * 2))
        if [ "$backoff" -gt "$BACKOFF_MAX" ]; then
            backoff="$BACKOFF_MAX"
        fi
        continue
    fi
    backoff="$BACKOFF_INITIAL"

    # 2. Queue is clear! Block on inotifywait for the next new file event.
    # -t 300 acts as a 5-minute keep-alive check.
    echo "Queue clear. Waiting for new files..."
    if inotifywait -q -t 300 -e close_write,moved_to "$QUEUE_DIR"; then
        # Let the just-closed file age past MIN_AGE_SECONDS so the scan above
        # sees it as complete instead of skipping it as still in flight.
        sleep "$MIN_AGE_SECONDS"
    fi
done
