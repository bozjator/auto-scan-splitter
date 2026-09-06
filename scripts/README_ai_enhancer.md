# ai_enhancer.py — AI photo restoration

Restores scanned photos on this Windows machine: a colour-correction pass plus two GPU stages.
This file covers only `scripts/ai_enhancer.py`; the project README covers the scan-splitter and
uploader services.

## What it does

For every `*.jpg` in `RAW_DIR`:

1. **Colour correction.** Faded scans carry a per-channel colour veil (usually blue/cyan) that
   neither AI tool removes. A per-channel percentile stretch lifts the faded blacks, a
   white-patch balance neutralises the cast using the brightest neutral regions (shirts, paper,
   flash highlights), then a mild saturation and midtone lift restores a fresh-print look.
2. **Real-ESRGAN** (`realesrgan-ncnn-vulkan`) at its native x4 scale, then Lanczos-downscaled
   back to the scan's original pixel dimensions. This removes scan noise, dust and JPEG
   artifacts and sharpens detail _without_ changing the resolution.
3. **CodeFormer** face restoration at native resolution.

The result is re-encoded as JPEG (quality 95, 4:4:4 chroma) and written to `ENHANCED_DIR` with
the same pixel dimensions as the scan.

## Requirements (already set up on this machine)

| What                      | Where                                                                                                                                                      |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Real-ESRGAN               | `D:\AI_Tools\realesrgan-ncnn-vulkan-20220424-windows\realesrgan-ncnn-vulkan.exe`                                                                           |
| CodeFormer                | `D:\AI_Tools\CodeFormer` (repo clone)                                                                                                                      |
| Python env for CodeFormer | `D:\AI_Tools\CodeFormer\venv` (torch 2.14 + cu126)                                                                                                         |
| Model weights             | `weights\CodeFormer\codeformer.pth`, `weights\facelib\detection_Resnet50_Final.pth`, `weights\facelib\parsing_parsenet.pth` under `D:\AI_Tools\CodeFormer` |
| Input scans               | `D:\PhotoRestoration\extracted_photos`                                                                                                                     |
| Output                    | `D:\PhotoRestoration\EnhancedPhotos`                                                                                                                       |
| Scratch space             | `D:\AI_Photo_Restoration_Temp\ai_processing`                                                                                                               |

All paths are absolute constants at the top of the script; edit them there if your layout
differs. CodeFormer ships its own copy of `basicsr`, so BasicSR must **not** be pip-installed.
It does need `D:\AI_Tools\CodeFormer\basicsr\version.py` to exist (a normally generated file);
if it is missing, `import basicsr` fails — recreate it containing:

```python
__version__ = '1.3.2'
__gitsha__ = 'unknown'
version_info = (1, 3, 2)
```

## Running

Any Python 3 with Pillow and numpy works; the CodeFormer venv's Python always has both:

```
python scripts\ai_enhancer.py                 # full pipeline
python scripts\ai_enhancer.py --no-realesrgan # CodeFormer face restoration only
python scripts\ai_enhancer.py --no-color      # skip colour correction
python scripts\ai_enhancer.py --help
```

Works from any directory. On an RTX 3070 expect roughly 45 s per 3000x2000 photo for the full
pipeline and about 13 s per photo with `--no-realesrgan`.

## Flags

| Flag              | Effect                                                                                              |
| ----------------- | --------------------------------------------------------------------------------------------------- |
| `--no-realesrgan` | Skip the Real-ESRGAN background stage; run CodeFormer only. Output files get a `_facesonly` suffix. |
| `--no-color`      | Skip the colour-correction stage. Output files get a `_nocolor` suffix.                             |

## Output naming and re-running

- Full pipeline: `EnhancedPhotos\<name>.jpg`
- Faces only: `EnhancedPhotos\<name>_facesonly.jpg`
- No colour correction: `EnhancedPhotos\<name>_nocolor.jpg`
- Neither stage: `EnhancedPhotos\<name>_facesonly_nocolor.jpg`

The modes use different filenames on purpose, so you can keep several and compare them side by
side, and so the skip logic never confuses them. Any photo whose output file already exists is
skipped; delete that one file to reprocess a single photo. A photo that fails leaves no output
file behind, so it is retried on the next run.

## Tuning (constants at the top of the script)

| Constant                       | Default       | Notes                                                                                                          |
| ------------------------------ | ------------- | -------------------------------------------------------------------------------------------------------------- |
| `FIDELITY_WEIGHT`              | 0.7           | 1.0 = keep the original face, 0.0 = full AI reinterpretation                                                   |
| `JPEG_QUALITY`                 | 95            | Output JPEG quality (4:4:4 chroma)                                                                             |
| `REALESRGAN_SCALE`             | 4             | **Do not set to 1 or 2** — see the warning below                                                               |
| `CODEFORMER_UPSCALE`           | 1             | Must stay 1; CodeFormer's own default of 2 is a pointless bilinear doubling                                    |
| `COLOR_STRETCH_PERCENTILES`    | `(0.5, 99.5)` | Fade/veil removal strength; narrow the range (e.g. `(1, 99)`) to correct less aggressively                     |
| `COLOR_WHITE_PATCH_PERCENTILE` | `95.0`        | Which pixels are treated as the white reference. Lower it if a cast remains, raise it if whites pick up a tint |
| `COLOR_SATURATION`             | 1.2           | Saturation lift applied after white balance; 1.0 disables                                                      |
| `COLOR_GAMMA`                  | 0.9           | Midtone brightness lift; below 1 brightens, 1.0 disables                                                       |

> **Warning:** this `realesrgan-ncnn-vulkan` build emits blocky tile corruption at `-s 1` and
> `-s 2` while still exiting 0. The corruption also makes CodeFormer detect zero faces, so the
> damage is completely silent. Only the native x4 scale is clean, which is why the script runs
> x4 and downscales afterwards. If output ever looks blocky, or CodeFormer reports 0 faces on a
> photo that clearly has them, suspect this first.

## Troubleshooting

- `ModuleNotFoundError: No module named 'basicsr.version'` — the generated version file is
  missing; recreate it as shown under Requirements.
- `detect 0 faces` — either the Real-ESRGAN warning above, or the photo genuinely has no
  detectable face. CodeFormer then returns the image unchanged, which is harmless.
- `X Failed to process <file>: ...` — the exception message follows the photo name; temp files
  are cleaned up and the photo is retried on the next run.
- CUDA out of memory — close other GPU applications. Real-ESRGAN tiles automatically and
  CodeFormer processes one 512x512 face crop at a time, so this is unlikely on an 8 GB card.
- Colour over- or under-corrected on a particular photo — the white-patch step assumes the
  brightest regions of the scene are neutral (shirts, paper, flash highlights). If a photo's
  brightest area is strongly coloured (a lamp, a sky), tune `COLOR_WHITE_PATCH_PERCENTILE` or
  process that batch with `--no-color`.
