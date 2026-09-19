from tools.base import Tool
from tools.browser.session import SESSION

def browser_scroll() -> str:
    if not SESSION.page:
        return "Browser is not Opened"
    SESSION.page.mouse.wheel(0, 2000)
    return "Scrolled down"

browser_scroll_tool = Tool(
    name="browser_scroll",
    description="Scroll the webpage",
    args_schema={},
    func=browser_scroll
)
