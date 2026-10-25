import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.db import configure, session
from app.ingest.api import router as ingest_router
from app.ingest.service import recover
from app.routes import router
from app.seed import seed
from app.snapshots import AccessError


def _load_company(db) -> None:
    if os.environ.get("INGEST_INLINE") == "1":
        return
    from app.ingest.decagon import PACK, start_decagon

    if not PACK.exists():
        return
    try:
        start_decagon(db)
    except ValueError:
        return


def create_app(database_url: str | None = None) -> FastAPI:
    url = database_url or os.environ.get("DATABASE_URL", "sqlite:///./worldstate.db")
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://") and "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    configure(url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        with session() as db:
            seed(db)
            recover(db)
            _load_company(db)
        yield

    app = FastAPI(title="WorldState", lifespan=lifespan)
    origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
    origins.extend(item.strip() for item in os.environ.get("CORS_ORIGINS", "").split(",") if item.strip())
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AccessError)
    async def access_error(_: Request, exc: AccessError):
        return JSONResponse(status_code=403, content={"code": exc.code})

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.include_router(router)
    app.include_router(ingest_router)
    return app


app = create_app()
