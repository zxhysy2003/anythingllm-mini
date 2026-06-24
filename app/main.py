from fastapi import FastAPI

from app.api.chat import router as chat_router
from app.api.documents import router as documents_router
from app.api.rag import router as rag_router

app = FastAPI(title="AnythingLLM Mini")
app.include_router(chat_router)
app.include_router(documents_router)
app.include_router(rag_router)


@app.get("/")
def root():
    return {"message": "AnythingLLM Mini is running"}


@app.get("/health")
def health():
    return {"status": "ok"}
