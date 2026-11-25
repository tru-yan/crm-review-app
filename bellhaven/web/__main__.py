"""Start the review app at http://127.0.0.1:8000. It listens on this machine only."""
import uvicorn

from .app import PORT, build


def main() -> None:
    try:
        app = build()
    except RuntimeError as e:  # a database tied to another CRM, or no token set
        raise SystemExit(str(e)) from None
    uvicorn.run(app, host="127.0.0.1", port=PORT)


if __name__ == "__main__":
    main()
