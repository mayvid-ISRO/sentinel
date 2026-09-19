from tools import TOOLS

def build_prompt(user_query: str, steps_plan: str, knowledge_context: str = "") -> str:
    ctx_section = ""
    if knowledge_context:
        ctx_section = (
            "\n--- RELEVANT KNOWLEDGE BASE EXCERPTS ---\n"
            + knowledge_context
            + "\nUse this context when responding. Cite the source file when relevant.\n"
        )
    return f"""
{ctx_section}You are an AI agent controlling a WINDOWS desktop.

You can ONLY respond in JSON.

Available actions:
{{
{', '.join(f'    "{name}": {func.args_schema}' for name, func in TOOLS.items())}
}}

CRITICAL RULES:
1. You MUST respond ONLY in valid JSON format
2. NO markdown code blocks (no ```json```)
3. NO explanations outside the JSON
4. Execute ONE action at a time
5. Wait for observation before next action

Use this JSON format to answer:
{{
  "tool": "<tool_name>",
  "args": {{ ... }}
}}

If the task is complete, respond below to stop:

{{
  "tool": "done",
  "args": {{}}
}}

Goal : {user_query}

Just for better understanding you can refer them just as example but don't blindly follow them: {steps_plan}

"""


# print(build_prompt("Open notepad and type 'Hello World'"))

