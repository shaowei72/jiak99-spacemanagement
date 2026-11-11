"""Build the FastAPI app.

Run the dev server:  uv run uvicorn jiak_api.main:app --reload
Then open http://127.0.0.1:8000/docs to try every route in the browser, or
http://127.0.0.1:8000/ for the web pages.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
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
from jiak_api.outlook_inbound import sweep_and_record
from jiak_api.outlook_sync import make_gateway
from jiak_api.pages import router as pages_router
from jiak_api.routes import no_longer_fits_handler, router

STATIC = Path(__file__).parent / "static"


async def sweep_forever(
    database: Database, gateway: GraphGateway, clock: Callable[[], Now], minutes: float
) -> None:
    """Read Outlook now and then every `minutes` (SPEC §5.4). Each sweep runs in a worker thread,
    so the web pages stay quick; its errors are logged and shown, never raised."""
    while True:
        await anyio.to_thread.run_sync(sweep_and_record, database, gateway, clock)
        await asyncio.sleep(minutes * 60)


def create_app(
    database: Database,
    clock: Callable[[], Now] = singapore_now,
    gateway: GraphGateway | None = None,
    sweep_minutes: float = 0,
) -> FastAPI:
    """The app, using `database` and the Outlook calendar `gateway` (default: a new in-memory fake).
    Tests pass a test database, a fixed clock and, when they look at Outlook, their own fake.
    `sweep_minutes` > 0 reads Outlook when the app starts and then that often (0: never)."""
    calendar = gateway if gateway is not None else FakeGateway()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        task = None
        if sweep_minutes > 0:
            task = asyncio.create_task(sweep_forever(database, calendar, clock, sweep_minutes))
        yield
        if task is not None:
            task.cancel()

    app = FastAPI(title="Jiak99 zone capacity", lifespan=lifespan)
    app.state.database = database
    app.state.clock = clock
    app.state.gateway = calendar
    app.include_router(router)
    app.include_router(pages_router)
    app.include_router(ops_pages_router)
    app.include_router(booking_pages_router)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    app.add_exception_handler(NoLongerFits, no_longer_fits_handler)
    return app


def _app_from_environment() -> FastAPI:
    config = load_config()
    return create_app(
        Database(config.database_url),
        gateway=make_gateway(config),
        sweep_minutes=config.sweep_minutes,
    )


app = _app_from_environment()
