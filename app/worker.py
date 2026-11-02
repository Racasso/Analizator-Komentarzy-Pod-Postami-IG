from __future__ import annotations

import logging
import os
import socket
import time
from typing import Any

from pymongo import MongoClient

from app.apify import ApifyConfigurationError, ApifyRunError, fetch_campaign_comments
from app.db import (
    claim_next_job,
    ensure_indexes,
    mark_job_done,
    mark_job_failed,
    utc_now,
    write_campaign_snapshot,
)


logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("campaign-worker")


def process_job(database: Any, job: dict[str, Any]) -> None:
    job_id = job["_id"]
    campaign_id = job["campaign_id"]
    campaign = database.campaigns.find_one({"_id": campaign_id})
    if campaign is None:
        mark_job_failed(database, job_id, "Campaign document was not found.")
        logger.error("Job %s failed: campaign document was not found.", job_id)
        return

    database.campaigns.update_one(
        {"_id": campaign_id},
        {"$set": {"status": "running", "updated_at": utc_now()}},
    )
    definition = campaign["definition"]
    try:
        result = fetch_campaign_comments(
            definition["post_urls"],
            definition["comments_per_post_limit"],
        )
        write_campaign_snapshot(database, campaign_id, result["posts"])
        mark_job_done(database, job_id, result["run_id"], result["cost_usd"])
        logger.info("Job %s completed.", job_id)
    except ApifyConfigurationError as exc:
        mark_job_failed(database, job_id, str(exc))
        database.campaigns.update_one(
            {"_id": campaign_id},
            {"$set": {"status": "failed", "updated_at": utc_now()}},
        )
        logger.error("Job %s failed: %s", job_id, str(exc))
    except ApifyRunError as exc:
        mark_job_failed(database, job_id, str(exc))
        database.campaigns.update_one(
            {"_id": campaign_id},
            {"$set": {"status": "failed", "updated_at": utc_now()}},
        )
        logger.error("Job %s failed: %s", job_id, str(exc))
    except Exception as exc:
        safe_error = f"Unexpected worker error ({type(exc).__name__}); details omitted from logs."
        mark_job_failed(database, job_id, safe_error)
        database.campaigns.update_one(
            {"_id": campaign_id},
            {"$set": {"status": "failed", "updated_at": utc_now()}},
        )
        logger.error("Job %s failed: %s", job_id, safe_error)


def run_worker() -> None:
    mongo_client = MongoClient(
        os.environ.get("MONGO_URI", "mongodb://localhost:27017"),
        serverSelectionTimeoutMS=5000,
        tz_aware=True,
    )
    database = mongo_client[os.environ.get("MONGO_DATABASE", "campaign_analytics")]
    database.command("ping")
    ensure_indexes(database)

    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    poll_seconds = float(os.environ.get("WORKER_POLL_SECONDS", "2"))
    lease_seconds = int(os.environ.get("JOB_LEASE_SECONDS", "86400"))
    logger.info("Worker %s started.", worker_id)
    try:
        while True:
            job = claim_next_job(database, worker_id, lease_seconds)
            if job is None:
                time.sleep(poll_seconds)
                continue
            process_job(database, job)
    finally:
        mongo_client.close()


if __name__ == "__main__":
    run_worker()
