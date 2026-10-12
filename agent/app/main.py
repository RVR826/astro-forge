import asyncio
import os

from openai import AsyncOpenAI
from agents import (
    Agent,
    Runner,
    set_default_openai_api,
    set_default_openai_client,
    set_tracing_disabled,
)
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from app.rustfs_tools import (
    check_for_new_files,
    finalize_ingestion,
)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL")

def setup_client() -> AsyncOpenAI:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")

    if not OPENROUTER_MODEL:
        raise RuntimeError(
            "OPENROUTER_MODEL is not configured. "
            "Set it to a model ID available through OpenRouter."
        )


    return AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=OPENROUTER_API_KEY,
        default_headers={
            "X-OpenRouter-Title": "Astro Forge Data Lake",
    }
)

def setup_agent() -> Agent:
    client = setup_client()
    model = OpenAIChatCompletionsModel(
        model=OPENROUTER_MODEL,
        openai_client=client
    )

    # Avoid sending traces to OpenAI when using an external provider.
    set_tracing_disabled(True)

    return Agent(
        name="Astronomy Data Ingestion Agent",
        model=model,
        instructions="""
            You are the agent for the Astro Forge astronomical data lake.

            Architecture:
            - RustFS stores source files and artifacts.
            - New source files arrive in the incoming bucket.
            - astro-forge stores preserved raw files, ingestion artifacts,
            semantic metadata, and ingestion state.
            - Apache Polaris manages the Iceberg catalog.
            - Spark performs ingestion and transformation.
            - Trino provides SQL querying.

            Available tools:
            - check_for_new_files: discover files waiting for ingestion.
            - finalize_ingestion: record a completed and validated ingestion
            and preserve its source, prompt, script, and execution log.

            Rules:
            1. Begin by discovering files with check_for_new_files.
            2. Report the discovered files and their metadata.
            3. Do not execute generated code or claim that ingestion succeeded
            unless the ingestion has actually been run and validated.
            4. Do not call finalize_ingestion merely because a file was found.
            It is only for ingestion that has already been validated.
            5. Do not invent table schemas, execution results, or file contents.
            6. If no files are waiting, report that clearly.
        """,
        tools=[
            check_for_new_files
            # finalize_ingestion,
        ],
    )


async def main() -> None:
    print(f"Model: {OPENROUTER_MODEL}")

    agent = setup_agent()
    result = await Runner.run(
        agent,
        "Check for new files waiting in the incoming bucket. "
        "Report the results without ingesting anything.",
    )

    print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())