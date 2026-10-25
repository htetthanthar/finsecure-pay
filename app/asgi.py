"""ASGI application object loaded by uvicorn."""

from app.main import create_app

app = create_app()
