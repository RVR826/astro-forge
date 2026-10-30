import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import PurePosixPath
from app.spark_tools import build_ingestion_script
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
                previous
                and previous.get("status") in ("success", "artifacts_saved")
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
def read_file(
    source_key: str,
    start_byte: int = 0,
    max_bytes: int = 65536,
    bucket: str = "",
    read_count = 0
) -> str:
    """Read a bounded byte range from a RustFS object.

    Use start_byte=0 to read from the beginning. For subsequent
    reads, use the next_start_byte returned by the previous call.
    The content is returned as text without schema inference.
    """
    if not source_key or source_key.startswith("/"):
        return json.dumps({
            "error": "source_key must be a relative object key"
        })

    if start_byte < 0:
        return json.dumps({
            "error": "start_byte must be non-negative"
        })

    if read_count > 5: 
        return json.dumps({
            "error": "a single file can be read max. 5 times. please proceed with the next step in the instructions"
        })

    # Restrict reads to the configured data buckets.
    selected_bucket = bucket or INCOMING_BUCKET
    allowed_buckets = {INCOMING_BUCKET, DATA_BUCKET}

    if selected_bucket not in allowed_buckets:
        return json.dumps({
            "error": "Bucket is not permitted",
            "allowed_buckets": sorted(allowed_buckets),
        })

    # Keep each read bounded, even if the agent requests a huge range.
    max_bytes = max(1, min(int(max_bytes), 65536))

    try:
        metadata = s3.head_object(
            Bucket=selected_bucket,
            Key=source_key,
        )
        file_size = metadata["ContentLength"]

        if start_byte >= file_size:
            return json.dumps({
                "bucket": selected_bucket,
                "source_key": source_key,
                "file_size_bytes": file_size,
                "start_byte": start_byte,
                "content": "",
                "bytes_read": 0,
                "next_start_byte": file_size,
                "eof": True,
            })

        end_byte = min(start_byte + max_bytes - 1, file_size - 1)

        response = s3.get_object(
            Bucket=selected_bucket,
            Key=source_key,
            Range=f"bytes={start_byte}-{end_byte}",
        )

        try:
            raw = response["Body"].read(max_bytes)
        finally:
            response["Body"].close()

        bytes_read = len(raw)
        next_start_byte = start_byte + bytes_read

        return json.dumps({
            "bucket": selected_bucket,
            "source_key": source_key,
            "file_size_bytes": file_size,
            "start_byte": start_byte,
            "content": raw.decode("utf-8", errors="replace"),
            "bytes_read": bytes_read,
            "next_start_byte": next_start_byte,
            "eof": next_start_byte >= file_size,
            "next_read_count": read_count + 1,
            "note": (
                "Content is a raw byte-range sample decoded as UTF-8. "
                "A range may begin or end partway through a line or "
                "multibyte character. Use the returned byte offset "
                "to continue reading."
            ),
        }, ensure_ascii=False)

    except ClientError as exc:
        return json.dumps({
            "error": "Unable to read RustFS object",
            "bucket": selected_bucket,
            "source_key": source_key,
            "details": str(exc),
        })

