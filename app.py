#!/usr/bin/env python3
"""LS430 Live: local server behind the dashboard.

Run:  python3 desktop.py    (own window; this is what the Mac app runs)
 or:  python3 app.py        (opens in your web browser at http://127.0.0.1:8430)
Everything runs on this computer; the only network use is an optional check
for a newer version.

Reading live data and trouble codes is always allowed. The one action that
changes anything (clearing trouble codes) is locked until the user unlocks
write actions, needs the engine off, a typed confirmation, and is logged to
logs/actions.log. ECU reprogramming is deliberately not supported.
"""
import csv
import datetime as dt
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
FROZEN = getattr(sys, "frozen", False)          # True inside the packaged .app
RES = getattr(sys, "_MEIPASS", HERE)            # where bundled files live
sys.path.insert(0, HERE)
from ls430live import j2534, obd  # noqa: E402
from ls430live.transports import ElmTransport, J2534Transport, SimTransport  # noqa: E402
from ls430live.version import UPDATE_REPO, VERSION  # noqa: E402

PORT = 8430
# Inside the app bundle nothing is writable, so recordings go to Documents.
LOG_DIR = os.environ.get("LS430_LOG_DIR") or (
    os.path.join(os.path.expanduser("~"), "Documents", "LS430 Live", "logs") if FROZEN
    else os.path.join(HERE, "logs"))


