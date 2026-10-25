"""Container entrypoint: ``python -m app``.

Runs uvicorn with JSON logging. No shell is needed, which lets the image use
a distroless base that has no shell at all.
"""

from __future__ import annotations

import os

import uvicorn

from app import __version__
from app.config import load_settings
from app.logging_config import build_logging_config


def main() -> None:
    settings = load_settings()
    uvicorn.run(
        "app.asgi:app",
        # Inside a container the app must listen on all interfaces (justified B104).
        host=os.environ.get("HOST", "0.0.0.0"),  # nosec B104
        port=int(os.environ.get("PORT", "8080")),
        log_config=build_logging_config(settings.log_level, __version__),
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        timeout_graceful_shutdown=20,
    )


if __name__ == "__main__":
    main()
