"""Generic OBD-II mode 01 (live data) definitions. Read-only requests only."""

# pid: (name, unit, number of data bytes, formula on data bytes)
PIDS = {
    0x04: ("engine_load", "%", 1, lambda d: d[0] * 100 / 255),
    0x05: ("coolant_temp", "C", 1, lambda d: d[0] - 40),
    0x06: ("stft_b1", "%", 1, lambda d: (d[0] - 128) * 100 / 128),
    0x07: ("ltft_b1", "%", 1, lambda d: (d[0] - 128) * 100 / 128),
    0x08: ("stft_b2", "%", 1, lambda d: (d[0] - 128) * 100 / 128),
    0x09: ("ltft_b2", "%", 1, lambda d: (d[0] - 128) * 100 / 128),
    0x0C: ("rpm", "rpm", 2, lambda d: (d[0] * 256 + d[1]) / 4),
    0x0D: ("speed", "km/h", 1, lambda d: d[0]),
    0x0E: ("timing_advance", "deg", 1, lambda d: d[0] / 2 - 64),
    0x0F: ("intake_air_temp", "C", 1, lambda d: d[0] - 40),
    0x10: ("maf", "g/s", 2, lambda d: (d[0] * 256 + d[1]) / 100),
    0x11: ("throttle", "%", 1, lambda d: d[0] * 100 / 255),
}

DEFAULT_PIDS = [0x0C, 0x0D, 0x05, 0x0F, 0x11, 0x04, 0x10, 0x06, 0x07, 0x08, 0x09, 0x0E]


def decode(pid, data):
    name, unit, n, fn = PIDS[pid]
    if len(data) < n:
        raise ValueError(f"PID {pid:02X}: expected {n} bytes, got {len(data)}")
    return name, unit, round(fn(data[:n]), 2)


def decode_supported(base, data):
    """Mode 01 PID 00/20/40 bitmask -> list of supported PIDs."""
    bits = int.from_bytes(data[:4], "big")
    return [base + i + 1 for i in range(32) if bits & (1 << (31 - i))]


# ---- Trouble codes (DTCs) --------------------------------------------------
# Mode 03 = stored codes (these turn the check-engine light on)
# Mode 07 = pending codes (seen once, not yet confirmed)
# Mode 04 = clear codes. The ONLY request in this app that changes anything.

DTC_LETTERS = "PCBU"


def decode_dtc(b1, b2):
    return f"{DTC_LETTERS[b1 >> 6]}{(b1 >> 4) & 3}{b1 & 0xF:X}{b2 >> 4:X}{b2 & 0xF:X}"


def encode_dtc(code):
    first = (DTC_LETTERS.index(code[0]) << 6) | (int(code[1]) << 4) | int(code[2], 16)
    return bytes([first, int(code[3:5], 16)])


def parse_dtc_frames(bodies, mode):
    """Turn mode 03/07 reply frames into a sorted list of codes like 'P0171'."""
    codes = set()
    for body in bodies:
        if not body or body[0] != mode + 0x40:
            continue
        data = body[1:]
        # ISO 9141/14230: 3 codes (6 bytes) per frame. A trailing checksum byte
        # can make the length odd; pairs past the 6th byte are ignored.
        data = data[:6] if len(data) % 2 else data[:len(data) - len(data) % 2]
        for i in range(0, len(data) - 1, 2):
            if data[i] or data[i + 1]:
                codes.add(decode_dtc(data[i], data[i + 1]))
    return sorted(codes)


def read_dtcs(transport, pending=False):
    mode = 0x07 if pending else 0x03
    return parse_dtc_frames(transport.query(bytes([mode]), timeout=2.0), mode)


def decode_monitor_status(data):
    """Mode 01 PID 01 -> check-engine light on?, number of stored codes."""
    return {"mil_on": bool(data[0] & 0x80), "dtc_count": data[0] & 0x7F}


NRC = {0x11: "service not supported", 0x12: "not supported", 0x22: "conditions not correct "
       "(engine must be off, ignition on)", 0x31: "request out of range", 0x33: "security access denied"}


