from io import BytesIO

import pytest
from PIL import Image

from tgbot.services.image_processor import (
    MAX_IMAGE_BYTES,
    ImageProcessingError,
    prepare_image,
)


def make_image(size: tuple[int, int] = (200, 100), image_format: str = "PNG") -> bytes:
    image = Image.new("RGBA", size, (255, 0, 0, 128))
    output = BytesIO()
    image.save(output, format=image_format)
    return output.getvalue()


def test_prepare_image_converts_png_to_jpeg_and_resizes() -> None:
    prepared = prepare_image(make_image((3000, 2000)))

    with Image.open(BytesIO(prepared)) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert max(image.size) == 1536


def test_prepare_image_rejects_corrupted_file() -> None:
    with pytest.raises(ImageProcessingError):
        prepare_image(b"not an image")


def test_prepare_image_rejects_oversized_input() -> None:
    with pytest.raises(ImageProcessingError, match="слишком большое"):
        prepare_image(b"x" * (MAX_IMAGE_BYTES + 1))
