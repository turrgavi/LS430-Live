# LS430 Live (prototype)

Diagnostics for a 2002 Lexus LS430: live engine data with recording, trouble
codes with plain-English meanings, a cable self-test, and clearing codes.

**Safety model.** Reading is always allowed. The only action that changes the
car is clearing trouble codes, which needs: write lock switched off, engine
off with ignition on (the app checks RPM and speed), and typing CLEAR. Each
write is logged to `logs/actions.log` and the lock re-engages afterwards.
Active tests and body customization pages are ready but stay empty until real
commands are recorded from Techstream. ECU reprogramming is not supported.

**Test cable** (sidebar) checks the Mini VCI without talking to the car: driver
present, USB device present, the cable's secure handshake, and that a K-line
channel opens. The Mini VCI cannot measure battery voltage (it reports a
fixed 12 V), so that isn't shown.

## Install the Mac app
Download `LS430-Live-mac.zip` from this repo's **Releases** page, unzip it, and
drag **LS430 Live** into Applications. The first time, right-click it and choose
Open (macOS warns about apps from outside the App Store; if it still refuses,
go to System Settings > Privacy & Security and click "Open Anyway"). The Mini VCI
driver is built in. Recordings are saved to `~/Documents/LS430 Live/logs`.
When a newer version is published, the app shows a "Download update" banner.

Developers: `bash packaging/build_mac.sh` builds and installs the app locally;
pushing a tag like `v0.4.1` builds a release on GitHub automatically.

## Running from source
Double-click **`LS430 Live.command`** in Finder (first time: right-click it, choose
Open, then Open again, because macOS asks about files from the internet).
If macOS says you "do not have appropriate access privileges", open Terminal,
type `chmod +x ` (with a space), drag the file onto the Terminal window, press
Enter, and double-click it again. Or just run `python3 app.py` in this folder.
A dashboard opens in your browser with a Connect button, live tiles with
mini-graphs, a big chart (click any tile to chart it), and a Record button that
saves the drive to `logs/`. Close the Terminal window to quit.
You can also start it with `python3 app.py`. It needs nothing extra installed.
For the Mini VCI, the app shows a **Build driver now** button the first time;
click it once (or run `bash setup_mac.sh` yourself, section 2 below).

## Files
- `app.py` + `ls430live/dashboard.html`: the dashboard app
- `live.py`: the same thing as a terminal program
- `setup_mac.sh`: one-time build of the open-source Mini VCI driver for macOS
- `ls430live/transports.py`: ways to reach the car (Mini VCI, ELM/STN adapter, simulator)
- `ls430live/j2534.py`: small wrapper around a J2534 driver (rmvci on Mac, vendor DLL on Windows)
- `ls430live/obd.py`: the list of values and their formulas
- `plot_log.R`: plots a CSV log in R

## 1. Try it with no car
```
python3 live.py --transport sim --seconds 30
Rscript plot_log.R          # plots the newest log
```

## 2. Mini VCI on macOS (no VM needed)
Uses [rmvci](https://github.com/FletcherD/rmvci), an open-source, reverse-engineered
driver for the Mini VCI (GPL-3.0). macOS already includes a driver for the
cable's USB chip, so nothing gets installed at the system level.

One-time setup, in Terminal, inside this folder:
```
bash setup_mac.sh
```
It installs Apple's command-line tools and Rust if missing (you may need to run
it twice: once for Apple's installer, once to build), then builds
`librmvci_j2534.dylib` here.

Then each time:
1. **Quit the Windows VM** (or disconnect the cable from it) so the Mac owns the cable.
2. Plug the Mini VCI into the Mac and the car. `ls /dev/cu.usbserial-*` should show one entry.
3. Ignition ON (engine running or not).
4. `python3 live.py --transport mvci --seconds 60`

It tries ISO 9141-2 first, then two KWP2000 variants, and prints which one the
ECU answered. If something fails, rerun with driver logging and send the output:
```
RUST_LOG=rmvci_core=debug python3 live.py --transport mvci --seconds 20
```

Note: on the Mini VCI the battery-voltage and firmware-version readings are
fixed values in the cable's firmware, so the program doesn't show them.

## 3. Mini VCI on Windows (vendor driver)
32-bit Python required: install "Windows installer (32-bit)" Python 3.11, close
Techstream, then `py -3.11-32 live.py --transport mvci --seconds 60`.

## 4. ELM327/STN adapters (e.g. OBDLink)
```
pip3 install pyserial
python3 live.py --transport elm --port /dev/cu.usbserial-XXXX
```

## Third-party code
The Mac app bundles the Mini VCI driver from
[rmvci](https://github.com/FletcherD/rmvci) (GPL-3.0-or-later), built from
commit `618491b`. Its source is available at that link.
