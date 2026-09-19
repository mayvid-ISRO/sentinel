from tools.base import Tool
from tools.browser.session import SESSION

def browser_close() -> str:
    if not SESSION.browser:
        return "Browser is already closed"

    SESSION.close()
    return "Browser closed"

browser_close_tool = Tool(
    name="browser_close",
    description="Close the browser",
    args_schema={},
    func=browser_close
)