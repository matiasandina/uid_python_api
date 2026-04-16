#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from doric_light_source import get_doric_dll_search_directories, resolve_doric_dll_path


@dataclass(frozen=True)
class ImportScanResult:
    path: Path
    imports: list[str]
    error: str | None = None


def _read_c_string(data: bytes, offset: int) -> str:
    end = data.find(b"\0", offset)
    if end < 0:
        end = len(data)
    return data[offset:end].decode("ascii", errors="replace")


def _rva_to_offset(rva: int, sections: list[tuple[int, int, int, int]]) -> int | None:
    for virtual_address, virtual_size, raw_size, raw_pointer in sections:
        size = max(virtual_size, raw_size)
        if virtual_address <= rva < virtual_address + size:
            return raw_pointer + (rva - virtual_address)
    return None


def read_pe_imports(path: Path) -> ImportScanResult:
    try:
        data = path.read_bytes()
        if data[:2] != b"MZ":
            return ImportScanResult(path=path, imports=[], error="not a PE file")

        pe_offset = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe_offset : pe_offset + 4] != b"PE\0\0":
            return ImportScanResult(path=path, imports=[], error="missing PE signature")

        coff_offset = pe_offset + 4
        number_of_sections = struct.unpack_from("<H", data, coff_offset + 2)[0]
        optional_header_size = struct.unpack_from("<H", data, coff_offset + 16)[0]
        optional_offset = coff_offset + 20
        magic = struct.unpack_from("<H", data, optional_offset)[0]
        if magic == 0x10B:
            data_directory_offset = optional_offset + 96
        elif magic == 0x20B:
            data_directory_offset = optional_offset + 112
        else:
            return ImportScanResult(path=path, imports=[], error=f"unknown PE optional header magic {magic:#x}")

        import_rva, import_size = struct.unpack_from("<II", data, data_directory_offset + 8)
        if import_rva == 0 or import_size == 0:
            return ImportScanResult(path=path, imports=[])

        section_offset = optional_offset + optional_header_size
        sections = []
        for idx in range(number_of_sections):
            header = section_offset + idx * 40
            virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from("<IIII", data, header + 8)
            sections.append((virtual_address, virtual_size, raw_size, raw_pointer))

        import_offset = _rva_to_offset(import_rva, sections)
        if import_offset is None:
            return ImportScanResult(path=path, imports=[], error=f"cannot map import RVA {import_rva:#x}")

        imports = []
        descriptor_offset = import_offset
        while descriptor_offset + 20 <= len(data):
            original_first_thunk, _time, _forwarder, name_rva, first_thunk = struct.unpack_from(
                "<IIIII", data, descriptor_offset
            )
            if not any((original_first_thunk, name_rva, first_thunk)):
                break
            name_offset = _rva_to_offset(name_rva, sections)
            if name_offset is not None:
                imports.append(_read_c_string(data, name_offset))
            descriptor_offset += 20

        return ImportScanResult(path=path, imports=sorted(set(imports), key=str.lower))
    except Exception as exc:
        return ImportScanResult(path=path, imports=[], error=str(exc))


def _index_dll_dirs(search_dirs: list[Path]) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for directory in search_dirs:
        if not directory.exists() or not directory.is_dir():
            continue
        for dll_path in directory.glob("*.dll"):
            index.setdefault(dll_path.name.lower(), dll_path)
    return index


def _system_search_dirs() -> list[Path]:
    dirs = []
    system_root = os.environ.get("SystemRoot")
    if system_root:
        dirs.extend([Path(system_root) / "System32", Path(system_root) / "SysWOW64"])
    for part in os.environ.get("PATH", "").split(os.pathsep):
        if part.strip():
            dirs.append(Path(part))
    return dirs


def _is_api_set(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith("api-ms-win-") or lowered.startswith("ext-ms-win-")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect DoricSystem.dll imported DLL dependencies.")
    parser.add_argument("dll_path", help="Path to DoricSystem.dll or a containing directory.")
    parser.add_argument("--recursive", action="store_true", help="Also inspect imported DLLs found in search dirs.")
    args = parser.parse_args()

    root = resolve_doric_dll_path(args.dll_path)
    search_dirs = get_doric_dll_search_directories(root)
    all_search_dirs = search_dirs + _system_search_dirs()
    dll_index = _index_dll_dirs(all_search_dirs)

    print(f"resolved_dll: {root}")
    print("search_dirs:")
    for directory in search_dirs:
        print(f"  - {directory} exists={directory.exists()}")

    pending = [root]
    visited: set[str] = set()
    missing: dict[str, list[Path]] = {}

    while pending:
        path = pending.pop(0)
        key = str(path).lower()
        if key in visited:
            continue
        visited.add(key)

        result = read_pe_imports(path)
        print(f"\n{path}")
        if result.error:
            print(f"  error: {result.error}")
            continue
        if not result.imports:
            print("  imports: none")
            continue

        for imported in result.imports:
            resolved = dll_index.get(imported.lower())
            if resolved:
                print(f"  OK      {imported} -> {resolved}")
                if args.recursive:
                    pending.append(resolved)
            elif _is_api_set(imported):
                print(f"  APISET  {imported}")
            else:
                print(f"  MISSING {imported}")
                missing.setdefault(imported, []).append(path)

        if not args.recursive:
            break

    if missing:
        print("\nmissing_imports:")
        for name, parents in sorted(missing.items(), key=lambda item: item[0].lower()):
            parent_list = ", ".join(str(parent) for parent in parents[:5])
            print(f"  - {name} imported_by={parent_list}")
        return 2

    print("\nmissing_imports: none detected in static import table")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
