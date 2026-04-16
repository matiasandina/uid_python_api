from __future__ import annotations

import ctypes
import os
import re
from ctypes import c_int
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import serial.tools.list_ports

from doric_light_source import (
    ChannelHardwareConfig,
    DoricLightSource,
    LightSourceSquareConfig,
    close_dll_directory_handles,
    dll_has_symbol,
    load_doric_dll,
)
from ttl_capture.reader import TeensyTTLReader


@dataclass(frozen=True)
class LaserProbeResult:
    port: int
    open_result: Optional[int]
    wait_result: Optional[int]
    close_result: Optional[int]
    close_wait_result: Optional[int]
    error: Optional[str]

    @property
    def ok(self) -> bool:
        return self.error is None and self.open_result == 2 and self.close_result == 2


@dataclass(frozen=True)
class SerialPortCandidate:
    device: str
    description: str
    hwid: str
    vid: Optional[int]
    pid: Optional[int]

    def label(self) -> str:
        bits = [self.device]
        if self.description and self.description != "n/a":
            bits.append(self.description)
        if self.hwid and self.hwid != "n/a":
            bits.append(self.hwid)
        return " | ".join(bits)


@dataclass(frozen=True)
class WindowsUsbDeviceCandidate:
    description: str
    friendly_name: str
    location: str
    hardware_id: str
    vid: Optional[str]
    pid: Optional[str]
    serial: Optional[str]

    def label(self) -> str:
        bits: List[str] = []
        if self.friendly_name and self.friendly_name != "n/a":
            bits.append(self.friendly_name)
        elif self.description and self.description != "n/a":
            bits.append(self.description)
        if self.location and self.location != "n/a":
            bits.append(self.location)
        if self.vid and self.pid:
            bits.append(f"VID={self.vid} PID={self.pid}")
        if self.serial:
            bits.append(f"SER={self.serial}")
        if self.hardware_id and self.hardware_id != "n/a":
            bits.append(self.hardware_id)
        return " | ".join(bits)


def list_serial_candidates() -> List[SerialPortCandidate]:
    candidates: List[SerialPortCandidate] = []
    for port in sorted(serial.tools.list_ports.comports(), key=lambda item: item.device):
        candidates.append(
            SerialPortCandidate(
                device=str(port.device),
                description=str(port.description or "n/a"),
                hwid=str(port.hwid or "n/a"),
                vid=getattr(port, "vid", None),
                pid=getattr(port, "pid", None),
            )
        )
    return candidates


