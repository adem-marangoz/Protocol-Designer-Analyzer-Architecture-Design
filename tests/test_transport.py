import socket
import threading
import time
import uuid

import pytest

from protocol_designer.protocol import Decoder, Encoder
from protocol_designer.protocol.model import TransportSettings, TransportType
from protocol_designer.transport import (
    CanTransport,
    LoopbackTransport,
    RS485Transport,
    SerialTransport,
    SimulatorTransport,
    TcpTransport,
    TransportError,
    UdpTransport,
    create_transport,
    list_can_interfaces,
    list_serial_ports,
)


def collect(transport, timeout=1.0, until=None):
    """Receive until ``until(bytes)`` is true or the timeout elapses."""
    data = b""
    frames = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = transport.receive(0.05)
        frames += got
        data += b"".join(f.data for f in got)
        if until and until(data, frames):
            break
    return data, frames


# ------------------------------------------------------------------ factory ---


@pytest.mark.parametrize(
    "ttype,cls",
    [
        (TransportType.UART, SerialTransport),
        (TransportType.RS485, RS485Transport),
        (TransportType.TCP, TcpTransport),
        (TransportType.UDP, UdpTransport),
        (TransportType.CAN, CanTransport),
        (TransportType.CANFD, CanTransport),
        (TransportType.LOOPBACK, LoopbackTransport),
    ],
)
def test_factory(ttype, cls):
    assert isinstance(create_transport(TransportSettings(type=ttype)), cls)


def test_factory_simulator_needs_protocol(tpms):
    with pytest.raises(TransportError):
        create_transport(TransportSettings(type=TransportType.SIMULATOR))
    assert isinstance(create_transport(TransportSettings(type=TransportType.SIMULATOR), tpms), SimulatorTransport)


def test_discovery_functions():
    assert isinstance(list_serial_ports(), list)
    assert "virtual" in list_can_interfaces()


# ----------------------------------------------------------------- loopback ---


def test_loopback_roundtrip():
    t = LoopbackTransport(TransportSettings(type=TransportType.LOOPBACK))
    with pytest.raises(TransportError):
        t.send(b"x")
    with t:
        assert t.is_connected()
        t.send(b"\x01\x02")
        frames = t.receive(0.5)
        assert [f.data for f in frames] == [b"\x01\x02"]
        assert t.receive(0.01) == []
    assert not t.is_connected()


def test_loopback_delayed_injection_respects_timeout():
    t = LoopbackTransport(TransportSettings(type=TransportType.LOOPBACK))
    t.open()
    t.inject(b"\xaa", delay_s=0.2)
    start = time.monotonic()
    assert t.receive(0.05) == []
    assert time.monotonic() - start < 0.15
    frames = t.receive(0.5)
    assert frames and frames[0].data == b"\xaa"


def test_loopback_receive_wakes_on_send_from_other_thread():
    t = LoopbackTransport(TransportSettings(type=TransportType.LOOPBACK))
    t.open()
    threading.Timer(0.05, lambda: t.send(b"\x42")).start()
    start = time.monotonic()
    frames = t.receive(2.0)
    assert frames[0].data == b"\x42"
    assert time.monotonic() - start < 1.0


# ------------------------------------------------------------------- serial ---


def serial_settings(**kw):
    s = TransportSettings(type=TransportType.UART, port="loop://", baudrate=115200)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def test_serial_loop_url():
    with SerialTransport(serial_settings()) as t:
        t.send(b"\xaa\x01\x10\x01\x05\x94")
        data, _ = collect(t, until=lambda d, f: len(d) >= 6)
        assert data == b"\xaa\x01\x10\x01\x05\x94"


@pytest.mark.parametrize("parity", ["NONE", "EVEN", "ODD", "MARK", "SPACE", "n", "e"])
def test_serial_parities(parity):
    with SerialTransport(serial_settings(parity=parity)) as t:
        assert t.is_connected()


@pytest.mark.parametrize("kw", [{"parity": "BAD"}, {"data_bits": 9}, {"stop_bits": 3}, {"port": ""}])
def test_serial_invalid_settings(kw):
    with pytest.raises(TransportError):
        SerialTransport(serial_settings(**kw)).open()


def test_serial_missing_port():
    with pytest.raises(TransportError, match="cannot open"):
        SerialTransport(serial_settings(port="/dev/does-not-exist-xyz")).open()


