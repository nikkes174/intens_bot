from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_SIDE = 1536
MAX_IMAGE_PIXELS = 40_000_000


class ImageProcessingError(ValueError):
    """Изображение нельзя безопасно подготовить к анализу."""


def prepare_image(image_bytes: bytes, max_side: int = MAX_IMAGE_SIDE) -> bytes:
    if not image_bytes:
        raise ImageProcessingError("Пустое изображение")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ImageProcessingError("Изображение слишком большое")
    if max_side <= 0:
        raise ValueError("max_side должен быть положительным")

    try:
        with Image.open(BytesIO(image_bytes)) as source:
            width, height = source.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ImageProcessingError("Изображение содержит слишком много пикселей")

            source.load()
            image = ImageOps.exif_transpose(source)
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba_image = image.convert("RGBA")
                background = Image.new("RGB", rgba_image.size, "white")
                background.paste(rgba_image, mask=rgba_image.getchannel("A"))
                image = background
            else:
                image = image.convert("RGB")

            if max(image.size) > max_side:
                image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)

            result = BytesIO()
            image.save(result, format="JPEG", quality=85, optimize=True)
            return result.getvalue()
    except ImageProcessingError:
        raise
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ImageProcessingError("Повреждённый или неподдерживаемый файл") from error