def _normalize_usb_id(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def _extract_vid_pid(hardware_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    if not hardware_id:
        return None, None
    vid_match = re.search(r"VID_([0-9A-Fa-f]{4})", hardware_id)
    pid_match = re.search(r"PID_([0-9A-Fa-f]{4})", hardware_id)
    return (
        vid_match.group(1).upper() if vid_match else None,
        pid_match.group(1).upper() if pid_match else None,
    )


def _extract_usb_serial(hardware_id: Optional[str]) -> Optional[str]:
    if not hardware_id:
        return None
    parts = str(hardware_id).split("\\")
    if len(parts) > 2:
        return parts[-1] or None
    return None


def list_windows_usb_devices(
    target_vid: Optional[str] = None,
    target_pid: Optional[str] = None,
) -> List[WindowsUsbDeviceCandidate]:
    if os.name != "nt":
        return []

    normalized_vid = _normalize_usb_id(target_vid)
    normalized_pid = _normalize_usb_id(target_pid)

    DIGCF_PRESENT = 0x00000002
    DIGCF_ALLCLASSES = 0x00000004
    SPDRP_DEVICEDESC = 0x00000000
    SPDRP_HARDWAREID = 0x00000001
    SPDRP_FRIENDLYNAME = 0x0000000C
    SPDRP_LOCATION_INFORMATION = 0x0000000D
    ERROR_NO_MORE_ITEMS = 259
    INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

    class SP_DEVINFO_DATA(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("ClassGuid", ctypes.c_byte * 16),
            ("DevInst", wintypes.DWORD),
            ("Reserved", ctypes.POINTER(ctypes.c_ulong)),
        ]

    setupapi = ctypes.WinDLL("setupapi")
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetLastError.restype = wintypes.DWORD
    kernel32.GetLastError.argtypes = []

    setup_get_class_devs = setupapi.SetupDiGetClassDevsW
    setup_get_class_devs.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD]
    setup_get_class_devs.restype = wintypes.HANDLE

    setup_enum_device_info = setupapi.SetupDiEnumDeviceInfo
    setup_enum_device_info.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)]
    setup_enum_device_info.restype = wintypes.BOOL

    setup_get_property = setupapi.SetupDiGetDeviceRegistryPropertyW
    setup_get_property.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(SP_DEVINFO_DATA),
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    setup_get_property.restype = wintypes.BOOL

    setup_destroy_info = setupapi.SetupDiDestroyDeviceInfoList
    setup_destroy_info.argtypes = [wintypes.HANDLE]
    setup_destroy_info.restype = wintypes.BOOL

    def get_property(device_info_set: Any, devinfo: SP_DEVINFO_DATA, prop: int) -> Optional[str]:
        buffer = (ctypes.c_byte * 1024)()
        reg_type = wintypes.DWORD()
        size = wintypes.DWORD()
        success = setup_get_property(
            device_info_set,
            ctypes.byref(devinfo),
            prop,
            ctypes.byref(reg_type),
            buffer,
            ctypes.sizeof(buffer),
            ctypes.byref(size),
        )
        if not success:
            return None
        return ctypes.wstring_at(buffer)

    device_info_set = setup_get_class_devs(None, None, None, DIGCF_PRESENT | DIGCF_ALLCLASSES)
    if device_info_set == INVALID_HANDLE_VALUE:
        raise ctypes.WinError()

    matches: List[WindowsUsbDeviceCandidate] = []
    try:
        index = 0
        while True:
            devinfo = SP_DEVINFO_DATA()
            devinfo.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not setup_enum_device_info(device_info_set, index, ctypes.byref(devinfo)):
                err = kernel32.GetLastError()
                if err == ERROR_NO_MORE_ITEMS:
                    break
                raise ctypes.WinError(err)

            desc = str(get_property(device_info_set, devinfo, SPDRP_DEVICEDESC) or "n/a")
            friendly = str(get_property(device_info_set, devinfo, SPDRP_FRIENDLYNAME) or "n/a")
            location = str(get_property(device_info_set, devinfo, SPDRP_LOCATION_INFORMATION) or "n/a")
            hardware_id = str(get_property(device_info_set, devinfo, SPDRP_HARDWAREID) or "n/a")

            if hardware_id.startswith("USB"):
                vid, pid = _extract_vid_pid(hardware_id)
                if normalized_vid and vid != normalized_vid:
                    index += 1
                    continue
                if normalized_pid and pid != normalized_pid:
                    index += 1
                    continue
                matches.append(
                    WindowsUsbDeviceCandidate(
                        description=desc,
                        friendly_name=friendly,
                        location=location,
                        hardware_id=hardware_id,
                        vid=vid,
                        pid=pid,
                        serial=_extract_usb_serial(hardware_id),
                    )
                )
            index += 1
    finally:
        setup_destroy_info(device_info_set)

    return matches


def probe_teensy_handshake(
    port: str,
    baudrate: int,
    timeout_seconds: float,
    read_chunk_bytes: int,
    handshake_timeout_seconds: float = 3.0,
) -> Dict[str, Any]:
    reader = TeensyTTLReader(
        port=port,
        baudrate=baudrate,
        timeout_seconds=timeout_seconds,
        read_chunk_bytes=read_chunk_bytes,
    )
    try:
        reader.open()
        handshake = reader.read_handshake(timeout_seconds=handshake_timeout_seconds)
        return {
            "ok": True,
            "port": port,
            "sampling_rate_hz": handshake.sampling_rate_hz,
            "frame_size": handshake.frame_size,
            "channel_map": list(handshake.channel_map),
            "firmware_version": handshake.firmware_version,
            "git_hash": handshake.git_hash,
        }
    finally:
        reader.close()


def resolve_active_stimulus_channels(config: Dict[str, Any]) -> Dict[str, Any]:
    stimulus_cfg = config.get("stimulus", {})
    all_channels = stimulus_cfg.get("channels", {})
    if not isinstance(all_channels, dict):
        return {}

    control_mode = str(stimulus_cfg.get("control_mode", "closed_loop")).strip().lower()
    if control_mode == "open_loop":
        requested = stimulus_cfg.get("target_channels", [])
    else:
        requested = []
        for rule in config.get("closed_loop", {}).get("rules", []):
            outputs = rule.get("outputs", {})
            if not isinstance(outputs, dict):
                continue
            laser_channels = outputs.get("laser_channels", [])
            if isinstance(laser_channels, list):
                requested.extend(laser_channels)

    names = [str(name).strip() for name in requested if str(name).strip()]
    if not names:
        return {}
    resolved: Dict[str, Any] = {}
    for name in names:
        if name in all_channels and name not in resolved:
            resolved[name] = all_channels[name]
    return resolved


