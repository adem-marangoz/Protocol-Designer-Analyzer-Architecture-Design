# Changelog

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
