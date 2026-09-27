"""Code generator (Section 24): protocol definition -> C, C++, Python, C#."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Union

from ...protocol.model import ProtocolDefinition
from .c_gen import generate_c
from .cpp_gen import generate_cpp
from .csharp_gen import generate_csharp
from .plan import build_plan, snake
from .python_gen import generate_python

LANGUAGES = {
    "c": ("C (C99)", generate_c),
    "cpp": ("C++ (C++17, uses the C code)", generate_cpp),
    "python": ("Python 3", generate_python),
    "csharp": ("C# (.NET)", generate_csharp),
}


def generate(
    protocol: ProtocolDefinition,
    language: str,
    basename: Optional[str] = None,
    prefix: Optional[str] = None,
) -> Dict[str, str]:
    """Return ``{filename: source}`` for the requested language."""
    key = language.lower().replace("+", "p").replace("#", "sharp")
    key = {"c++": "cpp", "cplusplus": "cpp", "cs": "csharp", "py": "python"}.get(key, key)
    if key not in LANGUAGES:
        raise ValueError(f"unknown language '{language}' (choose from {', '.join(LANGUAGES)})")
    plan = build_plan(protocol, prefix)
    return LANGUAGES[key][1](plan, basename or snake(protocol.name))


def write_files(files: Dict[str, str], directory: Union[str, Path]) -> List[Path]:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in files.items():
        path = directory / name
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
    return written


__all__ = ["LANGUAGES", "generate", "write_files", "build_plan"]
