#!/usr/bin/env python3
"""LS430 live data reader (read-only).

Examples:
  python live.py --transport sim                      # no car needed
  python3 live.py --transport mvci                    # Mini VCI on macOS (rmvci driver)
  py -3.11-32 live.py --transport mvci                # Mini VCI on Windows (vendor DLL)
  python3 live.py --transport elm --port /dev/cu.usbserial-XXXX   # ELM/STN adapter
"""
import argparse
import csv
import datetime as dt
import os
import sys
import time

from ls430live import obd
from ls430live.transports import ElmTransport, J2534Transport, SimTransport


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--transport", choices=["mvci", "j2534", "elm", "sim"], default="sim")
    ap.add_argument("--lib", help="J2534 library path (default: auto-detect)")
    ap.add_argument("--port", help="serial port (default: auto-detect /dev/cu.usbserial-*)")
    ap.add_argument("--seconds", type=float, default=0, help="stop after N seconds (0 = until Ctrl+C)")
    ap.add_argument("--outdir", default="logs")
    args = ap.parse_args()

    if args.transport in ("mvci", "j2534"):
        tr = J2534Transport(args.lib, args.port)
    elif args.transport == "elm":
        if not args.port:
            sys.exit("--port is required with --transport elm")
        tr = ElmTransport(args.port)
    else:
        tr = SimTransport()

    print("Connecting...")
    print("Connected:", tr.connect())

    # Ask the ECU which PIDs it supports, keep the ones we know how to decode.
    supported = []
    for base in (0x00, 0x20):
        data = tr.request(0x01, base)
        if not data:
            break
        supported += obd.decode_supported(base, data)
        if (base + 0x20) not in supported:
            break
    pids = [p for p in obd.DEFAULT_PIDS if p in supported] if supported else obd.DEFAULT_PIDS
    print("ECU supports:", " ".join(f"{p:02X}" for p in supported) or "(unknown)")
    print("Reading:", ", ".join(obd.PIDS[p][0] for p in pids))

    os.makedirs(args.outdir, exist_ok=True)
    path = os.path.join(args.outdir, dt.datetime.now().strftime("drive_%Y%m%d_%H%M%S.csv"))
    names = [obd.PIDS[p][0] for p in pids]
    t0 = time.time()
    n = 0
    try:
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["time_s"] + names)
            while not args.seconds or time.time() - t0 < args.seconds:
                row, shown = {}, []
                for p in pids:
                    data = tr.request(0x01, p)
                    if data:
                        name, unit, val = obd.decode(p, data)
                        row[name] = val
                        shown.append(f"{name}={val}{unit}")
                t = round(time.time() - t0, 2)
                w.writerow([t] + [row.get(nm, "") for nm in names])
                n += 1
                print(f"\r{t:7.1f}s  " + "  ".join(shown[:6]) + "   ", end="", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        tr.close()
    print(f"\nSaved {n} rows to {path}")


if __name__ == "__main__":
    main()