def test_rs485_local_echo_suppression():
    s = serial_settings(type=TransportType.RS485)
    s.options = {"local_echo": True, "rts_toggle": True}
    with RS485Transport(s) as t:
        t.send(b"\x01\x02\x03")  # loop:// echoes exactly what we sent
        data, _ = collect(t, timeout=0.3)
        assert data == b""


def test_rs485_without_echo_suppression_sees_bytes():
    with RS485Transport(serial_settings(type=TransportType.RS485)) as t:
        t.send(b"\x01\x02\x03")
        data, _ = collect(t, until=lambda d, f: len(d) >= 3)
        assert data == b"\x01\x02\x03"


# ---------------------------------------------------------------------- TCP ---


def _echo_server():
    server = socket.create_server(("127.0.0.1", 0))
    port = server.getsockname()[1]

    def run():
        conn, _ = server.accept()
        with conn:
            while True:
                data = conn.recv(1024)
                if not data:
                    break
                conn.sendall(data)
        server.close()

    threading.Thread(target=run, daemon=True).start()
    return port


def test_tcp_client_echo():
    port = _echo_server()
    t = TcpTransport(TransportSettings(type=TransportType.TCP, host="127.0.0.1", tcp_port=port))
    with t:
        t.send(b"hello")
        data, _ = collect(t, until=lambda d, f: d == b"hello")
        assert data == b"hello"


def test_tcp_connection_refused():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(TransportError, match="cannot connect"):
        TcpTransport(TransportSettings(type=TransportType.TCP, host="127.0.0.1", tcp_port=port)).open()


def test_tcp_listen_mode_and_peer_close():
    settings = TransportSettings(type=TransportType.TCP, host="127.0.0.1", tcp_port=0)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        settings.tcp_port = probe.getsockname()[1]
    settings.options = {"listen": True, "accept_timeout": 5}
    t = TcpTransport(settings)

    def client():
        for _ in range(50):
            try:
                c = socket.create_connection(("127.0.0.1", settings.tcp_port))
                break
            except OSError:
                time.sleep(0.05)
        c.sendall(b"\x01\x02")
        time.sleep(0.2)
        c.close()

    threading.Thread(target=client, daemon=True).start()
    t.open()
    try:
        data, _ = collect(t, until=lambda d, f: d == b"\x01\x02")
        assert data == b"\x01\x02"
        with pytest.raises(TransportError, match="closed"):
            collect(t, timeout=2.0)
        assert not t.is_connected()
    finally:
        t.close()


# ---------------------------------------------------------------------- UDP ---


def test_udp_datagrams():
    a = UdpTransport(TransportSettings(type=TransportType.UDP, host="127.0.0.1", tcp_port=1))
    a.open()
    b = UdpTransport(TransportSettings(type=TransportType.UDP, host="127.0.0.1", tcp_port=a.local_port))
    b.open()
    a.settings.tcp_port = b.local_port
    try:
        b.send(b"\x10\x20")
        b.send(b"\x30")
        _, frames = collect(a, until=lambda d, f: len(f) >= 2)
        assert [f.data for f in frames] == [b"\x10\x20", b"\x30"]
    finally:
        a.close()
        b.close()


# ---------------------------------------------------------------------- CAN ---


def can_settings(channel, fd=False):
    return TransportSettings(
        type=TransportType.CANFD if fd else TransportType.CAN, can_interface="virtual", can_channel=channel
    )


def test_can_virtual_bus():
    channel = f"test-{uuid.uuid4()}"
    with CanTransport(can_settings(channel)) as a, CanTransport(can_settings(channel)) as b:
        a.send(b"\x01\x02\x03", can_id=0x123)
        a.send(b"\xff", can_id=0x18FF0000)
        _, frames = collect(b, until=lambda d, f: len(f) >= 2)
        assert [(f.can_id, f.data, f.extended) for f in frames] == [
            (0x123, b"\x01\x02\x03", False),
            (0x18FF0000, b"\xff", True),
        ]
        assert b.message_oriented


def test_can_requires_id_and_limits_payload():
    with CanTransport(can_settings(f"t-{uuid.uuid4()}")) as t:
        with pytest.raises(TransportError, match="CAN id"):
            t.send(b"\x01")
        with pytest.raises(TransportError, match="exceed"):
            t.send(bytes(9), can_id=1)


