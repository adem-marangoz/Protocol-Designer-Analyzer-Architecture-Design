# Protocol Definition Format

A protocol file is JSON (`.json` or `.pdproj`). This is the format from Section 8 of the design
document, extended with enumerations, tests and simulator rules. Unknown transport keys are
preserved when the file is saved.

```json
{
  "format_version": 1,
  "protocol":  { "name": "TPMS RS485 Protocol", "version": "1.0", "description": "", "endianness": "BIG" },
  "transport": { "type": "RS485", "port": "COM3", "baudrate": 115200, "data_bits": 8, "parity": "NONE", "stop_bits": 1 },
  "enums":     { "SENSOR_STATUS": { "values": { "0x00": "OFF", "0x01": "ACTIVE", "0x02": "ERROR" } } },
  "frames":    [ { "name": "READ_SENSOR", "id": 16, "direction": "TX", "fields": [ ... ] } ],
  "tests":     [ ... ],
  "simulator": [ ... ]
}
```

Numbers can be written as `170`, `"0xAA"`, `"0b1010"` or `"AAh"`.

## protocol

| Key | Default | Meaning |
| --- | --- | --- |
| `name`, `version`, `description` | | shown in the program and reports |
| `endianness` | `BIG` | default byte order: `BIG` or `LITTLE` |

## transport

| Key | Used by | Default |
| --- | --- | --- |
| `type` | all | `RS485`. Other values: `UART` (alias `SERIAL`), `TCP`, `UDP`, `CAN`, `CANFD`, `LOOPBACK`, `SIMULATOR` |
| `port`, `baudrate`, `data_bits`, `parity` (`NONE/EVEN/ODD/MARK/SPACE`), `stop_bits` (1, 1.5, 2) | UART, RS485 | `""`, 115200, 8, NONE, 1 |
| `host`, `tcp_port` | TCP, UDP | 127.0.0.1, 5000 |
| `can_interface`, `can_channel`, `can_bitrate`, `can_data_bitrate` | CAN, CANFD | virtual, 0, 500000, 2000000 |
| `timeout_ms` | all | 100 |
| `options` | see below | `{}` |

Options: `local_echo`, `rts_toggle` (RS485); `listen`, `accept_timeout` (TCP); `local_port` (UDP);
`receive_own_messages`, `can_kwargs` (CAN, passed to `python-can`).

## enums

```json
"enums": { "NAME": { "description": "...", "values": { "0x00": "OFF", "0x01": "ON" } } }
```

A shorter form `"NAME": { "0": "OFF", "1": "ON" }` is also accepted. A field or bit group can
also define its enumeration inline: `"enum": { "0": "OFF", "1": "ON" }`.

## frames

| Key | Meaning |
| --- | --- |
| `name` | unique message name |
| `id` | command / message ID (informational; recognition uses the CONSTANT fields) |
| `direction` | `TX`, `RX` or `BOTH` |
| `can_id`, `can_extended` | CAN arbitration ID; frames with a CAN ID are recognised by it |
| `expected_response` | name of the reply message (used by `transact` test steps) |
| `description` | free text |
| `fields` | ordered list of fields (below) |

A frame may contain **one** variable-size field (BYTES or STRING without `size`). Its size comes
from a LENGTH field that covers it. Without one, the frame must arrive as one complete message
(for example over CAN), and the field takes the bytes left over before the trailing fixed-size
fields.

## fields

| Key | Default | Meaning |
| --- | --- | --- |
| `name` | | unique within the frame |
| `type` | `UINT8` | `UINT8/16/32/64`, `INT8/16/32/64`, `FLOAT32/64`, `BOOLEAN`, `ENUM`, `BITFIELD`, `BYTES`, `STRING` |
| `size` | by type | bytes. BOOLEAN/ENUM/BITFIELD: 1, 2, 3, 4 or 8. BYTES/STRING: fixed size, or omit for variable |
| `offset` | | optional; if given, the validator checks it matches the layout |
| `encoding` | `VALUE` | `VALUE`, `CONSTANT` (`CONST`), `LENGTH` (`AUTO`), `CRC`, or a CRC name such as `CRC8` |
| `value` | | the constant for CONSTANT fields (number, or hex bytes for BYTES) |
| `endianness` | protocol | `BIG` or `LITTLE` |
| `crc` | `CRC8` | algorithm name, or a custom definition `{ "width": 16, "poly": "0x1021", "init": "0xFFFF", "refin": false, "refout": false, "xorout": 0 }` |
| `crc_from`, `crc_to` | first field, field before the CRC | range of fields covered by the CRC (inclusive) |
| `length_from`, `length_to` | next field, field before the first following CRC | range counted by a LENGTH field |
| `length_adjust` | 0 | added to the counted size |
| `scale`, `value_offset`, `unit` | 1, 0, "" | `physical = raw × scale + value_offset` |
| `min`, `max` | | physical limits |
| `enum` | | enumeration name or inline table |
| `bits` | | BITFIELD groups: `{ "name": "Sensor State", "start": 3, "length": 2, "enum": "SENSOR_STATE" }`, or `"bits": "3-4"` |
| `default` | 0 / "" | value used by the builder when none is given |
| `string_encoding` | `ascii` | for STRING, e.g. `utf-8`, `latin-1` |
| `description` | | free text |

### CRC and checksum algorithms

`CRC8`, `CRC8_MAXIM`, `CRC8_SAE_J1850`, `CRC8_AUTOSAR`, `CRC8_CDMA2000`, `CRC16_MODBUS`,
`CRC16_CCITT_FALSE`, `CRC16_XMODEM`, `CRC16_KERMIT`, `CRC16_ARC`, `CRC16_USB`, `CRC32`,
`CRC32_MPEG2`, `CRC32C`, `SUM8`, `SUM16`, `XOR8` (LRC) and `TWOS_COMPLEMENT8`, plus custom
parametric CRCs. Aliases such as `CRC-16/MODBUS` and `CRC16` are accepted. Run `pdcli crc --list`
for the full list.

## tests

```json
{ "name": "Flash", "description": "", "timeout_ms": 500, "steps": [ { "action": "...", ... } ] }
```

| action | keys |
| --- | --- |
| `send` | `message`, `values` |
| `expect` | `message`, `timeout_ms`, `checks` |
| `transact` | `message`, `values`, `timeout_ms`, `checks` (applied to the expected response) |
| `send_raw` | `data` (hex) |
| `delay` | `delay_ms` |
| `set` | `variable`, `value` (literal or expression with `${var}`, `+ - * / // % << >> & \| ^ ~`) |
| `log` | `text` (may contain `${var}`) |

A check is `{ "field": "...", "equals" | "not_equals" | "min" | "max": ..., "raw": false, "save_as": "var" }`.
`equals` accepts numbers, enum names, strings, hex bytes, or a bit dictionary.

## simulator

```json
{ "on": "READ_SENSOR", "reply": "SENSOR_DATA", "copy": ["ADDRESS"], "values": { "PRESSURE": 2.4 },
  "match": { "SENSOR_ID": 5 }, "delay_ms": 0 }
```

The first rule whose `on` and `match` fit the received request is used. A rule without `reply`
makes the device stay silent.
