from spark_config import create_spark_session

def ingest():
    spark = create_spark_session()

    target_table = "__TARGET_TABLE__"

    try:
        # [SOURCE-SPECIFIC LOGIC GENERATED GOES HERE]

    except Exception:
        print(f"An error occured while loading {target_table}.")

    finally:
        spark.stop()


if __name__ == "__main__":
    ingest()
