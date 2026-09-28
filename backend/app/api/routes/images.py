import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.api.deps import ChatLimit, CurrentUser, DbSession, Storage, UploadLimit
from app.core.config import get_settings
from app.core.errors import NotFoundError
from app.llm import claude
from app.rag import embeddings, reranking
from app.rag.conversation import rewrite_query
from app.rag.pipeline import answer_question
from app.rag.prompts import DEFAULT_IMAGE_QUESTION
from app.schemas.rag import AnswerResponse
from app.services import image_service, rag_service
from app.services.retrieval_service import ensure_knowledge_bases_owned

images_router = APIRouter(prefix="/images", tags=["images"])
multimodal_router = APIRouter(prefix="/multimodal", tags=["images"])

_IMAGE_ERRORS = {
    413: {"description": "Image larger than MAX_IMAGE_SIZE"},
    415: {"description": "Not a PNG, JPEG, GIF or WebP image"},
    422: {"description": "Corrupt or unreadable image, or too many pixels"},
}


class ImageRead(BaseModel):
    id: uuid.UUID
    filename: str
    media_type: str
    width: int
    height: int
    size_bytes: int


@images_router.post(
    "",
    response_model=ImageRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload an image to attach to a chat message",
    responses=_IMAGE_ERRORS,
)
async def upload_image(
    db: DbSession,
    storage: Storage,
    current_user: CurrentUser,
    _: UploadLimit,
    file: Annotated[UploadFile, File(description="PNG, JPEG, GIF or WebP")],
) -> ImageRead:
    image = await image_service.upload(db, storage, current_user.id, file)
    return ImageRead.model_validate(image, from_attributes=True)


@images_router.get(
    "/{image_id}/content",
    response_class=FileResponse,
    summary="The image file (owner only)",
    responses={404: {"description": "Image not found (or owned by another user)"}},
)
async def get_image_content(
    image_id: uuid.UUID, db: DbSession, storage: Storage, current_user: CurrentUser
) -> FileResponse:
    image = await image_service.get_owned(db, current_user.id, image_id)
    if not await storage.exists(image.storage_key):
        raise NotFoundError("The image file is no longer available.")
    return FileResponse(
        storage.path_for(image.storage_key),
        media_type=image.media_type,  # from the decoded format, never the client's claim
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"},
    )


@images_router.delete(
    "/{image_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an uploaded image that has not been sent",
    responses={
        404: {"description": "Image not found"},
        409: {"description": "Image already sent in a message"},
    },
)
async def delete_image(
    image_id: uuid.UUID, db: DbSession, storage: Storage, current_user: CurrentUser
) -> Response:
    await image_service.delete_unattached(db, storage, current_user.id, image_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@multimodal_router.post(
    "/image",
    response_model=AnswerResponse,
    summary="Ask a one-off question about an image (optionally with a knowledge base)",
    responses={**_IMAGE_ERRORS, 404: {"description": "Knowledge base not found"}},
)
async def ask_about_image(
    db: DbSession,
    current_user: CurrentUser,
    _: ChatLimit,
    file: Annotated[UploadFile, File(description="PNG, JPEG, GIF or WebP")],
    question: Annotated[str, Form(max_length=2000)] = "",
    knowledge_base_id: Annotated[uuid.UUID | None, Form()] = None,
) -> AnswerResponse:
    """Not saved to any conversation: the image is validated and processed in memory.
    With a knowledge base, the image's visible text is used to search it and the answer
    cites the documents (`answer_type=multimodal`); otherwise `answer_type=image`."""
    settings = get_settings()
    if knowledge_base_id:
        await ensure_knowledge_bases_owned(db, current_user.id, [knowledge_base_id])
    llm = claude.get_llm_client()
    data = await image_service.read_upload(file)
    await image_service.validate(data)
    blocks = [await image_service.model_block(data)]
    question = question.strip() or DEFAULT_IMAGE_QUESTION

    retrieval_query = question
    if knowledge_base_id and settings.query_rewrite_enabled:
        retrieval_query, _ = await rewrite_query(
            llm, [], question, effort=settings.query_rewrite_effort, images=blocks
        )

    result = await answer_question(
        db,
        user_id=current_user.id,
        knowledge_base_ids=[knowledge_base_id] if knowledge_base_id else [],
        question=question,
        embedder=embeddings.get_embedding_provider(),
        reranker=reranking.get_reranker(),
        llm=llm,
        candidates=settings.top_k,
        rerank_candidates=settings.rerank_candidates,
        top_k=settings.rerank_top_k,
        similarity_threshold=settings.similarity_threshold,
        retrieval_query=retrieval_query,
        images=blocks,
    )
    return rag_service.to_response(result)
