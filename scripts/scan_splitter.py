import cv2
import numpy as np
import sys
import os
from datetime import datetime

# Accepted input formats. Kept in step with is_scannable_image() in
# 01_split_incoming.sh, which filters inotify events before calling us.
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff")

# Scanner bed pixels brighter than this count as background. Assumes a light
# bed; a dark one (lid open, black backing) inverts the detection and finds
# nothing, which now surfaces as an error rather than a silent empty run.
BACKGROUND_THRESHOLD = 220

# Detected regions smaller than this are scanner dust or border artifacts.
# Roughly 200x200px, so ~17x17mm at 300 DPI.
MIN_PHOTO_AREA = 40000

# Tilt beyond this is more likely a misdetection than a real skew, so the
# rotation is abandoned rather than risking a wildly wrong crop.
MAX_DESKEW_ANGLE = 15.0


def _unique_path(output_dir, prefix, idx):
    """Return a free "<prefix>_<idx>.jpg", stepping past files that exist.

    The timestamp prefix only carries one-second resolution, so two scans
    handled inside the same second would otherwise overwrite each other.
    Stage 2 only ever deletes from the queue, never creates, so a name that is
    free here stays free until we write it.
    """
    while True:
        candidate = os.path.join(output_dir, f"{prefix}_{idx:02d}.jpg")
        if not os.path.exists(candidate):
            return candidate
        idx += 1


def find_images(folder):
    """List image files in folder, sorted, skipping hidden temp files.

    Case-insensitive on purpose: scanners commonly emit uppercase .JPG, and the
    old *.jpg/*.png glob silently skipped those along with jpeg/tif/tiff.
    """
    found = []
    for name in sorted(os.listdir(folder)):
        if name.startswith("."):
            continue
        if name.lower().endswith(IMAGE_EXTENSIONS):
            found.append(os.path.join(folder, name))
    return found


