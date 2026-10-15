Discover and process every new source file reported by check_for_new_files().
For each file in the returned new_files list:
1. Use the exact bucket and object key returned by discovery.
2. Inspect the file with read_file(), starting at byte offset 0.
3. Examine the raw content and decide whether additional bounded reads are needed. Use next_start_byte for subsequent reads and stay within the tool's per-call byte limit. Do not read a large file completely unless there is a clear reason.
4. Infer the file structure and likely astronomical meaning from the available evidence. Distinguish direct observations from hypotheses, include confidence and unresolved questions, and do not invent units or coordinate conventions.
5. Prepare profile.json, prompt.md, proposal.md, semantic-metadata.md, and a proposed ingest.py for human review.
6. Save these artifacts using save_ingestion_proposal(). Pass the exact source key, a JSON string for the profile, and the complete content of each artifact.
Process each discovered file independently. If discovery reports no new files, say so and stop.
This is a proposal-only test. Do not execute generated code, create or modify Iceberg tables, copy files into the raw archive, update ingestion state, or call save_ingestion_artifacts(). Only report artifacts as saved after the save tool confirms success. In the final response, summarize the files processed, the main findings and uncertainties, and the exact artifact locations returned by the tool.

For every discovered file, you MUST call
save_ingestion_proposal() with all five artifacts.

Do not finish after describing the artifacts or saying you will save
them. Wait for the tool result.

If saving fails, report the returned error and do not claim success.
In your final response, list the proposal ID and artifact keys returned
by the tool for each successfully saved file.