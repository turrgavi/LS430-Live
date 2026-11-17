#!/usr/bin/env python3
"""LS430 Live desktop app: the dashboard in its own native window.

This is the entry point of the macOS .app. It starts its own local server on
a free port (so an older copy that is still running can never be shown by
mistake) and opens the dashboard in a native window (WebKit via pywebview).
Without pywebview installed it falls back to the web browser.
"""
import sys
import time
import webbrowser

import app
from ls430live.version import VERSION


def main():
    server, url = app.start_server(port=0)
    try:
        import webview
    except ImportError:
        webbrowser.open(url)
        print(f"pywebview not installed; opened {url} in the browser. Ctrl+C to quit.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        webview.create_window(f"LS430 Live {VERSION}", url, width=1280, height=840,
                              min_size=(900, 600), background_color="#1e1e1e")
        webview.start(private_mode=False)
    app.ENGINE.disconnect()   # closing the window ends the car session cleanly
    time.sleep(0.8)
    server.shutdown()
    sys.exit(0)


if __name__ == "__main__":
    main()
