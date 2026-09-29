# Changelog

## 1.1.0

- **Protocol specification export (PDF / HTML).** *File → Export Protocol Specification (PDF)…*
  (`Ctrl+E`), the Dashboard button, or `pdcli export`. The document contains a title page with
  contents, the transport settings, a message overview, all enumerations, and for every message
  its properties, a colour-coded byte layout, a field table (offset, size, type and byte order,
  encoding, rule, scaling, range, unit, enumeration), bit-field tables, the LENGTH/CRC rules and
  an example packet. It ends with the CRC parameters and check values, the tests, the simulator
  rules and the validation results. It is A4 with a running header and page numbers.
- Example protocols that become available after the first start are now copied to Documents.

## 1.0.0

First release, implementing `Protocol_Designer_Architecture.md`.

- Declarative protocol engine: all field types in both byte orders, scaling, enums, bit fields,
  variable-length fields, CONSTANT/LENGTH/CRC encodings, 18 CRC/checksum algorithms plus custom
  CRCs, a stream frame detector with resynchronisation, and a validator.
- Transports: UART, RS485 (echo suppression, RTS direction control), TCP (client/server), UDP,
  CAN and CAN-FD (python-can), loopback, and a protocol-driven device simulator.
- Application: live session, test engine with sequences and variables, HTML/JSON/text reports,
  packet builder and analyzer, traffic log export.
- Code generator for C, C++, Python and C#, verified byte for byte against the engine.
- PySide6 desktop GUI with Dashboard, Protocol Designer, Message Designer, Packet Builder,
  Analyzer, Live Monitor, Test Runner, Code Generator and Logs pages.
- `pdcli` command line interface.
- Windows installer (Inno Setup) with a license agreement, choice of install folder, components,
  desktop shortcut, `.pdproj` association, an *Installed apps* entry, in-place upgrades, and an
  uninstaller that keeps user data unless the user asks otherwise.
