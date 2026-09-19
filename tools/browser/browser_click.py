from tools.base import Tool
from tools.browser.session import SESSION
import time

def browser_click(selector: str) -> str:
    if not SESSION.page:
        return "Browser not opened"

    SESSION.page.click(selector)
    SESSION.page.wait_for_load_state("networkidle")
    time.sleep(1)

    return f"Clicked {selector}"

browser_click_tool = Tool(
    name="browser_click",
    description="Click an element using CSS selector",
    args_schema={"selector": "string"},
    func=browser_click
)
