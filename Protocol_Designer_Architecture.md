# Protocol Designer & Analyzer — Architecture Design

I will design it as a general-purpose tool rather than just an RS485 sender, so that you can later add UART, CAN, CAN-FD, TCP and others without rebuilding the system. It will also be delivered as a proper desktop application with a standard installer (see Section 26).

## 1. The General Idea

A suitable name for the project:

> **Protocol Designer & Analyzer**

The architecture would be:

```text
┌─────────────────────────────────────────────────────────────┐
│                    Protocol Designer                       │
├─────────────────────────────────────────────────────────────┤
│                         GUI Layer                           │
│                                                             │
│  Protocols │ Messages │ Packet Builder │ Analyzer │ Tests   │
├─────────────────────────────────────────────────────────────┤
│                     Application Layer                       │
│                                                             │
│  Protocol Manager │ Encoder │ Decoder │ Test Engine │ Logger │
├─────────────────────────────────────────────────────────────┤
│                    Protocol Engine                          │
│                                                             │
│  Field Parser │ Validation │ CRC │ Enum │ Scaling │ State   │
├─────────────────────────────────────────────────────────────┤
│                   Transport Layer                           │
│                                                             │
│ UART │ RS485 │ CAN │ CAN-FD │ TCP │ USB │ Custom            │
├─────────────────────────────────────────────────────────────┤
│                     Hardware / OS                           │
└─────────────────────────────────────────────────────────────┘
```

The core idea is **separating the Transport from the Protocol**.

---

## 2. The Most Important Architectural Decision

Do not treat:

```text
RS485 = Protocol
```

because RS485 is the physical communication medium.

Instead:

```text
Transport
   │
   └── RS485
          │
          ▼
Application Protocol
          │
          ├── Packet
          ├── Command
          ├── Fields
          └── CRC
```

For example:

```text
RS485
Baudrate = 115200
Parity   = None
Stop     = 1
```

And on top of it:

```text
TPMS Diagnostic Protocol
```

This way, the same protocol can later run over UART, USB or TCP if needed.

---

## 3. Software Layers

### Layer 1 — Transport

Responsible only for moving bytes.

For example:

```text
ITransport
│
├── SerialTransport
│
├── RS485Transport
│
├── CANTransport
│
├── CANFDTransport
│
└── TCPTransport
```

The internal interface could be:

```text
open()
close()
send(bytes)
receive()
is_connected()
```

The RS485 implementation knows:

```text
COM3
115200
8N1
```

But it **does not know what byte 0x34 means**.

---

## 4. Protocol Definition Layer

This is the heart of the project.

It defines:

```text
Protocol
    │
    ├── Transport settings
    │
    ├── Frames
    │
    ├── Fields
    │
    ├── Enumerations
    │
    ├── CRC
    │
    └── Validation rules
```

For example:

```text
Protocol: TPMS_RS485
Version: 1.0

Transport:
    RS485
    115200
    8N1
```

Then:

```text
Messages:

0x01 READ_SENSOR
0x02 SENSOR_RESPONSE
0x03 SET_CONFIG
0x04 ACK
0x05 ERROR
```

---

## 5. A Real RS485 Packet Example

Let's design a simple, realistic protocol.

Assume the PC wants to read sensor data.

### Request

```text
AA 01 01 05 10 55
```

We define the packet as follows:

| Byte | Field     | Size | Value      |
| ---: | --------- | ---: | ---------- |
|    0 | SOF       |    1 | `0xAA`     |
|    1 | Address   |    1 | `0x01`     |
|    2 | Command   |    1 | `0x01`     |
|    3 | Length    |    1 | `0x05`     |
|    4 | Sensor ID |    1 | `0x10`     |
|    5 | CRC       |    1 | calculated |

But let's use a more realistic packet that contains a payload and a clear CRC.

---

## 6. Proposed RS485 Protocol

The frame:

