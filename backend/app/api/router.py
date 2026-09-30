from fastapi import APIRouter

from app.api.routes import (
    auth,
    chat,
    chat_socket,
    chunks,
    documents,
    health,
    images,
    knowledge_bases,
    rag,
    retrieval,
    system,
    voice,
)

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(knowledge_bases.router)
api_router.include_router(documents.router)
api_router.include_router(chunks.router)
api_router.include_router(retrieval.router)
api_router.include_router(rag.router)
api_router.include_router(chat.chat_router)
api_router.include_router(chat.conversations_router)
api_router.include_router(chat_socket.router)
api_router.include_router(images.images_router)
api_router.include_router(images.multimodal_router)
api_router.include_router(voice.router)
api_router.include_router(system.router)
