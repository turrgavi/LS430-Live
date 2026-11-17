"""Ways of reaching the car. Each transport exposes the same small interface:

    connect()               -> str describing what answered
    request(mode, pid)      -> bytes after the mode/pid echo, or None
    query(data, timeout)    -> list of reply bodies (each starts with the
                               response service byte, or 7F for "refused")
    close()

The transports themselves are neutral pipes. What may be sent is decided in
obd.py: live data (01), trouble codes (03/07), and clearing codes (04), which
is the only service that changes anything and is gated by the app.
"""
import math
import random
import time


class Transport:
    def connect(self):
        raise NotImplementedError

    def query(self, data, timeout=1.0):
        raise NotImplementedError

    def request(self, mode, pid):
        for body in self.query(bytes([mode, pid]), timeout=0.5):
            if len(body) >= 2 and body[0] == mode + 0x40 and body[1] == pid:
                return body[2:]
        return None

    def close(self):
        pass


class J2534Transport(Transport):
    """Mini VCI through a J2534 library.

    * macOS / Linux: rmvci's open-source driver (librmvci_j2534.dylib / .so)
      talking to /dev/cu.usbserial-* through the OS's built-in FTDI driver.
    * Windows: the Mini VCI's own 32-bit DLL (needs 32-bit Python).

    2002 LS430 engine ECU: K-line (OBD pin 7). We try, in order:
      1. ISO 9141-2 with 5-baud init to 0x33 (the generic OBD-II way)
      2. ISO 14230 fast init, functional address 0x33
      3. ISO 14230 fast init, engine ECU physical address 0x10
    Each init only says "wake up and talk to me"; nothing is changed.
    """

    ATTEMPTS = [
        ("ISO 9141-2, 5-baud init", 3, 0x33, None),
        ("ISO 14230 fast init, address 33", 4, 0x33, bytes([0xC1, 0x33, 0xF1, 0x81])),
        ("ISO 14230 fast init, address 10", 4, 0x10, bytes([0x81, 0x10, 0xF1, 0x81])),
    ]

    def __init__(self, lib_path=None, port=None):
        from . import j2534
        self.j = j2534
        if lib_path is None:
            if j2534.IS_WINDOWS:
                drivers = j2534.list_windows_drivers()
                if not drivers:
                    raise RuntimeError("No J2534 drivers found in the registry.")
                mini = [d for d in drivers if "vci" in str(d[0]).lower()]
                name, lib_path = (mini or drivers)[0]
                print(f"Using J2534 driver: {name} ({lib_path})")
            else:
                lib_path = j2534.find_rmvci_library()
                if not lib_path:
                    raise RuntimeError("rmvci library not found. Build it (see README) "
                                       "or pass --lib /path/to/librmvci_j2534.dylib")
                print(f"Using rmvci driver: {lib_path}")
        if port is None and not j2534.IS_WINDOWS:
            port = j2534.find_mac_port() if j2534.sys.platform == "darwin" else None
            if j2534.sys.platform == "darwin" and not port:
                raise RuntimeError("No /dev/cu.usbserial-* found. Is the Mini VCI plugged in?")
        self.port = port
        self.pt = j2534.PassThru(lib_path)
        self.channel = self.protocol = self.target = None

    def connect(self):
        j = self.j
        if self.port:
            print(f"Opening cable on {self.port}")
        self.pt.open(self.port)
        errors = []
        for label, proto, target, init_msg in self.ATTEMPTS:
            ch = None
            try:
                ch = self.pt.connect(proto, 10400, j.ISO9141_K_LINE_ONLY)
                # Filter + Techstream timing must be in place before the init.
                self.pt.pass_all_filter(ch, proto)
                timing = dict(j.KLINE_TIMING)
                if proto == j.ISO9141:
                    timing[j.DATA_RATE] = 10400
                self.pt.set_config(ch, timing)
                self.pt.clear_rx(ch)
                if init_msg is None:
                    keys = self.pt.five_baud_init(ch, proto, target)
                else:
                    keys = self.pt.fast_init(ch, proto, init_msg)
                self.channel, self.protocol, self.target = ch, proto, target
                if self.request(0x01, 0x00) is None:
                    raise j.J2534Error("init OK but no reply to 01 00")
                return f"{label} (reply {keys.hex(' ').upper() or '-'})"
            except j.J2534Error as e:
                errors.append(f"{label}: {e}")
                self.channel = None
                if ch is not None:
                    try:
                        self.pt.disconnect(ch)
                    except Exception:
                        pass
                time.sleep(2.5)  # let the ECU's K-line session time out before retrying
        raise RuntimeError("No ECU answered.\n  " + "\n  ".join(errors))

    def query(self, data, timeout=1.0):
        j = self.j
        if self.protocol == j.ISO9141:
            frame = bytes([0x68, 0x6A, 0xF1]) + data
        elif self.target == 0x33:
            frame = bytes([0xC0 | len(data), 0x33, 0xF1]) + data
        else:
            frame = bytes([0x80 | len(data), self.target, 0xF1]) + data
        self.pt.clear_rx(self.channel)
        self.pt.write(self.channel, self.protocol, frame)
        bodies, deadline, quiet_until = [], time.time() + timeout, None
        while time.time() < deadline:
            if quiet_until and time.time() > quiet_until:
                break  # replies arrived and the line has gone quiet
            rx = self.pt.read(self.channel, 50)
            if not rx:
                continue
            body = rx[3:]  # drop the 3 header bytes (a trailing checksum is harmless)
            if not body:
                continue
            if body[0] == 0x7F and len(body) >= 3 and body[2] == 0x78:
                deadline = max(deadline, time.time() + 5)  # "busy, wait": keep listening
                continue
            if body[0] == data[0] + 0x40 or (body[0] == 0x7F and body[1:2] == data[:1]):
                bodies.append(body)
                quiet_until = time.time() + 0.12
        return bodies

    def close(self):
        if self.channel is not None:
            self.pt.disconnect(self.channel)
        self.pt.close()


