#!/bin/bash

# Enable case-insensitive file matching
shopt -s nocaseglob

# Define directories
WATCH_DIR="./incoming_scans"
OUTPUT_DIR="./extracted_photos"
PROCESSED_DIR="./processed_scans"

# Create folders if they don't exist
mkdir -p "$WATCH_DIR" "$OUTPUT_DIR" "$PROCESSED_DIR"

echo "Watching '$WATCH_DIR' for new scans..."

for scan in "$WATCH_DIR"/*.jpg "$WATCH_DIR"/*.jpeg "$WATCH_DIR"/*.png; do
    [ -e "$scan" ] || continue

    echo "Processing new file: $scan"
    
    python3 scan_splitter.py "$scan" "$OUTPUT_DIR"
    
    mv "$scan" "$PROCESSED_DIR/"
    echo "Moved $scan to $PROCESSED_DIR/"
    echo "--------------------------------------"
done