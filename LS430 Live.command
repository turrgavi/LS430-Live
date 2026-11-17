#!/bin/bash
# Double-click this in Finder to start LS430 Live. Close the Terminal window to quit.
cd "$(dirname "$0")"
exec python3 app.py
