"""Create the local S3 bucket configured the way the R2 bucket is in production.

- CORS lets the browser PUT to presigned URLs from the web app's origin.
- A public-read policy stands in for R2's public bucket URL.

Local only (moto from docker-compose); R2 is set up in the Cloudflare dashboard.
Safe to re-run.
"""

import json

from botocore.exceptions import ClientError

from app import storage
from app.config import settings


def main() -> None:
    s3 = storage.client()
    bucket = settings.s3_bucket
    try:
        s3.create_bucket(Bucket=bucket)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
            raise
    s3.put_bucket_cors(
        Bucket=bucket,
        CORSConfiguration={
            "CORSRules": [
                {
                    "AllowedOrigins": settings.cors_origin_list,
                    "AllowedMethods": ["PUT", "GET"],
                    "AllowedHeaders": ["*"],
                    "MaxAgeSeconds": 3600,
                }
            ]
        },
    )
    s3.put_bucket_policy(
        Bucket=bucket,
        Policy=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": "*",
                        "Action": "s3:GetObject",
                        "Resource": f"arn:aws:s3:::{bucket}/*",
                    }
                ],
            }
        ),
    )
    print(f"bucket {bucket!r} ready at {settings.s3_endpoint}")


if __name__ == "__main__":
    main()
