"""Land raw payloads in object storage, unmodified.

The lake is the replay source (ARCHITECTURE decision 1). The source is
volunteer-run and disclaims uptime and correctness, so the warehouse must be
rebuildable without re-hitting the API — which only holds if what lands here is
exactly what came back.

Object keys are **deterministic**: the same logical page always writes to the
same key. Re-running an extraction therefore overwrites rather than
accumulating near-duplicates, which is what makes the land step idempotent. A
timestamp or uuid in the key would silently double storage on every re-run and
leave the warehouse loader unable to tell which copy is current.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import boto3

from ingestion.config import Settings


@dataclass(frozen=True)
class LandedObject:
    key: str
    size_bytes: int


class Lake:
    """Writes and reads raw objects. Knows nothing about their contents."""

    def __init__(self, settings: Settings, client=None) -> None:
        self.settings = settings
        self.client = client or boto3.client(
            "s3",
            region_name=settings.lake_region,
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
        )

    def key_for(self, entity: str, scope: str, offset: int,
                ingestion_date: str | None = None) -> str:
        """Build the object key for one page.

        Shape follows ARCHITECTURE §3 — `raw/<entity>/<YYYY-MM-DD>/…` —
        partitioned by *ingestion* date rather than by event date. Ingestion
        date is what makes a replay traceable: it answers "what did the source
        say on the day we asked", which is the question you need when a result
        turns out to have been amended.

        The offset is zero-padded so keys sort lexicographically in the same
        order they were fetched. Unpadded, page 10 sorts before page 2.
        """
        date = ingestion_date or datetime.now(UTC).strftime("%Y-%m-%d")
        return (f"{self.settings.lake_prefix}/{entity}/{date}/"
                f"{entity}_{scope}_offset={offset:05d}.json")

    def put(self, key: str, content: bytes) -> LandedObject:
        self.client.put_object(
            Bucket=self.settings.lake_bucket,
            Key=key,
            Body=content,
            ContentType="application/json",
        )
        return LandedObject(key=key, size_bytes=len(content))

    def get(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.settings.lake_bucket, Key=key)
        return response["Body"].read()

    def list_keys(self, prefix: str) -> list[str]:
        """List every key under a prefix, following pagination.

        `list_objects_v2` returns at most 1000 keys per call and signals more
        with a continuation token. Ignoring it silently truncates the listing,
        which would make the warehouse load quietly incomplete rather than
        fail — the worst kind of bug in a pipeline that reports success.
        """
        keys: list[str] = []
        token: str | None = None

        while True:
            kwargs = {"Bucket": self.settings.lake_bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            response = self.client.list_objects_v2(**kwargs)

            keys.extend(item["Key"] for item in response.get("Contents", []))
            if not response.get("IsTruncated"):
                return sorted(keys)
            token = response.get("NextContinuationToken")
