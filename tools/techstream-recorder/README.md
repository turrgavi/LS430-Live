# Techstream recorder (LS430 Logger)

A 32-bit Windows J2534 "pass-through" driver that sits between Techstream and
the real Mini VCI driver. Every call goes straight to the real driver
unchanged; a copy of what was sent and received is written to
`C:\LS430Logger\logs\capture-*.txt`. It sends nothing of its own.

We use these recordings to learn the Toyota commands behind active tests,
customization and the enhanced Data List, then add them to LS430 Live.

- `LS430-Logger.exe`: menu with Install / Take notes / Open recordings / Uninstall.
  Install copies the Mini VCI's registry entry to a new device called
  "LS430 Logger (records Mini VCI)" and points it at `ls430_logger.dll`.
  The Mini VCI's own entry is not touched.
- Build: `sh build.sh` (needs `i686-w64-mingw32-gcc`, e.g. `apt install gcc-mingw-w64-i686`).

Recordings can contain your VIN. Don't commit them to this public repo.
