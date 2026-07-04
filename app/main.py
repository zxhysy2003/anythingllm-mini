from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.agents import router as agents_router
from app.api.workspaces import router as workspaces_router
from app.db.init_db import create_db_and_tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    yield


app = FastAPI(title="AnythingLLM Mini", lifespan=lifespan)
app.include_router(workspaces_router)
app.include_router(agents_router)


@app.get("/")
def root():
    return {"message": "AnythingLLM Mini is running"}


@app.get("/health")
def health():
    return {"status": "ok"}
