"""ASGI entrypoint: `uvicorn app.asgi:app`. Separate from app.main so that
importing app.main (e.g. from tests, via create_app) never has the side
effect of touching disk at import time."""
from app.main import build_production_app

app = build_production_app()
