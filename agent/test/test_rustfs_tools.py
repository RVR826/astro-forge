import asyncio
import json
import os
import uuid

import boto3
from botocore.exceptions import ClientError

from app import rustfs_tools


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

ENDPOINT = os.environ["RUSTFS_ENDPOINT"]
INCOMING_BUCKET = os.getenv("RUSTFS_INCOMING_BUCKET", "incoming")
DATA_BUCKET = os.getenv("RUSTFS_DATA_BUCKET", "astro-forge")

RUN_ID = str(uuid.uuid4())
TEST_ROOT = f"__rustfs_tools_test__/{RUN_ID}/"

# Isolate the test from real ingestion state and raw artifacts.
# Patch the imported module because it reads these constants
# when the module is imported.
TEST_STATE_KEY = f"{TEST_ROOT}metadata/system/ingestion-state.json"
TEST_RAW_PREFIX = f"{TEST_ROOT}raw/"

rustfs_tools.STATE_KEY = TEST_STATE_KEY
rustfs_tools.RAW_PREFIX = TEST_RAW_PREFIX

s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    region_name=os.getenv("AWS_REGION", "us-east-1"),
)

source_filename = f"sample-{RUN_ID}.json"
source_key = (
    f"{rustfs_tools.INCOMING_PREFIX}/{source_filename}"
    if rustfs_tools.INCOMING_PREFIX
    else source_filename
)

source_content = json.dumps(
    {
        "source_id": 123,
        "ra": 187.5,
        "decl": -2.3,
        "test_run": RUN_ID,
    },
    indent=2,
)


# ---------------------------------------------------------
# Helpers
# ---------------------------------------------------------

def check(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")

def exists(bucket, key):
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] in (
            "404",
            "NoSuchKey",
            "NotFound",
        ):
            return False
        raise


def read_text(bucket, key):
    response = s3.get_object(Bucket=bucket, Key=key)
    return response["Body"].read().decode("utf-8")


def cleanup():
    """Remove only objects belonging to this test run."""
    print("\nCleaning up test objects...")

    # Remove the disposable source from incoming.
    s3.delete_object(
        Bucket=INCOMING_BUCKET,
        Key=source_key,
    )

    # Remove this test's state and artifacts from astro-forge.
    paginator = s3.get_paginator("list_objects_v2")

    for page in paginator.paginate(
        Bucket=DATA_BUCKET,
        Prefix=TEST_ROOT,
    ):
        objects = [
            {"Key": obj["Key"]}
            for obj in page.get("Contents", [])
        ]

        if objects:
            s3.delete_objects(
                Bucket=DATA_BUCKET,
                Delete={"Objects": objects, "Quiet": True},
            )

    print("Cleanup complete.")


# ---------------------------------------------------------
# Integration tests
# ---------------------------------------------------------

