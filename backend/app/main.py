from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.agents import router as agents_router
from app.api.workspaces import router as workspaces_router
from app.db.init_db import create_db_and_tables

AGENT_UI_PATH = Path(__file__).resolve().parent / "web" / "agent_ui.html"


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


@app.get("/ui", include_in_schema=False)
def agent_ui():
    return FileResponse(AGENT_UI_PATH)
