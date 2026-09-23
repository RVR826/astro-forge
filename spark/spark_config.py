import os
from pyspark.sql import SparkSession

def create_spark_session(app_name: str) -> SparkSession:
    """
    Create a Spark session configured for the project's
    Polaris REST catalog and RustFS S3 storage.
    """
    polaris_client_id = os.environ["POLARIS_CLIENT_ID"]
    polaris_client_secret = os.environ["POLARIS_CLIENT_SECRET"]

    s3_access_key = os.environ["AWS_ACCESS_KEY_ID"]
    s3_secret_key = os.environ["AWS_SECRET_ACCESS_KEY"]
    s3_region = os.environ.get("AWS_REGION", "us-east-1")

    builder = (
        SparkSession.builder.appName(app_name)
        .config("spark.sql.defaultCatalog", "polaris")

        # Iceberg extensions
        .config(
            "spark.sql.extensions",
            "org.apache.iceberg.spark.extensions.IcebergSparkSessionExtensions",
        )

        # Polaris REST catalog
        .config(
            "spark.sql.catalog.polaris",
            "org.apache.iceberg.spark.SparkCatalog",
        )
        .config(
            "spark.sql.catalog.polaris.catalog-impl",
            "org.apache.iceberg.rest.RESTCatalog",
        )
        .config(
            "spark.sql.catalog.polaris.uri",
            "http://polaris:8181/api/catalog",
        )
        .config(
            "spark.sql.catalog.polaris.warehouse",
            "astronomy",
        )
        .config(
            "spark.sql.catalog.polaris.credential",
            f"{polaris_client_id}:{polaris_client_secret}",
        )
        .config(
            "spark.sql.catalog.polaris.scope",
            "PRINCIPAL_ROLE:ALL",
        )
        .config(
            "spark.sql.catalog.polaris.oauth2-server-uri",
            "http://polaris:8181/api/catalog/v1/oauth/tokens",
        )
        .config(
            "spark.sql.catalog.polaris.token-refresh-enabled",
            "false",
        )

        # Direct S3 access via S3FileIO
        .config(
            "spark.sql.catalog.polaris.io-impl",
            "org.apache.iceberg.aws.s3.S3FileIO",
        )
        .config(
            "spark.sql.catalog.polaris.client.region",
            s3_region,
        )
        .config(
            "spark.sql.catalog.polaris.s3.access-key-id",
            s3_access_key,
        )
        .config(
            "spark.sql.catalog.polaris.s3.secret-access-key",
            s3_secret_key,
        )
        .config(
            "spark.sql.catalog.polaris.s3.endpoint",
            "http://rustfs:9000",
        )
        .config(
            "spark.sql.catalog.polaris.s3.path-style-access",
            "true",
        )
    )

    return builder.getOrCreate()