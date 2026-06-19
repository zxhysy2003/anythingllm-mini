from fastapi import FastAPI

from app.api.chat import router as chat_router

app = FastAPI(title="AnythingLLM Mini")
app.include_router(chat_router)


@app.get("/")
def root():
    return {"message": "AnythingLLM Mini is running"}


@app.get("/health")
def health():
    return {"status": "ok"}
