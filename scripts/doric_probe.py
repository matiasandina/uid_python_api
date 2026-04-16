#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from ctypes import c_char_p, c_int
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from doric_light_source import (
    close_dll_directory_handles,
    describe_doric_dll_search,
    dll_has_symbol,
    load_doric_dll,
)


def _print_uid_config_hint(uid: str) -> None:
    print("config.local.yaml update:")
    print("stimulus:")
    print(f'  uid: "{uid}"')


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Doric DLL connectivity and candidate ports.")
    parser.add_argument(
        "--dll-path",
        required=True,
        help="Path to DoricSystem.dll or a directory containing it.",
    )
    parser.add_argument(
        "--ports",
        default="0,1,2,3,4,5,6,7,8",
        help="Comma-separated integer ports to probe with open_device/close_device",
    )
    parser.add_argument(
        "--uid",
        default="",
        help="Doric device UID to probe with open_device_uid/close_device_uid. If set, port probing is skipped.",
    )
    parser.add_argument(
        "--list-infos",
        action="store_true",
        help="Call available_devices_infos to ask the Doric DLL to print device names, ports, and UIDs.",
    )
    parser.add_argument(
        "--wait-ms",
        type=int,
        default=20,
        help="Delay after each DLL call that needs settle time",
    )
    parser.add_argument(
        "--debug-loader",
        action="store_true",
        help="Print Doric DLL resolution and dependency search directories before loading.",
    )
    args = parser.parse_args()

    ports = [int(part.strip()) for part in args.ports.split(",") if part.strip()]

    if args.debug_loader:
        for line in describe_doric_dll_search(args.dll_path):
            print(line)

    try:
        loaded_dll = load_doric_dll(args.dll_path)
    except Exception as exc:
        print(f"Doric DLL load failed: {exc}")
        print("Rerun with --debug-loader to print resolved paths and discovered dependency DLL directories.")
        raise

    dll_path = loaded_dll.path

    try:
        dll = loaded_dll.dll
        dll.init.argtypes = [c_int]
        dll.init.restype = c_int
        dll.wait.argtypes = [c_int]
        dll.wait.restype = c_int
        has_info_api = dll_has_symbol(dll, "available_devices_infos")
        has_port_api = all(
            dll_has_symbol(dll, name)
            for name in ("available_devices_with_ports", "open_device", "close_device")
        )

        if (args.uid or args.list_infos) and has_info_api:
            dll.available_devices_infos.argtypes = []
            dll.available_devices_infos.restype = c_int
        if args.uid:
            dll.open_device_uid.argtypes = [c_char_p]
            dll.open_device_uid.restype = c_int
            dll.close_device_uid.argtypes = [c_char_p]
            dll.close_device_uid.restype = c_int
        if has_port_api:
            dll.available_devices_with_ports.argtypes = []
            dll.available_devices_with_ports.restype = c_int
            dll.open_device.argtypes = [c_int]
            dll.open_device.restype = c_int
            dll.close_device.argtypes = [c_int]
            dll.close_device.restype = c_int
        dll.quit.argtypes = []
        dll.quit.restype = c_int

        print(f"DLL: {dll_path}")
        print(f"Init result: {dll.init(1)}")
        print(f"Wait result after init: {dll.wait(args.wait_ms)}")
        print(f"Capabilities: info_api={has_info_api} port_api={has_port_api}")

        if args.uid:
            if not has_info_api:
                print("UID probe unavailable: this DLL does not export available_devices_infos/open_device_uid helpers.")
                print(f"quit result: {dll.quit()}")
                return 2
            uid = args.uid.strip().encode("ascii")
            enum_result = dll.available_devices_infos()
            print(f"available_devices_infos result: {enum_result}")
            print(f"Probing UID {args.uid}...")
            open_result = dll.open_device_uid(uid)
            wait_result = dll.wait(args.wait_ms)
            print(f"  uid {args.uid}: open_device_uid={open_result} wait={wait_result}")
            close_result = dll.close_device_uid(uid)
            close_wait = dll.wait(250)
            print(f"               close_device_uid={close_result} wait={close_wait}")
            _print_uid_config_hint(args.uid.strip())
            print(f"quit result: {dll.quit()}")
            return 0

        if args.list_infos:
            if not has_info_api:
                print("available_devices_infos is not exported by this DLL.")
                print(f"quit result: {dll.quit()}")
                return 2
            enum_result = dll.available_devices_infos()
            print(f"available_devices_infos result: {enum_result}")
            print("If the Doric output above includes a line like `Laser Driver (Port #<n> | UID <uid>)`,")
            print("copy the reported UID value into `config.local.yaml` as:")
            print("stimulus:")
            print('  uid: "<paste_uid_here>"')
            print(f"quit result: {dll.quit()}")
            return 0

        if not has_port_api:
            print("Legacy port probe unavailable: this DLL does not export available_devices_with_ports/open_device/close_device.")
            print("Use --list-infos or --uid with newer Doric DLL builds.")
            print(f"quit result: {dll.quit()}")
            return 2

        enum_result = dll.available_devices_with_ports()
        print(f"available_devices_with_ports result: {enum_result}")
        print("Probing candidate ports...")

        for port in ports:
            try:
                open_result = dll.open_device(port)
                wait_result = dll.wait(args.wait_ms)
                print(f"  port {port}: open_device={open_result} wait={wait_result}")
                close_result = dll.close_device(port)
                close_wait = dll.wait(250)
                print(f"           close_device={close_result} wait={close_wait}")
            except Exception as exc:
                print(f"  port {port}: exception={exc}")

        print(f"quit result: {dll.quit()}")
        return 0
    finally:
        close_dll_directory_handles(loaded_dll.directory_handles)


if __name__ == "__main__":
    raise SystemExit(main())
