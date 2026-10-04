import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

from backend.routers import companies, jobs
from backend.routers.search import router as search_router
from backend.routers.auth import router as auth_router
from backend.routers.cv import router as cv_router
from backend.routers.preferences import router as preferences_router
from backend.routers.search_advanced import router as search_advanced_router
from backend.search.models_lifecycle import init_models
from backend.opensearch_client import init_opensearch

logger = logging.getLogger(__name__)

_checkpointer: AsyncPostgresSaver | None = None


def get_checkpointer() -> AsyncPostgresSaver:
    if _checkpointer is None:
        raise RuntimeError("Checkpointer not initialized")
    return _checkpointer


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _checkpointer
    init_models()
    await init_opensearch()

    db_url = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    async with AsyncPostgresSaver.from_conn_string(db_url) as checkpointer:
        _checkpointer = checkpointer
        yield


def cors_origins() -> list[str]:
    origins = ["http://localhost:3000"]
    for origin in os.environ.get("CORS_ORIGINS", "").split(","):
        origin = origin.strip()
        if origin and origin not in origins:
            origins.append(origin)
    return origins


app = FastAPI(title="Jobsifty backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(companies.router)
app.include_router(jobs.router)
app.include_router(search_router)
app.include_router(auth_router)
app.include_router(cv_router)
app.include_router(preferences_router)
app.include_router(search_advanced_router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    import traceback
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": "internal server error"})
