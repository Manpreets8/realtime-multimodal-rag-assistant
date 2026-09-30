"""Image uploads for chat: storage, ownership, attaching to messages, model inputs."""

import logging
import uuid
from collections.abc import Sequence

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.errors import ConflictError, FileTooLargeError, NotFoundError
from app.llm.base import ImagePart
from app.models import ImageUpload
from app.multimodal.images import ImageInfo, InvalidImageError, prepare_for_model, validate_image
from app.services.storage import LocalFileStorage
from app.utils.files import sanitize_filename

logger = logging.getLogger(__name__)


async def read_upload(file: UploadFile) -> bytes:
    """Read an uploaded image, refusing to buffer more than the limit."""
    limit = get_settings().max_image_size
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise FileTooLargeError(f"Images must be at most {limit // (1024 * 1024)} MB.")
    return data


async def validate(data: bytes) -> ImageInfo:
    settings = get_settings()
    return await run_in_threadpool(
        validate_image, data, max_bytes=settings.max_image_size, max_dimension=settings.max_image_dimension
    )


async def model_block(data: bytes) -> ImagePart:
    return await run_in_threadpool(prepare_for_model, data, max_edge=get_settings().image_model_max_edge)


async def upload(
    db: AsyncSession, storage: LocalFileStorage, user_id: uuid.UUID, file: UploadFile
) -> ImageUpload:
    data = await read_upload(file)
    info = await validate(data)
    image_id = uuid.uuid4()
    key = f"images/{user_id}/{image_id}{info.extension}"
    await storage.write_bytes(key, data)
    image = ImageUpload(
        id=image_id,
        user_id=user_id,
        filename=sanitize_filename(file.filename or f"image{info.extension}"),
        media_type=info.media_type,
        width=info.width,
        height=info.height,
        size_bytes=len(data),
        storage_key=key,
    )
    db.add(image)
    try:
        await db.commit()
    except BaseException:
        await storage.delete(key)
        raise
    await db.refresh(image)
    logger.info(
        "image_uploaded",
        extra={
            "image_id": str(image.id),
            "media_type": info.media_type,
            "width": info.width,
            "height": info.height,
        },
    )
    return image


async def get_owned(db: AsyncSession, user_id: uuid.UUID, image_id: uuid.UUID) -> ImageUpload:
    image = await db.scalar(
        select(ImageUpload).where(ImageUpload.id == image_id, ImageUpload.user_id == user_id)
    )
    if image is None:
        raise NotFoundError("Image not found.")
    return image


async def get_attachable(
    db: AsyncSession, user_id: uuid.UUID, image_ids: Sequence[uuid.UUID]
) -> list[ImageUpload]:
    """The user's images in the given order, each not yet attached to a message."""
    if not image_ids:
        return []
    rows = {
        image.id: image
        for image in await db.scalars(
            select(ImageUpload).where(ImageUpload.id.in_(image_ids), ImageUpload.user_id == user_id)
        )
    }
    missing = [image_id for image_id in image_ids if image_id not in rows]
    if missing:
        raise NotFoundError("One or more images were not found. Upload them again.")
    if any(rows[image_id].message_id is not None for image_id in image_ids):
        raise ConflictError("An image can only be sent once. Upload it again to reuse it.")
    return [rows[image_id] for image_id in image_ids]


async def model_blocks(storage: LocalFileStorage, images: Sequence[ImageUpload]) -> list[ImagePart]:
    """Images as sent to the model. Fails loudly: never answer as if an image was seen when it wasn't."""
    blocks = []
    for image in images:
        try:
            data = await storage.read_bytes(image.storage_key)
        except OSError as exc:
            logger.error("image_file_missing", extra={"image_id": str(image.id)})
            raise InvalidImageError(
                f"The image “{image.filename}” is no longer available. Upload it again."
            ) from exc
        blocks.append(await model_block(data))
    return blocks


async def delete_unattached(
    db: AsyncSession, storage: LocalFileStorage, user_id: uuid.UUID, image_id: uuid.UUID
) -> None:
    image = await get_owned(db, user_id, image_id)
    if image.message_id is not None:
        raise ConflictError("This image is part of a conversation. Delete the conversation instead.")
    key = image.storage_key
    await db.delete(image)
    await db.commit()
    await storage.delete(key)


async def delete_files(storage: LocalFileStorage, keys: Sequence[str]) -> None:
    for key in keys:
        try:
            await storage.delete(key)
        except OSError:
            logger.exception("image_file_delete_failed", extra={"storage_key": key})
