from tools.base import Tool
from tools.browser.session import SESSION

def browser_extract(selector: str) -> str:
    if not SESSION.page:
        return "Browser is not Opened"

    text = SESSION.page.inner_text(selector)
    return text

browser_extract_tool = Tool(
    name="browser_extract",
    description="Extract text from a webpage element",
    args_schema={"selector": "string"},
    func=browser_extract
)