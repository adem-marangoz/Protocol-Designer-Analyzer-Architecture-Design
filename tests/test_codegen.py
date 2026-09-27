"""The generated code must agree byte-for-byte with the protocol engine."""

import importlib.util
import os
import random
import shutil
import subprocess
import sys

import pytest

from helpers import make_protocol

from protocol_designer.application.codegen import LANGUAGES, generate, write_files
from protocol_designer.application.codegen.plan import build_plan, pascal, snake
from protocol_designer.protocol import Decoder, Encoder
from protocol_designer.protocol.errors import DefinitionError
from protocol_designer.protocol.fields import fixed_size, raw_range, resolve_enum
from protocol_designer.protocol.model import Encoding, FieldType
from protocol_designer.storage import load_protocol

EXAMPLES = ["tpms_rs485", "tpms_can", "ecu_bootloader"]

EXTRA = {
    "format_version": 1,
    "protocol": {"name": "Edge Cases", "endianness": "LITTLE"},
    "enums": {"MODE": {"values": {"0": "IDLE", "7": "DEFAULT"}}},
    "frames": [
        {"name": "ALL_TYPES", "fields": [
            {"name": "SYNC", "type": "UINT16", "encoding": "CONSTANT", "value": "0xBEEF", "endianness": "BIG"},
            {"name": "LEN", "type": "UINT16", "encoding": "LENGTH", "length_adjust": 2},
            {"name": "U64", "type": "UINT64"},
            {"name": "I8", "type": "INT8"},
            {"name": "I16", "type": "INT16", "endianness": "BIG"},
            {"name": "I32", "type": "INT32"},
            {"name": "I64", "type": "INT64"},
            {"name": "F32", "type": "FLOAT32"},
            {"name": "F64", "type": "FLOAT64", "endianness": "BIG"},
            {"name": "FLAG", "type": "BOOLEAN"},
            {"name": "MODE", "type": "ENUM", "size": 2, "enum": "MODE"},
            {"name": "BITS", "type": "BITFIELD", "size": 3, "bits": [{"name": "A", "start": 0}, {"name": "B", "start": 12, "length": 8}]},
            {"name": "TAG", "type": "STRING", "size": 6},
            {"name": "RAW", "type": "BYTES", "size": 3},
            {"name": "DATA", "type": "BYTES"},
            {"name": "SUM", "type": "UINT8", "encoding": "SUM8", "crc_from": "U64"},
            {"name": "CRC", "type": "UINT32", "encoding": "CRC32"},
        ]},
        {"name": "TAIL", "fields": [
            {"name": "ID", "type": "UINT8", "encoding": "CONSTANT", "value": 9},
            {"name": "TEXT", "type": "STRING"},
            {"name": "X", "type": "UINT8", "encoding": "XOR8"},
            {"name": "T", "type": "UINT8", "encoding": "TWOS_COMPLEMENT8", "crc_from": "ID", "crc_to": "TEXT"},
            {"name": "S", "type": "UINT16", "encoding": "SUM16"},
        ]},
        {"name": "EMPTY", "fields": [{"name": "K", "type": "UINT8", "encoding": "CONSTANT", "value": "0x42"},
                                     {"name": "default", "type": "UINT8"}]},
    ],
}


def extra_protocol():
    from protocol_designer.storage import protocol_from_dict

    return protocol_from_dict(EXTRA)


def sample_values(protocol, frame, rng):
    values = {}
    for f in frame.user_fields:
        size = fixed_size(f)
        if f.type == FieldType.BYTES:
            values[f.name] = bytes(rng.randrange(256) for _ in range(size if size else rng.randint(0, 6)))
        elif f.type == FieldType.STRING:
            n = size if size else rng.randint(0, 8)
            values[f.name] = "".join(rng.choice("ABCXYZ019-") for _ in range(rng.randint(0, n)))
        elif f.type == FieldType.BOOLEAN:
            values[f.name] = rng.random() < 0.5
        elif f.type in (FieldType.FLOAT32, FieldType.FLOAT64):
            values[f.name] = rng.choice([1.5, -2.25, 1024.0625])
        elif f.type == FieldType.BITFIELD:
            values[f.name] = {b.name: rng.randrange(1 << b.length) for b in f.bits}
        elif f.enum is not None:
            values[f.name] = rng.choice(list(resolve_enum(f.enum, protocol).values()))
        else:
            lo, hi = raw_range(f, size)
            if f.minimum is not None:
                lo = max(lo, int((f.minimum - f.value_offset) / f.scale) + 1)
            if f.maximum is not None:
                hi = min(hi, int((f.maximum - f.value_offset) / f.scale) - 1)
            raw = rng.randint(lo, hi)
            values[f.name] = raw
    return values


