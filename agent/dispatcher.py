from tools import TOOLS

def dispatch(action: dict):
    tool_name = action["tool"]
    args = action.get("args", {})
    
    # print(tool_name, list(TOOLS.keys()))
    # print(action)
    # print(args)

    if tool_name not in TOOLS:
        return f"Unknown tool: {tool_name}"

    return TOOLS[tool_name].run(args)

