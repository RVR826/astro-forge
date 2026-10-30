import asyncio
import app.agent_utils as utils
from openai import AsyncOpenAI
from agents import (
    Agent,
    set_tracing_disabled,
)
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai.types.responses import ResponseTextDeltaEvent
from app.rustfs_tools import (
    check_for_new_files,
    read_file,
    save_ingestion_artifacts,
)

def setup_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url="http://host.docker.internal:8080/v1",
        api_key="dummy_key"
    )

def setup_agent() -> Agent:
    client = setup_client()
    model = OpenAIChatCompletionsModel(
        model="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:Q8_0",
        openai_client=client
    )

    # Avoid sending traces to OpenAI when using an external provider.
    set_tracing_disabled(True)

    return Agent(
        name="Astronomy Data Ingestion Agent",
        model=model,
        instructions=utils.load_agent_instructions(),
        tools=[
            check_for_new_files,
            read_file,
            save_ingestion_artifacts,
        ],
    )


async def main() -> None:
    agent = setup_agent()
    await utils.run_agent(agent, utils.load_prompt("ingestion_artifacts_test"))

if __name__ == "__main__":
    asyncio.run(main())