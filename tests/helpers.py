"""Small builders so tests can declare protocols inline."""

from protocol_designer.storage import protocol_from_dict


def make_protocol(frames, enums=None, endianness="BIG", **extra):
    data = {
        "protocol": {"name": "Test", "version": "1.0", "endianness": endianness},
        "transport": {"type": "LOOPBACK"},
        "enums": enums or {},
        "frames": frames,
    }
    data.update(extra)
    return protocol_from_dict(data)


def header(cmd, crc="CRC8"):
    """SOF | ADDR | CMD | LEN ... used by most test frames."""
    return [
        {"name": "SOF", "type": "UINT8", "encoding": "CONSTANT", "value": "0xAA"},
        {"name": "ADDRESS", "type": "UINT8"},
        {"name": "COMMAND", "type": "UINT8", "encoding": "CONSTANT", "value": cmd},
        {"name": "LENGTH", "type": "UINT8", "encoding": "AUTO"},
    ]


def crc_field(alg="CRC8", **kw):
    size = {"CRC8": 1, "CRC16_MODBUS": 2, "CRC32": 4}.get(alg, 1)
    return {"name": "CRC", "type": {1: "UINT8", 2: "UINT16", 4: "UINT32"}[size], "encoding": alg, **kw}
