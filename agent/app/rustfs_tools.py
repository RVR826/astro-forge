import json
import os
import uuid
import ast
import re
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


def _validate_profile(profile_json: str, source_key: str) -> dict:
    """Validate profile JSON and its package-name-only dependencies."""

    profile = json.loads(profile_json)

    if not isinstance(profile, dict):
        raise ValueError("profile.json must contain a JSON object")

    if profile.get("source_key") != source_key:
        raise ValueError("profile.json source_key does not match source_key")

    dependencies = profile.get("dependencies", [])

    if not isinstance(dependencies, list):
        raise ValueError("'dependencies' must be a list of package names")

    package_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

    normalized = []
    for package in dependencies:
        if (
            not isinstance(package, str)
            or not package
            or not package_pattern.fullmatch(package)
        ):
            raise ValueError(
                f"Invalid dependency package name: {package!r}. "
                "Provide package names only, without versions or URLs."
            )

        normalized.append(package)

    if len(normalized) != len(set(name.lower() for name in normalized)):
        raise ValueError("Duplicate dependencies are not allowed")

    profile["dependencies"] = normalized
    return profile


def _validate_source_loader(source_loader_script: str) -> None:
    """Check loader syntax and its required public interface."""

    try:
        tree = ast.parse(source_loader_script)
    except SyntaxError as exc:
        raise ValueError(
            f"Generated source_loader.py has invalid Python syntax: {exc}"
        ) from exc

    loaders = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "load_data"
    ]

    if len(loaders) != 1:
        raise ValueError(
            "source_loader.py must define exactly one top-level "
            "load_data() function"
        )

    args = loaders[0].args

    if (
        [arg.arg for arg in args.args] != ["spark", "source_path"]
        or args.posonlyargs
        or args.vararg
        or args.kwarg
        or args.kwonlyargs
    ):
        raise ValueError(
            "load_data() must have exactly this interface: "
            "load_data(spark, source_path)"
        )

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
    source_loader_script: str,
    profile_json: str,
    semantic_metadata: str,
    proposal: str,
    target_table: str,
) -> str:
    """
    Save the source file and generated ingestion artifacts.

    This operation does not execute the generated loader.
    """

    # Validate generated artifacts before writing anything.
    profile = _validate_profile(profile_json, source_key)
    _validate_source_loader(source_loader_script)

    if not all(isinstance(value, str) and value.strip() for value in (
        prompt,
        source_loader_script,
        profile_json,
        semantic_metadata,
        proposal,
        target_table,
    )):
        raise ValueError("All artifact contents and target_table are required")

    # Verify the original source exists.
    source = s3.head_object(
        Bucket=INCOMING_BUCKET,
        Key=source_key,
    )
    source_etag = source.get("ETag", "").strip('"')

    proposal_id = str(uuid.uuid4())
    raw_prefix = _raw_artifact_prefix(source_key, proposal_id)
    source_filename = _source_filename(source_key)

    source_destination = f"{raw_prefix}{source_filename}"

    artifact_keys = {
        "source_loader": f"{raw_prefix}source_loader.py",
        "profile": f"{raw_prefix}profile.json",
        "prompt": f"{raw_prefix}prompt.md",
        "proposal": f"{raw_prefix}proposal.md",
        "semantic_metadata": f"{raw_prefix}semantic-metadata.md",
        "execution_log": f"{raw_prefix}execution.log",
    }

    # Preserve the original source.
    s3.copy_object(
        Bucket=DATA_BUCKET,
        CopySource={
            "Bucket": INCOMING_BUCKET,
            "Key": source_key,
        },
        Key=source_destination,
    )

    # Save the five generated artifacts.
    _upload_text(
        artifact_keys["source_loader"],
        source_loader_script,
        "text/x-python",
    )

    _upload_text(
        artifact_keys["profile"],
        json.dumps(profile, indent=2, ensure_ascii=False),
        "application/json",
    )

    _upload_text(
        artifact_keys["prompt"],
        prompt,
        "text/markdown",
    )

    _upload_text(
        artifact_keys["proposal"],
        proposal,
        "text/markdown",
    )

    _upload_text(
        artifact_keys["semantic_metadata"],
        semantic_metadata,
        "text/markdown",
    )

    # Record explicitly that execution has not taken place.
    _upload_text(
        artifact_keys["execution_log"],
        "Status: NOT EXECUTED\n"
        "The source loader was generated and saved for review. "
        "No Spark job was submitted.\n",
        "text/plain",
    )

    # Verify all saved objects.
    expected_keys = [
        source_destination,
        *artifact_keys.values(),
    ]

    for key in expected_keys:
        s3.head_object(
            Bucket=DATA_BUCKET,
            Key=key,
        )

    # Update processing state.
    state = load_ingestion_state()

    state["processed"][source_key] = {
        "status": "artifacts_saved",
        "proposal_id": proposal_id,
        "source_bucket": INCOMING_BUCKET,
        "source": source_key,
        "etag": source_etag,
        "processed_at": utc_now(),
        "raw_bucket": DATA_BUCKET,
        "raw_location": source_destination,
        "artifacts": artifact_keys,
        "target_table": target_table,
        "dependencies": profile["dependencies"],
        "execution_performed": False,
    }

    save_ingestion_state(state)

    return json.dumps(
        {
            "status": "artifacts_saved",
            "proposal_id": proposal_id,
            "source_bucket": INCOMING_BUCKET,
            "source": source_key,
            "raw_bucket": DATA_BUCKET,
            "raw_location": source_destination,
            "artifact_bucket": DATA_BUCKET,
            "artifacts": artifact_keys,
            "state_bucket": DATA_BUCKET,
            "state_key": STATE_KEY,
            "target_table": target_table,
            "dependencies": profile["dependencies"],
            "execution_performed": False,
        },
        indent=2,
    )