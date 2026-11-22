"""Generate a crisp multi-resolution Windows .ico icon for Gevik Clip (Obsidian Black & Silver 'G')."""

from pathlib import Path
import struct

import cv2
import numpy as np


def _draw_logo(size: int) -> np.ndarray:
    """Render the Gevik Clip black & brushed-silver 'G' monogram as BGRA numpy array."""
    canvas_size = max(256, size * 4)
    img = np.zeros((canvas_size, canvas_size, 4), dtype=np.uint8)

    def pt(v: float) -> int:
        return round(v * canvas_size / 256.0)

    # Rounded squircle mask
    pad = pt(12)
    radius = pt(54)
    x0, y0, x1, y1 = pad, pad, canvas_size - pad, canvas_size - pad

    mask = np.zeros((canvas_size, canvas_size), dtype=np.uint8)
    cv2.rectangle(mask, (x0 + radius, y0), (x1 - radius, y1), 255, -1)
    cv2.rectangle(mask, (x0, y0 + radius), (x1, y1 - radius), 255, -1)
    for cx, cy in ((x0 + radius, y0 + radius), (x1 - radius, y0 + radius),
                   (x0 + radius, y1 - radius), (x1 - radius, y1 - radius)):
        cv2.circle(mask, (cx, cy), radius, 255, -1)

    # Obsidian matte black gradient (#181B22 -> #050608)
    ys = np.linspace(0.0, 1.0, canvas_size, dtype=np.float32)[:, None]
    xs = np.linspace(0.0, 1.0, canvas_size, dtype=np.float32)[None, :]
    grad = (xs * 0.45 + ys * 0.55)
    b = (0x24 - grad * (0x24 - 0x09)).astype(np.uint8)
    g = (0x1D - grad * (0x1D - 0x07)).astype(np.uint8)
    r = (0x19 - grad * (0x19 - 0x06)).astype(np.uint8)

    img[..., 0] = np.where(mask > 0, b, 0)
    img[..., 1] = np.where(mask > 0, g, 0)
    img[..., 2] = np.where(mask > 0, r, 0)
    img[..., 3] = mask

    # Metallic silver border ring
    inner_pad = pt(19)
    inner_r = pt(47)
    inner_mask = np.zeros((canvas_size, canvas_size), dtype=np.uint8)
    ix0, iy0, ix1, iy1 = inner_pad, inner_pad, canvas_size - inner_pad, canvas_size - inner_pad
    cv2.rectangle(inner_mask, (ix0 + inner_r, iy0), (ix1 - inner_r, iy1), 255, -1)
    cv2.rectangle(inner_mask, (ix0, iy0 + inner_r), (ix1, iy1 - inner_r), 255, -1)
    for cx, cy in ((ix0 + inner_r, iy0 + inner_r), (ix1 - inner_r, iy0 + inner_r),
                   (ix0 + inner_r, iy1 - inner_r), (ix1 - inner_r, iy1 - inner_r)):
        cv2.circle(inner_mask, (cx, cy), inner_r, 255, -1)

    border_mask = (mask > 0) & (inner_mask == 0)
    silver_val = (235 - grad * 95).astype(np.uint8)
    img[..., 0] = np.where(border_mask, np.clip(silver_val.astype(np.int16) + 8, 0, 255).astype(np.uint8), img[..., 0])
    img[..., 1] = np.where(border_mask, silver_val, img[..., 1])
    img[..., 2] = np.where(border_mask, np.clip(silver_val.astype(np.int16) - 4, 0, 255).astype(np.uint8), img[..., 2])

    # Geometric brushed-silver 'G' monogram on separate mask so we can shade it metallically
    g_mask = np.zeros((canvas_size, canvas_size), dtype=np.uint8)
    center = (pt(126), pt(130))
    outer_rad = pt(64)
    thickness = max(6, pt(24))
    # Main 'G' arc (from 42 deg around to 322 deg, leaving clean opening on upper-right)
    cv2.ellipse(g_mask, center, (outer_rad, outer_rad), 0, 12, 326, 255, thickness, lineType=cv2.LINE_AA)
    # Horizontal crossbar of 'G'
    bar_h = max(4, pt(22))
    cv2.rectangle(g_mask, (pt(126), pt(124)), (pt(190), pt(124) + bar_h), 255, -1)
    # Right vertical stem connecting crossbar down to arc
    stem_w = max(4, pt(23))
    cv2.rectangle(g_mask, (pt(190) - stem_w, pt(124)), (pt(190), pt(168)), 255, -1)
    # Subtle metallic play-clip triangle accent top-right
    tri = np.array([[pt(194), pt(58)], [pt(214), pt(70)], [pt(194), pt(82)]], dtype=np.int32)
    cv2.fillPoly(g_mask, [tri], 255, lineType=cv2.LINE_AA)

    # Apply metallic chrome gradient (#FFFFFF top-left -> #8B97A8 bottom-right)
    g_alpha = g_mask.astype(np.float32) / 255.0
    metal_r = (252 - grad * 112).astype(np.float32)
    metal_g = (254 - grad * 105).astype(np.float32)
    metal_b = (255 - grad * 92).astype(np.float32)

    img[..., 0] = np.clip(img[..., 0] * (1.0 - g_alpha) + metal_b * g_alpha, 0, 255).astype(np.uint8)
    img[..., 1] = np.clip(img[..., 1] * (1.0 - g_alpha) + metal_g * g_alpha, 0, 255).astype(np.uint8)
    img[..., 2] = np.clip(img[..., 2] * (1.0 - g_alpha) + metal_r * g_alpha, 0, 255).astype(np.uint8)

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

    header = struct.pack('<HHH', 0, 1, len(png_blobs))
    entries = []
    offset = 6 + 16 * len(png_blobs)
    for size, blob in png_blobs:
        dim = 0 if size >= 256 else size
        entries.append(struct.pack('<BBBBHHII', dim, dim, 0, 0, 1, 32, len(blob), offset))
        offset += len(blob)

    destination.write_bytes(header + b''.join(entries) + b''.join(blob for _, blob in png_blobs))
    return destination