async def run_tests():
    print("RustFS tools integration test")
    print(f"Endpoint:        {ENDPOINT}")
    print(f"Incoming bucket: {INCOMING_BUCKET}")
    print(f"Data bucket:     {DATA_BUCKET}")
    print(f"Test ID:         {RUN_ID}\n")

    try:
        # 1. Both buckets must exist and be accessible.
        s3.head_bucket(Bucket=INCOMING_BUCKET)
        check(True, "Incoming bucket is accessible")

        s3.head_bucket(Bucket=DATA_BUCKET)
        check(True, "astro-forge bucket is accessible")

        # 2. The isolated state should initially be empty.
        state = rustfs_tools.load_ingestion_state()

        check(
            state == {"version": 1, "processed": {}},
            "Missing test state returns an empty state",
        )

        # 3. State can be written and read back.
        rustfs_tools.save_ingestion_state(state)

        check(
            rustfs_tools.load_ingestion_state() == state,
            "Ingestion state persists in astro-forge",
        )

        check(
            exists(DATA_BUCKET, TEST_STATE_KEY),
            "State file exists at the configured test path",
        )

        # 4. Upload a disposable source to incoming.
        s3.put_object(
            Bucket=INCOMING_BUCKET,
            Key=source_key,
            Body=source_content.encode("utf-8"),
            ContentType="application/json",
        )

        check(
            exists(INCOMING_BUCKET, source_key),
            "Test source uploaded to incoming",
        )

        check(
            not exists(DATA_BUCKET, source_key),
            "Source has not been copied to astro-forge prematurely",
        )

        # 5. Discovery should find the source.
        discovery = json.loads(rustfs_tools.check_for_new_files())

        check(
            discovery["bucket"] == INCOMING_BUCKET,
            "Discovery reports the incoming bucket",
        )

        check(
            discovery["state_bucket"] == DATA_BUCKET,
            "Discovery reports astro-forge as the state bucket",
        )

        check(
            discovery["state_key"] == TEST_STATE_KEY,
            "Discovery reports the configured state path",
        )

        found = {
            item["key"]: item
            for item in discovery["new_files"]
        }

        check(
            source_key in found,
            "Discovery finds the uploaded source",
        )

        check(
            found[source_key]["previously_seen"] is False,
            "New source is marked as previously unseen",
        )

        # 6. Finalize a simulated successful ingestion.
        prompt = "Test prompt: parse the sample astronomy JSON."
        script = "# Test-only script. Do not execute.\n"
        log = "Simulated Spark success; no Spark job was run.\n"
        target_table = "astronomy.test.sample"

        result = json.loads(
            rustfs_tools.finalize_ingestion(
                source_key,
                prompt,
                script,
                log,
                target_table
            )
        )

        check(
            result["status"] == "success",
            "Finalization returns success",
        )

        check(
            result["source_bucket"] == INCOMING_BUCKET,
            "Finalization identifies incoming as the source bucket",
        )

        check(
            result["raw_bucket"] == DATA_BUCKET,
            "Artifacts are assigned to astro-forge",
        )

        check(
            result["raw_location"].startswith(TEST_RAW_PREFIX),
            "Raw source copy uses the isolated raw prefix",
        )

        # 7. All four artifacts must exist in astro-forge.
        artifact_keys = {
            "source copy": result["raw_location"],
            "prompt": result["prompt"],
            "ingestion script": result["ingestion_script"],
            "execution log": result["execution_log"],
        }

        for label, key in artifact_keys.items():
            check(
                exists(DATA_BUCKET, key),
                f"{label.capitalize()} exists in astro-forge",
            )

        # 8. Verify copied source and artifact contents.
        check(
            read_text(DATA_BUCKET, result["raw_location"])
            == source_content,
            "Copied source content matches the original",
        )

        check(
            read_text(DATA_BUCKET, result["prompt"]) == prompt,
            "Prompt content is preserved exactly",
        )

        check(
            read_text(DATA_BUCKET, result["ingestion_script"])
            == script,
            "Generated script content is preserved exactly",
        )

        check(
            read_text(DATA_BUCKET, result["execution_log"]) == log,
            "Execution log content is preserved exactly",
        )

        # 9. Verify the state record.
        saved_state = json.loads(
            read_text(DATA_BUCKET, TEST_STATE_KEY)
        )

        record = saved_state["processed"][source_key]

        check(
            record["status"] == "success",
            "State records a successful ingestion",
        )

        check(
            record["ingestion_id"] == result["ingestion_id"],
            "State records the correct ingestion ID",
        )

        check(
            record["source_bucket"] == INCOMING_BUCKET,
            "State records the incoming source bucket",
        )

        check(
            record["raw_bucket"] == DATA_BUCKET,
            "State records the artifact bucket",
        )

        check(
            record["target_table"] == target_table,
            "State records the target table",
        )

        check(
            rustfs_tools.load_ingestion_state() == saved_state,
            "State can be reloaded from astro-forge",
        )

        # 10. Unchanged, successfully processed objects are skipped.
        discovery = json.loads(rustfs_tools.check_for_new_files())

        remaining_keys = {
            item["key"] for item in discovery["new_files"]
        }

        check(
            source_key not in remaining_keys,
            "Unchanged successful source is skipped",
        )

        # 11. Changed content should be discovered again.
        changed_content = source_content + "\n"
        s3.put_object(
            Bucket=INCOMING_BUCKET,
            Key=source_key,
            Body=changed_content.encode("utf-8"),
            ContentType="application/json",
        )

        discovery = json.loads(rustfs_tools.check_for_new_files())

        changed = {
            item["key"]: item
            for item in discovery["new_files"]
        }

        check(
            source_key in changed,
            "Modified source is discovered again",
        )

        check(
            changed[source_key]["previously_seen"] is True,
            "Modified source is identified as previously seen",
        )

        check(
            changed[source_key]["previous_status"] == "success",
            "Modified source retains its previous success status",
        )

        print("\nALL TESTS PASSED")

    finally:
        try:
            cleanup()
        except Exception as exc:
            print(
                "\nWARNING: Cleanup failed. Remove only objects "
                f"under test ID {RUN_ID} manually if necessary."
            )
            print(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(run_tests())