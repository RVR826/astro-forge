import asyncio
import os

from pathlib import Path
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
    read_file,
    save_ingestion_proposal,
    save_ingestion_artifacts,
)

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL")

def load_agent_instructions() -> str:
    instructions_path = (
        Path(__file__).resolve().parent.parent / "prompts" / "agent_instructions.md"
    )
    return instructions_path.read_text(encoding="utf-8")

def load_prompt(prompt_name: str) -> str:
    prompt_path = (
        Path(__file__).resolve().parent.parent / "prompts" / f"{prompt_name}.md"
    )
    return prompt_path.read_text(encoding="utf-8")

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
        instructions=load_agent_instructions(),
        tools=[
            check_for_new_files,
            read_file,
            save_ingestion_proposal,
        ],
    )


async def main() -> None:
    print(f"Model: {OPENROUTER_MODEL}")

    agent = setup_agent()
    result = await Runner.run(
        agent,
        load_prompt("ingestion_artifacts_test"),
    )

    print(result.final_output)


if __name__ == "__main__":
    asyncio.run(main())