import argparse
import os
import glob
import subprocess
import shutil

from PIL import Image

# x4 output can exceed PIL's ~89MP decompression-bomb guard on large scans
Image.MAX_IMAGE_PIXELS = None

# --- CONFIGURATION ---
# Map these paths to your network drive letters or UNC paths
RAW_DIR = r"D:\PhotoRestoration\extracted_photos"
ENHANCED_DIR = r"D:\PhotoRestoration\EnhancedPhotos"
TEMP_DIR = r"D:\AI_Photo_Restoration_Temp\ai_processing"  # Fast local SSD directory for intermediate work

# Tool Paths (Update these to your local paths)
REALESRGAN_EXE = r"D:\AI_Tools\realesrgan-ncnn-vulkan-20220424-windows\realesrgan-ncnn-vulkan.exe"
CODEFORMER_DIR = r"D:\AI_Tools\CodeFormer"
PYTHON_EXE = os.path.join(CODEFORMER_DIR, "venv", "Scripts", "python.exe")

# Real-ESRGAN only produces clean output at its native x4 scale; -s 1/-s 2 emit blocky
# tile artifacts in this build. The x4 result is downscaled back to the scan's native
# resolution afterwards, so the stage acts as a denoise/deartifact pass, not an upscale.
REALESRGAN_SCALE = 4
# CodeFormer's -s defaults to 2, a plain bilinear resize that adds no detail.
CODEFORMER_UPSCALE = 1
FIDELITY_WEIGHT = 0.7  # 1.0 = original facial accuracy, 0.0 = full AI restoration
JPEG_QUALITY = 95


def run_realesrgan(input_file, output_file):
    """
    Removes scan noise, dust and JPEG artifacts and sharpens details using the GPU.
    Output is the native x4 model scale; see REALESRGAN_SCALE for why.
    """
    cmd = [
        REALESRGAN_EXE,
        "-i", input_file,
        "-o", output_file,
        "-s", str(REALESRGAN_SCALE),
        "-n", "realesrgan-x4plus",    # Standard high-quality photographic model
        "-g", "0",                    # Use primary GPU (0)
        "-f", "png"                   # Lossless intermediate, no JPEG re-compression
    ]
    subprocess.run(cmd, check=True)


def downscale_to_native(png_path, output_path, size):
    """Resample the x4 result back to the scan's original pixel dimensions."""
    with Image.open(png_path) as img:
        img.resize(size, Image.LANCZOS).save(output_path, "PNG")


def run_codeformer(input_file, output_dir, fidelity=FIDELITY_WEIGHT):
    """
    Restores faces. Returns the path of the restored full-resolution image.

    CodeFormer's -o is a result ROOT DIRECTORY, not a file. It writes the pasted-back
    image to <output_dir>/final_results/<input basename>.png, always as PNG, and also
    emits cropped_faces/ and restored_faces/ debug subfolders alongside it.
    """
    cmd = [
        PYTHON_EXE, "inference_codeformer.py",
        "-i", input_file,
        "-o", output_dir,
        "-w", str(fidelity),
        "-s", str(CODEFORMER_UPSCALE),
        "--bg_upsampler", "none"  # Background handled by Real-ESRGAN
    ]
    subprocess.run(cmd, cwd=CODEFORMER_DIR, check=True)

    basename = os.path.splitext(os.path.basename(input_file))[0]
    return os.path.join(output_dir, "final_results", f"{basename}.png")


def convert_to_jpeg(png_path, jpeg_path):
    """Re-encode CodeFormer's lossless PNG as a high-quality JPEG (4:4:4, no chroma loss)."""
    with Image.open(png_path) as img:
        img.convert("RGB").save(jpeg_path, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True)


def process_photos(use_realesrgan=True):
    os.makedirs(ENHANCED_DIR, exist_ok=True)
    os.makedirs(TEMP_DIR, exist_ok=True)

    raw_files = sorted(glob.glob(os.path.join(RAW_DIR, "*.jpg")))
    print(f"Found {len(raw_files)} photos in Raw directory.")
    if not use_realesrgan:
        print("Real-ESRGAN disabled: running CodeFormer face restoration only.")

    for raw_path in raw_files:
        filename = os.path.basename(raw_path)
        stem = os.path.splitext(filename)[0]
        # Faces-only runs get their own suffix so the two modes never overwrite each
        # other and the skip check below stays correct per mode.
        suffix = "" if use_realesrgan else "_facesonly"
        final_output_path = os.path.join(ENHANCED_DIR, f"{stem}{suffix}.jpg")

        # Skip photos that have already been enhanced in this mode
        if os.path.exists(final_output_path):
            print(f"--> Skipping {filename}, already enhanced.")
            continue

        print(f"--> Processing: {filename}")

        # Local temporary working paths on fast NVMe/SSD
        temp_raw = os.path.join(TEMP_DIR, f"raw_{filename}")
        temp_x4 = os.path.join(TEMP_DIR, f"x4_{stem}.png")
        temp_native = os.path.join(TEMP_DIR, f"native_{stem}.png")
        temp_codeformer_dir = os.path.join(TEMP_DIR, f"codeformer_{stem}")
        temp_jpeg = os.path.join(TEMP_DIR, f"out_{stem}.jpg")

        try:
            # Copy raw file from SMB share to local SSD for fast processing
            shutil.copy2(raw_path, temp_raw)

            if use_realesrgan:
                with Image.open(temp_raw) as img:
                    native_size = img.size

                print("    Cleaning up background (Real-ESRGAN x4)...")
                run_realesrgan(temp_raw, temp_x4)

                print("    Downscaling to native resolution...")
                downscale_to_native(temp_x4, temp_native, native_size)
                os.remove(temp_x4)
                codeformer_input = temp_native
            else:
                codeformer_input = temp_raw

            print("    Restoring facial details (CodeFormer)...")
            restored_png = run_codeformer(codeformer_input, temp_codeformer_dir)

            # Encode to a local JPEG first, so a failure part-way through can never
            # leave a truncated file in the Enhanced directory that the skip check
            # above would then treat as already done.
            convert_to_jpeg(restored_png, temp_jpeg)
            shutil.move(temp_jpeg, final_output_path)
            print(f"    OK Saved to {final_output_path}")

        except Exception as e:
            print(f"    X Failed to process {filename}: {e}")

        finally:
            # Clean up local temp files, including CodeFormer's whole result tree
            shutil.rmtree(temp_codeformer_dir, ignore_errors=True)
            for tmp in [temp_raw, temp_x4, temp_native, temp_jpeg]:
                if os.path.exists(tmp):
                    os.remove(tmp)


def main():
    parser = argparse.ArgumentParser(
        description="Enhance scanned photos: Real-ESRGAN background cleanup plus CodeFormer face restoration.")
    parser.add_argument(
        "--no-realesrgan", action="store_true",
        help="Skip the Real-ESRGAN background stage and run CodeFormer face restoration only. "
             "Outputs are written with a _facesonly suffix so they can be compared side by side.")
    args = parser.parse_args()
    process_photos(use_realesrgan=not args.no_realesrgan)


if __name__ == "__main__":
    main()