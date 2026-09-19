import pyautogui
from tools.base import Tool

def type_text(text: str):
    pyautogui.write(text, interval=0.05)
    # pyautogui.write(" ", interval=0.05)
    return f"Typed: {text}"

type_tool = Tool(
    name="type_tool",
    description="Type text using keyboard",
    args_schema={"text": "string"},
    func=type_text
)