```text
┌──────┬──────┬──────┬──────┬────────────┬──────┐
│ SOF  │ ADDR │ CMD  │ LEN  │ PAYLOAD    │ CRC  │
│ 1B   │ 1B   │ 1B   │ 1B   │ 0..255 B   │ 1B   │
└──────┴──────┴──────┴──────┴────────────┴──────┘
```

For example:

```text
AA 01 10 04 12 34 56 78 XX
```

Meaning:

```text
SOF     = AA
ADDR    = 01
CMD     = 10
LEN     = 04
PAYLOAD = 12 34 56 78
CRC     = XX
```

---

## 7. Defining a Message Inside the Program

We create:

```text
Message
Name:
    READ_SENSOR

Command:
    0x10

Direction:
    TX
```

Then the fields:

```text
┌──────────┬────────┬──────┬──────────┬──────────┐
│ Name     │ Offset │ Size │ Type     │ Encoding │
├──────────┼────────┼──────┼──────────┼──────────┤
│ SOF      │ 0      │ 1    │ UINT8    │ CONST    │
│ ADDRESS  │ 1      │ 1    │ UINT8    │ UINT8    │
│ COMMAND  │ 2      │ 1    │ UINT8    │ CONST    │
│ LENGTH   │ 3      │ 1    │ UINT8    │ AUTO     │
│ SENSORID │ 4      │ 1    │ UINT8    │ UINT8    │
│ CRC      │ 5      │ 1    │ UINT8    │ CRC8     │
└──────────┴────────┴──────┴──────────┴──────────┘
```

This is where the power of the program shows.

`LENGTH`, for example, is not typed in by the user.

The program knows:

```text
LENGTH = payload size
```

And the CRC:

```text
CRC = CRC8(bytes[0 ... n-2])
```

---

## 8. JSON Definition

I recommend that you have your own **Protocol Definition Format**.

For example:

```json
{
  "protocol": {
    "name": "Example RS485 Protocol",
    "version": "1.0"
  },

  "transport": {
    "type": "RS485",
    "baudrate": 115200,
    "data_bits": 8,
    "parity": "NONE",
    "stop_bits": 1
  },

  "frames": [
    {
      "name": "READ_SENSOR",
      "id": 16,
      "direction": "TX",

      "fields": [
        {
          "name": "SOF",
          "offset": 0,
          "size": 1,
          "type": "UINT8",
          "encoding": "CONSTANT",
          "value": "0xAA"
        },
        {
          "name": "ADDRESS",
          "offset": 1,
          "size": 1,
          "type": "UINT8"
        },
        {
          "name": "COMMAND",
          "offset": 2,
          "size": 1,
          "type": "UINT8",
          "encoding": "CONSTANT",
          "value": "0x10"
        },
        {
          "name": "LENGTH",
          "offset": 3,
          "size": 1,
          "type": "UINT8",
          "encoding": "AUTO"
        },
        {
          "name": "SENSOR_ID",
          "offset": 4,
          "size": 1,
          "type": "UINT8"
        },
        {
          "name": "CRC",
          "offset": 5,
          "size": 1,
          "type": "UINT8",
          "encoding": "CRC8"
        }
      ]
    }
  ]
}
```

This file becomes **the single source of truth for the protocol**.

---

## 9. Don't Make a Field Just a Byte

This is a very important point.

The Field Engine should support:

```text
UINT8
UINT16
UINT32

INT8
INT16
INT32

FLOAT32

BITFIELD

BYTES

STRING

ENUM

BOOLEAN
```

As well as:

```text
Little Endian
Big Endian
```

---

## 10. Scaling

For example, a pressure sensor sends:

```text
250
```

But the real value is:

```text
25.0 bar
```

So you define:

```text
raw = 250

scale = 0.1
offset = 0

value = raw × scale + offset
```

And the GUI shows:

```text
Pressure
Raw: 250
Value: 25.0 bar
```

---

## 11. ENUM

For example:

```text
STATUS
```

Its values:

```text
0x00 = OFF
0x01 = ON
0x02 = ERROR
0x03 = NOT_AVAILABLE
```

