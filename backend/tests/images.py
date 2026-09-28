"""Generate real images in memory for tests."""

import io

from PIL import Image


def image_bytes(
    fmt: str = "PNG", size: tuple[int, int] = (64, 48), color: str = "navy", **save: object
) -> bytes:
    buffer = io.BytesIO()
    image = Image.new("RGB", size, color)
    image.save(buffer, format=fmt, **save)
    return buffer.getvalue()


PNG = image_bytes("PNG")
JPEG = image_bytes("JPEG")