def vectors(protocol, count=6):
    """(frame_name, bytes) pairs covering every frame with random values."""
    rng = random.Random(42)
    enc = Encoder(protocol)
    out = []
    for frame in protocol.frames:
        for _ in range(count):
            out.append((frame.name, enc.encode(frame, sample_values(protocol, frame, rng), raw=True).data))
    return out


def all_protocols(protocols_dir):
    return [(n, load_protocol(protocols_dir / f"{n}.json")) for n in EXAMPLES] + [("edge_cases", extra_protocol())]


# --------------------------------------------------------------- Python ---


def load_generated_python(tmp_path, protocol, name):
    files = generate(protocol, "python", basename=name)
    write_files(files, tmp_path)
    spec = importlib.util.spec_from_file_location(name, tmp_path / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("which", EXAMPLES + ["edge_cases"])
def test_python_roundtrip(tmp_path, protocols_dir, which):
    protocol = dict(all_protocols(protocols_dir))[which]
    mod = load_generated_python(tmp_path, protocol, f"gen_{which}")
    decoder = Decoder(protocol)
    for frame_name, data in vectors(protocol):
        cls = getattr(mod, pascal(frame_name))
        msg = cls.decode(data)
        assert msg.encode() == data, frame_name
        assert type(mod.identify(data)).__name__ == cls.__name__ or decoder.identify(data).name != frame_name
        engine = decoder.decode(frame_name, data)
        for f in engine.fields:
            if f.encoding == Encoding.VALUE:
                generated = getattr(msg, snake(f.name))
                expected = f.raw
                if isinstance(expected, float):
                    assert generated == pytest.approx(expected)
                elif f.type == FieldType.BOOLEAN:
                    assert generated == bool(expected)
                else:
                    assert generated == expected, (frame_name, f.name)
        bad = bytearray(data)
        bad[-1] ^= 0x01
        crcs = [f for f in protocol.frame(frame_name).fields if f.encoding == Encoding.CRC]
        if crcs:
            with pytest.raises(mod.DecodeError):
                cls.decode(bytes(bad))


def test_python_enum_and_constants(tmp_path, tpms):
    mod = load_generated_python(tmp_path, tpms, "gen_consts")
    assert mod.SensorStatus.ACTIVE == 1
    assert mod.SensorData.PRESSURE_SCALE == 0.1
    assert mod.ReadSensor.ID == 16
    assert mod.ReadSensor(address=1, sensor_id=5).encode() == bytes.fromhex("AA0110010594")
    with pytest.raises(ValueError):
        mod.ReadSensor(address=300).encode()


# ------------------------------------------------------------------ C/C++ ---

HARNESS_C = r"""
#include "{name}.h"
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static size_t parse_hex(const char *s, uint8_t *out)
{{
    size_t n = 0;
    while (s[0] && s[1]) {{ unsigned v; sscanf(s, "%2x", &v); out[n++] = (uint8_t)v; s += 2; }}
    return n;
}}

int main(void)
{{
    char frame[128], hex[4096];
    uint8_t in[2048], out[2048];
    while (scanf("%127s %4095s", frame, hex) == 2) {{
        size_t len = strcmp(hex, "-") == 0 ? 0 : parse_hex(hex, in);
        size_t olen = 0;
        int ok = 0;
{branches}
        if (!ok) {{ printf("FAIL\n"); continue; }}
        printf("%d ", (int){prefix}_Identify(in, len));
        for (size_t i = 0; i < olen; ++i) printf("%02X", out[i]);
        printf("\n");
    }}
    return 0;
}}
"""


def c_harness(protocol, name):
    plan = build_plan(protocol)
    branches = []
    for fr in plan.frames:
        fname = fr.type_name[len(plan.type_prefix):]
        branches.append(
            f'        if (strcmp(frame, "{fr.frame.name}") == 0) {{ {fr.type_name}_t m; '
            f"ok = {plan.prefix}_Decode_{fname}(in, len, &m) && {plan.prefix}_Encode_{fname}(&m, out, sizeof out, &olen); }}"
        )
    return HARNESS_C.format(name=name, branches="\n".join(branches), prefix=plan.prefix), plan


def run_harness(cmd, protocol, vecs):
    lines = "".join(f"{frame} {data.hex().upper() or '-'}\n" for frame, data in vecs)
    result = subprocess.run(cmd, input=lines, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return result.stdout.splitlines()


def check_harness_output(protocol, plan, vecs, output):
    order = [fr.frame.name for fr in plan.frames]
    decoder = Decoder(protocol)
    assert len(output) == len(vecs)
    for (frame, data), line in zip(vecs, output):
        assert line != "FAIL", f"{frame} {data.hex()}"
        ident, hexed = (line.split(" ") + [""])[:2]
        assert bytes.fromhex(hexed) == data, frame
        identified = order[int(ident) - 1] if int(ident) else None
        assert identified == decoder.identify(data).name


def corrupt(vecs, protocol):
    out = []
    for frame, data in vecs:
        if any(f.encoding == Encoding.CRC for f in protocol.frame(frame).fields):
            bad = bytearray(data)
            bad[-1] ^= 0x80
            out.append((frame, bytes(bad)))
    return out


@pytest.mark.skipif(shutil.which("gcc") is None, reason="gcc not available")
@pytest.mark.parametrize("which", EXAMPLES + ["edge_cases"])
def test_c_roundtrip(tmp_path, protocols_dir, which):
    protocol = dict(all_protocols(protocols_dir))[which]
    name = f"gen_{which}"
    write_files(generate(protocol, "c", basename=name), tmp_path)
    harness, plan = c_harness(protocol, name)
    (tmp_path / "main.c").write_text(harness, encoding="utf-8")
    exe = tmp_path / "harness"
    subprocess.run(
        ["gcc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-pedantic", "-O2", "-I", str(tmp_path),
         str(tmp_path / f"{name}.c"), str(tmp_path / "main.c"), "-o", str(exe)],
        check=True, capture_output=True, text=True,
    )
    vecs = vectors(protocol)
    check_harness_output(protocol, plan, vecs, run_harness([str(exe)], protocol, vecs))
    bad = corrupt(vecs, protocol)
    assert all(line == "FAIL" for line in run_harness([str(exe)], protocol, bad))


@pytest.mark.skipif(shutil.which("g++") is None, reason="g++ not available")
def test_cpp_wrappers(tmp_path, tpms):
    write_files(generate(tpms, "cpp", basename="tpms"), tmp_path)
    (tmp_path / "main.cpp").write_text(r"""
#include "tpms.hpp"
#include <cstdio>
int main() {
    tpms_rs485_protocol::ReadSensor m;
    m.address = 1; m.sensor_id = 5;
    auto bytes = m.encode();
    for (auto b : bytes) std::printf("%02X", b);
    auto back = tpms_rs485_protocol::ReadSensor::decode(bytes);
    std::printf(" %d %d", back.has_value(), back ? back->sensor_id : -1);
    bytes.back() ^= 1;
    std::printf(" %d %d\n", tpms_rs485_protocol::ReadSensor::decode(bytes).has_value(),
                (int)tpms_rs485_protocol::identify(bytes));
    return 0;
}
""", encoding="utf-8")
    subprocess.run(["gcc", "-std=c99", "-c", str(tmp_path / "tpms.c"), "-o", str(tmp_path / "tpms.o")], check=True)
    subprocess.run(
        ["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(tmp_path), str(tmp_path / "main.cpp"),
         str(tmp_path / "tpms.o"), "-o", str(tmp_path / "cpp")],
        check=True, capture_output=True, text=True,
    )
    out = subprocess.run([str(tmp_path / "cpp")], capture_output=True, text=True, check=True).stdout.split()
    assert out == ["AA0110010594", "1", "5", "0", "0"]


# --------------------------------------------------------------------- C# ---

HARNESS_CS = r"""
using System;
using System.Linq;
class Harness {{
    static byte[] Hex(string s) => s == "-" ? new byte[0] : Enumerable.Range(0, s.Length / 2).Select(i => Convert.ToByte(s.Substring(i * 2, 2), 16)).ToArray();
    static void Main() {{
        string line;
        while ((line = Console.ReadLine()) != null) {{
            var parts = line.Split(' ');
            var d = Hex(parts[1]);
            byte[] o = null;
            switch (parts[0]) {{
{cases}
            }}
            if (o == null) {{ Console.WriteLine("FAIL"); continue; }}
            var ident = {ns}.Messages.Identify(d);
            Console.WriteLine((ident == null ? "0" : ident.GetType().Name) + " " + BitConverter.ToString(o).Replace("-", ""));
        }}
    }}
}}
"""


@pytest.mark.skipif(shutil.which("dotnet") is None, reason=".NET SDK not available")
@pytest.mark.parametrize("which", ["tpms_rs485", "edge_cases"])
def test_csharp_roundtrip(tmp_path, protocols_dir, which):
    protocol = dict(all_protocols(protocols_dir))[which]
    plan = build_plan(protocol)
    ns = pascal(plan.prefix)
    write_files(generate(protocol, "csharp", basename="gen"), tmp_path)
    cases = "\n".join(
        f'                case "{fr.frame.name}": if ({ns}.{pascal(fr.frame.name)}.TryDecode(d, out var m{i})) o = m{i}.Encode(); break;'
        for i, fr in enumerate(plan.frames)
    )
    (tmp_path / "Harness.cs").write_text(HARNESS_CS.format(cases=cases, ns=ns), encoding="utf-8")
    (tmp_path / "h.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
        "<TargetFramework>net8.0</TargetFramework><Nullable>disable</Nullable>"
        "<TreatWarningsAsErrors>true</TreatWarningsAsErrors></PropertyGroup></Project>",
        encoding="utf-8",
    )
    env = dict(os.environ, DOTNET_CLI_TELEMETRY_OPTOUT="1", DOTNET_NOLOGO="1")
    build = subprocess.run(["dotnet", "build", "-nologo", "-v", "q", "-o", str(tmp_path / "out")],
                           cwd=tmp_path, capture_output=True, text=True, env=env, timeout=600)
    assert build.returncode == 0, build.stdout + build.stderr
    dll = tmp_path / "out" / "h.dll"
    vecs = vectors(protocol)
    output = run_harness(["dotnet", str(dll)], protocol, vecs)
    decoder = Decoder(protocol)
    for (frame, data), line in zip(vecs, output):
        ident, hexed = line.split(" ") if " " in line else (line, "")
        assert line != "FAIL", frame
        assert bytes.fromhex(hexed) == data
        assert ident == pascal(decoder.identify(data).name)
    assert all(line == "FAIL" for line in run_harness(["dotnet", str(dll)], protocol, corrupt(vecs, protocol)))


# ------------------------------------------------------------------ misc ---


def test_languages_and_aliases(tpms):
    assert set(LANGUAGES) == {"c", "cpp", "python", "csharp"}
    assert set(generate(tpms, "C++", basename="x")) == {"x.c", "x.h", "x.hpp"}
    assert set(generate(tpms, "c#", basename="x")) == {"x.cs"}
    assert set(generate(tpms, "py", basename="x")) == {"x.py"}
    with pytest.raises(ValueError):
        generate(tpms, "cobol")


def test_invalid_protocol_is_refused():
    p = make_protocol([{"name": "A", "fields": [{"name": "X", "type": "UINT8"}, {"name": "X", "type": "UINT8"}]}])
    with pytest.raises(DefinitionError, match="fix the protocol errors"):
        generate(p, "c")


def test_reserved_identifiers():
    assert snake("default") == "default_"
    assert snake("SENSOR_ID") == "sensor_id"
    assert snake("Sensor Active") == "sensor_active"
    assert snake("2ND") == "f_2nd"
    assert pascal("READ_SENSOR") == "ReadSensor"


def test_c_header_contents(tpms):
    files = generate(tpms, "c", basename="tpms", prefix="TPMS")
    h = files["tpms.h"]
    assert "typedef struct" in h and "TpmsReadSensor_t" in h
    assert "bool TPMS_Encode_ReadSensor(const TpmsReadSensor_t *msg" in h
    assert "#define TPMS_SENSOR_DATA_PRESSURE_SCALE (0.1)" in h
    assert "TPMS_SENSOR_STATUS_ACTIVE = 0x1" in h
    assert "#define TPMS_SENSOR_DATA_FLAGS_SENSOR_STATE_MASK 0x18u" in h
