# User Guide — Protocol Designer & Analyzer

The core idea: **you describe the protocol; the program does the encoding, decoding, CRC and
length work.** A protocol file is the single source of truth for the Packet Builder, the
Analyzer, the Live Monitor, the Test Runner and the Code Generator.

The window has a navigation list on the left. `Ctrl+1` … `Ctrl+9` jump to the pages, `Ctrl+S`
saves, and `F5` connects or disconnects.

## 1. Dashboard

Shows the open protocol, its transport, any validation problems, and recent files. Double-click a
recent file to open it. Validation runs continuously, so mistakes show up here as you edit
(for example a CRC field that doesn't fit its algorithm, or a message referencing an unknown
enumeration).

## 2. Protocols (Protocol Designer)

- **Protocol & Messages**: name, version, description, and the default byte order (BIG or
  LITTLE; each field can override it). The message table lists every message with its ID,
  direction, CAN ID and expected response. Use *Add / Duplicate / Remove / Move*;
  double-click a message to edit its fields.
- **Transport**: the transport only moves bytes, so the same protocol can run over:
  - `RS485` / `UART`: port (COM3, /dev/ttyUSB0, or `loop://` for a loopback), baud rate, data
    bits, parity and stop bits. For RS485, *Options* accepts `{"local_echo": true}` for
    adapters that echo what they send, and `{"rts_toggle": true}` to drive RTS as the
    driver-enable line.
  - `TCP` (client; `{"listen": true}` to wait for a device to connect) and `UDP`.
  - `CAN` / `CANFD` using python-can: interface `pcan`, `vector`, `kvaser`, `ixxat`,
    `slcan`, `socketcan`, or `virtual`, plus channel and bit rates. CAN frames are identified
    by their CAN ID.
  - `SIMULATOR`: a virtual device that answers requests. Useful for learning, demos and
    developing tests before the hardware exists.
  - `LOOPBACK`: everything sent is received back.
- **Enumerations**: value → name tables, so the monitor shows `STATUS = ERROR` instead of
  `0x02`.
- **Device Simulator**: rules such as *"on READ_SENSOR reply SENSOR_DATA with PRESSURE=2.4, and
  copy ADDRESS and SENSOR_ID from the request"*. A rule can also require certain request
  values (`match`), for example to accept only the correct bootloader key.

## 3. Messages (Message Designer)

Choose a message. The top section holds its name, command ID, direction, CAN ID and
*expected response*, which the Test Runner's `transact` step uses. The table lists the fields
with their computed offsets. Select a field to edit it on the right:

| Property | Meaning |
| --- | --- |
| Type | UINT8/16/32/64, INT8/16/32/64, FLOAT32/64, BOOLEAN, ENUM, BITFIELD, BYTES, STRING |
| Size | bytes. For BYTES/STRING, `auto / variable` means the size comes from a LENGTH field |
| Encoding | `VALUE` (you supply it), `CONSTANT` (fixed, e.g. SOF `0xAA`; also used to recognise the message), `LENGTH` (calculated), `CRC` (calculated) |
| CRC algorithm / from / to | e.g. CRC8, CRC16_MODBUS, CRC32. By default the CRC covers every byte before it |
| Length counts from / to / adjust | by default LENGTH counts the fields after it up to the CRC (the payload) |
| Scale / Value offset / Unit | `value = raw × scale + offset`, e.g. raw 250 × 0.1 = 25.0 bar |
| Minimum / Maximum | physical limits: the builder refuses values outside them, and the analyzer warns |
| Enumeration | show names instead of numbers |
| Bit groups | for BITFIELD: named bits or bit ranges (e.g. bits 3–4 = *Sensor State*), optionally with an enumeration |

Press **Apply field**. The *Example packet* box shows the message encoded with default values,
plus any problems found in this message.

## 4. Packet Builder

Choose a message and fill in the form. Enumerations appear as drop-down lists and bit fields as
checkboxes. Press **BUILD** to get the raw packet, e.g. `AA 01 10 01 05 94`, and a table showing
each field's offset and bytes. **Send** transmits the packet when you are connected (optionally
several times).

