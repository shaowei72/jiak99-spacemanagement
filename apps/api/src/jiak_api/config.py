"""Settings read from environment variables (secrets never live in code, SPEC §8)."""

import os
from dataclasses import dataclass

DEFAULT_DATABASE_URL = "sqlite:///./jiak99-dev.db"


@dataclass(frozen=True)
class AppConfig:
    """How the app is wired up. Business settings (turnaround etc.) live in the database."""

    database_url: str
    graph_mode: str = "fake"  # "real" only once the M365 admin steps in README.md are done


def load_config() -> AppConfig:
    """Read the config from the environment, with dev-friendly defaults."""
    return AppConfig(
        database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        graph_mode=os.environ.get("GRAPH_MODE", "fake").strip().lower() or "fake",
    )
