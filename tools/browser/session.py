from playwright.sync_api import sync_playwright


# CHROME_PATH = r"C:\Users\Admin\AppData\Local\ms-playwright\chromium-1124\chrome-win\chrome.exe"
CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

class BrowserSession:

    def __init__(self):
        self.playwright= None
        self.browser = None
        self.page = None
    
    def start(self):
        if self.browser is None:
            self.playwright = sync_playwright().start()

            self.browser = self.playwright.chromium.launch(
                executable_path=CHROME_PATH,
                headless=False
            )

            self.page = self.browser.new_page()

    def close(self):

        if self.browser:
            self.browser.close()
            self.playwright.stop()

            self.browser=None
            self.page = None

SESSION = BrowserSession()


