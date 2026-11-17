#!/bin/sh
# Runs on Windows XP SP2 and later (no Vista+ APIs; subsystem 5.1). Builds the 32-bit Techstream recorder with MinGW (Linux or macOS with mingw-w64).
set -e
cd "$(dirname "$0")"
CC=${CC:-i686-w64-mingw32-gcc}
WINDRES=${WINDRES:-i686-w64-mingw32-windres}
mkdir -p dist
$CC -O2 -Wall -shared -o ls430_logger.dll logger.c logger.def -static-libgcc -Wl,--kill-at,--enable-stdcall-fixup,--major-subsystem-version,5,--minor-subsystem-version,1 -s
$WINDRES setup.rc -O coff -o setup.res
$CC -O2 -Wall -o dist/LS430-Logger.exe setup.c setup.res -static-libgcc -s -Wl,--major-subsystem-version,5,--minor-subsystem-version,1 -ladvapi32 -lshell32
cp ls430_logger.dll dist/
rm -f setup.res
echo "Built dist/LS430-Logger.exe (installer, contains the DLL) and dist/ls430_logger.dll"
