"""Minimal ctypes wrapper for SAE J2534-1 (v04.04) PassThru libraries.

Works with two kinds of library:
  * Windows: the Mini VCI's own 32-bit driver DLL (run with 32-bit Python).
  * macOS/Linux: rmvci's open-source driver, librmvci_j2534.dylib / .so
    (https://github.com/FletcherD/rmvci), which talks to the same cable
    through the operating system's built-in FTDI serial driver.

Only the calls needed to read data are wrapped.
"""
import ctypes
import glob
import os
import sys
from ctypes import (POINTER, Structure, byref, c_char_p, c_ubyte, c_uint32,
                    c_ulong, create_string_buffer)

IS_WINDOWS = sys.platform.startswith("win")

# Protocol IDs (only 1..6 are valid; anything else can wedge the Mini VCI)
ISO9141, ISO14230 = 3, 4
# Ioctl IDs
SET_CONFIG, READ_VBATT, FIVE_BAUD_INIT, FAST_INIT = 2, 3, 4, 5
CLEAR_RX_BUFFER = 8
# Connect flag: K-line only (no L-line), as Techstream uses
ISO9141_K_LINE_ONLY = 0x1000
# Config parameter ids
DATA_RATE, LOOPBACK = 0x01, 0x03
P1_MAX, P3_MIN, P3_MAX = 0x07, 0x0A, 0x0B
TIDLE, TINIL, TWUP = 0x13, 0x14, 0x15
W1, W2, W3, W4, W5 = 0x0E, 0x0F, 0x10, 0x11, 0x12

# Techstream's own K-line timing profile (captured by the rmvci project).
# Installing it plus a filter BEFORE the init is what Toyota ECUs expect.
KLINE_TIMING = {DATA_RATE: 9600, P1_MAX: 40, P3_MIN: 10, P3_MAX: 10,
                TIDLE: 300, TINIL: 35, TWUP: 50,
                W1: 25, W2: 20, W3: 20, W4: 25, W5: 10}

PASS_FILTER = 1
TX_MSG_TYPE, START_OF_MESSAGE = 0x01, 0x02
ERR_TIMEOUT, ERR_BUFFER_EMPTY = 0x09, 0x10


# Struct fields are 32-bit on every platform; function scalars are C `unsigned long`.
class PASSTHRU_MSG(Structure):
    _fields_ = [("ProtocolID", c_uint32), ("RxStatus", c_uint32),
                ("TxFlags", c_uint32), ("Timestamp", c_uint32),
                ("DataSize", c_uint32), ("ExtraDataIndex", c_uint32),
                ("Data", c_ubyte * 4128)]


class SBYTE_ARRAY(Structure):
    _fields_ = [("NumOfBytes", c_uint32), ("BytePtr", POINTER(c_ubyte))]


class SCONFIG(Structure):
    _fields_ = [("Parameter", c_uint32), ("Value", c_uint32)]


class SCONFIG_LIST(Structure):
    _fields_ = [("NumOfParams", c_uint32), ("ConfigPtr", POINTER(SCONFIG))]


class J2534Error(Exception):
    pass


def list_windows_drivers():
    """Return [(name, dll_path)] for J2534 drivers in the Windows registry."""
    import winreg
    found = []
    for root in (r"SOFTWARE\WOW6432Node\PassThruSupport.04.04",
                 r"SOFTWARE\PassThruSupport.04.04"):
        try:
            base = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root)
        except OSError:
            continue
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(base, i)
            except OSError:
                break
            i += 1
            with winreg.OpenKey(base, sub) as k:
                try:
                    name = winreg.QueryValueEx(k, "Name")[0]
                except OSError:
                    name = sub
                try:
                    found.append((name, winreg.QueryValueEx(k, "FunctionLibrary")[0]))
                except OSError:
                    pass
    return found


def find_rmvci_library():
    """Look for a built rmvci library next to this project or in RMVCI_LIB."""
    if os.environ.get("RMVCI_LIB"):
        return os.environ["RMVCI_LIB"]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bundle = getattr(sys, "_MEIPASS", here)  # inside the packaged .app
    ext = "dylib" if sys.platform == "darwin" else "so"
    for pattern in (f"{bundle}/librmvci_j2534.{ext}",
                    f"{here}/librmvci_j2534.{ext}",
                    f"{here}/rmvci/target/release/librmvci_j2534.{ext}"):
        hits = glob.glob(pattern)
        if hits:
            return hits[0]
    return None


def find_mac_port():
    """The Mini VCI's FTDI chip shows up as /dev/cu.usbserial-XXXXXXXX on macOS."""
    ports = sorted(glob.glob("/dev/cu.usbserial-*"))
    return ports[0] if ports else None


