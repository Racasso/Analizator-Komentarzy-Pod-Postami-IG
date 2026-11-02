from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_instagram_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or parts.hostname not in {
        "instagram.com",
        "www.instagram.com",
    }:
        raise ValueError("Każdy URL musi wskazywać publiczny post lub rolkę Instagram.")
    match = re.fullmatch(r"/(p|reel|reels|tv)/([^/]+)/?", parts.path)
    if not match:
        raise ValueError("URL musi wskazywać post (/p/), rolkę (/reel/) lub materiał TV (/tv/).")
    kind, shortcode = match.groups()
    if kind == "reels":
        kind = "reel"
    return f"https://www.instagram.com/{kind}/{shortcode}/"


class CreateCampaignRequest(BaseModel):
    name: str = Field(default="Oskar Kuban", min_length=1, max_length=120)
    platform: Literal["instagram"] = "instagram"
    post_urls: list[str] = Field(min_length=1, max_length=50)
    comments_per_post_limit: int = Field(default=100, ge=1, le=100)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Nazwa kampanii nie może być pusta.")
        return value

    @field_validator("post_urls")
    @classmethod
    def normalize_post_urls(cls, values: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(normalize_instagram_url(value) for value in values))
        if not normalized:
            raise ValueError("Podaj co najmniej jeden URL posta.")
        if len(normalized) > 50:
            raise ValueError("Jedna kampania może zawierać maksymalnie 50 postów.")
        return normalized


class DocumentModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(alias="_id")
    type: str
    schema_version: int = 1
    created_at: datetime
    updated_at: datetime


class CommentClassification(BaseModel):
    price_question: bool
    link_request: bool
    negative: bool


class CommentDocument(BaseModel):
    source_id: str
    text: str
    created_at: str | None = None
    classification: CommentClassification


class CategoryMetrics(BaseModel):
    count: int
    share_pct: float | None


class PostMetrics(BaseModel):
    comments_total: int
    price_questions: CategoryMetrics
    link_requests: CategoryMetrics
    negative_comments: CategoryMetrics


class PostDocument(BaseModel):
    url: str
    comments: list[CommentDocument] = Field(default_factory=list)
    metrics: PostMetrics | None = None


class CampaignSummary(BaseModel):
    comments_total: int = 0
    price_questions: CategoryMetrics = Field(
        default_factory=lambda: CategoryMetrics(count=0, share_pct=None)
    )
    link_requests: CategoryMetrics = Field(
        default_factory=lambda: CategoryMetrics(count=0, share_pct=None)
    )
    negative_comments: CategoryMetrics = Field(
        default_factory=lambda: CategoryMetrics(count=0, share_pct=None)
    )


class CampaignDocument(DocumentModel):
    type: Literal["campaign"] = "campaign"
    business_key: str
    definition: dict
    status: Literal["queued", "running", "done", "failed"]
    posts: list[PostDocument] = Field(default_factory=list)
    summary: CampaignSummary = Field(default_factory=CampaignSummary)
    last_job_id: str | None = None


class JobDocument(DocumentModel):
    type: Literal["job"] = "job"
    campaign_id: str
    status: Literal["queued", "running", "done", "failed"]
    attempts: int = 0
    idempotency_key_hash: str | None = None
    worker_id: str | None = None
    lease_expires_at: datetime | None = None
    apify_run_id: str | None = None
    cost_usd: float | None = None
    error: str | None = None


class JobAccepted(BaseModel):
    job_id: str
    campaign_id: str
    status: str


class JobStatus(DocumentModel):
    type: Literal["job"] = "job"
    campaign_id: str
    status: Literal["queued", "running", "done", "failed"]
    attempts: int = 0
    apify_run_id: str | None = None
    cost_usd: float | None = None
    error: str | None = None


def campaign_identity(name: str, platform: str, post_urls: list[str]) -> tuple[str, str]:
    business_key = json.dumps(
        {"name": name.casefold(), "platform": platform, "post_urls": sorted(post_urls)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(business_key.encode("utf-8")).hexdigest()
    return f"campaign:{digest}", digest


def hash_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
