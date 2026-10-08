"""Clean up the vehicle image before it is served.

The T03 image pack has a semi transparent line along the full height of the right edge:
light with alpha ≈ 87 at the edge, next to it a column with alpha ≈ 7. On a dark
background this is a visible stroke, and the crop for the map marker would treat it as
part of the car. Such edge lines (up to 3 pixels) are made transparent when the edge is
almost fully covered and the band next to it is almost empty. A car that reaches the
edge stays untouched.
"""
from __future__ import annotations

import io
import logging

log = logging.getLogger("leapmotor_gateway")


def _covered(mask) -> int:
    return mask.point(lambda a: 255 if a else 0).histogram()[255]


def _line(side: str, k: int, w: int, h: int) -> tuple:
    """The k-th column or row from the edge."""
    return {"right": (w - 1 - k, 0, w - k, h), "left": (k, 0, k + 1, h),
            "top": (0, k, w, k + 1), "bottom": (0, h - 1 - k, w, h - k)}[side]


def _band(side: str, n: int, w: int, h: int) -> tuple:
    """The three columns or rows inside after n edge lines."""
    return {"right": (w - n - 3, 0, w - n, h), "left": (n, 0, n + 3, h),
            "top": (0, n, w, n + 3), "bottom": (0, h - n - 3, w, h - n)}[side]


def remove_edge_lines(png: bytes) -> bytes:
    """Remove lines along the four image edges, otherwise return the image unchanged."""
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(png)).convert("RGBA")
        w, h = image.size
        if w < 16 or h < 16:
            return png
        alpha = image.getchannel("A")
        clear = []
        for side, length in (("right", h), ("left", h), ("top", w), ("bottom", w)):
            n = 0
            while n < 3 and _covered(alpha.crop(_line(side, n, w, h))) > 0.8 * length:
                n += 1
            if n and _covered(alpha.crop(_band(side, n, w, h))) < 0.05 * 3 * length:
                clear += [_line(side, k, w, h) for k in range(n)]
        if not clear:
            return png
        for line in clear:
            image.paste((0, 0, 0, 0), line)
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()
    except Exception as error:  # noqa: BLE001  an image with a stroke beats no image
        log.info("Edge lines not removed: %s", error)
        return png
