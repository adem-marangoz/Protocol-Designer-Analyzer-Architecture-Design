"""Protocol manager: the currently open protocol project.

Keeps the protocol, its file path and a modified flag, and notifies listeners
(the GUI) when any of them change. Editing operations used by the designer
screens live here so they are testable without a GUI.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Callable, List, Optional, Union

from .. import paths
from ..protocol.model import (
    Direction,
    Encoding,
    FieldDefinition,
    FieldType,
    FrameDefinition,
    ProtocolDefinition,
    TestDefinition,
)
from ..protocol.validator import Issue, validate_protocol
from ..storage import list_protocol_files, load_protocol, save_protocol

Listener = Callable[[], None]


def default_frame(name: str, frame_id: int) -> FrameDefinition:
    """A new message pre-filled with the common SOF/ADDR/CMD/LEN/CRC layout."""
    return FrameDefinition(
        name=name,
        id=frame_id,
        direction=Direction.TX,
        fields=[
            FieldDefinition("SOF", FieldType.UINT8, encoding=Encoding.CONSTANT, value="0xAA"),
            FieldDefinition("ADDRESS", FieldType.UINT8, default=1),
            FieldDefinition("COMMAND", FieldType.UINT8, encoding=Encoding.CONSTANT, value=f"0x{frame_id:02X}"),
            FieldDefinition("LENGTH", FieldType.UINT8, encoding=Encoding.LENGTH),
            FieldDefinition("CRC", FieldType.UINT8, encoding=Encoding.CRC, crc="CRC8"),
        ],
    )


class ProtocolManager:
    def __init__(self, protocol: Optional[ProtocolDefinition] = None, path: Optional[Path] = None):
        self.protocol = protocol or ProtocolDefinition()
        self.path: Optional[Path] = Path(path) if path else None
        self.modified = False
        self._listeners: List[Listener] = []

    # ---------------------------------------------------------- listeners ---

    def subscribe(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Listener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    def mark_modified(self) -> None:
        self.modified = True
        self._notify()

    # -------------------------------------------------------------- files ---

    @property
    def title(self) -> str:
        name = self.path.name if self.path else "Untitled"
        return f"{name}{' *' if self.modified else ''}"

    def new(self, name: str = "New Protocol") -> None:
        self.protocol = ProtocolDefinition(name=name)
        self.path = None
        self.modified = False
        self._notify()

    def open(self, path: Union[str, Path]) -> ProtocolDefinition:
        protocol = load_protocol(path)
        self.protocol = protocol
        self.path = Path(path)
        self.modified = False
        self._notify()
        return protocol

    def save(self, path: Optional[Union[str, Path]] = None) -> Path:
        target = Path(path) if path else self.path
        if target is None:
            target = paths.protocols_dir() / f"{safe_filename(self.protocol.name)}.json"
        save_protocol(self.protocol, target)
        self.path = target
        self.modified = False
        self._notify()
        return target

    def validate(self) -> List[Issue]:
        return validate_protocol(self.protocol)

    @staticmethod
    def user_protocols() -> List[Path]:
        return list_protocol_files(paths.protocols_dir())

    # ------------------------------------------------------------ editing ---

    def unique_frame_name(self, base: str = "NEW_MESSAGE") -> str:
        existing = {f.name for f in self.protocol.frames}
        if base not in existing:
            return base
        n = 2
        while f"{base}_{n}" in existing:
            n += 1
        return f"{base}_{n}"

    def add_frame(self, name: Optional[str] = None) -> FrameDefinition:
        used = {f.id for f in self.protocol.frames if f.id is not None}
        frame_id = next(i for i in range(1, 256) if i not in used) if len(used) < 255 else 0
        frame = default_frame(name or self.unique_frame_name(), frame_id)
        self.protocol.frames.append(frame)
        self.mark_modified()
        return frame

    def duplicate_frame(self, name: str) -> FrameDefinition:
        original = self.protocol.frame(name)
        clone = copy.deepcopy(original)
        clone.name = self.unique_frame_name(f"{original.name}_COPY")
        self.protocol.frames.insert(self.protocol.frames.index(original) + 1, clone)
        self.mark_modified()
        return clone

    def remove_frame(self, name: str) -> None:
        frame = self.protocol.frame(name)
        self.protocol.frames.remove(frame)
        for other in self.protocol.frames:
            if other.expected_response == name:
                other.expected_response = None
        self.mark_modified()

    def rename_frame(self, old: str, new: str) -> None:
        new = new.strip()
        if not new:
            raise ValueError("name cannot be empty")
        if new != old and self.protocol.has_frame(new):
            raise ValueError(f"a message named '{new}' already exists")
        self.protocol.frame(old).name = new
        for other in self.protocol.frames:
            if other.expected_response == old:
                other.expected_response = new
        for test in self.protocol.tests:
            for step in test.steps:
                if step.message == old:
                    step.message = new
        for rule in self.protocol.simulator:
            if rule.on == old:
                rule.on = new
            if rule.reply == old:
                rule.reply = new
        self.mark_modified()

    def move_frame(self, name: str, delta: int) -> None:
        frames = self.protocol.frames
        i = frames.index(self.protocol.frame(name))
        j = max(0, min(len(frames) - 1, i + delta))
        if i != j:
            frames.insert(j, frames.pop(i))
            self.mark_modified()

    def add_field(self, frame_name: str, index: Optional[int] = None, name: Optional[str] = None) -> FieldDefinition:
        frame = self.protocol.frame(frame_name)
        existing = {f.name for f in frame.fields}
        base = name or "FIELD"
        candidate, n = base, 2
        while candidate in existing:
            candidate, n = f"{base}_{n}", n + 1
        f = FieldDefinition(candidate, FieldType.UINT8)
        if index is None:
            # insert before a trailing CRC so new payload fields land inside the frame
            index = len(frame.fields)
            if frame.fields and frame.fields[-1].encoding == Encoding.CRC:
                index -= 1
        frame.fields.insert(index, f)
        self.mark_modified()
        return f

    def remove_field(self, frame_name: str, field_name: str) -> None:
        frame = self.protocol.frame(frame_name)
        frame.fields.remove(frame.field(field_name))
        self.mark_modified()

    def move_field(self, frame_name: str, field_name: str, delta: int) -> None:
        fields = self.protocol.frame(frame_name).fields
        i = next(k for k, f in enumerate(fields) if f.name == field_name)
        j = max(0, min(len(fields) - 1, i + delta))
        if i != j:
            fields.insert(j, fields.pop(i))
            self.mark_modified()

    def add_test(self, name: str = "New Test") -> TestDefinition:
        existing = {t.name for t in self.protocol.tests}
        candidate, n = name, 2
        while candidate in existing:
            candidate, n = f"{name} {n}", n + 1
        test = TestDefinition(candidate)
        self.protocol.tests.append(test)
        self.mark_modified()
        return test


def safe_filename(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_ ." else "_" for c in name).strip().replace(" ", "_")
    return cleaned.lower() or "protocol"
