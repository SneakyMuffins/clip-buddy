"""Start Clip Buddy: `python -m clipbuddy` or the `clipbuddy` command."""
import json
import os
import socket
import threading
import urllib.request
import webbrowser

import uvicorn

from . import __version__
from .config import CFG, ROOT


def _running(url):
    """The copy already answering on our port, if any: its version and folder."""
    try:
        with urllib.request.urlopen(url + "/api/setup", timeout=2) as r:
            return json.loads(r.read())
    except Exception:
        return None


def main():
    url = f"http://localhost:{CFG['port']}"
    # Take the port before anything else. Only the copy that owns it starts the background work, so a second
    # launch can never reset or repeat the jobs of the copy that is running.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", CFG["port"]))
    except OSError:
        sock.close()
        other = _running(url)
        if other and other.get("version") == __version__ and os.path.normcase(other.get("app", "")) == os.path.normcase(str(ROOT)):
            print(f"Clip Buddy is already running at {url}; opening it.")
            if not os.environ.get("CLIPBUDDY_NO_BROWSER"):
                webbrowser.open(url)
            return
        if other:
            print(f"Another Clip Buddy (version {other.get('version', 'older')}, in {other.get('app', 'another folder')}) "
                  f"is already running.\nClose its window first, then start this one again.")
        else:
            print(f"Port {CFG['port']} is in use by another program. Set a different \"port\" in config.json.")
        if not os.environ.get("CLIPBUDDY_NO_BROWSER"):
            try:
                input("Press Enter to close.")
            except (EOFError, OSError):
                pass
        raise SystemExit(1)
    if not os.environ.get("CLIPBUDDY_NO_BROWSER"):
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Clip Buddy {__version__} running at {url}  (close this window or press Ctrl+C to stop)")
    uvicorn.Server(uvicorn.Config("clipbuddy.server:app", log_level="warning")).run(sockets=[sock])


if __name__ == "__main__":
    main()
