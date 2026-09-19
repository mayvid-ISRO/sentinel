from tools.base import Tool
from tools.browser.session import SESSION
import time

def browser_open(url: str) -> str:
    SESSION.start()

    SESSION.page.goto(url)
    SESSION.page.wait_for_load_state("networkidle")
    time.sleep(1)

    return f"Opened {url}"

browser_open_tool = Tool(
    name="browser_open",
    description="Open a website URL in browser",
    args_schema={"url": "string"},
    func=browser_open
)
