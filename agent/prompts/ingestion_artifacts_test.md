# Runner Prompt

Discover and process new astronomical source files using `check_for_new_files()`.

**Process exactly one source file per run.** If multiple files are returned, select one and leave the others for subsequent runs.

## Workflow

1. Call `check_for_new_files()` and select one file from `new_files`.
2. Inspect its contents using `read_file()`. Read additional chunks only when necessary to understand its structure.
3. Create a source profile based on observed contents. Distinguish observations from assumptions.
4. Identify any third-party Python packages required by the generated loader.
5. Generate all five required artifacts:
   - `profile.json`
   - `source_loader.py`
   - `semantic-metadata.md`
   - `proposal.md`
   - `prompt.md`
6. Review the artifacts for completeness and consistency.
7. Call `save_ingestion_artifacts()` with all required arguments.
8. Wait for the tool result, then stop and report the proposal ID and returned artifact keys.

Do not read the entire source file. Stop inspecting once you have enough information to understand its format and generate the artifacts.

For every discovered file, you MUST call `save_ingestion_artifacts()` with all five artifacts. Do not finish after merely describing them.

If saving fails, report the returned error and do not claim success.

## Generated loader contract

`source_loader.py` must define:

```python
def load_data(spark, source_path):
    ...
```

It must return a Spark DataFrame containing the parsed and transformed source data.

The loader must not create or stop a Spark session, write data, access Polaris, or manage Iceberg tables. The application handles these operations separately.

## Dependencies

Include a `dependencies` array in `profile.json` containing only the names of third-party Python packages required by the loader.

Example:

```json
"dependencies": ["astropy", "numpy"]
```

Do not include versions, reasons, installation commands, URLs, or standard-library modules. Use an empty array if no additional packages are required.

Do not install dependencies or execute generated code.

## Execution boundary

This workflow only generates and saves artifacts. Never execute the generated loader, submit a Spark job, or create or modify Iceberg tables.