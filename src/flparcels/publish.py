"""Step 6: upload the PMTiles and lookup.parquet to an S3-compatible bucket."""
import logging
import os
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

ENV_VARS = {
    "endpoint": "FLPARCELS_PUBLISH_ENDPOINT",
    "bucket": "FLPARCELS_PUBLISH_BUCKET",
    "access_key": "FLPARCELS_PUBLISH_ACCESS_KEY",
    "secret_key": "FLPARCELS_PUBLISH_SECRET_KEY",
}


REGION_VAR = "FLPARCELS_PUBLISH_REGION"


@dataclass(frozen=True)
class Target:
    endpoint: str
    bucket: str
    access_key: str
    secret_key: str
    region: str = "us-east-1"

    @classmethod
    def from_env(cls) -> "Target":
        values = {field: os.environ.get(var) for field, var in ENV_VARS.items()}
        missing = [ENV_VARS[field] for field, value in values.items() if not value]
        if missing:
            raise SystemExit(f"publish needs {', '.join(missing)} set")
        # optional: Supabase wants the project's region (Dashboard > Storage > S3 connection)
        return cls(**values, region=os.environ.get(REGION_VAR) or cls.region)


def publish(name: str, files: list[Path], target: Target) -> list[str]:
    """Upload each file to s3://<bucket>/<name>/<filename>; returns the keys written."""
    import boto3  # deferred: only the publish step needs it
    from botocore.config import Config

    for f in files:
        if not f.exists():
            raise FileNotFoundError(f"{f} is missing; run the earlier steps first")
    client = boto3.client(
        "s3",
        endpoint_url=target.endpoint,
        aws_access_key_id=target.access_key,
        aws_secret_access_key=target.secret_key,
        region_name=target.region,
        config=Config(
            s3={"addressing_style": "path"},
            # recent boto3 adds CRC checksums to every upload; non-AWS S3 servers may reject them
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )
    keys = []
    for f in files:
        key = f"{name}/{f.name}"
        log.info("uploading %s (%.2f GB) to %s/%s", f, f.stat().st_size / 1e9, target.bucket, key)
        client.upload_file(str(f), target.bucket, key)  # multipart above 8 MB
        keys.append(key)
    return keys
