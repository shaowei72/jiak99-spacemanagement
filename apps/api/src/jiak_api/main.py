"""Build the FastAPI app.

Run the dev server:  uv run uvicorn jiak_api.main:app --reload
Then open http://127.0.0.1:8000/docs to try every route in the browser.
"""

from collections.abc import Callable

from fastapi import FastAPI

from jiak_api.bookings import NoLongerFits
from jiak_api.clock import Now, singapore_now
from jiak_api.config import load_config
from jiak_api.db import Database
from jiak_api.routes import no_longer_fits_handler, router


def create_app(database: Database, clock: Callable[[], Now] = singapore_now) -> FastAPI:
    """The app, using `database`. Tests pass a test database and a fixed clock."""
    app = FastAPI(title="Jiak99 zone capacity")
    app.state.database = database
    app.state.clock = clock
    app.include_router(router)
    app.add_exception_handler(NoLongerFits, no_longer_fits_handler)
    return app


def _app_from_environment() -> FastAPI:
    return create_app(Database(load_config().database_url))


app = _app_from_environment()