## 5. Analyzer

Paste bytes in hex (spaces, commas or `0x` prefixes are all accepted) or load a binary file.
Frames are found automatically, even several in a row with garbage in between, and decoded into
a tree: message, then fields, then bit groups. CRC and length problems are shown in red. Choose
a message under *Decode as* to force decoding as that message. For CAN, enter the CAN ID.

## 6. Monitor (Live Monitor)

**Connect** opens the configured transport. Every frame appears with time, direction, raw data,
message name and status (`OK`, `WARN` for a value out of range, `CRC ERROR`, or `UNKNOWN` for
bytes that match no message). Select a row to see it decoded. You can filter TX/RX/events,
pause the display, clear the log, or export it to CSV/TXT. Use *Quick send* for a message with
default values, *Edit in builder* to set values first, or *Send raw* for arbitrary bytes.

Frame detection copes with bytes that arrive in pieces, and resynchronises after noise. A partial
frame is reported as unknown bytes after a short idle time.

## 7. Test (Test Runner)

A test is a list of steps, written as JSON:

```json
{
  "name": "Sensor Read",
  "timeout_ms": 500,
  "steps": [
    { "action": "send", "message": "READ_SENSOR", "values": { "ADDRESS": 1, "SENSOR_ID": 5 } },
    { "action": "expect", "message": "SENSOR_DATA", "checks": [
        { "field": "SENSOR_ID", "equals": 5 },
        { "field": "PRESSURE", "min": 1.5, "max": 3.5 },
        { "field": "STATUS", "equals": "ACTIVE" } ] }
  ]
}
```

| Step | Purpose |
| --- | --- |
| `send` | build and send a message (`values` as in the builder) |
| `expect` | wait up to `timeout_ms` for a message and check its fields; CRC validity is checked automatically |
| `transact` | `send` + `expect` the message's *expected response* |
| `send_raw` | send hex bytes, e.g. to test how the device handles a bad CRC |
| `delay` | wait `delay_ms` |
| `set` | set a variable: `"value": "${seed} ^ 0x5A5A5A5A"` (integer arithmetic) |
| `log` | add a note to the report: `"text": "seed was ${seed}"` |

A check can use `equals`, `not_equals`, `min`/`max` (physical values), `"raw": true` to compare
the raw number, and `save_as` to store the value in a variable. The example
`ecu_bootloader.json` uses these to unlock with seed and key, download data, verify and reset.

*Insert step* adds a template step. *Apply changes* saves the test into the protocol. *RUN TEST*
and *Run all* execute tests in the background (*Stop* cancels). The result appears as a ✓/✗ list,
like the design document, ending with `RESULT: PASS` or `FAIL`. An HTML report is saved to
`Documents\ProtocolDesigner\TestResults`.

## 8. Code Generator

Choose C, C++, Python or C#, then *Preview* or *Generate files*:

- **C (C99)**: `name.h` / `name.c` with a struct per message and
  `PREFIX_Encode_<Msg>(const T *msg, uint8_t *buf, size_t capacity, size_t *length)`,
  `PREFIX_Decode_<Msg>(const uint8_t *buf, size_t length, T *msg)` and `PREFIX_Identify()`. It
  uses no dynamic memory and has macros for IDs, sizes, scales and bit masks.
- **C++17**: type-safe wrappers (`encode()` returns `std::vector`; `decode()` returns
  `std::optional`) around the C code.
- **Python**: one dependency-free module with dataclasses and `identify()`.
- **C#**: classes with `Encode()` and `TryDecode()`.

Structures hold raw values; the scale and offset are provided as constants. The generated code
handles constants, LENGTH and CRC exactly like the program itself.

## 9. Logs

Shows the decoded traffic of this session and the application log, which is useful when
reporting problems.

## Files and data

- Protocols are JSON files (`.json` or `.pdproj`, which opens with a double-click when the file
  association was selected during installation). See [PROTOCOL_FORMAT.md](PROTOCOL_FORMAT.md).
- *File → Restore Example Protocols* copies the examples again. Existing files are never
  overwritten.
- *Help → About / License / Third-party Licenses* show the version and license texts.