class PassThru:
    def __init__(self, lib_path):
        self.rmvci = "rmvci" in os.path.basename(lib_path).lower()
        # Windows J2534 DLLs use __stdcall; elsewhere it's the plain C ABI.
        self.dll = ctypes.WinDLL(lib_path) if IS_WINDOWS else ctypes.CDLL(lib_path)
        self.device_id = None

    def _check(self, rc, what):
        if rc != 0:
            buf = create_string_buffer(256)
            try:
                self.dll.PassThruGetLastError(buf)
            except Exception:
                pass
            raise J2534Error(f"{what} failed (code 0x{rc:02X}): "
                             f"{buf.value.decode(errors='replace')}")

    def open(self, port=None):
        """`port` is the serial device for rmvci (e.g. /dev/cu.usbserial-A1B2C3);
        Windows drivers ignore it."""
        dev = c_ulong()
        name = c_char_p(port.encode()) if port else None
        self._check(self.dll.PassThruOpen(name, byref(dev)), "PassThruOpen")
        self.device_id = dev.value

    def close(self):
        if self.device_id is not None:
            self.dll.PassThruClose(c_ulong(self.device_id))
            self.device_id = None

    def read_version(self):
        fw, dll, api = (create_string_buffer(80) for _ in range(3))
        self._check(self.dll.PassThruReadVersion(c_ulong(self.device_id), fw, dll, api),
                    "ReadVersion")
        return fw.value.decode(errors="replace")

    def connect(self, protocol, baud, flags=0):
        if protocol not in (1, 2, 3, 4, 5, 6):
            raise ValueError("invalid protocol id")  # bad ids wedge the Mini VCI
        ch = c_ulong()
        self._check(self.dll.PassThruConnect(c_ulong(self.device_id), c_ulong(protocol),
                                             c_ulong(flags), c_ulong(baud), byref(ch)),
                    "PassThruConnect")
        return ch.value

    def disconnect(self, channel):
        self.dll.PassThruDisconnect(c_ulong(channel))

    def set_config(self, channel, params):
        arr = (SCONFIG * len(params))(*[SCONFIG(p, v) for p, v in params.items()])
        lst = SCONFIG_LIST(len(params), arr)
        self._check(self.dll.PassThruIoctl(c_ulong(channel), c_ulong(SET_CONFIG),
                                           byref(lst), None), "SET_CONFIG")

    def clear_rx(self, channel):
        self._check(self.dll.PassThruIoctl(c_ulong(channel), c_ulong(CLEAR_RX_BUFFER),
                                           None, None), "CLEAR_RX_BUFFER")

    def five_baud_init(self, channel, protocol, address=0x33):
        if self.rmvci:
            # rmvci takes the init address and returns key bytes as PASSTHRU_MSGs
            msg_in, msg_out = self._msg(protocol, bytes([address])), PASSTHRU_MSG()
            self._check(self.dll.PassThruIoctl(c_ulong(channel), c_ulong(FIVE_BAUD_INIT),
                                               byref(msg_in), byref(msg_out)),
                        "FIVE_BAUD_INIT")
            return bytes(msg_out.Data[:msg_out.DataSize])
        inbuf, outbuf = (c_ubyte * 1)(address), (c_ubyte * 2)()
        sin, sout = SBYTE_ARRAY(1, inbuf), SBYTE_ARRAY(2, outbuf)
        self._check(self.dll.PassThruIoctl(c_ulong(channel), c_ulong(FIVE_BAUD_INIT),
                                           byref(sin), byref(sout)), "FIVE_BAUD_INIT")
        return bytes(outbuf[:sout.NumOfBytes])

    def fast_init(self, channel, protocol, request):
        msg_in, msg_out = self._msg(protocol, request), PASSTHRU_MSG()
        self._check(self.dll.PassThruIoctl(c_ulong(channel), c_ulong(FAST_INIT),
                                           byref(msg_in), byref(msg_out)), "FAST_INIT")
        return bytes(msg_out.Data[:msg_out.DataSize])

    def pass_all_filter(self, channel, protocol):
        # mask 00 / pattern 00 on the first header byte = let every reply through
        mask, pattern = self._msg(protocol, b"\x00"), self._msg(protocol, b"\x00")
        fid = c_ulong()
        self._check(self.dll.PassThruStartMsgFilter(c_ulong(channel), c_ulong(PASS_FILTER),
                                                    byref(mask), byref(pattern), None,
                                                    byref(fid)), "StartMsgFilter")

    @staticmethod
    def _msg(protocol, data):
        m = PASSTHRU_MSG()
        m.ProtocolID = protocol
        m.DataSize = len(data)
        for i, b in enumerate(data):
            m.Data[i] = b
        return m

    def write(self, channel, protocol, data, timeout_ms=200):
        m = self._msg(protocol, data)
        n = c_ulong(1)
        self._check(self.dll.PassThruWriteMsgs(c_ulong(channel), byref(m), byref(n),
                                               c_ulong(timeout_ms)), "WriteMsgs")

    def read(self, channel, timeout_ms=100):
        """Return one received frame (bytes), b"" for an echo/indication, or None."""
        m = PASSTHRU_MSG()
        n = c_ulong(1)
        rc = self.dll.PassThruReadMsgs(c_ulong(channel), byref(m), byref(n),
                                       c_ulong(timeout_ms))
        if rc in (ERR_TIMEOUT, ERR_BUFFER_EMPTY) or n.value == 0:
            return None
        self._check(rc, "ReadMsgs")
        if m.RxStatus & (TX_MSG_TYPE | START_OF_MESSAGE) or m.DataSize == 0:
            return b""
        return bytes(m.Data[:m.DataSize])
