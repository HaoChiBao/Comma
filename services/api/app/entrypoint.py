import uvicorn

from app.bootstrap import seed
from app.migrate import apply
from app.settings import settings


def main() -> None:
    apply()
    if settings.seed_on_start:
        seed(reset=False, include_demo_task=settings.seed_demo_task)
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, factory=False)


if __name__ == "__main__":
    main()
