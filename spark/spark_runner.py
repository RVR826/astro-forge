import argparse
import os
import importlib.util
import logging
import shutil
import tempfile
from pathlib import Path

import boto3
from botocore.config import Config
from pyspark.sql import DataFrame

from spark_config import create_spark_session

APP_NAME = "astro_forge"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("spark-runner")


def create_s3_client():
    """Create an S3 client configured for RustFS."""
    return boto3.client(
        "s3",
        endpoint_url="http://localhost:9000",
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
        ),
    )


def download_object(s3, bucket: str, key: str, destination: Path):
    """Download a RustFS object to a local path."""
    logger.info("Downloading s3://%s/%s", bucket, key)

    s3.download_file(
        Bucket=bucket,
        Key=key,
        Filename=str(destination),
    )


def load_adapter(loader_path: Path):
    """Import the generated loader and validate its interface."""
    spec = importlib.util.spec_from_file_location(
        "generated_source_loader",
        loader_path,
    )

    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load source loader module")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    load_data = getattr(module, "load_data", None)

    if not callable(load_data):
        raise RuntimeError(
            "Generated loader must define load_data(spark, source_path)"
        )

    return module

def write_to_iceberg(spark, df, target_table: str):
    """
    Create the Iceberg table if it does not exist;
    otherwise append to it.
    """
    logger.info("Checking target table: %s", target_table)

    if spark.catalog.tableExists(target_table):
        logger.info("Table exists. Appending data.")
        df.writeTo(target_table).append()
    else:
        logger.info("Table does not exist. Creating it.")
        df.writeTo(target_table).create()

def run_ingestion(loader_key: str, source_key: str, target_table: str):
    bucket = os.environ["AWS_DEFAULT_BUCKET"]
    s3 = create_s3_client()

    temp_dir = Path(tempfile.mkdtemp(prefix="spark-loader-"))
    loader_path = temp_dir / "source_loader.py"

    spark = None

    try:
        # Create Spark session.
        spark = create_spark_session(APP_NAME)

        # Download and import the generated loader.
        download_object(s3, bucket, loader_key, loader_path)
        adapter = load_adapter(loader_path)


        # Spark's S3A filesystem uses s3a:// rather than s3://.
        source_uri = f"s3a://{bucket}/{source_key}"

        logger.info("Loading source data: %s", source_uri)

        df = adapter.load_data(spark, source_uri)

        if not isinstance(df, DataFrame):
            raise TypeError("load_data() did not return a Spark DataFrame")

        if not df.columns:
            raise ValueError("The returned DataFrame has no columns")

        logger.info("Loaded DataFrame with columns: %s", df.columns)

        # Append data to the existing Iceberg table.
        logger.info("Writing to Iceberg table: %s", target_table)

        write_to_iceberg(spark, df, target_table)

        logger.info("Ingestion completed successfully")

    finally:
        if spark is not None:
            spark.stop()

        shutil.rmtree(temp_dir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(
        description="Run a generated astronomical source loader"
    )

    parser.add_argument(
        "--loader-key",
        required=True,
        help="RustFS object key of the generated source_loader.py",
    )

    parser.add_argument(
        "--source-key",
        required=True,
        help="RustFS object key of the raw source file",
    )

    parser.add_argument(
        "--target",
        required=True,
        help="Fully qualified Iceberg target table",
    )

    args = parser.parse_args()

    run_ingestion(
        loader_key=args.loader_key,
        source_key=args.source_key,
        target_table=args.target,
    )

if __name__ == "__main__":
    main()