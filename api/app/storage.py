"""S3-compatible object storage (Cloudflare R2 in production, moto locally).

Image bytes never pass through the API: it hands the browser a short-lived
presigned PUT URL, and the browser uploads straight to the bucket.
"""

import asyncio
import logging
from functools import lru_cache

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import settings

log = logging.getLogger(__name__)

# Content type → file extension for the images we accept.
IMAGE_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}


@lru_cache
def client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def presign_put(key: str, content_type: str) -> str:
    """URL the browser can PUT the file to. The signature pins the content type."""
    return client().generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.s3_bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=settings.upload_url_ttl_seconds,
    )


async def object_size(key: str) -> int | None:
    """Size in bytes, or None if there's no such object."""

    def head() -> int | None:
        try:
            return client().head_object(Bucket=settings.s3_bucket, Key=key)["ContentLength"]
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise

    return await asyncio.to_thread(head)


async def object_exists(key: str) -> bool:
    return await object_size(key) is not None


async def delete_quietly(key: str) -> None:
    """Best effort: a leftover object is only storage, so failures are just logged."""
    try:
        await asyncio.to_thread(client().delete_object, Bucket=settings.s3_bucket, Key=key)
    except Exception:
        log.warning("could not delete %s from the bucket", key, exc_info=True)


def public_url(key: str) -> str:
    return f"{settings.image_base_url.rstrip('/')}/{key}"
