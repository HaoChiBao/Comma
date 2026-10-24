import os

import uvicorn

from app.bootstrap import seed
from app.migrate import apply
from app.settings import settings


def main() -> None:
    if os.environ.get("PROCESS_ROLE") == "worker":
        from app.worker_main import main as run_worker

        run_worker()
        return
    apply()
    if settings.seed_on_start:
        seed(reset=False, include_demo_task=settings.seed_demo_task)
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, factory=False)


if __name__ == "__main__":
    main()
