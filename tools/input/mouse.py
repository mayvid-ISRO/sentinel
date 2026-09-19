import pyautogui
from tools.base import Tool

def click(x: int, y: int) -> str:
    pyautogui.click(x, y)
    return f"Clicked at ({x},{y})"

click_tool = Tool(
    name="click_tool",
    description="Click at screen coordinates",
    args_schema={"x": "int", "y": "int"},
    func=click
)
