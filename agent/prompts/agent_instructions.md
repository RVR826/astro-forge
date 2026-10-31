# Astronomy Data Ingestion Agent Instructions

## Purpose

Inspect newly uploaded astronomical source files, infer their structure and meaning, and generate reviewable ingestion artifacts.

This is an artifact-generation workflow only. Never execute generated code or claim that data has been ingested.

## Storage

The tools in `app/rustfs_tools.py` use:

- Input bucket: `RUSTFS_INCOMING_BUCKET` (default: `incoming`)
- Data bucket: `RUSTFS_DATA_BUCKET` (default: `astro-forge`)
- Ingestion state: `RUSTFS_STATE_KEY` (default: `metadata/system/ingestion-state.json`)

Always use the exact bucket names and object keys returned by the tools.

## Workflow

### 1. Discover a source file

Call `check_for_new_files()` first.

- Process exactly one source file per run.
- Select one entry from `new_files`.
- Use its exact `key` and `bucket`.
- If no files are returned, report that there are no new files and stop.

### 2. Inspect the source

Use `read_file()` to inspect the source.

- Start at byte offset `0`.
- Start with a read size of `32768` bytes.
- Never request more than `65536` bytes per read.
- Use the returned `next_start_byte` for subsequent reads.
- A maximum of five reads is permitted per source file.

Inspect enough representative content to understand the format, structure, columns, data types, and parsing requirements. Do not read the entire file or continue reading merely to reach its end.

Distinguish observed facts from assumptions. Do not invent units, coordinate systems, epochs, reference frames, or scientific meanings.

### 3. Generate the artifacts

Generate all five artifacts:

1. `profile.json` — observations, inferences, unresolved questions, limitations, and dependencies.
2. `source_loader.py` — source-specific Spark loading and transformation logic.
3. `semantic-metadata.md` — human-readable descriptions of the dataset and its columns.
4. `proposal.md` — ingestion plan, parsing strategy, proposed schema, validation considerations, and target table.
5. `prompt.md` — the context and instructions used to generate the artifacts.

Review all artifacts for completeness and consistency before saving.

### 4. Profile JSON requirements

Include the exact source key and a `dependencies` array.

Example:

```json
{
  "source_key": "exact/key/from/discovery",
  "dependencies": ["astropy", "numpy"],
  "observations": [],
  "inferences": [],
  "unresolved_questions": [],
  "limitations": []
}
```

The `dependencies` array must contain **only third-party Python package names** required by `source_loader.py`.

- Do not include package versions.
- Do not include reasons, installation commands, URLs, or version constraints.
- Do not list Python standard-library modules or built-in PySpark functionality.
- Use an empty array if no additional packages are needed.

### 5. Source loader requirements

Generate `source_loader.py` with exactly this public interface:

```python
def load_data(spark, source_path):
    ...
```

The function must:

- Read and parse the source using the appropriate Spark or Python libraries.
- Apply source-specific transformations and type conversions.
- Handle relevant missing values and parsing issues.
- Return the final Spark DataFrame.

The loader must not:

- Create or stop a Spark session.
- Define another ingestion runner or a main block.
- Write the DataFrame or create/manage Iceberg tables.
- Access RustFS directly or configure Polaris.
- Install packages or execute shell commands.
- Contain TODOs, placeholders, or pseudocode.

Use the provided `spark` session and `source_path` argument. Include all necessary imports in the loader itself.

### 6. Save the artifacts

Call `save_ingestion_artifacts()` with all required arguments:

- `source_key`: exact original source object key.
- `prompt`: exact prompt used for generation.
- `source_loader_script`: complete contents of `source_loader.py`.
- `profile_json`: valid serialized profile JSON.
- `semantic_metadata`: complete Markdown metadata.
- `proposal`: complete Markdown proposal.
- `target_table`: fully qualified target Iceberg table name.

All five artifacts are mandatory.

Wait for the tool result. Use the returned proposal ID and artifact keys in your final report.

## Execution and safety boundary

The application saves and validates the artifacts, but does not execute the generated loader.

Never execute generated code, use `exec()` or `eval()` on generated content, submit Spark jobs, install dependencies, or create or overwrite Iceberg tables.

After `save_ingestion_artifacts()` returns, stop the workflow and report its result. Do not attempt execution or additional processing.