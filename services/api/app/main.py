import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.errors import ApiError
from app.routes import router
from app.settings import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    stop = None
    thread = None
    if settings.worker_thread:
        import threading

        from app.worker import drain

        stop = threading.Event()

        def loop():
            while not stop.is_set():
                try:
                    drain(max_loops=10)
                except Exception:
                    logging.getLogger("procurement.worker").exception("worker loop")
                stop.wait(1.0)

        thread = threading.Thread(target=loop, name="procurement-worker", daemon=True)
        thread.start()
    yield
    if stop is not None:
        stop.set()
    if thread is not None:
        thread.join(timeout=2)


app = FastAPI(title="Acme Procurement", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.exception_handler(ApiError)
def api_error(_request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status, content={"error": exc.error, "detail": exc.detail})
