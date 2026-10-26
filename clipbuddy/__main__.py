"""Start Clip Buddy: `python -m clipbuddy` or the `clipbuddy` command."""
import os
import threading
import urllib.request
import webbrowser

import uvicorn

from .config import CFG


def _already_running(url):
    try:
        with urllib.request.urlopen(url + "/api/setup", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    url = f"http://localhost:{CFG['port']}"
    if _already_running(url):
        # started twice: show the copy that is already running instead of failing on the busy port
        print(f"Clip Buddy is already running at {url}; opening it.")
        if not os.environ.get("CLIPBUDDY_NO_BROWSER"):
            webbrowser.open(url)
        return
    if not os.environ.get("CLIPBUDDY_NO_BROWSER"):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Clip Buddy running at {url}  (close this window or press Ctrl+C to stop)")
    uvicorn.run("clipbuddy.server:app", host="127.0.0.1", port=CFG["port"], log_level="warning")


if __name__ == "__main__":
    main()
