from __future__ import annotations

import os
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request, status
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError

from app.db import ensure_indexes, utc_now
from app.models import (
    CampaignDocument,
    CreateCampaignRequest,
    JobAccepted,
    JobStatus,
    campaign_identity,
    hash_idempotency_key,
)


@asynccontextmanager
async def lifespan(application: FastAPI):
    client = MongoClient(
        os.environ.get("MONGO_URI", "mongodb://localhost:27017"),
        serverSelectionTimeoutMS=5000,
        tz_aware=True,
    )
    client.admin.command("ping")
    database = client[os.environ.get("MONGO_DATABASE", "campaign_analytics")]
    ensure_indexes(database)
    application.state.mongo_client = client
    application.state.database = database
    try:
        yield
    finally:
        client.close()


app = FastAPI(
    title="Campaign Comment Analytics API",
    version="1.0.0",
    lifespan=lifespan,
)


def database_from_request(request: Request):
    return request.app.state.database


@app.get("/health")
def health(request: Request) -> dict[str, str]:
    try:
        database_from_request(request).command("ping")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="MongoDB is unavailable.") from exc
    return {"status": "ok"}


@app.post("/campaigns", response_model=JobAccepted, status_code=status.HTTP_202_ACCEPTED)
def create_campaign_job(
    payload: CreateCampaignRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=200),
) -> JobAccepted:
    database = database_from_request(request)
    campaign_id, business_key = campaign_identity(payload.name, payload.platform, payload.post_urls)
    key_hash = hash_idempotency_key(idempotency_key)

    if key_hash:
        existing_job = database.jobs.find_one({"idempotency_key_hash": key_hash})
        if existing_job:
            if existing_job["campaign_id"] != campaign_id:
                raise HTTPException(
                    status_code=409,
                    detail="Idempotency-Key was already used for another campaign request.",
                )
            return JobAccepted(
                job_id=existing_job["_id"],
                campaign_id=existing_job["campaign_id"],
                status=existing_job["status"],
            )

    now = utc_now()
    job_id = f"job:{uuid4()}"
    campaign_document = {
        "_id": campaign_id,
        "type": "campaign",
        "schema_version": 1,
        "created_at": now,
        "updated_at": now,
        "business_key": business_key,
        "definition": {
            "name": payload.name,
            "platform": payload.platform,
            "post_urls": payload.post_urls,
            "comments_per_post_limit": payload.comments_per_post_limit,
        },
        "status": "queued",
        "posts": [],
        "summary": {
            "comments_total": 0,
            "price_questions": {"count": 0, "share_pct": None},
            "link_requests": {"count": 0, "share_pct": None},
            "negative_comments": {"count": 0, "share_pct": None},
        },
        "last_job_id": None,
    }
    database.campaigns.update_one(
        {"_id": campaign_id},
        {"$setOnInsert": campaign_document},
        upsert=True,
    )

    job_document = {
        "_id": job_id,
        "type": "job",
        "schema_version": 1,
        "created_at": now,
        "updated_at": now,
        "campaign_id": campaign_id,
        "status": "queued",
        "attempts": 0,
        "worker_id": None,
        "lease_expires_at": None,
        "apify_run_id": None,
        "cost_usd": None,
        "error": None,
    }
    if key_hash is not None:
        job_document["idempotency_key_hash"] = key_hash
    try:
        database.jobs.insert_one(job_document)
    except DuplicateKeyError:
        if not key_hash:
            raise
        existing_job = database.jobs.find_one({"idempotency_key_hash": key_hash})
        if not existing_job or existing_job["campaign_id"] != campaign_id:
            raise HTTPException(status_code=409, detail="Idempotency-Key conflict.")
        return JobAccepted(
            job_id=existing_job["_id"],
            campaign_id=existing_job["campaign_id"],
            status=existing_job["status"],
        )

    database.campaigns.update_one(
        {"_id": campaign_id},
        {"$set": {"last_job_id": job_id, "updated_at": now}},
    )
    return JobAccepted(job_id=job_id, campaign_id=campaign_id, status="queued")


@app.get("/campaigns/{campaign_id}", response_model=CampaignDocument)
def get_campaign(campaign_id: str, request: Request) -> dict:
    campaign = database_from_request(request).campaigns.find_one({"_id": campaign_id})
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found.")
    return campaign


@app.get(
    "/jobs/{job_id}",
    response_model=JobStatus,
    response_model_exclude={"worker_id", "lease_expires_at", "idempotency_key_hash"},
)
def get_job(job_id: str, request: Request) -> dict:
    job = database_from_request(request).jobs.find_one({"_id": job_id})
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job
