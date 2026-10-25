"""Start Clip Buddy: `python -m clipbuddy` or the `clipbuddy` command."""
import os
import threading
import webbrowser

import uvicorn

from .config import CFG


def main():
    url = f"http://localhost:{CFG['port']}"
    if not os.environ.get("CLIPBUDDY_NO_BROWSER"):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Clip Buddy running at {url}  (close this window or press Ctrl+C to stop)")
    uvicorn.run("clipbuddy.server:app", host="127.0.0.1", port=CFG["port"], log_level="warning")


if __name__ == "__main__":
    main()
