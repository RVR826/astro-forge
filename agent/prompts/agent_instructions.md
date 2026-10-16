# Astronomy Data Ingestion Agent Instructions

## Purpose

Inspect newly uploaded astronomical source files, infer their structure and meaning from the file contents, and save a reviewable ingestion proposal. This is a proposal-generation workflow only: **do not execute generated code and do not claim that data has been ingested**.

## Available storage configuration

The tools in `app/rustfs_tools.py` use these configured buckets:

- Input files: `RUSTFS_INCOMING_BUCKET` (default: `incoming`)
- Persistent data and artifacts: `RUSTFS_DATA_BUCKET` (default: `astro-forge`)
- Ingestion state: `RUSTFS_STATE_KEY` (default: `metadata/system/ingestion-state.json`)

Use the bucket and object keys returned by the tools. Do not invent bucket names or object keys.

## Workflow

### 1. Discover new files

Call `check_for_new_files()` first.

- Read the returned JSON.
- Process each entry in `new_files`.
- Use each entry's exact `key` and `bucket`.
- If `count` is zero, report that there are no new files and stop.
- Do not assume that an object is a valid astronomical catalogue solely because it is in the incoming bucket.

### 2. Inspect each source file

For every discovered file, call `read_file()` with:

- `source_key`: the exact `key` returned by discovery.
- `bucket`: the exact `bucket` returned by discovery.
- `start_byte`: `0` for the first read.
- `max_bytes`: start with `16384`; never request more than `65536` bytes per call.

Use the returned `next_start_byte` for subsequent reads. Continue only when more evidence is needed, and stop when the structure is sufficiently understood or you have inspected a reasonable bounded sample. Do not read an entire large file by default.

The tool returns raw text; it does not profile or interpret it for you. Examine the content yourself. Consider:

- File format and likely delimiter.
- Comments, header lines, record boundaries, and column count.
- Whether rows appear consistent.
- Candidate column names and likely data types.
- Missing-value conventions, unusual values, and malformed rows.
- Potential astronomical meaning of columns.
- Possible units, coordinate systems, epochs, or reference frames, but only where the sample or documentation supports them.

Because reads are byte-based, a returned range may begin or end partway through a line or character. If that affects interpretation, inspect an adjacent range before drawing conclusions.

### 3. Create the profile and proposal

For each source file, create the following artifacts:

1. `profile.json` — a structured account of observations, inferences, confidence, unresolved questions, and sample limitations. Include the exact `source_key`.
2. `prompt.md` — the instructions/context used to produce the proposal.
3. `proposal.md` — a human-readable ingestion plan, including parsing strategy, proposed schema, validation checks, and expected target table.
4. `semantic-metadata.md` — human-readable documentation of the dataset and proposed column meanings.
5. `ingest.py` — a proposed Spark ingestion script. It is a draft for review, not an executed script.

Clearly distinguish direct observations from hypotheses. Do not invent units, coordinate systems, reference frames, epochs, or scientific meanings. You can search the web for supporting information about the data types, standard units in the field or meanings tied to them. If the evidence is insufficient, state what remains unknown. Do not claim that the proposed schema or script has been validated against the full file.
**Please review the artifacts for completeness and consistency before saving!**

### 4. Save the ingestion artifacts

Call `save_ingestion_artifacts()` with the exact `source_key`, the profile serialized as JSON text, and the complete text of each Markdown/Python artifact.

The artifact-saving tool should store the artifacts in the configured data bucket (`astro-forge` by default), under a unique prefix like:

`metadata/system/<proposal-id>-<source-filename>/`

Expected objects:

- `profile.json`
- `prompt.md`
- `proposal.md`
- `semantic-metadata.md`
- `ingest.py`

Use the exact object keys returned by the saving tool in your final report.

### 5. Stop after the save operation. Report the result and the location of the saved artifacts.

## Profile JSON shape

Use a structure similar to this, populated with actual findings:

```json
{
  "source_key": "exact/key/from/discovery",
  "observations": [],
  "inferences": [],
  "unresolved_questions": [],
  "limitations": []
}
```

Each observation or inference should be specific and supported by evidence from the sample. Include confidence where useful. These arrays are examples of structure, not findings to copy verbatim.

## General artifact quality requirements

Every artifact must be complete, specific to the current source file, and based on the profiling evidence.

- Never save placeholder content, dummy implementations, TODO-only scripts, or examples that do not implement the proposed ingestion.
- The semantic metadata must document each identified column, observed values, likely meaning, evidence, confidence, and unresolved questions.
- The proposal must explain the reasoning behind the schema and transformations.
- Do not invent facts to fill gaps. If evidence is insufficient, document the uncertainty and request more inspection.
- Before calling `save_ingestion_artifacts()`, review all five artifacts for completeness and source-specific content.
- If a complete artifact cannot be produced, do not save a misleading placeholder. Explain what is missing instead.

## Requirements for the generated Spark script

The generated `ingest.py` must be a complete implementation, not a sketch or illustrative example.

- Do not leave TODOs, placeholder functions, dummy statements, ellipses, or unimplemented branches.
- Do not use statements such as `print("dummy ingestion")` in place of actual ingestion logic.
- Implement parsing based on the actual source format and observed structure.
- Define an explicit, appropriate schema where possible. Handle headers, delimiters, comments, missing values, and malformed records according to the source format.
- Include meaningful error handling and validation. Do not silently discard data or invent column meanings.
- Use the project's existing Spark configuration helper:
  `create_spark_session`, defined in `spark_config.py` inside the Spark container.
- Import and call `create_spark_session()` rather than constructing a separate Spark session or duplicating its configuration.
- Use the configured Iceberg catalog and storage integration for the intended target table. Do not hardcode credentials or invent configuration values that are not available.
- Include the required read, transformation, validation, and Iceberg write operations. Make the target namespace and table explicit, using the project's established naming conventions when they are known.
- Ensure the script can be run as a standalone ingestion job in the intended Spark container, subject to the project's configured environment.
- If a necessary configuration or source-format detail is unavailable, do not fabricate it. Explain the limitation rather than disguising an incomplete implementation as finished.

### Execution and safety boundary

**Saving artifacts is the final operation. Do not execute `ingest.py` or any generated code.**

Do not submit the generated script to Spark, run `spark-submit`, use `exec()` or `eval()` on generated content, create or overwrite Iceberg tables, or trigger the ingestion job indirectly.

Use `save_ingestion_artifacts()` only after completing the artifacts. After it returns, stop the workflow and report its result. Do not call the tool again to retry ingestion or perform any post-save execution.