def process_scan(image_path, output_dir, margin_trim=10):
    """Split one multi-photo scan into individual cropped photos.

    Returns the number of crops written, or a negative value when the scan was
    not fully processed. Callers must treat a result <= 0 as failure and keep
    the source scan: deleting it on a zero result destroys the original with
    nothing to show for it.
    """
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    source_name = os.path.basename(image_path)

    img = cv2.imread(image_path)
    if img is None:
        print(f"ERROR: could not decode '{source_name}'. Corrupt, or a format "
              f"cv2.imread cannot read (PDF and multi-page TIFF both fail here).",
              file=sys.stderr)
        return -1

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 1. Threshold background (assumes lighter scanner bed background)
    _, thresh = cv2.threshold(gray, BACKGROUND_THRESHOLD, 255, cv2.THRESH_BINARY_INV)

    # Clean up small scanner dust/noise
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
    # This dilation grows every region by ~5px and merges photos sitting closer
    # than that. It is deliberately paired with margin_trim, which pulls the
    # crop back in by 10px per side; retuning one without the other shifts every
    # crop boundary.
    thresh = cv2.dilate(thresh, kernel, iterations=2)

    # 2. Find individual photo contours
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    # Generate timestamp naming prefix: YYYYMMDD_HHMMSS
    timestamp_prefix = datetime.now().strftime("%Y%m%d_%H%M%S")

    written = 0
    region_no = 0
    skipped_small = 0
    empty_crops = 0
    write_failures = 0

    for cnt in contours:
        # Ignore tiny regions (dust or small border artifacts)
        if cv2.contourArea(cnt) < MIN_PHOTO_AREA:
            skipped_small += 1
            continue

        region_no += 1

        # Get minimum area bounding box to detect rotation angle
        rect = cv2.minAreaRect(cnt)
        (center), (w, h), angle = rect

        # Fix OpenCV angle output conventions
        if w < h:
            angle = angle - 90

        # SAFEGUARD: Normalize angles to prevent 45° / 90° rotation bugs
        while angle < -45:
            angle += 90
        while angle > 45:
            angle -= 90

        # Only deskew if it's a realistic slight tilt (-15° to +15°)
        if not (-MAX_DESKEW_ANGLE <= angle <= MAX_DESKEW_ANGLE):
            # Without the rotation the crop is the axis-aligned box of a tilted
            # rectangle, so it keeps triangular wedges of background.
            print(f"WARN: {source_name} region {region_no}: tilt of {angle:.2f}° "
                  f"exceeds ±{MAX_DESKEW_ANGLE:.0f}°, not deskewing. Crop will "
                  f"include background corners.", file=sys.stderr)
            angle = 0.0

        box = cv2.boxPoints(rect)

        if angle == 0.0:
            # An identity warp would resample the whole page for no reason.
            rotated_img = img
        else:
            # Rotate entire canvas around box center to straighten
            M = cv2.getRotationMatrix2D(center, angle, 1.0)
            rotated_img = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]),
                                         flags=cv2.INTER_CUBIC,
                                         borderMode=cv2.BORDER_REPLICATE)
            # Get upright bounding box of the photo from the rotated image
            box = cv2.transform(np.array([box]), M)[0]

        x, y, w_box, h_box = cv2.boundingRect(np.int32(box))

        # Apply safety bounds
        x = max(0, x + margin_trim)
        y = max(0, y + margin_trim)
        w_box = max(1, w_box - (2 * margin_trim))
        h_box = max(1, h_box - (2 * margin_trim))

        crop = rotated_img[y:y+h_box, x:x+w_box]

        if crop.size == 0:
            empty_crops += 1
            print(f"WARN: {source_name} region {region_no}: crop at x={x} y={y} "
                  f"w={w_box} h={h_box} fell outside the page; skipped.",
                  file=sys.stderr)
            continue

        # Output filename format: YYYYMMDD_HHMMSS_01.jpg
        output_filename = _unique_path(output_dir, timestamp_prefix, written + 1)

        # imwrite returns False instead of raising when the write fails (queue
        # not writable, disk full). Treating that as success would report a crop
        # that does not exist and get the original scan deleted.
        if not cv2.imwrite(output_filename, crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95]):
            write_failures += 1
            print(f"ERROR: {source_name} region {region_no}: failed writing "
                  f"'{output_filename}'. Is the queue writable and the disk "
                  f"not full?", file=sys.stderr)
            continue

        print(f"Saved: {output_filename} (Angle applied: {angle:.2f}°)")
        written += 1

    if write_failures:
        detail = (f" The {written} that did succeed are already in the queue and"
                  f" will be duplicated on a retry." if written else "")
        print(f"ERROR: {write_failures} of {written + write_failures} crops from "
              f"'{source_name}' failed to write.{detail}", file=sys.stderr)
        return -1

    if written == 0:
        print(f"ERROR: no photos extracted from '{source_name}' - {len(contours)} "
              f"region(s) detected, {skipped_small} below the {MIN_PHOTO_AREA}px "
              f"area floor, {empty_crops} cropped empty. Source scan retained.",
              file=sys.stderr)

    return written


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 scan_splitter.py <input_image_or_folder> <output_folder>")
        sys.exit(1)

    input_target = sys.argv[1]
    out_dir = sys.argv[2]

    if os.path.isdir(input_target):
        files = find_images(input_target)
        if not files:
            print(f"ERROR: no images in '{input_target}'. Looked for "
                  f"{', '.join(IMAGE_EXTENSIONS)}.", file=sys.stderr)
            sys.exit(1)

        failed = [f for f in files if process_scan(f, out_dir) <= 0]

        print(f"Folder run: {len(files) - len(failed)}/{len(files)} scan(s) "
              f"produced crops.")
        for f in failed:
            print(f"ERROR: no usable output from '{f}'.", file=sys.stderr)
        sys.exit(1 if failed else 0)

    if not os.path.isfile(input_target):
        print(f"ERROR: '{input_target}' is neither a file nor a directory.",
              file=sys.stderr)
        sys.exit(1)

    sys.exit(0 if process_scan(input_target, out_dir) > 0 else 1)
