import argparse

from app.bootstrap import seed
from app.migrate import apply


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the Acme procurement demo database")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--no-demo-task", action="store_true")
    args = parser.parse_args()
    apply()
    seed(reset=args.reset, include_demo_task=not args.no_demo_task)
    print("seed complete")


if __name__ == "__main__":
    main()