Instead of displaying:

```text
STATUS = 0x02
```

It displays:

```text
STATUS = ERROR
```

---

## 12. Bit Fields

This is very important in CAN and embedded protocols.

For example:

```text
STATUS = 0x35
```

And you define:

```text
Bit 0       Sensor Active
Bit 1       Battery Low
Bit 2       RF Error
Bit 3-4     Sensor State
Bit 5-7     Reserved
```

In the GUI:

```text
STATUS = 0x35

Sensor Active     ✓
Battery Low       ✗
RF Error          ✓
Sensor State      2
```

---

## 13. Packet Decoder

When this arrives:

```text
AA 01 10 01 05 3C
```

The decoder works as follows:

```text
Raw Bytes
    ↓
Frame Detector
    ↓
Find SOF
    ↓
Read Header
    ↓
Read Length
    ↓
Extract Payload
    ↓
Validate CRC
    ↓
Identify Command
    ↓
Decode Fields
    ↓
Apply Scaling
    ↓
Apply Enum
    ↓
Decoded Message
```

The result:

```text
READ_SENSOR

Address:
    0x01

Sensor ID:
    5

CRC:
    VALID
```

---

## 14. Packet Builder

The exact reverse:

```text
User Input
    ↓
Field Validation
    ↓
Encoding
    ↓
Calculate Length
    ↓
Calculate CRC
    ↓
Raw Packet
```

For example, the user selects:

```text
Message:
READ_SENSOR

Address:
1

Sensor ID:
5
```

Then:

```text
BUILD
```

And the program displays:

```text
AA 01 10 01 05 XX
```

---

## 15. GUI Structure

I would divide the program into these pages:

```text
┌─────────────────────────────────────────────┐
│ Protocol Tool                               │
├─────────────┬───────────────────────────────┤
│ Dashboard   │                               │
│             │                               │
│ Protocols   │                               │
│             │                               │
│ Messages    │                               │
│             │                               │
│ Packet      │                               │
│ Builder     │                               │
│             │                               │
│ Analyzer    │                               │
│             │                               │
│ Monitor     │                               │
│             │                               │
│ Test        │                               │
│             │                               │
│ Logs        │                               │
└─────────────┴───────────────────────────────┘
```

---

## 16. Protocol Designer

This is the most important screen:

```text
Protocol
────────────────────────────────────

Name       [ TPMS RS485 Protocol ]
Version    [ 1.0 ]

Transport  [ RS485 ▼ ]

Messages
────────────────────────────────────

+ Add Message

┌───────────────┬──────┬───────────┐
│ Message       │ ID   │ Direction │
├───────────────┼──────┼───────────┤
│ READ_SENSOR   │ 10   │ TX        │
│ SENSOR_DATA   │ 11   │ RX        │
│ SET_CONFIG    │ 20   │ TX        │
│ ACK           │ 80   │ RX        │
└───────────────┴──────┴───────────┘
```

---

## 17. Message Designer

When selecting:

```text
SENSOR_DATA
```

This appears:

```text
Message
────────────────────────────────────

Name       SENSOR_DATA
Command    0x11
Direction  RX

Fields
──────────────────────────────────────────────

SOF          0    1    UINT8    CONSTANT
ADDRESS      1    1    UINT8
COMMAND      2    1    UINT8    CONSTANT
LENGTH       3    1    UINT8    AUTO

SENSOR_ID    4    1    UINT8
PRESSURE     5    2    UINT16   Scale 0.1
TEMPERATURE  7    2    INT16    Scale 0.1
STATUS       9    1    ENUM
CRC         10    1    UINT8    CRC8

[ + Add Field ]
```

---

## 18. Live Monitor

This will be very useful for you in embedded projects.

