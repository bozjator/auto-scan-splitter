# Auto Scan Splitter

Automated scripts to monitor incoming multi-photo scans and split them into individual cropped photos.

## Overview

This project provides an automated pipeline for batch photo restoration.
It continuously monitors an incoming folder for full-page flatbed scanner images, automatically detects individual photos, deskews them, crops them into standalone files, and routes scanned and processed files to downstream processing queues or local/NAS storage.

## Project Structure

```
auto-scan-splitter/
├── config/
│   └── upload_destinations.conf    # List of SMB network shares for upload
├── scripts/
│   ├── scan_splitter.py            # Core OpenCV cropping engine
│   ├── 01_split_incoming.sh        # Stage 1: New scans watcher & splitter
│   └── 02_upload_queue.sh          # Stage 2: SMB uploader with retry queue
├── systemd/
│   ├── scan-splitter.service       # Systemd unit file for Stage 1
│   └── scan-uploader.service       # Systemd unit file for Stage 2
├── upload_queue/                   # Staging area for cropped images
└── README.md
```

## Prerequisites

- Linux
- Python 3.x
- OpenCV & NumPy

```sh
# Refresh system local database of available software packages
$ sudo apt update

# Python
$ sudo apt install -y python3 python3-pip python3-venv

# OpenCV & NumPy
$ sudo apt install -y python3-opencv python3-numpy

# Install inotify-tools used to monitor for new scans
$ sudo apt install -y inotify-tools
```

## Commands

```sh
# Manually test the scan splitter script.
$ python3 scan_splitter.py /path/to/scan.jpg /path/to/output_dir
```

## Samba SMB

Setup Samba to store your scanned images to your server.

```sh
# Install Samba
$ sudo apt update
$ sudo apt install samba

# Create a folder where your scanned documents will land
$ sudo mkdir -p /srv/auto-scan-splitter
$ sudo chmod 777 /srv/auto-scan-splitter

# Open the Samba configuration
$ sudo nano /etc/samba/smb.conf

# Add following content to the bottom of the file.
; Defines the name of the share as it appears on the network (e.g., \\IP_ADDRESS\auto-scan-splitter)
[auto-scan-splitter]

   ; The absolute physical path on your Ubuntu filesystem where files will be saved
   path = /srv/auto-scan-splitter

   ; Makes this share visible when browsing network locations from other computers
   browsable = yes

   ; Allows connected clients (like your scanner) to write and upload files to this folder
   writable = yes

   ; Disables anonymous access; users must provide a valid username and password
   guest ok = no

   ; Explicitly allows modification/creation of files (redundant with writable = yes, but good practice)
   read only = no

   ; Restricts access exclusively to the specified Samba user account
   valid users = scanneruser

   ; Sets permissions on newly uploaded files so all users can read and write to them (rw-rw-rw-)
   create mask = 0666

   ; Sets permissions on newly created subfolders so they can be read, written, and opened (rwxrwxrwx)
   directory mask = 0777

# Create a system user 'scanneruser' without SSH terminal access
$ sudo useradd -M -s /usr/sbin/nologin scanneruser

# Assign a Samba password to scanneruser (you will use this password on the scanner's web UI)
$ sudo smbpasswd -a scanneruser

# Give scanneruser ownership of the scan folder
$ sudo chown -R scanneruser:scanneruser /srv/auto-scan-splitter

# Restart Samba to load the new share configuration and check if its running
$ sudo systemctl restart smbd
$ sudo systemctl status smbd

# Firewall
$ sudo ufw allow Samba
$ sudo ufw reload
$ sudo ufw status
```

### How to Fill Out the Scanner Web UI

Match the settings in your printer's web interface to what you configured on your server:

| Scanner UI Field    | What to Enter                               | Example               |
| :------------------ | :------------------------------------------ | :-------------------- |
| **Host Address**    | Your Server's local IP address              | `192.168.1.100`       |
| **Store Directory** | The share name                              | `auto-scan-splitter`  |
| **Username**        | The Samba user created on Server            | `scanneruser`         |
| **Password**        | The Samba password you set with `smbpasswd` | `your_samba_password` |

## Enable and Start Services

```sh
# 1. Copy the systemd unit files from your repo to the system directory
sudo cp systemd/scan-splitter.service /etc/systemd/system/
sudo cp systemd/scan-uploader.service /etc/systemd/system/

# 2. Set proper root permissions on the service files
sudo chmod 644 /etc/systemd/system/scan-splitter.service
sudo chmod 644 /etc/systemd/system/scan-uploader.service

# 3. Reload systemd configuration to register the new unit files
sudo systemctl daemon-reload

# 4. Enable (autostart on boot) and immediately start both services
sudo systemctl enable --now scan-splitter.service scan-uploader.service
```

#### Useful Commands for Verification

```sh
# Check status of both services
sudo systemctl status scan-splitter.service scan-uploader.service

# View real-time logs for the splitter service
sudo journalctl -u scan-splitter.service -f

# View real-time logs for the SMB uploader service
sudo journalctl -u scan-uploader.service -f
```
