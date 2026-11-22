"""Where is the webcam on the stream?

1. Find a face that sits in the same place on frames taken far apart in time
   (NPC faces in GTA move around, the streamer's face does not).
2. Around it, the webcam overlay shows a still room while the game changes all the time:
   per-pixel variation across frames separates the overlay from the gameplay.
Returns the overlay rectangle in fractions of the frame, or None.
"""
import os

import cv2
import numpy as np

from .util import ASSETS, log

MODEL = os.path.join(ASSETS, "face_detection_yunet_2023mar.onnx")
SW, SH = 480, 270


def _faces(frames):
    H, W = frames[0].shape[:2]
    scale = 960.0 / W
    w, h = int(W * scale), int(H * scale)
    det = cv2.FaceDetectorYN.create(MODEL, "", (w, h), 0.6, 0.3, 50)
    found = []
    for i, f in enumerate(frames):
        small = cv2.resize(f, (w, h), interpolation=cv2.INTER_AREA)
        det.setInputSize((w, h))
        _, res = det.detect(small)
        if res is None:
            continue
        for r in res:
            x, y, bw, bh = (float(v) / scale for v in r[:4])
            if bw >= W * 0.02:
                found.append((i, x, y, bw, bh, float(r[-1])))
    return found


def _stable_face(found, n_frames, W):
    best = None
    for f in found:
        cx, cy = f[1] + f[3] / 2, f[2] + f[4] / 2
        tol_x, tol_y = max(0.04 * W, 0.6 * f[3]), max(0.04 * W, 0.6 * f[4])
        members = [g for g in found
                   if abs(g[1] + g[3] / 2 - cx) < tol_x and abs(g[2] + g[4] / 2 - cy) < tol_y
                   and 0.6 < g[3] / f[3] < 1.6]
        hits = len({g[0] for g in members})
        if best is None or hits > best[0]:
            best = (hits, members)
    if not best or best[0] < max(3, 0.35 * n_frames):
        return None, (best[0] if best else 0)
    arr = np.array([g[1:5] for g in best[1]], np.float32)
    return tuple(float(v) for v in np.median(arr, axis=0)), best[0]


def std_map(frames):
    gs = [cv2.resize(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY), (SW, SH),
                     interpolation=cv2.INTER_AREA).astype(np.float32) for f in frames]
    return np.std(np.stack(gs), axis=0)


def overlay_rect(std, face):
    """face/result in std-map pixels (x, y, w, h)."""
    fx, fy, fw, fh = face
    T = max(12.0, 0.45 * float(np.median(std)))
    hi = std >= T
    lo = cv2.morphologyEx((~hi).astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(lo, connectivity=8)
    cx, cy = fx + fw / 2, fy + fh / 2
    rx0, rx1, ry0, ry1 = cx - 3 * fw, cx + 3 * fw, cy - 2.5 * fh, cy + 3.5 * fh
    x0, y0, x1, y1 = fx, fy, fx + fw, fy + fh
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if a < 20 or w > 0.7 * SW or h > 0.8 * SH:
            continue
        if x > rx1 or x + w < rx0 or y > ry1 or y + h < ry0:
            continue
        x0, y0, x1, y1 = min(x0, x), min(y0, y), max(x1, x + w), max(y1, y + h)
    x0, y0, x1, y1 = int(max(0, x0)), int(max(0, y0)), int(min(SW, x1)), int(min(SH, y1))

    def col(x):
        return hi[y0:y1, x].mean()

    def row(y):
        return hi[y, x0:x1].mean()

    # trim edges that ended up in the game, but never cut into the face
    while x0 < fx and col(x0) > 0.9:
        x0 += 1
    while x1 - 1 > fx + fw and col(x1 - 1) > 0.9:
        x1 -= 1
    while y0 < fy and row(y0) > 0.9:
        y0 += 1
    while y1 - 1 > fy + fh and row(y1 - 1) > 0.9:
        y1 -= 1
    # grow over the person (moving, but mixed with still room) until a full game strip
    grew = True
    while grew:
        grew = False
        if x0 > 0 and (col(x0 - 1) < 0.85 or (x0 > 1 and col(x0 - 2) < 0.85)):
            x0 -= 1
            grew = True
        if x1 < SW and (col(x1) < 0.85 or (x1 + 1 < SW and col(x1 + 1) < 0.85)):
            x1 += 1
            grew = True
        if y0 > 0 and (row(y0 - 1) < 0.85 or (y0 > 1 and row(y0 - 2) < 0.85)):
            y0 -= 1
            grew = True
        if y1 < SH and (row(y1) < 0.85 or (y1 + 1 < SH and row(y1 + 1) < 0.85)):
            y1 += 1
            grew = True
    w, h = x1 - x0, y1 - y0
    if w < 1.4 * fw or h < 1.4 * fh or w * h > 0.45 * SW * SH:
        return None
    return x0, y0, w, h


def detect(frames, debug_path=None):
    """frames: list of BGR images of the same size. Returns dict or None."""
    if len(frames) < 3:
        return None
    H, W = frames[0].shape[:2]
    face, hits = _stable_face(_faces(frames), len(frames), W)
    if face is None:
        log(f"лицо с вебки не нашлось (совпадений {hits} из {len(frames)} кадров)")
        return None
    sx, sy = SW / W, SH / H
    face_s = (face[0] * sx, face[1] * sy, face[2] * sx, face[3] * sy)
    rect = overlay_rect(std_map(frames), face_s)
    if rect is None:  # no clear overlay: take head and shoulders around the face
        fw, fh = face[2], face[3]
        h = min(H, fh * 3.4)
        w = min(W, h * 16 / 9)
        x = min(max(0, face[0] + fw / 2 - w / 2), W - w)
        y = min(max(0, face[1] + fh / 2 - h * 0.42), H - h)
        rect_full = (x, y, w, h)
        how = "вокруг лица"
    else:
        x, y, w, h = rect
        rect_full = (x / sx, y / sy, w / sx, h / sy)
        how = "рамка вебки"
    cam = {"box": [round(rect_full[0] / W, 4), round(rect_full[1] / H, 4),
                   round(rect_full[2] / W, 4), round(rect_full[3] / H, 4)],
           "face": [round(face[0] / W, 4), round(face[1] / H, 4),
                    round(face[2] / W, 4), round(face[3] / H, 4)],
           "how": how, "hits": hits, "frames": len(frames)}
    log(f"вебка: {how}, box={cam['box']} (лицо на {hits}/{len(frames)} кадрах)")
    if debug_path:
        draw_debug(frames[len(frames) // 2], cam, debug_path)
    return cam


def draw_debug(frame, cam, path, game_crop=None):
    img = frame.copy()
    H, W = img.shape[:2]

    def rect(b, color, t):
        x, y, w, h = b
        cv2.rectangle(img, (int(x * W), int(y * H)), (int((x + w) * W), int((y + h) * H)), color, t)

    rect(cam["box"], (46, 255, 200), 6)       # lime: webcam panel
    if cam.get("face"):
        rect(cam["face"], (136, 46, 255), 3)  # pink: the face
    if game_crop:
        rect(game_crop, (255, 255, 255), 2)
    cv2.imwrite(path, img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return path