```text
┌──────────────────────────────────────────────────┐
│ COM3 | 115200 | RS485             [Connected]    │
├──────────────────────────────────────────────────┤
│ Time       DIR   Raw Data                        │
│                                                  │
│ 22:31:01   TX    AA 01 10 01 05 3B               │
│ 22:31:01   RX    AA 01 11 07 05 C2 00 19 01 7A   │
│                                                  │
├──────────────────────────────────────────────────┤
│ Decoded                                          │
│                                                  │
│ SENSOR_DATA                                      │
│ Sensor ID       5                                │
│ Pressure        19.4                             │
│ Temperature     25.0 °C                          │
│ Status          ACTIVE                           │
│ CRC             VALID                            │
└──────────────────────────────────────────────────┘
```

This is where the program effectively becomes a **Protocol Analyzer**.

---

## 19. Test Engine

This is a feature I think will be very important for your projects.

You can define a test:

```text
Test: Sensor Read

1. Open RS485
2. Send READ_SENSOR
3. Wait for SENSOR_DATA
4. Timeout = 500 ms
5. Verify Sensor ID
6. Verify Pressure
7. Verify Temperature
8. Verify CRC
```

Then:

```text
RUN TEST
```

And the result:

```text
✓ Connection
✓ TX packet
✓ RX packet
✓ CRC
✓ Sensor ID
✓ Pressure range
✓ Temperature range

RESULT: PASS
```

This turns the program from an analysis tool into a **Protocol Test Tool**.

---

## 20. Internal Software Architecture

I suggest a structure like:

```text
protocol-tool/
│
├── app/
│   ├── application/
│   │   ├── protocol_manager
│   │   ├── packet_builder
│   │   ├── packet_analyzer
│   │   ├── test_engine
│   │   └── logger
│   │
│   ├── protocol/
│   │   ├── protocol_definition
│   │   ├── message_definition
│   │   ├── field_definition
│   │   ├── encoder
│   │   ├── decoder
│   │   ├── validator
│   │   └── crc
│   │
│   ├── transport/
│   │   ├── transport_interface
│   │   ├── serial
│   │   ├── rs485
│   │   ├── can
│   │   └── tcp
│   │
│   ├── storage/
│   │   ├── json_loader
│   │   └── json_writer
│   │
│   └── ui/
│       ├── protocol_editor
│       ├── message_editor
│       ├── packet_builder
│       ├── analyzer
│       ├── monitor
│       └── test_runner
│
├── installer/                  ← new (see Section 26)
│   ├── setup.iss
│   ├── EULA.rtf
│   ├── app.ico
│   └── wizard_images/
│
└── protocols/
    ├── tpms_rs485.json
    ├── tpms_can.json
    └── custom_protocol.json
```

---

## 21. Most Importantly: Don't Couple the GUI to the Decoder

For example, don't do:

```text
GUI → RS485 → Decode
```

Instead:

```text
                    ┌──────────────┐
                    │     GUI      │
                    └──────┬───────┘
                           │
                    ┌──────▼───────┐
                    │ Application  │
                    └──────┬───────┘
                           │
             ┌─────────────┴──────────────┐
             │                            │
      ┌──────▼────────┐           ┌───────▼──────┐
      │Protocol Engine│           │   Transport  │
      └───────────────┘           └───────┬──────┘
                                          │
                              ┌───────────┼───────────┐
                              │           │           │
                            UART        RS485        CAN
```

This will make adding CAN later much easier.

---

## 22. Database / Protocol File

I don't recommend using a complex database at the start.

Start with:

```text
JSON
```

For example:

```text
protocols/
    tpms_rs485.json
    tpms_can.json
    ecu_bootloader.json
```

But design the schema from the beginning so that you can later convert it into:

```text
JSON
   ↓
Protocol Definition Model
   ↓
Encoder / Decoder
```

---

## 23. A Very Advanced Level You Can Add Later

You can add a **Protocol State Machine**.

For example, a bootloader:

```text
IDLE
 │
 ▼
GET_SEED
 │
 ▼
SEND_KEY
 │
 ▼
TRANSFER_INIT
 │
 ▼
TRANSFER_DATA
 │
 ▼
VERIFY
 │
 ▼
RESET
```

And the program knows that:

