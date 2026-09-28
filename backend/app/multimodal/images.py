"""Image validation and preparation for the vision model.

Validation decodes the image with Pillow rather than trusting the extension or
Content-Type, so a renamed file, a truncated upload or an SVG (which can carry
script) is rejected. Preparation produces the copy sent to the model: EXIF
rotation applied, downscaled to the recommended long edge, re-encoded (which
also drops metadata such as GPS location), base64-encoded.

All functions are blocking (decoding/encoding); call them from a worker thread.
"""

import base64
import io
import warnings
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageOps, UnidentifiedImageError

from app.core.errors import AppError, FileTooLargeError, UnsupportedFileTypeError

# Formats accepted by the Claude API, keyed by Pillow format name.
SUPPORTED_FORMATS: dict[str, tuple[str, str]] = {
    "PNG": ("image/png", ".png"),
    "JPEG": ("image/jpeg", ".jpg"),
    "GIF": ("image/gif", ".gif"),
    "WEBP": ("image/webp", ".webp"),
}
# Refuse decompression bombs well before Pillow's own warning threshold.
MAX_PIXELS = 50_000_000


class InvalidImageError(AppError):
    status_code = 422
    code = "invalid_image"
    message = "The file could not be read as an image."


@dataclass(frozen=True, slots=True)
class ImageInfo:
    media_type: str
    extension: str
    width: int
    height: int


def validate_image(data: bytes, *, max_bytes: int, max_dimension: int) -> ImageInfo:
    if not data:
        raise InvalidImageError("The image is empty.")
    if len(data) > max_bytes:
        raise FileTooLargeError(f"Images must be at most {max_bytes // (1024 * 1024)} MB.")
    if data[:512].lstrip().lower().startswith((b"<svg", b"<?xml", b"<!doctype", b"<html")):
        # SVG is markup and can carry script; it is never accepted as an image.
        raise UnsupportedFileTypeError(
            "SVG and other markup files are not supported. Use PNG, JPEG, GIF or WebP."
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                image_format = image.format
                width, height = image.size
                if width * height > MAX_PIXELS:
                    raise InvalidImageError("The image has too many pixels to process.")
                image.verify()  # structural check without a full decode
            # verify() leaves the image unusable; a full decode catches truncated pixel data.
            with Image.open(io.BytesIO(data)) as image:
                image.load()
    except InvalidImageError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InvalidImageError("The image has too many pixels to process.") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise InvalidImageError("The file is not a valid image or is damaged.") from exc

    if image_format not in SUPPORTED_FORMATS:
        raise UnsupportedFileTypeError(
            f"{image_format or 'This'} images are not supported. Use PNG, JPEG, GIF or WebP."
        )
    if width > max_dimension or height > max_dimension:
        raise InvalidImageError(f"Images must be at most {max_dimension} x {max_dimension} pixels.")
    media_type, extension = SUPPORTED_FORMATS[image_format]
    return ImageInfo(media_type=media_type, extension=extension, width=width, height=height)


def prepare_for_model(data: bytes, *, max_edge: int) -> dict[str, Any]:
    """Return a Messages API image content block for `data` (already validated)."""
    try:
        with Image.open(io.BytesIO(data)) as source:
            source_format = source.format
            image = ImageOps.exif_transpose(source)  # honour camera orientation
            if getattr(source, "is_animated", False):
                source.seek(0)  # animated GIF/WebP: the first frame
                image = source.copy()
            image.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)  # only ever shrinks

            buffer = io.BytesIO()
            if source_format == "JPEG":
                image.convert("RGB").save(buffer, format="JPEG", quality=90, optimize=True)
                media_type = "image/jpeg"
            else:
                # PNG keeps screenshots' text crisp and preserves transparency.
                if image.mode not in ("RGB", "RGBA", "L", "LA"):
                    image = image.convert("RGBA")
                image.save(buffer, format="PNG", optimize=True)
                media_type = "image/png"
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise InvalidImageError("The image could not be processed.") from exc

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(buffer.getvalue()).decode("ascii"),
        },
    }
