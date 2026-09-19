from tools import TOOLS


def build_pre_prompt(task: str, knowledge_context: str = "") -> str:
    ctx_section = ""
    if knowledge_context:
        ctx_section = (
            "\n--- RELEVANT KNOWLEDGE BASE EXCERPTS ---\n"
            + knowledge_context
            + "\nUse this context while planning your approach.\n"
        )
    return f"""
{ctx_section}You are an AI agent helping with the following task:
{task}

Available actions:
{{
{', '.join(f'    "{name}": {func.args_schema}' for name, func in TOOLS.items())}
}}

Answer with an array containing the smaller tasks needed to complete the main task.

example:
Input: "Open notepad, type HELLO, realize it's wrong, replace it with HI."

You Have to strictly answer in the below format only nothing else:
output    [
"Open notepad",
"Type HELLO in that opened notepad",
"Realize it's wrong",
"Clear the typing from that opened notepad",
"Type HI"
]

Please note that use exact tools or application names eg use "cmd" not "terminal"
"""
