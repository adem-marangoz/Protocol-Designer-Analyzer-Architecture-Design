import time

import pytest

from protocol_designer.application.logger import ERROR, INFO, RX, TX
from protocol_designer.application.session import Session
from protocol_designer.protocol import Encoder
from protocol_designer.protocol.model import TransportSettings, TransportType
from protocol_designer.transport import LoopbackTransport, TransportError


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def rx(session):
    return [e for e in session.traffic.entries if e.direction == RX]


def test_simulator_session_decodes_reply(tpms):
    states = []
    session = Session(tpms)
    session.on_state_changed(lambda ok, msg: states.append(ok))
    with session:
        assert session.connected
        enc = session.send_message("READ_SENSOR", {"ADDRESS": 1, "SENSOR_ID": 5})
        assert enc.hex == "AA 01 10 01 05 94"
        assert wait_until(lambda: rx(session))
        entry = rx(session)[0]
        assert entry.message.name == "SENSOR_DATA"
        assert entry.status == "OK"
    directions = [e.direction for e in session.traffic.entries]
    assert directions[0] == INFO and directions[1] == TX and directions[-1] == INFO
    assert session.traffic.entries[1].message.name == "READ_SENSOR"
    assert states == [True, False]
    assert not session.connected


def test_loopback_partial_frame_is_flushed_after_idle(tpms):
    transport = LoopbackTransport(TransportSettings(type=TransportType.LOOPBACK))
    session = Session(tpms, transport, idle_flush_ms=30)
    with session:
        session.send_raw(b"\xaa\x01\x11")  # incomplete SENSOR_DATA header
        assert wait_until(lambda: rx(session))
        entry = rx(session)[0]
        assert entry.message is None and entry.data == b"\xaa\x01\x11"
        assert entry.status == "UNKNOWN" and entry.name == "??"


def test_loopback_split_chunks_are_reassembled(tpms):
    transport = LoopbackTransport(TransportSettings(type=TransportType.LOOPBACK))
    session = Session(tpms, transport, idle_flush_ms=500)
    packet = Encoder(tpms).encode("SENSOR_DATA", {"PRESSURE": 2.0}).data
    with session:
        transport.inject(packet[:3])
        transport.inject(packet[3:], delay_s=0.05)
        assert wait_until(lambda: rx(session))
        assert rx(session)[0].message.name == "SENSOR_DATA"


def test_crc_error_status(tpms):
    transport = LoopbackTransport(TransportSettings(type=TransportType.LOOPBACK))
    session = Session(tpms, transport)
    with session:
        transport.inject(bytes.fromhex("AA0110010500"))
        assert wait_until(lambda: rx(session))
        assert rx(session)[0].status == "CRC ERROR"


def test_subscribers_receive_entries(tpms):
    session = Session(tpms)
    with session:
        q = session.subscribe()
        session.send_message("GET_INFO", {})
        seen = []
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not any(e.direction == RX for e in seen):
            try:
                seen.append(q.get(timeout=0.1))
            except Exception:
                pass
        session.unsubscribe(q)
        assert [e.direction for e in seen][:1] == [TX]
        assert any(e.direction == RX and e.message.name == "DEVICE_INFO" for e in seen)


def test_send_when_disconnected(tpms):
    with pytest.raises(TransportError):
        Session(tpms).send_message("READ_SENSOR", {})


def test_connect_failure_is_logged(tpms):
    p = tpms.clone()
    p.transport.type = TransportType.UART
    p.transport.port = "/dev/nonexistent-port-xyz"
    session = Session(p)
    states = []
    session.on_state_changed(lambda ok, msg: states.append((ok, msg)))
    with pytest.raises(TransportError):
        session.connect()
    assert session.traffic.entries[-1].direction == ERROR
    assert states and states[0][0] is False
    assert session.last_error


class _Failing(LoopbackTransport):
    def receive(self, timeout=0.1):
        raise TransportError("cable unplugged")


def test_connection_lost(tpms):
    session = Session(tpms, _Failing(TransportSettings(type=TransportType.LOOPBACK)))
    states = []
    session.on_state_changed(lambda ok, msg: states.append((ok, msg)))
    session.connect()
    assert wait_until(lambda: not session.connected)
    assert "cable unplugged" in session.last_error
    assert states[-1][0] is False and "Connection lost" in states[-1][1]
    session.disconnect()


def test_can_session_uses_can_ids(protocols_dir):
    from protocol_designer.storage import load_protocol

    p = load_protocol(protocols_dir / "tpms_can.json")
    session = Session(p)
    with session:
        session.send_message("REQUEST_PRESSURE", {"WHEEL": "FRONT_RIGHT"})
        assert wait_until(lambda: rx(session))
        entry = rx(session)[0]
        assert entry.can_id == 0x300
        assert entry.message.field("WHEEL").value == "FRONT_RIGHT"
    assert session.traffic.entries[1].can_id == 0x301
