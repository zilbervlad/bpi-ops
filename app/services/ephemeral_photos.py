from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Iterable

from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.datastructures import FileStorage


MAX_PHOTOS = 5
MAX_PHOTO_BYTES = 10 * 1024 * 1024  # 10 MB each
MAX_IMAGE_PIXELS = 24_000_000       # guards against decompression bombs
MAX_RENDER_SIZE = (1400, 1400)
JPEG_QUALITY = 74

ALLOWED_MIMETYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
}

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


@dataclass
class EphemeralPhoto:
    filename: str
    content_type: str
    image_bytes: bytes
    width: int
    height: int


class PhotoValidationError(ValueError):
    pass


def _safe_filename(name: str | None, fallback: str) -> str:
    name = (name or "").strip()
    if not name:
        return fallback

    # Keep it boring for email/PDF metadata.
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in ("-", "_", ".", " "))
    cleaned = cleaned.strip(" .")
    return cleaned or fallback


def normalize_ephemeral_photos(
    uploaded_files: Iterable[FileStorage],
    *,
    max_photos: int = MAX_PHOTOS,
) -> list[EphemeralPhoto]:
    """
    Validate, auto-orient, resize, and compress uploaded photos for immediate PDF/email use.

    This intentionally does NOT save photos to DB or disk.
    The returned bytes are safe, compressed JPEGs held in memory only.
    """
    files = [
        file
        for file in uploaded_files
        if file and getattr(file, "filename", None)
    ]

    if len(files) > max_photos:
        raise PhotoValidationError(f"Please upload no more than {max_photos} photos.")

    normalized: list[EphemeralPhoto] = []

    for index, file in enumerate(files, start=1):
        mimetype = (file.mimetype or "").lower().strip()
        if mimetype not in ALLOWED_MIMETYPES:
            raise PhotoValidationError(
                "Only JPG and PNG photos are allowed. HEIC is not supported yet."
            )

        raw = file.read()
        if not raw:
            continue

        if len(raw) > MAX_PHOTO_BYTES:
            raise PhotoValidationError(
                "One of the photos is too large. Please use photos under 10 MB."
            )

        try:
            image = Image.open(BytesIO(raw))
            image.verify()
        except (UnidentifiedImageError, OSError, ValueError):
            raise PhotoValidationError("One of the uploaded files is not a valid image.")

        try:
            image = Image.open(BytesIO(raw))
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGB")
            image.thumbnail(MAX_RENDER_SIZE)

            output = BytesIO()
            image.save(
                output,
                format="JPEG",
                quality=JPEG_QUALITY,
                optimize=True,
                progressive=True,
            )

            output_bytes = output.getvalue()
        except Exception:
            raise PhotoValidationError("Could not process one of the uploaded photos.")

        normalized.append(
            EphemeralPhoto(
                filename=_safe_filename(file.filename, f"audit-photo-{index}.jpg"),
                content_type="image/jpeg",
                image_bytes=output_bytes,
                width=image.width,
                height=image.height,
            )
        )

    return normalized