def clear_dtcs(transport):
    """Send mode 04. Returns (ok, message)."""
    bodies = transport.query(b"\x04", timeout=4.0)
    if any(b[:1] == b"\x44" for b in bodies):
        return True, "Codes cleared"
    for b in bodies:
        if b[:2] == b"\x7f\x04" and len(b) >= 3:
            return False, "ECU refused: " + NRC.get(b[2], f"code {b[2]:02X}")
    return False, "No answer from the ECU"


# Plain-English meanings for common generic and Toyota/Lexus engine codes.
DTC_TEXT = {
    "P0100": "Mass air flow sensor circuit", "P0101": "Mass air flow sensor range/performance",
    "P0110": "Intake air temperature sensor circuit", "P0115": "Coolant temperature sensor circuit",
    "P0116": "Coolant temperature sensor range/performance", "P0120": "Throttle position sensor circuit",
    "P0125": "Engine slow to reach temperature for fuel control (often thermostat)",
    "P0128": "Coolant below thermostat temperature (thermostat stuck open)",
    "P0130": "O2 / air-fuel sensor circuit, bank 1 sensor 1", "P0135": "O2 / air-fuel sensor heater, bank 1 sensor 1",
    "P0136": "O2 sensor circuit, bank 1 sensor 2", "P0141": "O2 sensor heater, bank 1 sensor 2",
    "P0150": "O2 / air-fuel sensor circuit, bank 2 sensor 1", "P0155": "O2 / air-fuel sensor heater, bank 2 sensor 1",
    "P0156": "O2 sensor circuit, bank 2 sensor 2", "P0161": "O2 sensor heater, bank 2 sensor 2",
    "P0171": "System too lean, bank 1 (vacuum leak, dirty MAF, weak fuel delivery)",
    "P0172": "System too rich, bank 1", "P0174": "System too lean, bank 2 (vacuum leak, dirty MAF, weak fuel delivery)",
    "P0175": "System too rich, bank 2", "P0300": "Random/multiple cylinder misfire",
    "P0301": "Cylinder 1 misfire", "P0302": "Cylinder 2 misfire", "P0303": "Cylinder 3 misfire",
    "P0304": "Cylinder 4 misfire", "P0305": "Cylinder 5 misfire", "P0306": "Cylinder 6 misfire",
    "P0307": "Cylinder 7 misfire", "P0308": "Cylinder 8 misfire",
    "P0325": "Knock sensor 1 circuit", "P0330": "Knock sensor 2 circuit",
    "P0335": "Crankshaft position sensor circuit", "P0340": "Camshaft position sensor circuit",
    "P0400": "EGR flow", "P0401": "EGR flow insufficient",
    "P0420": "Catalyst efficiency below threshold, bank 1", "P0430": "Catalyst efficiency below threshold, bank 2",
    "P0440": "Evaporative (EVAP) system", "P0441": "EVAP purge flow incorrect",
    "P0442": "EVAP small leak (check fuel cap)", "P0446": "EVAP vent control circuit",
    "P0455": "EVAP large leak (check fuel cap)", "P0500": "Vehicle speed sensor",
    "P0505": "Idle control system", "P0700": "Transmission control system (read transmission codes)",
    "P0705": "Transmission range sensor circuit", "P0710": "Transmission fluid temperature sensor",
    "P0711": "Transmission fluid temperature sensor range", "P0750": "Shift solenoid A",
    "P0755": "Shift solenoid B", "P0760": "Shift solenoid C", "P0770": "Shift solenoid E",
    "P1130": "Air-fuel sensor circuit range, bank 1 (Toyota)", "P1135": "Air-fuel sensor heater, bank 1 (Toyota)",
    "P1150": "Air-fuel sensor circuit range, bank 2 (Toyota)", "P1155": "Air-fuel sensor heater, bank 2 (Toyota)",
    "P1300": "Igniter circuit, cylinder 1 (Toyota)", "P1349": "Variable valve timing (VVT) system (Toyota)",
    "P1346": "VVT sensor circuit range, bank 1 (Toyota)", "P1656": "Oil control valve (VVT) circuit (Toyota)",
    "P1600": "ECM battery backup circuit (Toyota)", "P1633": "ECM internal fault (Toyota)",
}


def describe_dtc(code):
    return DTC_TEXT.get(code, "No description in the built-in list. Look it up in the repair manual.")