```text
GET_SEED
    ↓
Expected Response = SEED_RESPONSE
```

So it can execute a complete sequence, not just a single packet.

---

## 24. You Could Also Have a Code Generator

This is one of the strongest features.

From the same:

```text
tpms_rs485.json
```

The program can generate:

```text
C
C++
Python
C#
```

For example:

```c
typedef struct
{
    uint8_t address;
    uint8_t sensor_id;
} ReadSensor_t;
```

And:

```c
bool Encode_ReadSensor(
    const ReadSensor_t *msg,
    uint8_t *buffer,
    uint16_t *length);
```

And:

```c
bool Decode_SensorData(
    const uint8_t *buffer,
    uint16_t length,
    SensorData_t *msg);
```

At this point the project is not only a **Protocol Analyzer** but:

> **Protocol Definition → Test Tool → Code Generator**

This is a very strong design for embedded projects.

---

## 25. The Final Architecture I Recommend

```text
                         ┌───────────────────────┐
                         │    Protocol Project   │
                         └───────────┬───────────┘
                                     │
                            Protocol Definition
                               JSON / YAML
                                     │
              ┌──────────────────────┼─────────────────────┐
              │                      │                     │
              ▼                      ▼                     ▼
        Message Model          Field Model          Enum Model
              │                      │                     │
              └──────────────────────┼─────────────────────┘
                                     ▼
                            Protocol Engine
                                     │
                ┌────────────────────┼──────────────────┐
                │                    │                  │
                ▼                    ▼                  ▼
             Encoder              Decoder          Validator
                │                    │                  │
                └────────────────────┼──────────────────┘
                                     │
                              Transport Layer
                                     │
          ┌──────────────┬───────────┼───────────┬───────────┐
          ▼              ▼           ▼           ▼           ▼
        UART           RS485        CAN        CAN-FD       TCP
          │              │           │           │           │
          └──────────────┴───────────┼───────────┴───────────┘
                                     ▼
                                  Hardware

                                     +
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
                Packet Builder   Live Analyzer    Test Engine
                    │                │                │
                    └────────────────┼────────────────┘
                                     ▼
                                   GUI
                                     │
                                     ▼
                         Installer / Distribution
```

---

## 26. Installation & Distribution (Installable Desktop Application)

The program must be delivered as a **real installed desktop application**, not a script or a portable folder. The user experience should be identical to installing any commercial program:

- A `Setup.exe` installer with a graphical installation wizard.
- A **License Agreement (EULA)** page that the user must accept before installation can continue.
- The program appears in **Settings → Apps → Installed apps** and in **Control Panel → Programs and Features**, with its name, version, publisher, icon and size.
- A Start Menu entry, an optional desktop shortcut, and a proper **Uninstall** option.

### 26.1 Installation Wizard Flow

```text
┌──────────────────────┐
│ 1. Welcome           │  Program name, version, publisher
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ 2. License Agreement │  EULA text
│                      │  ( ) I accept the agreement
│                      │  (•) I do not accept the agreement
│                      │  [Next] disabled until "I accept" is chosen
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ 3. Install Location  │  Default: C:\Program Files\ProtocolDesigner
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ 4. Components        │  ☑ Core application (required)
│                      │  ☑ Example protocols
│                      │  ☐ CAN adapter drivers
│                      │  ☐ Code generator templates
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ 5. Additional Tasks  │  ☑ Create desktop shortcut
│                      │  ☑ Associate .pdproj files with the program
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ 6. Ready to Install  │  Summary of choices
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ 7. Installing...     │  Progress bar
└──────────┬───────────┘
           ▼
┌──────────────────────┐
│ 8. Finish            │  ☑ Launch Protocol Designer now
└──────────────────────┘
```

### 26.2 Appearing in "Installed Programs"

Windows lists a program under Installed Apps when the installer writes an entry to:

```text
HKLM\Software\Microsoft\Windows\CurrentVersion\Uninstall\{AppId}     (all users)
HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{AppId}     (current user only)
```

With values such as:

