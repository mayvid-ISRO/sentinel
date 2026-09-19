import subprocess
from tools.base import Tool
import time

def open_app(app_name: str) -> str:
    # subprocess.Popen(app_name)
    # print(app_name)
    subprocess.Popen(f'start /max {app_name}', shell=True)
    time.sleep(2)  # VERY important for agents
    return f'Opened a new application: {app_name}'

open_app_tool = Tool(
    name="open_app_tool",
    description="Open an application by name",
    args_schema={"app_name": "string"},
    func=open_app
)
