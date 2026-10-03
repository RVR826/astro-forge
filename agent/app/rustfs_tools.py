import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath

import boto3
from botocore.exceptions import ClientError
from agents import function_tool


RUSTFS_ENDPOINT = os.environ["RUSTFS_ENDPOINT"]
RUSTFS_BUCKET = os.environ["RUSTFS_BUCKET"]

INCOMING_PREFIX = os.getenv(
    "RUSTFS_INCOMING_PREFIX",
    "incoming/",
)

RAW_PREFIX = os.getenv(
    "RUSTFS_RAW_PREFIX",
    "raw/",
)

STATE_KEY = os.getenv(
    "RUSTFS_STATE_KEY",
    "metadata/system/ingestion-state.json",
)


s3 = boto3.client(
    "s3",
    endpoint_url=RUSTFS_ENDPOINT,
    region_name=os.getenv("AWS_REGION", "us-east-1"),
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_ingestion_state() -> dict:
    """
    Load the ingestion state from RustFS.

    A missing state file is treated as an empty state.
    """

    try:
        response = s3.get_object(
            Bucket=RUSTFS_BUCKET,
            Key=STATE_KEY,
        )

        return json.loads(response["Body"].read())

    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]

        if error_code in ("NoSuchKey", "404", "NoSuchBucket"):
            return {
                "version": 1,
                "processed": {},
            }

        raise


def save_ingestion_state(state: dict) -> None:
    """
    Persist the ingestion state to RustFS.
    """

    s3.put_object(
        Bucket=RUSTFS_BUCKET,
        Key=STATE_KEY,
        Body=json.dumps(
            state,
            indent=2,
        ).encode("utf-8"),
        ContentType="application/json",
    )


@function_tool
def check_for_new_files() -> str:
    """
    Check the RustFS incoming bucket for files that have not
    previously been successfully ingested.

    Returns a JSON document containing the newly discovered files.
    """

    state = load_ingestion_state()
    processed = state.setdefault("processed", {})

    paginator = s3.get_paginator("list_objects_v2")

    new_files = []

    for page in paginator.paginate(
        Bucket=RUSTFS_BUCKET,
        Prefix=INCOMING_PREFIX,
    ):
        for obj in page.get("Contents", []):
            key = obj["Key"]

            if key == INCOMING_PREFIX:
                continue

            etag = obj.get("ETag", "").strip('"')
            last_modified = obj["LastModified"].astimezone(
                timezone.utc
            ).isoformat()

            previous = processed.get(key)

            # The object has already been successfully processed and its content has not changed.
            if (
                previous is not None
                and previous.get("status") == "success"
                and previous.get("etag") == etag
            ):
                continue

            new_files.append(
                {
                    "key": key,
                    "size": obj["Size"],
                    "etag": etag,
                    "last_modified": last_modified,
                    "previously_seen": previous is not None,
                    "previous_status": (
                        previous.get("status")
                        if previous is not None
                        else None
                    ),
                }
            )

    return json.dumps(
        {
            "bucket": RUSTFS_BUCKET,
            "prefix": INCOMING_PREFIX,
            "new_files": new_files,
            "count": len(new_files),
        },
        indent=2,
    )


def _source_filename(source_key: str) -> str:
    """
    Extract the filename from an S3 object key.
    """

    return PurePosixPath(source_key).name


def _raw_artifact_prefix(
    source_key: str,
    ingestion_id: str,
) -> str:
    """
    Build the raw artifact directory.

    Example:
        raw/550e8400-e29b-41d4-a716-446655440000-gaia.json/
    """

    filename = _source_filename(source_key)

    return (
        f"{RAW_PREFIX}"
        f"{ingestion_id}-{filename}/"
    )


def _upload_text(
    key: str,
    content: str,
    content_type: str = "text/plain",
) -> None:
    """
    Upload a text artifact to RustFS.
    """

    s3.put_object(
        Bucket=RUSTFS_BUCKET,
        Key=key,
        Body=content.encode("utf-8"),
        ContentType=content_type,
    )


@function_tool
def finalize_ingestion(
    source_key: str,
    prompt: str,
    ingestion_script: str,
    execution_log: str,
    target_table: str,
) -> str:
    """
    Finalize a successful ingestion.

    The source object is copied from the incoming area into a
    uniquely identified raw ingestion directory. The generated
    prompt, ingestion script, and execution log are stored there.

    Only after all artifacts have been successfully stored is the
    ingestion state updated to status='success'.

    This tool must only be called after the Spark ingestion and
    resulting Iceberg table have been successfully validated.
    """

    # Verify that the source object actually exists.
    source = s3.head_object(
        Bucket=RUSTFS_BUCKET,
        Key=source_key,
    )

    source_etag = source.get("ETag", "").strip('"')

    # Generate a unique ID for this ingestion run.
    ingestion_id = str(uuid.uuid4())

    raw_prefix = _raw_artifact_prefix(
        source_key,
        ingestion_id,
    )

    source_filename = _source_filename(source_key)

    source_destination = (
        f"{raw_prefix}{source_filename}"
    )

    prompt_key = f"{raw_prefix}prompt.md"
    script_key = f"{raw_prefix}ingest.py"
    log_key = f"{raw_prefix}execution.log"

    # 1. Preserve the original source file.
    s3.copy_object(
        Bucket=RUSTFS_BUCKET,
        CopySource={
            "Bucket": RUSTFS_BUCKET,
            "Key": source_key,
        },
        Key=source_destination,
    )

    # 2. Store the exact prompt used for generation.
    _upload_text(
        prompt_key,
        prompt,
        "text/markdown",
    )

    # 3. Store the generated Spark ingestion script.
    _upload_text(
        script_key,
        ingestion_script,
        "text/x-python",
    )

    # 4. Store the execution log.
    _upload_text(
        log_key,
        execution_log,
        "text/plain",
    )

    # 5. Update ingestion state only after all artifacts exist.
    state = load_ingestion_state()

    processed = state.setdefault(
        "processed",
        {},
    )

    processed[source_key] = {
        "status": "success",
        "ingestion_id": ingestion_id,
        "etag": source_etag,
        "processed_at": utc_now(),
        "source": source_key,
        "raw_location": source_destination,
        "prompt": prompt_key,
        "ingestion_script": script_key,
        "execution_log": log_key,
        "target_table": target_table,
    }

    save_ingestion_state(state)

    return json.dumps(
        {
            "status": "success",
            "ingestion_id": ingestion_id,
            "source": source_key,
            "raw_location": source_destination,
            "prompt": prompt_key,
            "ingestion_script": script_key,
            "execution_log": log_key,
            "target_table": target_table,
        },
        indent=2,
    )