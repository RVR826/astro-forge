## Objective

Discover and process new astronomical source files reported by `check_for_new_files()`.

**Process exactly one source file per run.** If multiple files are returned, select one and leave the others for subsequent runs.

## Workflow

1. Call `check_for_new_files()` and select one unprocessed source file from `new_files`.
2. Inspect its contents using `read_file()`. Read additional chunks only when necessary to understand the format and schema.
3. Profile the source based on observed contents. Identify its format, structure, columns, data types, likely meanings, missing values, and parsing requirements. Clearly distinguish observations from assumptions.
4. Generate all five required artifacts with complete, source-specific content:
   - `profile.json` — structured source profile.
   - `semantic-metadata.md` — human-readable table and column descriptions.
   - `proposal.md` — ingestion proposal and relevant decisions.
   - `prompt.md` — the instructions and context used to generate the ingestion logic.
   - `ingest.py` — the complete ingestion script.
5. Review the artifacts for completeness and consistency.
6. Call `save_ingestion_artifacts()` with all required arguments, including the five artifacts and the source file's exact key.
7. Stop after the save operation. Report the result and the saved artifact locations.

Do not attempt to read the entire source file. Read enough to understand its structure; do not continue reading merely to reach the end or the last chunk.

Do not finish after describing the artifacts or saying you intend to save them. You must call the save tool and wait for its result.

If saving fails, report the returned error and do not claim success.

In the final response, list the proposal ID and artifact keys returned by the tool for each successfully saved file.

## Spark Ingestion Requirements

The application assembles the final `ingest.py` using a predefined template. You must generate **only the source-specific ingestion logic**, not the complete script.

The following variables are already provided by the template:

- `spark` — an active Spark session created using `create_spark_session()` from `spark_config.py`.
- `target_table` — the fully qualified destination Iceberg table name.

Your generated logic must:

- Read and parse the source data using the appropriate Spark APIs.
- Apply source-specific transformations and type conversions.
- Handle relevant missing values and parsing issues.
- Produce a DataFrame suitable for the target Iceberg table.
- Write the resulting DataFrame to `target_table` through the configured Polaris catalog.

Do not:

- Create another Spark session or call `create_spark_session()`.
- Define another `ingest()` function or add a `__main__` block.
- Access RustFS directly or write Iceberg files manually.
- Configure Polaris or the storage backend yourself.
- Include placeholder code, TODOs, pseudocode, or instructions for a human to complete.
- Execute, submit, or test the generated script.

Generate complete, executable source-specific logic that fits inside the template's existing `try` block. The application is responsible for assembling and saving the final script.

**Execution boundary:** This workflow only generates and saves artifacts. It must never execute the generated Spark script or create/modify Iceberg tables.