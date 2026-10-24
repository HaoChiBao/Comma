import time

from app.migrate import apply
from app.worker import drain


def main() -> None:
    apply()
    while True:
        try:
            drain(max_loops=20)
        except Exception:
            import logging

            logging.getLogger("procurement.worker").exception("worker tick")
        time.sleep(1)


if __name__ == "__main__":
    main()
