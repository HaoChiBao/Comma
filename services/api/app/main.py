import os
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.db import configure, session
from app.ingest.api import router as ingest_router
from app.ingest.service import recover
from app.routes import router
from app.seed import seed
from app.snapshots import AccessError
from app.workbook import WorkbookError
from app.workbook_routes import router as workbook_router


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
    import threading

    def _index() -> None:
        from app.graphstore.store import ensure_company_index

        from app.ingest.decagon_workbook import ensure_workbook

        with session() as database:
            ensure_company_index(database)
            ensure_workbook(database)

    threading.Thread(target=_index, name="worldstate-index", daemon=True).start()


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
    # 3001 is the Excel add-in dev server (HTTPS, as Office requires).
    origins = ["http://localhost:3000", "http://127.0.0.1:3000", "https://localhost:3001"]
    origins.extend(item.strip() for item in os.environ.get("CORS_ORIGINS", "").split(",") if item.strip())
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AccessError)
    async def access_error(_: Request, exc: AccessError):
        return JSONResponse(status_code=403, content=_error(exc.code, "This source is not available to this actor at this date."))

    @app.exception_handler(WorkbookError)
    async def workbook_error(_: Request, exc: WorkbookError):
        return JSONResponse(status_code=exc.status, content=_error(exc.code, exc.message))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/v1/"):
            return JSONResponse(status_code=400, content=_error("INVALID_REQUEST", str(exc.errors()[0].get("msg", "Invalid request"))))
        return await request_validation_exception_handler(request, exc)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    app.include_router(router)
    app.include_router(ingest_router)
    app.include_router(workbook_router)
    return app


def _error(code: str, message: str) -> dict:
    return {"code": code, "message": message, "request_id": f"req_{uuid4().hex[:12]}"}


app = create_app()