| Value             | Example                                         |
| ----------------- | ----------------------------------------------- |
| `DisplayName`     | Protocol Designer & Analyzer                    |
| `DisplayVersion`  | 1.0.0                                           |
| `Publisher`       | Your Company                                    |
| `DisplayIcon`     | `C:\Program Files\ProtocolDesigner\ProtocolDesigner.exe` |
| `UninstallString` | `C:\Program Files\ProtocolDesigner\unins000.exe` |
| `InstallLocation` | `C:\Program Files\ProtocolDesigner`             |
| `EstimatedSize`   | Size in KB                                      |
| `URLInfoAbout`    | Project / support website                       |

Standard installer tools (Inno Setup, WiX, NSIS, MSIX) write these entries automatically, so you do not need to manage the registry by hand.

### 26.3 Choosing the Installer Technology

| Application stack         | Build the executable            | Recommended installer                    |
| ------------------------- | ------------------------------- | ---------------------------------------- |
| Python (PySide6 / PyQt)   | PyInstaller / Nuitka            | **Inno Setup**                           |
| C# / .NET (WPF / WinUI)   | `dotnet publish`                | **WiX Toolset (MSI)** or MSIX            |
| C++ / Qt                  | `windeployqt`                   | **Qt Installer Framework** or Inno Setup |
| Cross-platform (any)      | —                               | Qt IFW (Windows / Linux / macOS)         |

**Recommendation:** for a first version on Windows, **Inno Setup** is the simplest option. It provides the modern wizard, the license page, Start Menu shortcuts, uninstaller and the Installed Apps entry out of the box. If you later need corporate deployment (Group Policy, silent install via SCCM/Intune), move to **MSI with WiX**.

### 26.4 Example Inno Setup Script (`installer/setup.iss`)

```ini
#define AppName      "Protocol Designer & Analyzer"
#define AppVersion   "1.0.0"
#define AppPublisher "Your Company"
#define AppExeName   "ProtocolDesigner.exe"

[Setup]
; AppId must stay the same forever — it links upgrades and uninstall to this program
AppId={{8F3C2A10-5B7E-4D2A-9C61-2E4F0A7B1D35}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\ProtocolDesigner
DefaultGroupName={#AppName}
; License Agreement page — the user must accept before continuing
LicenseFile=EULA.rtf
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExeName}
OutputBaseFilename=ProtocolDesigner-Setup-{#AppVersion}
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
; Let the user choose "all users" or "current user only"
PrivilegesRequiredOverridesAllowed=dialog

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Types]
Name: "full";   Description: "Full installation"
Name: "custom"; Description: "Custom installation"; Flags: iscustom

[Components]
Name: "core";      Description: "Core application";           Types: full custom; Flags: fixed
Name: "examples";  Description: "Example protocols";           Types: full
Name: "codegen";   Description: "Code generator templates";    Types: full

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"
Name: "fileassoc";   Description: "Associate .pdproj files with {#AppName}"

[Files]
Source: "..\dist\ProtocolDesigner\*"; DestDir: "{app}"; Components: core; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\protocols\*.json";        DestDir: "{app}\protocols"; Components: examples; Flags: ignoreversion
Source: "..\templates\*";             DestDir: "{app}\templates"; Components: codegen;  Flags: ignoreversion recursesubdirs

[Icons]
Name: "{group}\{#AppName}";             Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}";   Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";       Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
Root: HKA; Subkey: "Software\Classes\.pdproj"; ValueType: string; ValueName: ""; ValueData: "ProtocolDesigner.Project"; Flags: uninsdeletevalue; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\ProtocolDesigner.Project\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#AppExeName},0"; Tasks: fileassoc
Root: HKA; Subkey: "Software\Classes\ProtocolDesigner.Project\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#AppExeName}"" ""%1"""; Flags: uninsdeletekey; Tasks: fileassoc

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent
```

Compiling this script produces a single `ProtocolDesigner-Setup-1.0.0.exe` that shows the full wizard, including the License Agreement page, and registers the program in Installed Apps automatically.