@function_tool
def save_ingestion_artifacts(
    source_key: str,
    prompt: str,
    source_specific_logic: str,
    profile_json: str,
    semantic_metadata: str,
    proposal: str,
    target_table: str,
) -> str:
    """
    Save all generated ingestion artifacts to RustFS.

    The source and generated artifacts are saved under:
        astro-forge/raw/<uuid>-<filename>/

    Artifacts:
        <filename>             Original source file
        ingest.py              Generated Spark ingestion script
        profile.json           Source data profile
        semantic-metadata.md   Semantic table and column metadata
        prompt.md              Exact prompt used for generation
        proposal.md            Ingestion design proposal

    The ingestion state is updated to:
        status = artifacts_saved

    The ingestion script is assembled deterministically from
    spark_template.py and the LLM-generated source-specific logic.
    This function does NOT execute the generated script or validate
    the resulting Iceberg table.
    """
    ingestion_script = build_ingestion_script(
        source_specific_logic,
        target_table,
    )

    # 1. Validate inputs.
    required_artifacts = {
        "prompt.md": prompt,
        "ingest.py": ingestion_script,
        "profile.json": profile_json,
        "semantic-metadata.md": semantic_metadata,
        "proposal.md": proposal,
    }

    for artifact_name, content in required_artifacts.items():
        if not isinstance(content, str) or not content.strip():
            raise ValueError(
                f"Artifact '{artifact_name}' is empty or invalid."
            )

    # Ensure profile.json is valid JSON.
    try:
        json.loads(profile_json)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"profile.json contains invalid JSON: {exc}"
        ) from exc

    if not source_key:
        raise ValueError("source_key cannot be empty.")

    # 2. Verify that the source exists in the incoming bucket.
    source = s3.head_object(
        Bucket=INCOMING_BUCKET,
        Key=source_key,
    )

    source_etag = source.get("ETag", "").strip('"')

    # 3. Generate a unique ID for this artifact run.
    ingestion_id = str(uuid.uuid4())

    raw_prefix = _raw_artifact_prefix(
        source_key,
        ingestion_id,
    )

    source_filename = _source_filename(source_key)
    source_destination = f"{raw_prefix}{source_filename}"

    # Artifact locations.
    artifact_keys = {
        "source": source_destination,
        "prompt": f"{raw_prefix}prompt.md",
        "ingestion_script": f"{raw_prefix}ingest.py",
        "profile": f"{raw_prefix}profile.json",
        "semantic_metadata": f"{raw_prefix}semantic-metadata.md",
        "proposal": f"{raw_prefix}proposal.md",
        "execution_log": f"{raw_prefix}execution.log",
    }

    # 4. Preserve the original source file.
    s3.copy_object(
        Bucket=DATA_BUCKET,
        CopySource={
            "Bucket": INCOMING_BUCKET,
            "Key": source_key,
        },
        Key=source_destination,
    )

    # 5. Upload all generated artifacts.
    _upload_text(
        artifact_keys["prompt"],
        prompt,
        "text/markdown",
    )

    _upload_text(
        artifact_keys["ingestion_script"],
        ingestion_script,
        "text/x-python",
    )

    _upload_text(
        artifact_keys["profile"],
        profile_json,
        "application/json",
    )

    _upload_text(
        artifact_keys["semantic_metadata"],
        semantic_metadata,
        "text/markdown",
    )

    _upload_text(
        artifact_keys["proposal"],
        proposal,
        "text/markdown",
    )

    # No Spark execution is performed at this stage.
    execution_log = (
        "Execution status: NOT EXECUTED\n"
        "The generated ingestion script has been saved but has not "
        "been executed in Spark.\n"
        "Iceberg table validation: NOT PERFORMED\n"
    )

    _upload_text(
        artifact_keys["execution_log"],
        execution_log,
        "text/plain",
    )

    # 6. Verify that every expected object exists.
    for key in artifact_keys.values():
        s3.head_object(
            Bucket=DATA_BUCKET,
            Key=key,
        )

    # 7. Update ingestion state.
    state = load_ingestion_state()
    processed = state["processed"]

    processed[source_key] = {
        "status": "artifacts_saved",
        "execution_performed": False,
        "ingestion_id": ingestion_id,
        "source_bucket": INCOMING_BUCKET,
        "source": source_key,
        "etag": source_etag,
        "processed_at": utc_now(),
        "raw_bucket": DATA_BUCKET,
        "raw_location": source_destination,
        "prompt": artifact_keys["prompt"],
        "ingestion_script": artifact_keys["ingestion_script"],
        "profile": artifact_keys["profile"],
        "semantic_metadata": artifact_keys["semantic_metadata"],
        "proposal": artifact_keys["proposal"],
        "execution_log": artifact_keys["execution_log"],
        "target_table": target_table,
    }

    save_ingestion_state(state)

    # 8. Return the artifact locations.
    return json.dumps(
        {
            "status": "artifacts_saved",
            "execution_performed": False,
            "ingestion_id": ingestion_id,
            "source_bucket": INCOMING_BUCKET,
            "source": source_key,
            "raw_bucket": DATA_BUCKET,
            "raw_location": source_destination,
            "artifacts": {
                name: key
                for name, key in artifact_keys.items()
                if name != "source"
            },
            "state_bucket": DATA_BUCKET,
            "state_key": STATE_KEY,
            "target_table": target_table,
        },
        indent=2,
    )