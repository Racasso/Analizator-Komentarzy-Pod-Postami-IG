from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ASCENDING, ReturnDocument


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_indexes(database: Any) -> None:
    database.campaigns.create_index("business_key", unique=True, name="campaign_business_key")
    database.jobs.create_index(
        [("status", ASCENDING), ("created_at", ASCENDING)],
        name="jobs_status_created_at",
    )
    database.jobs.create_index(
        [("status", ASCENDING), ("lease_expires_at", ASCENDING)],
        name="jobs_status_lease",
    )
    database.jobs.create_index(
        "idempotency_key_hash",
        unique=True,
        sparse=True,
        name="jobs_idempotency_key",
    )


def _post_metrics_expression() -> dict[str, Any]:
    def count(flag: str) -> dict[str, Any]:
        return {
            "$size": {
                "$filter": {
                    "input": "$$comments",
                    "as": "comment",
                    "cond": f"$$comment.classification.{flag}",
                }
            }
        }

    def category(count_var: str) -> dict[str, Any]:
        return {
            "count": f"$${count_var}",
            "share_pct": {
                "$cond": [
                    {"$gt": ["$$total", 0]},
                    {
                        "$round": [
                            {"$multiply": [100, {"$divide": [f"$${count_var}", "$$total"]}]},
                            2,
                        ]
                    },
                    None,
                ]
            },
        }

    return {
        "$let": {
            "vars": {"comments": "$$post.comments", "total": {"$size": "$$post.comments"}},
            "in": {
                "$let": {
                    "vars": {
                        "price": count("price_question"),
                        "links": count("link_request"),
                        "negative": count("negative"),
                    },
                    "in": {
                        "$mergeObjects": [
                            "$$post",
                            {
                                "metrics": {
                                    "comments_total": "$$total",
                                    "price_questions": category("price"),
                                    "link_requests": category("links"),
                                    "negative_comments": category("negative"),
                                }
                            },
                        ]
                    },
                }
            },
        }
    }


def _sum_post_metric(path: str) -> dict[str, Any]:
    return {
        "$reduce": {
            "input": "$posts",
            "initialValue": 0,
            "in": {"$add": ["$$value", f"$$this.metrics.{path}"]},
        }
    }


def _summary_expression() -> dict[str, Any]:
    def category(count_var: str) -> dict[str, Any]:
        return {
            "count": f"$${count_var}",
            "share_pct": {
                "$cond": [
                    {"$gt": ["$$total", 0]},
                    {
                        "$round": [
                            {"$multiply": [100, {"$divide": [f"$${count_var}", "$$total"]}]},
                            2,
                        ]
                    },
                    None,
                ]
            },
        }

    return {
        "$let": {
            "vars": {
                "total": _sum_post_metric("comments_total"),
                "price": _sum_post_metric("price_questions.count"),
                "links": _sum_post_metric("link_requests.count"),
                "negative": _sum_post_metric("negative_comments.count"),
            },
            "in": {
                "comments_total": "$$total",
                "price_questions": category("price"),
                "link_requests": category("links"),
                "negative_comments": category("negative"),
            },
        }
    }


def write_campaign_snapshot(
    database: Any,
    campaign_id: str,
    posts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    pipeline = [
        {
            "$set": {
                "posts": {
                    "$map": {
                        "input": {"$literal": posts},
                        "as": "post",
                        "in": _post_metrics_expression(),
                    }
                },
                "updated_at": utc_now(),
                "status": "done",
            }
        },
        {"$set": {"summary": _summary_expression()}},
    ]
    return database.campaigns.find_one_and_update(
        {"_id": campaign_id},
        pipeline,
        upsert=False,
        return_document=ReturnDocument.AFTER,
    )


def claim_next_job(database: Any, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
    now = utc_now()
    return database.jobs.find_one_and_update(
        {
            "$or": [
                {"status": "queued"},
                {"status": "running", "lease_expires_at": {"$lte": now}},
            ]
        },
        {
            "$set": {
                "status": "running",
                "worker_id": worker_id,
                "updated_at": now,
                "lease_expires_at": now + timedelta(seconds=lease_seconds),
                "error": None,
            },
            "$inc": {"attempts": 1},
        },
        sort=[("created_at", ASCENDING)],
        return_document=ReturnDocument.AFTER,
    )


def mark_job_done(
    database: Any,
    job_id: str,
    run_id: str | None,
    cost_usd: float | None,
) -> None:
    now = utc_now()
    database.jobs.update_one(
        {"_id": job_id},
        {
            "$set": {
                "status": "done",
                "updated_at": now,
                "apify_run_id": run_id,
                "cost_usd": cost_usd,
                "error": None,
            },
            "$unset": {"lease_expires_at": ""},
        },
    )


def mark_job_failed(database: Any, job_id: str, error: str) -> None:
    database.jobs.update_one(
        {"_id": job_id},
        {
            "$set": {"status": "failed", "updated_at": utc_now(), "error": error},
            "$unset": {"lease_expires_at": ""},
        },
    )
