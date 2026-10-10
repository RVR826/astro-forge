import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath

import boto3
from botocore.exceptions import ClientError
from agents import function_tool


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

RUSTFS_ENDPOINT = os.environ["RUSTFS_ENDPOINT"]

INCOMING_BUCKET = os.getenv(
    "RUSTFS_INCOMING_BUCKET",
    "incoming",
)

DATA_BUCKET = os.getenv(
    "RUSTFS_DATA_BUCKET",
    "astro-forge",
)

# The incoming bucket is the input area itself, so the default
# prefix is empty. Set a prefix only if inputs are in a subfolder.
INCOMING_PREFIX = os.getenv(
    "RUSTFS_INCOMING_PREFIX",
    "",
).strip("/")

RAW_PREFIX = os.getenv(
    "RUSTFS_RAW_PREFIX",
    "raw/",
).strip("/") + "/"

STATE_KEY = os.getenv(
    "RUSTFS_STATE_KEY",
    "metadata/system/ingestion-state.json",
).lstrip("/")


s3 = boto3.client(
    "s3",
    endpoint_url=RUSTFS_ENDPOINT,
    region_name=os.getenv("AWS_REGION", "us-east-1"),
)


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_ingestion_state() -> dict:
    """
    Load ingestion state from astro-forge.

    A missing state file is treated as an empty state.
    Other storage errors are raised.
    """

    try:
        response = s3.get_object(
            Bucket=DATA_BUCKET,
            Key=STATE_KEY,
        )

        state = json.loads(response["Body"].read())

        if not isinstance(state, dict):
            raise ValueError("Ingestion state must be a JSON object")

        state.setdefault("version", 1)
        state.setdefault("processed", {})

        if not isinstance(state["processed"], dict):
            raise ValueError(
                "Ingestion state 'processed' must be a JSON object"
            )

        return state

    except ClientError as exc:
        error_code = exc.response["Error"]["Code"]

        if error_code in ("NoSuchKey", "404", "NotFound"):
            return {
                "version": 1,
                "processed": {},
            }

        raise


def save_ingestion_state(state: dict) -> None:
    """
    Persist ingestion state to astro-forge.
    """

    s3.put_object(
        Bucket=DATA_BUCKET,
        Key=STATE_KEY,
        Body=json.dumps(
            state,
            indent=2,
        ).encode("utf-8"),
        ContentType="application/json",
    )


def _source_filename(source_key: str) -> str:
    """Extract the filename from an S3 object key."""

    filename = PurePosixPath(source_key).name

    if not filename:
        raise ValueError(
            f"Source key does not identify a file: {source_key}"
        )

    return filename


def _raw_artifact_prefix(
    source_key: str,
    ingestion_id: str,
) -> str:
    """
    Build the artifact directory in astro-forge.

    Example:
        raw/550e8400-e29b-41d4-a716-446655440000-gaia.json/
    """

    filename = _source_filename(source_key)

    return f"{RAW_PREFIX}{ingestion_id}-{filename}/"


def _upload_text(
    key: str,
    content: str,
    content_type: str = "text/plain",
) -> None:
    """Upload a text artifact to astro-forge."""

    s3.put_object(
        Bucket=DATA_BUCKET,
        Key=key,
        Body=content.encode("utf-8"),
        ContentType=content_type,
    )


def _object_etag(bucket: str, key: str) -> str:
    """Return an object's ETag without surrounding quotes."""

    response = s3.head_object(
        Bucket=bucket,
        Key=key,
    )

    return response.get("ETag", "").strip('"')


# ---------------------------------------------------------
# Agent tools
# ---------------------------------------------------------

@function_tool
def check_for_new_files() -> str:
    """
    Discover files in the incoming bucket that have not already
    been successfully ingested with unchanged content.

    Ingestion state is read from astro-forge.

    Returns a JSON document containing discovered files.
    """

    state = load_ingestion_state()
    processed = state["processed"]

    paginator = s3.get_paginator("list_objects_v2")

    new_files = []

    pagination_args = {
        "Bucket": INCOMING_BUCKET,
    }

    if INCOMING_PREFIX:
        pagination_args["Prefix"] = INCOMING_PREFIX + "/"

    for page in paginator.paginate(**pagination_args):
        for obj in page.get("Contents", []):
            key = obj["Key"]

            # Ignore directory marker objects.
            if key.endswith("/"):
                continue

            etag = obj.get("ETag", "").strip('"')
            last_modified = obj["LastModified"].astimezone(
                timezone.utc
            ).isoformat()

            previous = processed.get(key)

            # Skip only when a previous successful ingestion has
            # the same ETag.
            if (
                previous is not None
                and previous.get("status") == "success"
                and previous.get("etag") == etag
            ):
                continue

            new_files.append(
                {
                    "key": key,
                    "bucket": INCOMING_BUCKET,
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
            "bucket": INCOMING_BUCKET,
            "prefix": INCOMING_PREFIX,
            "state_bucket": DATA_BUCKET,
            "state_key": STATE_KEY,
            "new_files": new_files,
            "count": len(new_files),
        },
        indent=2,
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
    Finalize an already validated ingestion.

    The source is copied from the incoming bucket to:
        astro-forge/raw/<uuid>-<filename>/<filename>

    The prompt, generated script, and execution log are saved
    alongside the source copy.

    The state record is written to:
        astro-forge/metadata/system/ingestion-state.json

    Call this tool only after Spark ingestion and the resulting
    Iceberg table have been successfully validated.
    """

    # Verify that the source exists in the incoming bucket.
    source = s3.head_object(
        Bucket=INCOMING_BUCKET,
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

    source_destination = f"{raw_prefix}{source_filename}"

    prompt_key = f"{raw_prefix}prompt.md"
    script_key = f"{raw_prefix}ingest.py"
    log_key = f"{raw_prefix}execution.log"

    # 1. Preserve the source in astro-forge/raw.
    s3.copy_object(
        Bucket=DATA_BUCKET,
        CopySource={
            "Bucket": INCOMING_BUCKET,
            "Key": source_key,
        },
        Key=source_destination,
    )

    # 2. Save the exact prompt used for code generation.
    _upload_text(
        prompt_key,
        prompt,
        "text/markdown",
    )

    # 3. Save the generated ingestion script.
    _upload_text(
        script_key,
        ingestion_script,
        "text/x-python",
    )

    # 4. Save the execution log.
    _upload_text(
        log_key,
        execution_log,
        "text/plain",
    )

    # 5. Verify that all expected artifacts exist before
    #    recording the ingestion as successful.
    for key in (
        source_destination,
        prompt_key,
        script_key,
        log_key,
    ):
        s3.head_object(
            Bucket=DATA_BUCKET,
            Key=key,
        )

    # 6. Update state in astro-forge.
    state = load_ingestion_state()
    processed = state["processed"]

    processed[source_key] = {
        "status": "success",
        "ingestion_id": ingestion_id,
        "source_bucket": INCOMING_BUCKET,
        "source": source_key,
        "etag": source_etag,
        "processed_at": utc_now(),
        "raw_bucket": DATA_BUCKET,
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
            "source_bucket": INCOMING_BUCKET,
            "source": source_key,
            "raw_bucket": DATA_BUCKET,
            "raw_location": source_destination,
            "prompt": prompt_key,
            "ingestion_script": script_key,
            "execution_log": log_key,
            "state_bucket": DATA_BUCKET,
            "state_key": STATE_KEY,
            "target_table": target_table,
        },
        indent=2,
    )