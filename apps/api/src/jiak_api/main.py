"""Build the FastAPI app.

Run the dev server:  uv run uvicorn jiak_api.main:app --reload
Then open http://127.0.0.1:8000/docs to try every route in the browser, or
http://127.0.0.1:8000/ for the web pages.
"""

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from jiak_api.bookings import NoLongerFits
from jiak_api.clock import Now, singapore_now
from jiak_api.config import load_config
from jiak_api.db import Database
from jiak_api.ops_pages import router as ops_pages_router
from jiak_api.pages import router as pages_router
from jiak_api.routes import no_longer_fits_handler, router

STATIC = Path(__file__).parent / "static"


def create_app(database: Database, clock: Callable[[], Now] = singapore_now) -> FastAPI:
    """The app, using `database`. Tests pass a test database and a fixed clock."""
    app = FastAPI(title="Jiak99 zone capacity")
    app.state.database = database
    app.state.clock = clock
    app.include_router(router)
    app.include_router(pages_router)
    app.include_router(ops_pages_router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.add_exception_handler(NoLongerFits, no_longer_fits_handler)
    return app


def _app_from_environment() -> FastAPI:
    return create_app(Database(load_config().database_url))


app = _app_from_environment()
