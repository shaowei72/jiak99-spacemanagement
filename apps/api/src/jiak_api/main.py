"""Build the FastAPI app.

Run the dev server:  uv run uvicorn jiak_api.main:app --reload
Then open http://127.0.0.1:8000/docs to try every route in the browser, or
http://127.0.0.1:8000/ for the web pages.
"""

from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from graph.fake import FakeGateway
from graph.gateway import GraphGateway
from jiak_api.booking_pages import router as booking_pages_router
from jiak_api.bookings import NoLongerFits
from jiak_api.clock import Now, singapore_now
from jiak_api.config import load_config
from jiak_api.db import Database
from jiak_api.ops_pages import router as ops_pages_router
from jiak_api.outlook_sync import make_gateway
from jiak_api.pages import router as pages_router
from jiak_api.routes import no_longer_fits_handler, router

STATIC = Path(__file__).parent / "static"


def create_app(
    database: Database,
    clock: Callable[[], Now] = singapore_now,
    gateway: GraphGateway | None = None,
) -> FastAPI:
    """The app, using `database` and the Outlook calendar `gateway` (default: a new in-memory fake).
    Tests pass a test database, a fixed clock and, when they look at Outlook, their own fake."""
    app = FastAPI(title="Jiak99 zone capacity")
    app.state.database = database
    app.state.clock = clock
    app.state.gateway = gateway if gateway is not None else FakeGateway()
    app.include_router(router)
    app.include_router(pages_router)
    app.include_router(ops_pages_router)
    app.include_router(booking_pages_router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.add_exception_handler(NoLongerFits, no_longer_fits_handler)
    return app


def _app_from_environment() -> FastAPI:
    config = load_config()
    return create_app(Database(config.database_url), gateway=make_gateway(config))


app = _app_from_environment()