class ElmTransport(Transport):
    """ELM327 / STN11xx / OBDLink adapters over a serial port (USB or Bluetooth).
    Works on macOS, Windows, Linux. Needs `pip install pyserial`.
    """

    def __init__(self, port, baud=115200):
        import serial
        self.ser = serial.Serial(port, baud, timeout=2)

    def _cmd(self, cmd, timeout=3.0):
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\r").encode())
        buf, end = b"", time.time() + timeout
        while time.time() < end and not buf.endswith(b">"):
            buf += self.ser.read(self.ser.in_waiting or 1)
        return buf.decode(errors="replace").replace(">", "").strip()

    def connect(self):
        for c in ("ATZ", "ATE0", "ATL0", "ATS0", "ATH0", "ATSP3"):  # SP3 = ISO 9141-2
            self._cmd(c)
        r = self._cmd("0100", timeout=8)  # first request triggers the slow init
        if "41" not in r:
            raise RuntimeError(f"No ECU answered: {r!r}")
        return f"ELM/STN adapter, protocol {self._cmd('ATDP')}"

    def query(self, data, timeout=1.0):
        r = self._cmd(data.hex().upper(), timeout=max(3.0, timeout))
        bodies = []
        for line in r.splitlines():
            line = line.strip().replace(" ", "")
            try:
                b = bytes.fromhex(line)
            except ValueError:
                continue  # "NO DATA", "SEARCHING..." etc.
            if b and (b[0] == data[0] + 0x40 or b[0] == 0x7F):
                bodies.append(b)
        return bodies

    def close(self):
        self.ser.close()


class SimTransport(Transport):
    """Fake car for testing without hardware: idle, then a gentle drive cycle."""

    def connect(self):
        self.t0 = time.time()
        self.stored = ["P0171", "P0174"]
        self.pending = ["P0420"]
        return "simulator"

    def query(self, data, timeout=1.0):
        mode = data[0]
        if mode == 0x01 and len(data) == 2:
            v = self._pid(data[1])
            return [bytes([0x41, data[1]]) + v] if v is not None else [bytes([0x7F, 0x01, 0x12])]
        if mode in (0x03, 0x07):
            from .obd import encode_dtc
            codes = self.stored if mode == 0x03 else self.pending
            raw = b"".join(encode_dtc(c) for c in codes)
            raw += b"\x00" * ((-len(raw)) % 6)  # pad to 3 codes per frame
            return [bytes([mode + 0x40]) + raw[i:i + 6] for i in range(0, max(len(raw), 6), 6)]
        if mode == 0x04:
            if self.rpm_now() > 0:
                return [bytes([0x7F, 0x04, 0x22])]  # conditions not correct
            self.stored, self.pending = [], []
            return [bytes([0x44])]
        return [bytes([0x7F, mode, 0x11])]

    def rpm_now(self):
        return 0 if getattr(self, "engine_off", False) else 1

    def _pid(self, pid):
        t = time.time() - self.t0
        if pid in (0x00, 0x20):
            return bytes.fromhex("FE1FA813" if pid == 0 else "80000000")
        if pid == 0x01:
            return bytes([(0x80 if self.stored else 0) | len(self.stored), 0x07, 0x65, 0x65 if self.stored else 0x00])
        if getattr(self, "engine_off", False):
            return {0x0C: b"\x00\x00", 0x0D: b"\x00", 0x05: bytes([int(min(88, 20 + t * 2)) + 40])}.get(
                pid, bytes([0, 0]) if pid == 0x10 else (b"\x80" if pid in (6, 7, 8, 9, 0x0E) else b"\x00"))
        speed = max(0.0, 60 * math.sin(t / 20)) if t > 5 else 0.0
        rpm = 650 + speed * 30 + random.uniform(-15, 15)
        coolant = min(88, 20 + t * 2)
        thr = min(100, speed * 0.6 + random.uniform(0, 2))
        vals = {
            0x04: [int(20 + thr * 0.6)], 0x05: [int(coolant + 40)],
            0x06: [128 + random.randint(-4, 4)], 0x07: [130],
            0x08: [128 + random.randint(-4, 4)], 0x09: [127],
            0x0C: list(int(rpm * 4).to_bytes(2, "big")), 0x0D: [int(speed)],
            0x0E: [int((10 + thr / 10 + 64) * 2)], 0x0F: [25 + 40],
            0x10: list(int((3 + rpm / 400) * 100).to_bytes(2, "big")),
            0x11: [int(thr * 255 / 100)],
        }
        time.sleep(0.02)
        return bytes(vals[pid]) if pid in vals else None
