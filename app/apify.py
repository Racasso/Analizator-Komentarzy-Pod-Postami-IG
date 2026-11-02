from __future__ import annotations

import os
import re
from decimal import Decimal
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from apify_client import ApifyClient

from app.metrics import classify_comment
from app.models import normalize_instagram_url


class ApifyConfigurationError(RuntimeError):
    pass


class ApifyRunError(RuntimeError):
    pass


def _run_value(run: Any, *names: str) -> Any:
    for name in names:
        if isinstance(run, dict) and name in run:
            return run[name]
        if hasattr(run, name):
            return getattr(run, name)
    return None


def _source_url(item: dict[str, Any], requested_urls: list[str]) -> str | None:
    def shortcode(value: str) -> str | None:
        match = re.search(r"/(?:p|reel|reels|tv)/([^/?#]+)", urlsplit(value).path)
        return match.group(1) if match else None

    raw_url = item.get("postUrl")
    if not raw_url and isinstance(item.get("owner"), dict):
        raw_url = item["owner"].get("url")

    if raw_url:
        try:
            normalized = normalize_instagram_url(raw_url)
            if normalized in requested_urls:
                return normalized
        except ValueError:
            pass

        source_shortcode = shortcode(str(raw_url))
        if source_shortcode:
            for requested_url in requested_urls:
                if source_shortcode == shortcode(requested_url):
                    return requested_url

    # The actor's documented sample omits postUrl; attribution is unambiguous for one input URL.
    if len(requested_urls) == 1:
        return requested_urls[0]
    return None


def normalize_actor_items(
    items: list[dict[str, Any]], requested_urls: list[str]
) -> list[dict[str, Any]]:
    comments_by_post: dict[str, dict[str, dict[str, Any]]] = {
        url: {} for url in requested_urls
    }

    for item in items:
        source_id = item.get("id")
        if source_id is None or item.get("error"):
            continue
        post_url = _source_url(item, requested_urls)
        if post_url is None:
            raise ApifyRunError("Actor item is missing a post URL for a multi-post campaign.")

        text = item.get("text") or ""
        comments_by_post[post_url][str(source_id)] = {
            "source_id": str(source_id),
            "text": text,
            "created_at": item.get("timestamp"),
            "classification": classify_comment(text),
        }

    return [
        {"url": url, "comments": list(comments_by_post[url].values())}
        for url in requested_urls
    ]


def fetch_campaign_comments(
    post_urls: list[str],
    comments_per_post_limit: int,
) -> dict[str, Any]:
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        raise ApifyConfigurationError("APIFY_TOKEN is not configured on the worker.")

    actor_id = os.environ.get("APIFY_ACTOR_ID", "apify/instagram-comment-scraper")
    max_charge = Decimal(os.environ.get("APIFY_MAX_TOTAL_CHARGE_USD", "15.00"))
    client = ApifyClient(token)
    run = client.actor(actor_id).call(
        run_input={
            "directUrls": post_urls,
            "resultsLimit": comments_per_post_limit,
            "includeReplies": False,
        },
        max_total_charge_usd=max_charge,
        run_timeout=timedelta(seconds=3600),
        logger=None,
    )
    if run is None:
        raise ApifyRunError("Apify did not return a run record.")

    run_id = _run_value(run, "id")
    status = _run_value(run, "status")
    status = getattr(status, "value", status)
    if status != "SUCCEEDED":
        raise ApifyRunError(f"Apify run ended with status {status or 'unknown'}.")

    dataset_id = _run_value(run, "default_dataset_id", "defaultDatasetId")
    if not dataset_id:
        raise ApifyRunError("Apify run did not return a default dataset ID.")

    items = list(client.dataset(dataset_id).iterate_items())
    cost = _run_value(run, "usage_total_usd", "usageTotalUsd")
    return {
        "run_id": run_id,
        "cost_usd": float(cost) if cost is not None else None,
        "posts": normalize_actor_items(items, post_urls),
    }
