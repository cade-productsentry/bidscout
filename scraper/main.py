"""BidScout scraper — placeholder entrypoint.

Real scraping logic is not implemented yet. This exists so the
scheduled GitHub Actions workflow has something to run end to end.
"""

import os


def main() -> None:
    # DATABASE_URL is injected by GitHub Actions from repo secrets,
    # or loaded from a local .env file during development.
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("warning: DATABASE_URL is not set")
    print("ok")


if __name__ == "__main__":
    main()
