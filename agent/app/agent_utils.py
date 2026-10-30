import json
from pathlib import Path
from agents import (
    Runner,
)
from openai.types.responses import ResponseTextDeltaEvent


def format_arguments(arguments, shorten=False, max_length=120):
    """Format tool arguments, optionally shortening long parameter values."""
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (json.JSONDecodeError, TypeError):
        args = arguments

    if shorten and isinstance(args, dict):
        args = {
            key: (
                "-content-"
                if isinstance(value, (str, list, dict))
                and len(json.dumps(value, ensure_ascii=False)) > max_length
                else value
            )
            for key, value in args.items()
        }

    return json.dumps(args, indent=2, ensure_ascii=False, default=str)


def get_tool_status(output):
    """Determine success from a tool's structured result."""
    try:
        result = json.loads(output) if isinstance(output, str) else output
    except (json.JSONDecodeError, TypeError):
        result = None

    if isinstance(result, dict):
        if result.get("ok") is False or result.get("success") is False:
            return "FAILED"
        else:
            return "SUCCESS"

    # Fallback for tools that return plain error strings.
    if isinstance(output, str) and output.lstrip().lower().startswith(
        ("error:", "exception:", "failed:")
    ):
        return "FAILED"

    return "SUCCESS"

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

async def run_agent(agent, prompt, verbosity_level = 1):
    result = Runner.run_streamed(
        agent,
        prompt,
        max_turns=50,
    )

    # Level 0: wait for the run and print only its final output.
    if verbosity_level < 1:
        async for _ in result.stream_events():
            pass

        print(result.final_output)
        return result

    streamed_text = False

    async for event in result.stream_events():

        # Model-generated text / progress messages
        if event.type == "raw_response_event":
            if isinstance(event.data, ResponseTextDeltaEvent):
                if verbosity_level >= 1:
                    print(event.data.delta, end="", flush=True)
                    streamed_text = True

        elif event.type == "run_item_stream_event":

            # Tool call
            if event.name == "tool_called":
                tool_call = event.item.raw_item
                tool_name = tool_call.name
                arguments = tool_call.arguments

                if verbosity_level >= 1:
                    print(f"\n\n[TOOL CALL] {tool_name}")
                    print(
                        "Arguments:\n"
                        + format_arguments(
                            arguments,
                            shorten=(verbosity_level == 1),
                        )
                    )

            # Tool result
            elif event.name == "tool_output":
                output = event.item.output

                if verbosity_level >= 1:
                    status = get_tool_status(output)
                    print(f"\n[TOOL {status}]")

                if verbosity_level >= 3:
                    print(f"Output: {output}")

            # Handoff
            elif event.name == "handoff_requested":
                if verbosity_level >= 3:
                    print("\n[HANDOFF REQUESTED]")

            elif event.name == "handoff_occured":
                if verbosity_level >= 3:
                    print("\n[HANDOFF OCCURRED]")

            # Other events
            elif verbosity_level >= 3:
                print(f"\n[EVENT] {event.name}: {event.item}")

    # Final output
    if verbosity_level >= 3 or not streamed_text:
        print("\n\n[FINAL OUTPUT]")
        print(result.final_output)
    else:
        print()  # Finish the streamed text line.

    return result