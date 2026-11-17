#!/usr/bin/env python3
"""LS430 Live desktop app: the dashboard in its own native window.

This is the entry point of the macOS .app. It starts the local server and
shows the dashboard in a native window (WebKit via pywebview). Without
pywebview installed it falls back to the web browser.
"""
import socket
import sys
import time
import webbrowser

import app


def already_running():
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", app.PORT)) == 0


def main():
    url = f"http://127.0.0.1:{app.PORT}/"
    server = None
    if not already_running():
        server, url = app.start_server()
    try:
        import webview
    except ImportError:
        webbrowser.open(url)
        print("pywebview not installed; opened in the browser. Ctrl+C to quit.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    else:
        webview.create_window("LS430 Live", url, width=1360, height=880, min_size=(900, 600),
                              background_color="#0b1016")
        webview.start()
    if server is not None:
        app.ENGINE.disconnect()   # closing the window ends the car session cleanly
        time.sleep(0.8)
        server.shutdown()
    sys.exit(0)


if __name__ == "__main__":
    main()