### 26.5 The License Agreement (EULA)

The file `installer/EULA.rtf` contains the terms shown on the License Agreement page. A typical structure:

```text
END-USER LICENSE AGREEMENT
Protocol Designer & Analyzer

1. Grant of License
2. Restrictions (reverse engineering, redistribution, etc.)
3. Ownership / Intellectual Property
4. Third-Party Components and Their Licenses
5. Data and Privacy
6. Disclaimer of Warranty
7. Limitation of Liability
8. Termination
9. Governing Law
10. Contact Information
```

Notes:

- If the program uses open-source libraries (Qt, PySide6, pyserial, python-can, etc.), their licenses must be respected and listed (for example, in a `THIRD_PARTY_LICENSES.txt` installed with the program and shown in the **About** dialog).
- The EULA text itself should be reviewed by a legal professional before commercial distribution.
- Also show the version and license from inside the application: **Help → About → License**.

### 26.6 Uninstall and Upgrade Behavior

```text
Uninstall
    ├── Remove program files from Program Files
    ├── Remove Start Menu / desktop shortcuts
    ├── Remove file associations and registry entries
    ├── Remove the Installed Apps entry
    └── KEEP user data (user protocols, test results, logs)
            → optionally ask: "Also delete your projects and settings?"

Upgrade (installing a newer version)
    ├── Same AppId → detected as an update, not a second program
    ├── Installs over the old version in the same folder
    └── User data and settings are preserved
```

### 26.7 Where Data Is Stored After Installation

Program files must be kept separate from user data, because `Program Files` is read-only for normal users:

```text
C:\Program Files\ProtocolDesigner\            → executable, libraries, bundled examples (read-only)
%APPDATA%\ProtocolDesigner\settings.json      → user settings
%LOCALAPPDATA%\ProtocolDesigner\logs\         → logs
Documents\ProtocolDesigner\Protocols\         → user protocol definitions (.json / .pdproj)
Documents\ProtocolDesigner\TestResults\       → test reports
```

On first launch, the program copies the example protocols into the user's `Documents` folder so they can be edited.

### 26.8 Code Signing

Without a digital signature, Windows SmartScreen shows the warning *"Windows protected your PC"* and the publisher appears as **Unknown**. To look like a professional program:

- Obtain a code-signing certificate (OV or EV).
- Sign **both** the application `.exe` and the `Setup.exe` using `signtool`.
- The publisher name then appears in the UAC prompt and in Installed Apps.

### 26.9 Build & Release Pipeline

```text
Source Code
    ↓
Build executable        (PyInstaller / dotnet publish / windeployqt)
    ↓
Sign application .exe
    ↓
Compile installer       (Inno Setup / WiX)
    ↓
Sign Setup.exe
    ↓
Test: install → run → upgrade → uninstall (on a clean VM)
    ↓
Release: ProtocolDesigner-Setup-x.y.z.exe
```

### 26.10 Other Operating Systems (Later)

| OS      | Package format                   | License acceptance                    |
| ------- | -------------------------------- | ------------------------------------- |
| Windows | `.exe` (Inno Setup) / `.msi`     | License page in the wizard            |
| Linux   | `.deb` / `.rpm` / AppImage       | Qt IFW license page, or on first run  |
| macOS   | `.pkg` / `.dmg`                  | `.pkg` license page / DMG license     |

Using **Qt Installer Framework** gives one installer design (with license page) across all three platforms.

---

## 27. Project Name and Core Concept

If I were to actually start this project, I would name it:

**Protocol Definition, Analysis & Test Platform**

And the core concept:

**Declarative Protocol Definition**

This means you **don't manually program how each packet is interpreted**. Instead, you describe the protocol, its fields and its rules, and the engine performs the encoding/decoding.

This is what will make the program extensible from **RS485 → UART → CAN → CAN-FD → Bootloader protocols**, instead of becoming a collection of project-specific `if/else` statements — and, delivered through a proper installer with a license agreement, it becomes a complete, professional desktop product.
