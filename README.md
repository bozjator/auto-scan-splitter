# Auto Scan Splitter

Automated scripts to monitor incoming multi-photo scans and split them into individual cropped photos.

## Overview

This project provides an automated pipeline for batch photo restoration.
It continuously monitors an incoming folder for full-page flatbed scanner images, automatically detects individual photos, deskews them, crops them into standalone files, and routes scanned and processed files to downstream processing queues or local/NAS storage.

## Project Structure

```text
scripts/
├── scan_splitter.py          # Core OpenCV image segmentation and deskew script
└── 01_watch_incoming.sh      # Folder watcher for new scans & execution trigger script
```

## Prerequisites

- Linux
- Python 3.x
- OpenCV & NumPy

```sh
# Python
$ sudo apt update && sudo apt install -y python3 python3-pip python3-venv

# OpenCV & NumPy
$ sudo apt update && sudo apt install -y python3-opencv python3-numpy
```

## Commands

```sh
# Manually test the scan splitter script.
$ python3 scan_splitter.py /path/to/scan.jpg /path/to/output_dir
```
