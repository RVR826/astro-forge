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

Clearly distinguish direct observations from hypotheses. Do not invent units, coordinate systems, reference frames, epochs, or scientific meanings. If the evidence is insufficient, state what remains unknown. Do not claim that the proposed schema or script has been validated against the full file.

### 4. Save the proposal artifacts

Call `save_ingestion_proposal()` with the exact `source_key`, the profile serialized as JSON text, and the complete text of each Markdown/Python artifact.

The proposal-saving tool should store the artifacts in the configured data bucket (`astro-forge` by default), under a unique prefix like:

`metadata/proposals/<proposal-id>-<source-filename>/`

Expected objects:

- `profile.json`
- `prompt.md`
- `proposal.md`
- `semantic-metadata.md`
- `ingest.py`

Use the exact object keys returned by the saving tool in your final report.

**Important:** the current `save_ingestion_artifacts()` tool is a finalization tool, not a proposal-saving tool. It copies the source to `raw/`, updates ingestion state to `success`, and is documented for use only after a validated Spark ingestion. Do not call it during this proposal-only workflow. Use a separate `save_ingestion_proposal()` tool for the review artifacts.

### 5. Safety and reporting

- Never execute `ingest.py`.
- Never create or modify Iceberg tables.
- Never update ingestion state or mark a source as successfully ingested.
- Never call `save_ingestion_artifacts()` in this workflow.
- Do not overwrite or delete the incoming source file.
- If a tool fails, report the error and do not claim the artifacts were saved.
- In the final response, summarize each file inspected, what was inferred, any important uncertainty, and the saved artifact locations.

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