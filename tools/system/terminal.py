import subprocess
from tools.base import Tool
from tools.safety import is_safe

def run_terminal_command(command: str) -> str:
    if not is_safe(command):
        return "Error: Command is blocked for safety reasons."

    # print(command)
    
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True,timeout=10)
        if result.returncode == 0:
            return result.stdout
        else:
            return f"Error: {result.stderr}"
    except subprocess.TimeoutExpired:
        return "Error: Command timed out."
    
run_terminal_command_tool = Tool(
    name="run_terminal_command_tool",
    description="Run a terminal command safely",
    args_schema={"command": "string"},
    func=run_terminal_command
)
    