class Engine:
    """Owns the connection to the car and a polling thread."""

    def __init__(self):
        self.lock = threading.Lock()
        self.listeners = []
        self.thread = None
        self.stop_flag = threading.Event()
        self.state = "disconnected"   # disconnected | connecting | connected
        self.detail = ""
        self.pids = []
        self.recording = None          # (path, file, writer)
        self.rows = 0
        self.transport = None
        self.backlog = []
        self.building = False
        self.tr = None
        self.tr_lock = threading.Lock()
        self.write_unlocked = False
        self.busy = None               # text of a running one-off action
        self.cable_test = None
        self.update = None
        self.ports = []
        self.dtc = {"stored": None, "pending": None, "mil_on": None, "dtc_count": None, "read_at": None}

    # --- events to the browser -------------------------------------------
    def publish(self, kind, **data):
        msg = json.dumps({"type": kind, **data})
        with self.lock:
            for q in list(self.listeners):
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass

    def status(self):
        rec = os.path.basename(self.recording[0]) if self.recording else None
        return {"state": self.state, "detail": self.detail, "recording": rec,
                "transport": self.transport,
                "driver_ready": driver_ready(), "building": self.building,
                "version": VERSION, "update": self.update, "frozen": FROZEN, "ports": self.ports,
                "write_unlocked": self.write_unlocked, "busy": self.busy, "cable_test": self.cable_test,
                "dtc": {**self.dtc, "text": {c: obd.describe_dtc(c) for c in
                                             (self.dtc["stored"] or []) + (self.dtc["pending"] or [])}},
                "sim_engine_off": bool(getattr(self.tr, "engine_off", False)) if self.transport == "sim" else None,
                "rows": self.rows,
                "signals": [{"name": obd.PIDS[p][0], "unit": obd.PIDS[p][1]} for p in self.pids]}

    def push_status(self):
        self.publish("status", **self.status())

    def log(self, text):
        line = time.strftime("%H:%M:%S ") + text
        self.backlog = (self.backlog + [line])[-100:]
        self.publish("log", text=line)

    # --- commands ----------------------------------------------------------
    def connect(self, transport, port=None):
        if self.state != "disconnected" or self.building or self.busy:
            return
        if transport == "mvci" and not driver_ready():
            self.log("The Mini VCI driver isn't built on this Mac yet. "
                     "Click 'Build driver now' above (one-time, about 3-5 minutes, needs internet).")
            self.push_status()
            return
        self.stop_flag.clear()
        self.state, self.detail, self.transport = "connecting", "", transport
        self.push_status()
        self.thread = threading.Thread(target=self._run, args=(transport, port), daemon=True)
        self.thread.start()

    def disconnect(self):
        self.stop_flag.set()

    def check_update(self):
        """Ask GitHub for the latest release; never blocks or fails the app."""
        if not UPDATE_REPO:
            return

        def run():
            import urllib.request
            try:
                req = urllib.request.Request(
                    f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
                    headers={"Accept": "application/vnd.github+json", "User-Agent": "ls430-live"})
                with urllib.request.urlopen(req, timeout=6) as r:
                    rel = json.load(r)
                latest = rel.get("tag_name", "").lstrip("v")
                if version_tuple(latest) > version_tuple(VERSION):
                    url = rel.get("html_url", "")
                    if url.startswith("https://github.com/"):
                        self.update = {"version": latest, "url": url}
                        self.log(f"Version {latest} is available (you have {VERSION}).")
                        self.push_status()
            except Exception:
                pass  # offline in the car is normal
        threading.Thread(target=run, daemon=True).start()

    def build_driver(self):
        """Run setup_mac.sh (one-time build of the open-source Mini VCI driver)."""
        if FROZEN:
            return self.log("The driver is already included in the app.")
        if self.building or self.state != "disconnected" or sys.platform != "darwin":
            if sys.platform != "darwin":
                self.log("Driver build is only needed on macOS.")
            return
        self.building = True
        self.push_status()
        threading.Thread(target=self._build, daemon=True).start()

    def _build(self):
        self.log("Building the Mini VCI driver. This takes a few minutes; progress appears below.")
        try:
            proc = subprocess.Popen(["/bin/bash", os.path.join(HERE, "setup_mac.sh")], cwd=HERE,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    self.log("  " + line[-200:])
            proc.wait()
            if driver_ready():
                self.log("Driver built. Plug in the Mini VCI, turn the ignition on, and press Connect.")
            elif proc.returncode == 0:
                self.log("Setup paused: if an Apple 'install command line developer tools' window "
                         "appeared, click Install, wait for it to finish, then click 'Build driver now' again.")
            else:
                self.log(f"Build failed (code {proc.returncode}). Copy the Activity text and send it to Claude.")
        except Exception as e:
            self.log(f"Build failed: {e}")
        finally:
            self.building = False
            self.push_status()

    # --- trouble codes and write actions -------------------------------------
    def _action(self, label, fn):
        """Run fn(tr) in the background while live polling pauses."""
        if self.state != "connected" or self.busy:
            return
        self.busy = label
        self.push_status()

        def run():
            try:
                with self.tr_lock:
                    fn(self.tr)
            except Exception as e:
                self.log(f"{label} failed: {e}")
            finally:
                self.busy = None
                self.push_status()
        threading.Thread(target=run, daemon=True).start()

    def _read_codes(self, tr):
        status = tr.request(0x01, 0x01)
        if status:
            self.dtc.update(obd.decode_monitor_status(status))
        self.dtc["stored"] = obd.read_dtcs(tr)
        self.dtc["pending"] = obd.read_dtcs(tr, pending=True)
        self.dtc["read_at"] = time.strftime("%H:%M:%S")
        self.log(f"Trouble codes: stored {', '.join(self.dtc['stored']) or 'none'}; "
                 f"pending {', '.join(self.dtc['pending']) or 'none'}")

    def test_cable(self, port=None, simulate=False):
        """Check the Mini VCI works without talking to the car."""
        if self.state != "disconnected" or self.busy or self.building:
            return
        self.busy = "Testing cable"
        self.push_status()
        threading.Thread(target=self._test_cable, args=(port, simulate), daemon=True).start()

    CABLE_STEPS = [
        ("driver", "Driver", "The Mini VCI driver is installed"),
        ("usb", "USB connection", "The Mac can see the cable"),
        ("handshake", "Cable responds", "The cable's processor answers a secure handshake"),
        ("kline", "Car link ready", "The K-line channel to the engine computer opens"),
    ]

    def _test_cable(self, port, simulate):
        steps = [{"id": i, "title": t, "desc": d, "state": "pending", "detail": ""}
                 for i, t, d in self.CABLE_STEPS]
        self.cable_test = {"running": True, "ok": None, "steps": steps, "at": None}
        self.push_status()

        def step(sid, state, detail=""):
            st = next(x for x in steps if x["id"] == sid)
            st["state"], st["detail"] = state, detail
            if state in ("pass", "fail"):
                self.log(("PASS  " if state == "pass" else "FAIL  ") + st["title"] + (f": {detail}" if detail else ""))
            self.push_status()
            if state == "running":
                time.sleep(0.35)  # let each step be seen
            return state == "pass"

        def skip_rest():
            for x in steps:
                if x["state"] == "pending":
                    x["state"] = "skipped"

        self.log("Cable check started (no messages are sent to the car)")
        pt = None
        try:
            if simulate:
                for sid, _, _ in self.CABLE_STEPS:
                    step(sid, "running")
                    step(sid, "pass", {"usb": "/dev/cu.usbserial-SIM"}.get(sid, ""))
                return
            step("driver", "running")
            if j2534.IS_WINDOWS:
                drivers = j2534.list_windows_drivers()
                vci = [d for d in drivers if "vci" in str(d[0]).lower()] or drivers
                if not step("driver", "pass" if vci else "fail",
                            vci[0][0] if vci else "No J2534 driver found. Reinstall the Mini VCI software."):
                    return skip_rest()
                lib = vci[0][1]
                step("usb", "skipped", "Checked by the Windows driver")
            else:
                lib = j2534.find_rmvci_library()
                if not step("driver", "pass" if lib else "fail",
                            "Built into the app" if FROZEN else ("Found" if lib else "Not built yet. Use 'Build driver'.")):
                    return skip_rest()
                step("usb", "running")
                if not port:
                    ports = list_ports()
                    if not step("usb", "pass" if ports else "fail", ports[0] if ports else
                                "Not detected. Check the USB plug, try another port, and make sure the "
                                "Windows VM isn't holding the cable."):
                        return skip_rest()
                    port = ports[0]
                else:
                    step("usb", "pass", port)
            step("handshake", "running")
            pt = j2534.PassThru(lib)
            try:
                pt.open(port)
                step("handshake", "pass", "Cable firmware is running")
            except j2534.J2534Error as e:
                pt = None
                step("handshake", "fail", "No answer. Unplug the cable, wait 5 seconds, plug it back in. "
                     f"({str(e)[:120]})")
                return skip_rest()
            step("kline", "running")
            try:
                ch = pt.connect(j2534.ISO9141, 10400, j2534.ISO9141_K_LINE_ONLY)
                pt.disconnect(ch)
                step("kline", "pass", "Ready. Turn the ignition on and connect.")
            except j2534.J2534Error as e:
                step("kline", "fail", str(e)[:160])
        except Exception as e:
            self.log(f"Cable check error: {e}")
            for x in steps:
                if x["state"] == "running":
                    x["state"], x["detail"] = "fail", str(e)[:160]
            skip_rest()
        finally:
            if pt is not None:
                try:
                    pt.close()
                except Exception:
                    pass
                time.sleep(1.0)  # the cable needs a moment before it can be reopened
            ok = all(x["state"] in ("pass", "skipped") for x in steps) and any(x["state"] == "pass" for x in steps)
            self.log("Cable check passed." if ok else "Cable check found a problem.")
            self.cable_test = {"running": False, "ok": ok, "steps": steps, "at": time.strftime("%H:%M")}
            self.busy = None
            self.push_status()

    def watch_ports(self):
        """Notice the cable being plugged in or out, without opening it."""
        def run():
            last = None
            while True:
                ports = list_ports()
                if ports != last:
                    last, self.ports = ports, ports
                    self.push_status()
                time.sleep(2)
        threading.Thread(target=run, daemon=True).start()

    def read_codes(self):
        self._action("Reading trouble codes", self._read_codes)

    def set_write_unlock(self, on):
        self.write_unlocked = bool(on)
        self.log("Write actions UNLOCKED for this session" if on else "Write actions locked")
        self.push_status()

    def clear_codes(self, confirm):
        if not self.write_unlocked:
            return self.log("Clear refused: write actions are locked.")
        if confirm != "CLEAR":
            return self.log("Clear refused: confirmation text did not match.")

        def run(tr):
            # Safety: engine must be OFF (ignition ON) and the car stationary.
            rpm = tr.request(0x01, 0x0C)
            speed = tr.request(0x01, 0x0D)
            if rpm is None:
                return self.log("Clear refused: could not confirm the engine is off (no RPM reading).")
            rpm_v = obd.decode(0x0C, rpm)[2]
            if rpm_v > 0:
                return self.log(f"Clear refused: engine is running ({rpm_v:.0f} rpm). "
                                "Turn the engine off, leave the ignition ON, and try again.")
            if speed and obd.decode(0x0D, speed)[2] > 0:
                return self.log("Clear refused: the car is moving.")
            before = obd.read_dtcs(tr)
            ok, msg = obd.clear_dtcs(tr)
            time.sleep(0.5)
            self._read_codes(tr)
            audit(f"CLEAR CODES via {self.transport}: {'OK' if ok else 'FAILED'} ({msg}); "
                  f"before={before or 'none'} after={self.dtc['stored'] or 'none'}")
            self.log(("Cleared. " if ok else "Not cleared: ") + msg)
            self.write_unlocked = False  # re-lock after every write
        self._action("Clearing trouble codes", run)

    def sim_engine(self, off):
        if self.transport == "sim" and self.tr is not None:
            self.tr.engine_off = bool(off)
            self.log("Simulator: engine " + ("off (ignition on)" if off else "running"))
            self.push_status()

    def start_recording(self):
        if self.recording or self.state != "connected":
            return
        os.makedirs(LOG_DIR, exist_ok=True)
        path = os.path.join(LOG_DIR, dt.datetime.now().strftime("drive_%Y%m%d_%H%M%S.csv"))
        f = open(path, "w", newline="")
        w = csv.writer(f)
        w.writerow(["time_s"] + [obd.PIDS[p][0] for p in self.pids])
        self.recording, self.rows, self.rec_t0 = (path, f, w), 0, time.time()
        self.log(f"Recording to logs/{os.path.basename(path)}")
        self.push_status()

    def stop_recording(self):
        if self.recording:
            path, f, _ = self.recording
            f.close()
            self.recording = None
            self.log(f"Saved {self.rows} rows to logs/{os.path.basename(path)}")
            self.push_status()

    # --- polling thread ----------------------------------------------------
    def _run(self, transport, port):
        tr = None
        try:
            if transport == "mvci":
                tr = J2534Transport(None, port or None)
            elif transport == "elm":
                if not port:
                    raise RuntimeError("Enter the adapter's serial port, e.g. /dev/cu.usbserial-XXXX")
                tr = ElmTransport(port)
            else:
                tr = SimTransport()
            self.log(f"Connecting ({transport})... a K-line wake-up can take up to 10 s")
            self.detail = tr.connect()
            self.log("Connected: " + self.detail)

            supported = []
            for base in (0x00, 0x20):
                data = tr.request(0x01, base)
                if not data:
                    break
                supported += obd.decode_supported(base, data)
                if base + 0x20 not in supported:
                    break
            self.pids = ([p for p in obd.DEFAULT_PIDS if p in supported]
                         if supported else list(obd.DEFAULT_PIDS))
            self.log("ECU supports PIDs: " + (" ".join(f"{p:02X}" for p in supported) or "unknown"))
            self.tr = tr
            self.state = "connected"
            with self.tr_lock:
                self._read_codes(tr)
            self.push_status()

            misses, t0 = 0, time.time()
            while not self.stop_flag.is_set():
                values = {}
                with self.tr_lock:
                    for p in self.pids:
                        data = tr.request(0x01, p)
                        if data:
                            try:
                                name, _, val = obd.decode(p, data)
                                values[name] = val
                            except ValueError:
                                pass
                misses = 0 if values else misses + 1
                if misses >= 5:
                    raise RuntimeError("The car stopped answering (ignition off or cable unplugged?)")
                t = round(time.time() - t0, 2)
                self.publish("data", t=t, values=values)
                if self.recording:
                    _, f, w = self.recording
                    w.writerow([round(time.time() - self.rec_t0, 2)] +
                               [values.get(obd.PIDS[p][0], "") for p in self.pids])
                    self.rows += 1
                    if self.rows % 5 == 0:
                        f.flush()
                        self.push_status()
        except Exception as e:
            self.log(f"Error: {e}")
            traceback.print_exc()
        finally:
            self.stop_recording()
            if tr is not None:
                try:
                    tr.close()
                except Exception:
                    pass
            self.state, self.detail, self.pids, self.tr = "disconnected", "", [], None
            self.write_unlocked = False
            self.log("Disconnected")
            self.push_status()


def list_ports():
    import glob
    if sys.platform == "darwin":
        return sorted(glob.glob("/dev/cu.usbserial-*"))
    if sys.platform.startswith("linux"):
        return sorted(glob.glob("/dev/ttyUSB*"))
    return []


def version_tuple(v):
    try:
        return tuple(int(x) for x in v.split("."))
    except ValueError:
        return (0,)


def open_path(target):
    if sys.platform == "darwin":
        subprocess.Popen(["open", target])
    elif sys.platform.startswith("win"):
        os.startfile(target)
    else:
        webbrowser.open(target)


def audit(text):
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, "actions.log"), "a") as f:
        f.write(dt.datetime.now().isoformat(timespec="seconds") + " " + text + "\n")


