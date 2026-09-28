import base64
import io

import pytest
from PIL import Image

from app.core.errors import FileTooLargeError, UnsupportedFileTypeError
from app.multimodal.images import InvalidImageError, prepare_for_model, validate_image
from tests.images import PNG, image_bytes

LIMITS = {"max_bytes": 5 * 1024 * 1024, "max_dimension": 8000}


def decode(block: dict) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(block["source"]["data"])))


@pytest.mark.parametrize(
    ("fmt", "media_type"),
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("GIF", "image/gif"), ("WEBP", "image/webp")],
)
def test_supported_formats_are_identified_by_content(fmt: str, media_type: str) -> None:
    info = validate_image(image_bytes(fmt, (120, 80)), **LIMITS)

    assert info.media_type == media_type
    assert (info.width, info.height) == (120, 80)


@pytest.mark.parametrize(
    ("data", "error"),
    [
        pytest.param(b"", InvalidImageError, id="empty"),
        pytest.param(b"definitely not an image", InvalidImageError, id="garbage"),
        pytest.param(PNG[: len(PNG) // 2], InvalidImageError, id="truncated-png"),
        pytest.param(image_bytes("BMP"), UnsupportedFileTypeError, id="bmp"),
        pytest.param(image_bytes("TIFF"), UnsupportedFileTypeError, id="tiff"),
        pytest.param(
            b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
            UnsupportedFileTypeError,
            id="svg",
        ),
        pytest.param(b'<?xml version="1.0"?><svg/>', UnsupportedFileTypeError, id="svg-xml"),
    ],
)
def test_invalid_or_unsupported_images_are_rejected(data: bytes, error: type[Exception]) -> None:
    with pytest.raises(error):
        validate_image(data, **LIMITS)


def test_size_and_dimension_limits() -> None:
    with pytest.raises(FileTooLargeError):
        validate_image(PNG, max_bytes=100, max_dimension=8000)
    with pytest.raises(InvalidImageError, match="at most 100 x 100"):
        validate_image(image_bytes("PNG", (101, 50)), max_bytes=10**7, max_dimension=100)


def test_decompression_bomb_is_rejected_before_decoding() -> None:
    buffer = io.BytesIO()
    Image.new("1", (10_000, 6_000)).save(buffer, format="PNG")  # 60 MP but only a few KB compressed

    with pytest.raises(InvalidImageError, match="too many pixels"):
        validate_image(buffer.getvalue(), max_bytes=10**8, max_dimension=20_000)


def test_large_images_are_downscaled_to_the_model_edge() -> None:
    block = prepare_for_model(image_bytes("PNG", (3000, 2000)), max_edge=1568)

    assert block["type"] == "image" and block["source"]["type"] == "base64"
    assert decode(block).size == (1568, 1045)


def test_small_images_are_never_upscaled() -> None:
    assert decode(prepare_for_model(image_bytes("PNG", (200, 100)), max_edge=1568)).size == (200, 100)


def test_exif_rotation_is_applied_and_metadata_stripped() -> None:
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90° clockwise
    exif[0x010F] = "SecretCam"  # camera make: metadata that must not be forwarded
    photo = image_bytes("JPEG", (400, 200), exif=exif.tobytes())

    block = prepare_for_model(photo, max_edge=1568)

    output = decode(block)
    assert block["source"]["media_type"] == "image/jpeg"
    assert output.size == (200, 400)  # upright
    assert b"SecretCam" not in base64.b64decode(block["source"]["data"])


@pytest.mark.parametrize("fmt", ["WEBP", "GIF"])
def test_other_formats_are_sent_as_png(fmt: str) -> None:
    block = prepare_for_model(image_bytes(fmt, (50, 50)), max_edge=1568)

    assert block["source"]["media_type"] == "image/png"
    assert decode(block).format == "PNG"


def test_animated_gif_sends_the_first_frame() -> None:
    frames = [Image.new("RGB", (40, 40), color) for color in ("red", "blue", "green")]
    buffer = io.BytesIO()
    frames[0].save(buffer, format="GIF", save_all=True, append_images=frames[1:], duration=100)

    first = decode(prepare_for_model(buffer.getvalue(), max_edge=1568)).convert("RGB")

    assert first.getpixel((10, 10)) == (255, 0, 0)
