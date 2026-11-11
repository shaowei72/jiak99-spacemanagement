"""Settings read from environment variables (secrets never live in code, SPEC §8)."""

import os
from dataclasses import dataclass

DEFAULT_DATABASE_URL = "sqlite:///./jiak99-dev.db"


@dataclass(frozen=True)
class AppConfig:
    """How the app is wired up. Business settings (turnaround etc.) live in the database."""

    database_url: str
    graph_mode: str = "fake"  # "real" only once the M365 admin steps in README.md are done
    sweep_minutes: float = 5  # how often Outlook is read (SPEC §5.4); 0 = never (tests, tools)
    secret_expires: str | None = None  # 'YYYY-MM-DD' the app's Outlook secret expires, if known
    default_user: str = "dev"  # who changes are recorded under when nobody signs in (JIAK_USER)
    backup_dir: str | None = None  # hourly backups go here (JIAK_BACKUP_DIR); None = no backups


def load_config() -> AppConfig:
    """Read the config from the environment, with dev-friendly defaults."""
    return AppConfig(
        database_url=os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL),
        graph_mode=os.environ.get("GRAPH_MODE", "fake").strip().lower() or "fake",
        sweep_minutes=float(os.environ.get("OUTLOOK_SWEEP_MINUTES", "5") or "5"),
        secret_expires=os.environ.get("AZURE_CLIENT_SECRET_EXPIRES", "").strip() or None,
        default_user=os.environ.get("JIAK_USER", "").strip()[:200] or "dev",
        backup_dir=os.environ.get("JIAK_BACKUP_DIR", "").strip() or None,
    )