def driver_ready():
    if j2534.IS_WINDOWS:
        return True
    return j2534.find_rmvci_library() is not None


ENGINE = Engine()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/":
            with open(os.path.join(RES, "ls430live", "dashboard.html"), "rb") as f:
                self._send(200, f.read(), "text/html; charset=utf-8")
        elif self.path == "/status":
            self._send(200, json.dumps(ENGINE.status()))
        elif self.path == "/events":
            q = queue.Queue(maxsize=200)
            with ENGINE.lock:
                ENGINE.listeners.append(q)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            try:
                self.wfile.write(f"data: {json.dumps({'type': 'status', **ENGINE.status()})}\n\n".encode())
                self.wfile.write(f"data: {json.dumps({'type': 'backlog', 'lines': ENGINE.backlog})}\n\n".encode())
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=15)
                        self.wfile.write(f"data: {msg}\n\n".encode())
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with ENGINE.lock:
                    ENGINE.listeners.remove(q)
        else:
            self._send(404, "{}")

    def do_POST(self):
        # Only accept requests from our own page (blocks other websites).
        # Only accept requests from our own page: blocks other websites
        # (DNS rebinding via Host, cross-site posts via Origin + JSON-only).
        ours = (f"127.0.0.1:{PORT}", f"localhost:{PORT}")
        origin = self.headers.get("Origin")
        if (self.headers.get("Host", "") not in ours
                or (origin and origin.split("://", 1)[-1] not in ours)
                or not self.headers.get("Content-Type", "").startswith("application/json")):
            return self._send(403, "{}")
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if self.path == "/connect":
            ENGINE.connect(body.get("transport", "sim"), body.get("port"))
        elif self.path == "/test-cable":
            ENGINE.test_cable(body.get("port"), simulate=body.get("transport") == "sim")
        elif self.path == "/read-codes":
            ENGINE.read_codes()
        elif self.path == "/write-unlock":
            ENGINE.set_write_unlock(body.get("on"))
        elif self.path == "/clear-codes":
            ENGINE.clear_codes(body.get("confirm"))
        elif self.path == "/sim-engine":
            ENGINE.sim_engine(body.get("off"))
        elif self.path == "/build-driver":
            ENGINE.build_driver()
        elif self.path == "/disconnect":
            ENGINE.disconnect()
        elif self.path == "/record":
            ENGINE.start_recording() if body.get("on") else ENGINE.stop_recording()
        elif self.path == "/open-logs":
            os.makedirs(LOG_DIR, exist_ok=True)
            open_path(LOG_DIR)
        elif self.path == "/open-update":
            if ENGINE.update:
                open_path(ENGINE.update["url"])  # only the GitHub release page we found
        else:
            return self._send(404, "{}")
        self._send(200, json.dumps(ENGINE.status()))


def start_server(port=None):
    """Start the local server in a background thread; returns its URL.
    port=0 picks a free port, so an old copy still running can't get in the way."""
    global PORT
    server = ThreadingHTTPServer(("127.0.0.1", PORT if port is None else port), Handler)
    PORT = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ENGINE.check_update()
    ENGINE.watch_ports()
    return server, f"http://127.0.0.1:{PORT}/"


def main():
    server, url = start_server()
    print(f"LS430 Live is running at {url}  (close this window or press Ctrl+C to quit)")
    if "--no-browser" not in sys.argv:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        ENGINE.disconnect()
        time.sleep(0.5)
        server.shutdown()


if __name__ == "__main__":
    main()
