import cv2
import numpy as np
import sys
import os
import glob

def process_scan(image_path, output_dir, margin_trim=10):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    img = cv2.imread(image_path)
    if img is None:
        return

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    
    # 1. Threshold background (assumes lighter scanner bed background)
    # Adjust 230 lower if scanner lid is off-white/gray
    _, thresh = cv2.threshold(gray, 220, 255, cv2.THRESH_BINARY_INV)

    # Clean up small scanner dust/noise
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel)
    thresh = cv2.dilate(thresh, kernel, iterations=2)

    # 2. Find individual photo contours
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    idx = 0
    base_name = os.path.splitext(os.path.basename(image_path))[0]

    for cnt in contours:
        # Ignore tiny regions (dust or small border artifacts)
        if cv2.contourArea(cnt) < 40000:
            continue

        # Get minimum area bounding box to detect rotation angle
        rect = cv2.minAreaRect(cnt)
        (center), (w, h), angle = rect

        # Fix OpenCV angle output conventions
        if w < h:
            angle = angle - 90

        # SAFEGUARD: Prevent the 45° / 90° rotation bug!
        # If angle calculation flips to adjacent axis, normalize it back to zero
        while angle < -45:
            angle += 90
        while angle > 45:
            angle -= 90

        # Only deskew if it's a realistic slight tilt (-15° to +15°)
        if not (-15 <= angle <= 15):
            angle = 0.0

        # Rotate entire canvas around box center to straighten
        M = cv2.getRotationMatrix2D(center, angle, 1.0)
        rotated_img = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)

        # Get upright bounding box of the photo from the rotated image
        box = cv2.boxPoints(rect)
        box = cv2.transform(np.array([box]), M)[0]
        x, y, w_box, h_box = cv2.boundingRect(np.int32(box))

        # Apply safety bounds
        x = max(0, x + margin_trim)
        y = max(0, y + margin_trim)
        w_box = max(1, w_box - (2 * margin_trim))
        h_box = max(1, h_box - (2 * margin_trim))

        crop = rotated_img[y:y+h_box, x:x+w_box]

        if crop.size > 0:
            output_filename = os.path.join(output_dir, f"{base_name}_split_{idx:02d}.jpg")
            cv2.imwrite(output_filename, crop, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
            print(f"Saved: {output_filename} (Angle applied: {angle:.2f}°)")
            idx += 1

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 scan_splitter.py <input_image_or_folder> <output_folder>")
        sys.exit(1)

    input_target = sys.argv[1]
    out_dir = sys.argv[2]

    if os.path.isdir(input_target):
        files = glob.glob(os.path.join(input_target, "*.jpg")) + glob.glob(os.path.join(input_target, "*.png"))
        for f in files:
            process_scan(f, out_dir)
    else:
        process_scan(input_target, out_dir)
