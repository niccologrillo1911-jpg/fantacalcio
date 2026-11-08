"""Shared Mongo handle — import `client`/`db` from here (server.py, routers, seed.py)."""

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, DESCENDING, IndexModel

load_dotenv(Path(__file__).parent.parent / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

logger = logging.getLogger(__name__)

# One entry per collection: every field a route filters, sorts, or dedupes on. Applied by ensure_indexes() at startup.
INDEXES: dict[str, list[IndexModel]] = {
    "status_checks": [IndexModel([("timestamp", DESCENDING)], name="timestamp_desc")],
    "leagues": [
        IndexModel([("id", ASCENDING)], name="id", unique=True),
        IndexModel([("access_lookup", ASCENDING)], name="access_lookup", unique=True,
                   partialFilterExpression={"access_lookup": {"$type": "string"}}),
        IndexModel([("auto", ASCENDING)], name="auto"),
    ],
    "cron_runs": [
        IndexModel([("run_id", ASCENDING)], name="run_id", unique=True),
        IndexModel([("created_at", ASCENDING)], name="created_ttl", expireAfterSeconds=7 * 86400),
    ],
}


# Indexes from older versions that must go (league names no longer need to be unique).
OBSOLETE_INDEXES: dict[str, list[str]] = {"leagues": ["name_lower"]}


async def ensure_indexes() -> None:
    for collection, names in OBSOLETE_INDEXES.items():
        existing = await db[collection].index_information()
        for name in names:
            if name in existing:
                await db[collection].drop_index(name)
    for collection, models in INDEXES.items():
        for model in models:  # one at a time so a bad spec skips only itself
            try:
                await db[collection].create_indexes([model])
            except Exception as exc:  # never block boot on an index; the log line names what to fix
                logger.error("ensure_indexes(%s.%s): %s", collection, model.document["name"], exc)