def test_canfd_64_bytes():
    channel = f"fd-{uuid.uuid4()}"
    with CanTransport(can_settings(channel, fd=True)) as a, CanTransport(can_settings(channel, fd=True)) as b:
        a.send(bytes(range(64)), can_id=0x200)
        _, frames = collect(b, until=lambda d, f: len(f) >= 1)
        assert frames[0].data == bytes(range(64))
        assert frames[0].is_fd


def test_can_bad_interface():
    s = can_settings("x")
    s.can_interface = "no_such_interface"
    with pytest.raises(TransportError):
        CanTransport(s).open()


# ---------------------------------------------------------------- simulator ---


def test_simulator_replies_per_rules(tpms):
    t = SimulatorTransport(TransportSettings(type=TransportType.SIMULATOR), tpms)
    enc, dec = Encoder(tpms), Decoder(tpms)
    with t:
        assert not t.message_oriented
        t.send(enc.encode("READ_SENSOR", {"ADDRESS": 7, "SENSOR_ID": 3}).data)
        data, _ = collect(t, until=lambda d, f: len(d) >= 12)
        reply = dec.decode_any(data)
        assert reply.name == "SENSOR_DATA" and reply.valid
        assert reply.field("ADDRESS").value == 7  # copied
        assert reply.field("SENSOR_ID").value == 3  # copied
        assert reply.field("PRESSURE").value == pytest.approx(2.4)
        assert reply.field("STATUS").value == "ACTIVE"
        assert t.received[0].name == "READ_SENSOR"


def test_simulator_handles_split_requests(tpms):
    t = SimulatorTransport(TransportSettings(type=TransportType.SIMULATOR), tpms)
    packet = Encoder(tpms).encode("GET_INFO", {}).data
    with t:
        t.send(packet[:2])
        assert t.receive(0.05) == []
        t.send(packet[2:])
        data, _ = collect(t, until=lambda d, f: len(d) > 5)
        assert Decoder(tpms).decode_any(data).field("NAME").value == "TPMS-GW"


def test_simulator_ignores_invalid_and_unknown(tpms):
    t = SimulatorTransport(TransportSettings(type=TransportType.SIMULATOR), tpms)
    with t:
        t.send(bytes.fromhex("AA0110010500"))  # bad CRC
        t.send(Encoder(tpms).encode("ACK", {}).data)  # no rule for ACK
        assert collect(t, timeout=0.2)[0] == b""


def test_simulator_match_rules(protocols_dir):
    from protocol_designer.storage import load_protocol

    p = load_protocol(protocols_dir / "ecu_bootloader.json")
    enc, dec = Encoder(p), Decoder(p)
    t = SimulatorTransport(TransportSettings(type=TransportType.SIMULATOR), p)
    with t:
        t.send(enc.encode("SEND_KEY", {"KEY": 0x486E0C22}).data)
        data, _ = collect(t, until=lambda d, f: len(f) >= 1)
        assert dec.decode_any(data).field("RESULT").value == "OK"
        t.send(enc.encode("SEND_KEY", {"KEY": 1}).data)
        data, _ = collect(t, until=lambda d, f: len(f) >= 1)
        assert dec.decode_any(data).field("RESULT").value == "INVALID_KEY"


def test_simulator_message_oriented_for_can(protocols_dir):
    from protocol_designer.storage import load_protocol

    p = load_protocol(protocols_dir / "tpms_can.json")
    t = SimulatorTransport(TransportSettings(type=TransportType.SIMULATOR), p)
    with t:
        assert t.message_oriented
        req = p.frame("REQUEST_PRESSURE")
        t.send(Encoder(p).encode(req, {"WHEEL": "REAR_RIGHT"}).data, can_id=req.can_id)
        _, frames = collect(t, until=lambda d, f: len(f) >= 1)
        assert frames[0].can_id == 0x300
        reply = Decoder(p).decode_any(frames[0].data, frames[0].can_id)
        assert reply.name == "WHEEL_PRESSURE" and reply.valid
        assert reply.field("WHEEL").value == "REAR_RIGHT"
        assert reply.field("PRESSURE").value == pytest.approx(2.35)


def test_simulator_reply_delay(tpms):
    p = tpms.clone()
    p.simulator[0].delay_ms = 150
    t = SimulatorTransport(TransportSettings(type=TransportType.SIMULATOR), p)
    with t:
        t.send(Encoder(p).encode("READ_SENSOR", {"SENSOR_ID": 1}).data)
        assert t.receive(0.05) == []
        start = time.monotonic()
        frames = t.receive(1.0)
        assert frames and time.monotonic() - start < 0.5
