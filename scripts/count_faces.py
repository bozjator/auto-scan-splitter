"""Print how many faces CodeFormer would restore in an image.

Uses the same detector, resize and eye-distance threshold as CodeFormer's
FaceRestoreHelper so the answer matches what inference_codeformer.py reports.
Run with the CodeFormer venv's python and CODEFORMER_DIR as cwd / on PYTHONPATH.
"""
import sys

import cv2
import numpy as np
import torch

from facelib.detection import init_detection_model

RESIZE = 640
EYE_DIST_THRESHOLD = 5

det = init_detection_model("retinaface_resnet50", device="cuda")
img = cv2.imread(sys.argv[1])
h, w = img.shape[:2]
scale = RESIZE / min(h, w)
interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
small = cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=interp)
with torch.no_grad():
    boxes = det.detect_faces(small)

count = 0
if boxes is not None and boxes.shape[0] > 0:
    boxes = boxes / scale
    for bbox in boxes:
        eye_dist = np.linalg.norm([bbox[6] - bbox[8], bbox[7] - bbox[9]])
        if eye_dist >= EYE_DIST_THRESHOLD:
            count += 1
print(count)
