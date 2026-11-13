"""Check the API, served frontend assets, and the development API proxy."""

from html.parser import HTMLParser
import json
import os
from urllib.parse import urljoin
from urllib.request import urlopen


class Frontend(HTMLParser):
    def __init__(self):
        super().__init__()
        self.has_app = False
        self.scripts = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id") == "app":
            self.has_app = True
        if tag == "script" and attrs.get("type") == "module" and attrs.get("src"):
            self.scripts.append(attrs["src"])


client_url = os.environ["CLIENT_URL"]
api_url = os.environ.get("API_URL", "http://127.0.0.1:8000")
for base in dict.fromkeys((api_url, client_url)):
    with urlopen(base + "/api/v1/health", timeout=10) as response:
        assert json.load(response)["status"] == "ok", f"API health failed at {base}"
    print(f"API healthy at {base}")

with urlopen(client_url, timeout=10) as response:
    frontend = Frontend()
    frontend.feed(response.read().decode())
assert frontend.has_app and frontend.scripts, "Frontend entry point is missing"
for script in frontend.scripts:
    with urlopen(urljoin(client_url, script), timeout=10) as response:
        assert "javascript" in response.headers.get("Content-Type", ""), script
        assert response.read(), f"Empty frontend script: {script}"
print(f"Frontend and module scripts healthy at {client_url}")
