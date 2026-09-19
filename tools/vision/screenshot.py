import pyautogui
from tools.base import Tool

def take_screenshot() -> str:
    img = pyautogui.screenshot()
    path = "screen.png"
    img.save(path)
    return f"Screenshot saved to {path}"

take_screenshot_tool = Tool(
    name="take_screenshot_tool",
    description="Takes a screenshot of the current screen and saves it as screen.png",
    args_schema={},
    func=take_screenshot
)