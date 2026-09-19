from tools.base import Tool
from tools.browser.session import SESSION
import time

def browser_type(selector: str, text: str) -> str:
    if not SESSION.page:
        return "Browser is not Opened"

    SESSION.page.fill(selector, text)
    time.sleep(1)

    return f"Typed '{text}' in {selector}"

browser_type_tool = Tool(
    name="browser_type",
    description="Type text into an input field using CSS selector",
    args_schema={
        "selector": "string",
        "text": "string"
    },
    func=browser_type
)