def probe_doric_ports(dll_path: str, candidate_ports: List[int], wait_ms: int = 500) -> List[LaserProbeResult]:
    loaded_dll = load_doric_dll(dll_path)
    dll = loaded_dll.dll
    dll.init.argtypes = [c_int]
    dll.init.restype = c_int
    dll.wait.argtypes = [c_int]
    dll.wait.restype = c_int
    has_port_api = all(
        dll_has_symbol(dll, name)
        for name in ("available_devices_with_ports", "open_device", "close_device")
    )
    if not has_port_api:
        return [
            LaserProbeResult(
                port=port,
                open_result=None,
                wait_result=None,
                close_result=None,
                close_wait_result=None,
                error="Doric DLL does not expose the legacy port API. Configure stimulus.uid for this DLL build.",
            )
            for port in candidate_ports
        ]

    dll.available_devices_with_ports.argtypes = []
    dll.available_devices_with_ports.restype = c_int
    dll.open_device.argtypes = [c_int]
    dll.open_device.restype = c_int
    dll.close_device.argtypes = [c_int]
    dll.close_device.restype = c_int
    dll.quit.argtypes = []
    dll.quit.restype = c_int

    results: List[LaserProbeResult] = []
    dll.init(1)
    dll.wait(wait_ms)
    dll.available_devices_with_ports()
    try:
        seen_ports = set()
        for port in candidate_ports:
            if port in seen_ports:
                continue
            seen_ports.add(port)
            try:
                open_result = dll.open_device(port)
                wait_result = dll.wait(wait_ms)
                close_result = dll.close_device(port)
                close_wait_result = dll.wait(250)
                results.append(
                    LaserProbeResult(
                        port=port,
                        open_result=open_result,
                        wait_result=wait_result,
                        close_result=close_result,
                        close_wait_result=close_wait_result,
                        error=None,
                    )
                )
            except Exception as exc:
                results.append(
                    LaserProbeResult(
                        port=port,
                        open_result=None,
                        wait_result=None,
                        close_result=None,
                        close_wait_result=None,
                        error=str(exc),
                    )
                )
    finally:
        dll.quit()
        close_dll_directory_handles(loaded_dll.directory_handles)
    return results


def _build_square_config(square_cfg: Dict[str, Any]) -> LightSourceSquareConfig:
    return LightSourceSquareConfig(
        period_ms=float(square_cfg["period_ms"]),
        time_on_ms=float(square_cfg["time_on_ms"]),
        nb_of_seq=int(square_cfg["nb_of_seq"]),
        nb_of_pulses_per_seq=int(square_cfg["nb_of_pulses_per_seq"]),
        starting_delay_ms=int(square_cfg["starting_delay_ms"]),
        delay_between_seq_ms=int(square_cfg["delay_between_seq_ms"]),
        ttl_output=bool(square_cfg["ttl_output"]),
    )


def _build_channel_hw_map(channels: Dict[str, Any]) -> Dict[str, ChannelHardwareConfig]:
    return {
        str(name): ChannelHardwareConfig(index=int(ch["index"]), current_ma=int(ch["current_ma"]))
        for name, ch in channels.items()
    }


def verify_laser_setup(stimulus_cfg: Dict[str, Any], quiet_mode: bool = True) -> None:
    channel_map = _build_channel_hw_map(stimulus_cfg["channels"])
    verify_laser_channel_setup(
        dll_path=str(stimulus_cfg["dll_path"]),
        port=int(stimulus_cfg["port"]) if stimulus_cfg.get("port") is not None else None,
        uid=stimulus_cfg.get("uid"),
        channel_map=channel_map,
        square_cfg=stimulus_cfg["square"],
        quiet_mode=quiet_mode,
    )


def verify_laser_channel_setup(
    dll_path: str,
    port: Optional[int],
    channel_map: Dict[str, ChannelHardwareConfig],
    square_cfg: Dict[str, Any],
    quiet_mode: bool = True,
    uid: Optional[str] = None,
) -> None:
    square = _build_square_config(square_cfg)
    laser = DoricLightSource(
        dll_path=str(dll_path),
        port=int(port) if port is not None else None,
        uid=uid,
        channels=channel_map,
        square=square,
    )
    try:
        laser.connect()
    finally:
        laser.close()


def connect_laser_channel_for_verification(
    dll_path: str,
    port: Optional[int],
    channel_name: str,
    channel_idx: int,
    channel_current_ma: int,
    square_cfg: Dict[str, Any],
    uid: Optional[str] = None,
) -> DoricLightSource:
    square = _build_square_config(square_cfg)
    laser = DoricLightSource(
        dll_path=str(dll_path),
        port=int(port) if port is not None else None,
        uid=uid,
        channels={str(channel_name): ChannelHardwareConfig(index=int(channel_idx), current_ma=int(channel_current_ma))},
        square=square,
    )
    laser.connect()
    return laser


def connect_laser_for_run(
    dll_path: str,
    port: Optional[int],
    channel_map: Dict[str, ChannelHardwareConfig],
    square_cfg: Dict[str, Any],
    uid: Optional[str] = None,
) -> DoricLightSource:
    square = _build_square_config(square_cfg)
    laser = DoricLightSource(
        dll_path=str(dll_path),
        port=int(port) if port is not None else None,
        uid=uid,
        channels=channel_map,
        square=square,
    )
    laser.connect()
    return laser
