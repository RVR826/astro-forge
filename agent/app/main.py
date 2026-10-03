import asyncio

from agents import Agent, Runner

from app.rustfs_tools import (
    check_for_new_files,
    finalize_ingestion,
)


agent = Agent(
    name="Astronomy Data Ingestion Agent",
    instructions="""
You are an autonomous data ingestion agent for the Astro Forge
astronomical data lake.

Your responsibility is to manage the ingestion of heterogeneous
astronomical source files into Iceberg tables.

The data lake consists of:

- RustFS: object storage
- Polaris: Iceberg catalog
- Spark: data ingestion and transformation
- Iceberg: queryable table format
- Semantic metadata: YAML/JSON documentation stored in RustFS

Available RustFS tools allow you to:

- discover new files waiting in the incoming area
- finalize a successful ingestion by preserving the source file
  and generated artifacts

Follow the ingestion workflow carefully.

Do not modify or ingest data unless explicitly instructed.
Do not mark an ingestion as successful unless the ingestion and
resulting Iceberg table have been successfully validated.
""",
    tools=[
        check_for_new_files,
        finalize_ingestion,
    ],
)


async def main() -> None:
    result = await Runner.run(
        agent,
        "Check whether there are any new files waiting for ingestion.",
    )

    print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())