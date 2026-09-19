from tools.base import Tool
from tools.browser.session import SESSION

def browser_dom() -> str:
    if not SESSION.page:
        return "Browser is not Opened"

    return SESSION.page.content()

browser_dom_tool = Tool(
    name="browser_dom",
    description="Get full HTML content of current page",
    args_schema={},
    func=browser_dom
)