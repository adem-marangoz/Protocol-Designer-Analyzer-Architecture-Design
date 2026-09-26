import random

from protocol_designer.protocol import Encoder, StreamDecoder


def packets(tpms):
    enc = Encoder(tpms)
    return [
        enc.encode("READ_SENSOR", {"SENSOR_ID": 5}).data,
        enc.encode("SENSOR_DATA", {"SENSOR_ID": 5, "PRESSURE": 2.4, "TEMPERATURE": -5, "STATUS": "ACTIVE"}).data,
        enc.encode("DEVICE_INFO", {"NAME": "ABC"}).data,
        enc.encode("ACK", {"ACKED_COMMAND": 0x20}).data,
    ]


def names(events):
    return [e.message.name if e.kind == "message" else "garbage" for e in events]


def test_whole_packets(tpms):
    sd = StreamDecoder(tpms)
    events = sd.feed(b"".join(packets(tpms)))
    assert names(events) == ["READ_SENSOR", "SENSOR_DATA", "DEVICE_INFO", "ACK"]
    assert all(e.message.valid for e in events)
    assert sd.pending == b""


def test_byte_by_byte(tpms):
    sd = StreamDecoder(tpms)
    events = []
    for b in b"".join(packets(tpms)):
        events += sd.feed(bytes([b]))
    assert names(events) == ["READ_SENSOR", "SENSOR_DATA", "DEVICE_INFO", "ACK"]


def test_random_chunks(tpms):
    stream = b"".join(packets(tpms)) * 5
    rng = random.Random(1234)
    sd = StreamDecoder(tpms)
    events = []
    i = 0
    while i < len(stream):
        n = rng.randint(1, 9)
        events += sd.feed(stream[i : i + n])
        i += n
    assert len(events) == 20
    assert all(e.kind == "message" and e.message.valid for e in events)


def test_resync_after_garbage(tpms):
    p = packets(tpms)
    sd = StreamDecoder(tpms)
    events = sd.feed(b"\x00\x13\x37" + p[0] + b"\x99" + p[3])
    assert names(events) == ["garbage", "READ_SENSOR", "garbage", "ACK"]
    assert events[0].data == b"\x00\x13\x37"
    assert events[2].data == b"\x99"


def test_crc_error_frame_is_emitted_and_consumed(tpms):
    p = packets(tpms)
    bad = bytearray(p[0])
    bad[-1] ^= 0xFF
    sd = StreamDecoder(tpms)
    events = sd.feed(bytes(bad) + p[3])
    assert names(events) == ["READ_SENSOR", "ACK"]
    assert events[0].message.crc_valid is False
    assert events[1].message.valid


def test_partial_frame_waits_then_flush(tpms):
    p = packets(tpms)
    sd = StreamDecoder(tpms)
    assert sd.feed(p[1][:4]) == []
    assert sd.pending == p[1][:4]
    flushed = sd.flush()
    assert names(flushed) == ["garbage"]
    assert flushed[0].data == p[1][:4]
    assert sd.pending == b""


def test_flush_recovers_frames_after_truncated_frame(tpms):
    p = packets(tpms)
    sd = StreamDecoder(tpms)
    # A truncated SENSOR_DATA header whose LENGTH claims far more bytes than arrive
    events = sd.feed(bytes.fromhex("AA0111FF") + p[0])
    assert events == []  # waiting: could still be a long SENSOR_DATA
    events = sd.flush()
    assert "READ_SENSOR" in names(events)


def test_sof_inside_garbage(tpms):
    p = packets(tpms)
    sd = StreamDecoder(tpms)
    events = sd.feed(b"\xaa\xaa" + p[0])
    assert names(events) == ["garbage", "READ_SENSOR"]
    assert events[0].data == b"\xaa\xaa"


def test_reset(tpms):
    sd = StreamDecoder(tpms)
    sd.feed(b"\xaa\x01")
    sd.reset()
    assert sd.pending == b""
