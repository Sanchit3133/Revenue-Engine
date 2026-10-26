"""Initialize or update the configured Revenue Engine database schema."""

import argparse

from app.database import init_db


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="Explicitly approve additive schema migration during a production release phase.",
    )
    args = parser.parse_args()

    init_db(allow_production=args.allow_production)
    print("Revenue Engine database schema is ready.")


if __name__ == "__main__":
    main()
