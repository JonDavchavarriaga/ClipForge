"""Generate a crisp multi-resolution Windows .ico icon for ClipForge using OpenCV/NumPy."""

from pathlib import Path
import struct

import cv2
import numpy as np


def _draw_logo(size: int) -> np.ndarray:
    """Render the ClipForge 9:16 vertical frame + play button logo as BGRA numpy array."""
    # Supersample 4x for crisp antialiasing
    canvas_size = max(256, size * 4)
    img = np.zeros((canvas_size, canvas_size, 4), dtype=np.uint8)

    def pt(v: float) -> int:
        return round(v * canvas_size / 256.0)

    # Rounded dark charcoal background
    pad = pt(12)
    radius = pt(52)
    x0, y0, x1, y1 = pad, pad, canvas_size - pad, canvas_size - pad

    mask = np.zeros((canvas_size, canvas_size), dtype=np.uint8)
    cv2.rectangle(mask, (x0 + radius, y0), (x1 - radius, y1), 255, -1)
    cv2.rectangle(mask, (x0, y0 + radius), (x1, y1 - radius), 255, -1)
    for cx, cy in ((x0 + radius, y0 + radius), (x1 - radius, y0 + radius),
                   (x0 + radius, y1 - radius), (x1 - radius, y1 - radius)):
        cv2.circle(mask, (cx, cy), radius, 255, -1)

    # Gradient fill (#111827 -> #1F2937 in BGR)
    ys = np.linspace(0.0, 1.0, canvas_size, dtype=np.float32)[:, None]
    xs = np.linspace(0.0, 1.0, canvas_size, dtype=np.float32)[None, :]
    grad = (xs + ys) * 0.5
    b = (0x27 + grad * (0x37 - 0x27)).astype(np.uint8)
    g = (0x18 + grad * (0x29 - 0x18)).astype(np.uint8)
    r = (0x11 + grad * (0x1F - 0x11)).astype(np.uint8)

    img[..., 0] = np.where(mask > 0, b, 0)
    img[..., 1] = np.where(mask > 0, g, 0)
    img[..., 2] = np.where(mask > 0, r, 0)
    img[..., 3] = mask

    # Vertical 9:16 orange-red frame (#F97316 -> BGR (22, 115, 249))
    thickness = max(4, pt(13))
    fx0, fy0, fx1, fy1 = pt(78), pt(42), pt(178), pt(214)
    cv2.rectangle(img, (fx0, fy0), (fx1, fy1), (38, 100, 246, 255), thickness, lineType=cv2.LINE_AA)

    # Accent side cut bars
    bar_t = max(3, pt(9))
    cv2.line(img, (pt(42), pt(88)), (pt(62), pt(88)), (22, 115, 249, 255), bar_t, lineType=cv2.LINE_AA)
    cv2.line(img, (pt(194), pt(168)), (pt(214), pt(168)), (68, 68, 239, 255), bar_t, lineType=cv2.LINE_AA)

    # Play triangle (#F9FAFB)
    triangle = np.array([[pt(114), pt(96)], [pt(114), pt(160)], [pt(162), pt(128)]], dtype=np.int32)
    cv2.fillPoly(img, [triangle], (251, 250, 249, 255), lineType=cv2.LINE_AA)

    return cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)


def generate_ico(destination: Path, sizes: tuple[int, ...] = (16, 32, 48, 64, 128, 256)) -> Path:
    """Write a multi-resolution PNG-compressed Windows .ico file."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    png_blobs: list[tuple[int, bytes]] = []
    for size in sizes:
        rgba = _draw_logo(size)
        ok, encoded = cv2.imencode('.png', rgba)
        if not ok:
            raise RuntimeError(f'No se pudo codificar el icono {size}x{size}.')
        png_blobs.append((size, encoded.tobytes()))

    # ICONDIR header: reserved=0, type=1 (ICO), count=len(png_blobs)
    header = struct.pack('<HHH', 0, 1, len(png_blobs))
    entries = []
    offset = 6 + 16 * len(png_blobs)
    for size, blob in png_blobs:
        dim = 0 if size >= 256 else size
        entries.append(struct.pack('<BBBBHHII', dim, dim, 0, 0, 1, 32, len(blob), offset))
        offset += len(blob)

    destination.write_bytes(header + b''.join(entries) + b''.join(blob for _, blob in png_blobs))
    return destination
