Discover and process every new source file reported by check_for_new_files(). Process **one source file per run**. For each file in the returned new_files list:
1. Select one unprocessed source file and inspect its contents using `read_file()`. Read additional chunks only when necessary to understand the format and schema.
2. Profile the source using the observed contents. Infer its format, structure, column names, data types, likely meanings, missing values, and parsing requirements. Distinguish observed facts from assumptions.
3. Generate all required artifacts with complete, source-specific content.
4. Review the artifacts for completeness and consistency.
5. Call `save_ingestion_artifacts()` to save the completed artifacts and archive the source according to the tool's contract.
6. Stop after the save operation. Report the result and the location of the saved artifacts.

For every discovered file, you MUST call
save_ingestion_artifacts() with all five artifacts.

Do not finish after describing the artifacts or saying you will save
them. Wait for the tool result.

If saving fails, report the returned error and do not claim success.
In your final response, list the proposal ID and artifact keys returned
by the tool for each successfully saved file.