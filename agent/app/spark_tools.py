import ast
import textwrap
from pathlib import Path


SPARK_TEMPLATE_PATH = Path(__file__).with_name("spark_template.py")

SOURCE_LOGIC_MARKER = (
    "        # [SOURCE-SPECIFIC LOGIC GENERATED GOES HERE]"
)

TARGET_TABLE_MARKER = '"__TARGET_TABLE__"'


def build_ingestion_script(
    source_specific_logic: str,
    target_table: str,
) -> str:
    """
    Assemble the final Spark ingestion script from the fixed
    application template and LLM-generated source-specific logic.

    The generated logic is never executed here.
    """

    if not source_specific_logic.strip():
        raise ValueError("Generated source-specific logic is empty.")

    if not target_table.strip():
        raise ValueError("Target table cannot be empty.")

    template = SPARK_TEMPLATE_PATH.read_text(encoding="utf-8")

    if template.count(SOURCE_LOGIC_MARKER) != 1:
        raise ValueError(
            "Spark template must contain exactly one source logic marker."
        )

    if template.count(TARGET_TABLE_MARKER) != 1:
        raise ValueError(
            "Spark template must contain exactly one target table marker."
        )

    # Remove optional Markdown fences if the model included them.
    logic = source_specific_logic.strip()

    if logic.startswith("```"):
        lines = logic.splitlines()

        if lines[-1].strip() == "```":
            lines = lines[1:-1]

        logic = "\n".join(lines)

    # Normalize indentation and fit the code into the template's try block.
    logic = textwrap.dedent(logic).strip()

    if not logic:
        raise ValueError("Generated source-specific logic is empty.")

    logic = textwrap.indent(logic, "        ")

    # Replace the source-specific logic marker.
    script = template.replace(
        SOURCE_LOGIC_MARKER,
        logic,
    )

    # Safely insert the target table as a Python string literal.
    script = script.replace(
        TARGET_TABLE_MARKER,
        repr(target_table),
    )

    # Validate Python syntax without executing the script.
    try:
        ast.parse(script)
    except SyntaxError as exc:
        raise ValueError(
            f"Generated ingestion script has invalid Python syntax: {exc}"
        ) from exc

    return script