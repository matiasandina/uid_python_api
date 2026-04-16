from __future__ import annotations

import contextlib
import os
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from ctypes import CDLL, c_char_p, c_int, POINTER, pointer

from doric_system_defs import Channel, TriggerType, TriggerMode
from doric_light_source_defs import LightSourceSettings, LightSourceMode


LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR = 0x00000100
LOAD_LIBRARY_SEARCH_DEFAULT_DIRS = 0x00001000
LOAD_WITH_ALTERED_SEARCH_PATH = 0x00000008
DORIC_DLL_PACKAGE_URL = "https://doriclenses.com/downloads/.updates/softwares/DoricSystemDLL.zip"
DEFAULT_DORIC_DLL_DIR = Path(__file__).resolve().parent / "vendor" / "DoricSystemDLL"
SETTINGS_APPLY_WAIT_MS = 50
CHANNEL_COMMAND_WAIT_MS = 10


@dataclass(frozen=True)
class ChannelHardwareConfig:
    index: int
    current_ma: int


@dataclass(frozen=True)
class LightSourceSquareConfig:
    period_ms: float
    time_on_ms: float
    nb_of_seq: int
    nb_of_pulses_per_seq: int
    starting_delay_ms: int
    delay_between_seq_ms: int
    ttl_output: bool = True


@dataclass(frozen=True)
class DoricDllLoad:
    path: Path
    dll: Any
    directory_handles: List[Any]


def dll_has_symbol(dll: Any, name: str) -> bool:
    try:
        getattr(dll, name)
        return True
    except AttributeError:
        return False


def resolve_doric_dll_path(dll_path: str) -> Path:
    expanded = os.path.expandvars(os.path.expanduser(str(dll_path).strip()))
    expanded = re.sub(r"%([^%]+)%", lambda match: os.environ.get(match.group(1), match.group(0)), expanded)
    path = Path(expanded)
    if path.exists() and path.is_file():
        return path.resolve()
    if path.exists() and path.is_dir():
        candidates = sorted(path.rglob("DoricSystem.dll"))
        if not candidates:
            raise FileNotFoundError(f"Doric DLL not found under directory: {path}")
        preferred = _preferred_doric_dll_candidates(candidates)
        if len(preferred) == 1:
            return preferred[0].resolve()
        if len(candidates) == 1:
            return candidates[0].resolve()
        display = preferred if preferred else candidates
        rendered = "; ".join(str(candidate) for candidate in display[:5])
        raise ValueError(
            "Multiple Doric DLL candidates found. "
            f"Set stimulus.dll_path to the exact DLL file. Candidates: {rendered}"
        )
    raise FileNotFoundError(f"Doric DLL not found: {path}")


def _preferred_doric_dll_candidates(candidates: List[Path]) -> List[Path]:
    by_parts = [(candidate, {part.lower() for part in candidate.parts}) for candidate in candidates]

    x64_release = [
        candidate
        for candidate, parts in by_parts
        if "x64" in parts and "release" in parts
    ]
    if x64_release:
        return x64_release

    x64 = [
        candidate
        for candidate, parts in by_parts
        if "x64" in parts and not ({"x86", "win32"} & parts)
    ]
    if x64:
        return x64

    release = [
        candidate
        for candidate, parts in by_parts
        if "release" in parts
    ]
    return release


def add_doric_dll_search_paths(dll_path: Path) -> List[Any]:
    if os.name != "nt":
        return []

    handles: List[Any] = []
    seen = set()

    for directory in get_doric_dll_search_directories(dll_path):
        if not directory.exists() or not directory.is_dir():
            continue
        key = str(directory).lower()
        if key in seen:
            continue
        seen.add(key)
        handles.append(os.add_dll_directory(str(directory)))
    return handles


def get_doric_dll_search_directories(dll_path: Path) -> List[Path]:
    directories = [dll_path.parent, dll_path.parent / "Qt"]

    parts = [part.lower() for part in dll_path.parts]
    if "api" in parts:
        api_idx = parts.index("api")
        package_root = Path(*dll_path.parts[:api_idx])
        selected_is_x64 = "x64" in parts
        selected_is_release = "release" in parts
        selected_is_debug = "debug" in parts
        dll_dirs = [
            directory
            for directory in package_root.rglob("*")
            if directory.is_dir()
            and any(directory.glob("*.dll"))
            and _doric_dll_directory_matches_selection(
                directory,
                selected_is_x64=selected_is_x64,
                selected_is_release=selected_is_release,
                selected_is_debug=selected_is_debug,
            )
        ]
        directories.extend(sorted(dll_dirs, key=_dll_search_directory_sort_key))

    unique_directories = []
    seen = set()
    for directory in directories:
        directory = directory.resolve()
        key = str(directory).lower()
        if key in seen:
            continue
        seen.add(key)
        unique_directories.append(directory)
    return unique_directories


def _doric_dll_directory_matches_selection(
    directory: Path,
    *,
    selected_is_x64: bool,
    selected_is_release: bool,
    selected_is_debug: bool,
) -> bool:
    parts = {part.lower() for part in directory.parts}
    if selected_is_x64 and ({"x86", "win32"} & parts):
        return False
    if selected_is_release and "debug" in parts:
        return False
    if selected_is_debug and "release" in parts:
        return False
    return True


def _dll_search_directory_sort_key(directory: Path) -> tuple[int, int, str]:
    parts = {part.lower() for part in directory.parts}
    arch_rank = 0 if "x64" in parts else 2 if ({"x86", "win32"} & parts) else 1
    release_rank = 0 if "release" in parts else 1
    return (arch_rank, release_rank, str(directory).lower())


def describe_doric_dll_search(dll_path: str, max_dlls: int = 80) -> List[str]:
    path = resolve_doric_dll_path(dll_path)
    directories = get_doric_dll_search_directories(path)

    lines = [
        f"requested_dll_path: {dll_path}",
        f"resolved_dll: {path}",
        "search_dirs:",
    ]
    for directory in directories:
        dlls = sorted(directory.glob("*.dll")) if directory.exists() and directory.is_dir() else []
        lines.append(f"  - {directory} exists={directory.exists()} dll_count={len(dlls)}")
        for dll in dlls[:max_dlls]:
            lines.append(f"      {dll.name}")
        if len(dlls) > max_dlls:
            lines.append(f"      ... {len(dlls) - max_dlls} more")
    return lines


def close_dll_directory_handles(handles: List[Any]) -> None:
    for handle in reversed(handles):
        try:
            handle.close()
        except Exception:
            pass


def load_doric_dll(dll_path: str) -> DoricDllLoad:
    path = resolve_doric_dll_path(dll_path)
    handles = add_doric_dll_search_paths(path)
    try:
        dll = load_doric_cdll(path)
    except Exception:
        close_dll_directory_handles(handles)
        raise
    return DoricDllLoad(path=path, dll=dll, directory_handles=handles)


def load_doric_cdll(path: Path) -> Any:
    if os.name != "nt":
        return CDLL(str(path))

    attempts = [
        (
            "dll_load_dir_default_dirs",
            LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS,
        ),
        ("altered_search_path", LOAD_WITH_ALTERED_SEARCH_PATH),
        ("legacy_default", 0),
    ]
    errors = []
    for label, winmode in attempts:
        try:
            return CDLL(str(path), winmode=winmode)
        except OSError as exc:
            errors.append(f"{label}: {exc}")

    joined = "\n".join(errors)
    raise FileNotFoundError(f"Could not load Doric DLL or one of its dependencies:\n{joined}")


class DoricLightSource:
    def __init__(
        self,
        dll_path: str,
        port: Optional[int],
        channels: Dict[str, ChannelHardwareConfig],
        square: LightSourceSquareConfig,
        uid: Optional[str] = None,
        verbose: bool = True,
    ) -> None:
        self._dll_path_input = str(dll_path)
        self._dll_path: Optional[Path] = None
        self._uid = str(uid).strip() if uid is not None else ""
        self._uid_bytes = self._uid.encode("ascii") if self._uid else b""
        self._port = int(port) if port is not None else None
        if not self._uid and self._port is None:
            raise ValueError("DoricLightSource requires either uid or port.")
        self._channels = dict(channels)
        self._square = square
        self._verbose = bool(verbose)
        self._lock = threading.Lock()
        self._dll: Optional[CDLL] = None
        self._dll_dir_handles: List[Any] = []

    def connect(self) -> None:
        with self._lock:
            if self._dll is not None:
                return
            loaded_dll = load_doric_dll(self._dll_path_input)
            self._dll_path = loaded_dll.path
            self._dll_dir_handles = loaded_dll.directory_handles
            self._dll = loaded_dll.dll
            self._attach_signatures()
            with self._dll_console_context():
                self._dll.init(True)
                self._dll.wait(5000)
                if self._uid:
                    self._dll.available_devices_infos()
                    self._dll.open_device_uid(self._uid_bytes)
                else:
                    self._dll.available_devices_with_ports()
                    self._dll.open_device(self._port)
                self._dll.wait(5000)
            self._apply_square_settings()

    def close(self) -> None:
        with self._lock:
            if self._dll is None:
                return
            try:
                with self._dll_console_context():
                    if self._uid:
                        self._dll.ls_stop_all_uid(self._uid_bytes)
                    else:
                        self._dll.ls_stop_all(self._port)
                    self._dll.wait(1000)
                    if self._uid:
                        self._dll.close_device_uid(self._uid_bytes)
                    else:
                        self._dll.close_device(self._port)
                    self._dll.wait(1000)
                    self._dll.quit()
            finally:
                self._dll = None
                close_dll_directory_handles(self._dll_dir_handles)
                self._dll_dir_handles = []

    def start_channel(self, channel_name: str) -> None:
        channel_idx = self._resolve_channel(channel_name)
        with self._lock:
            self._require_connected()
            with self._dll_console_context():
                if self._uid:
                    self._dll.ls_start_channel_uid(self._uid_bytes, channel_idx)
                else:
                    self._dll.ls_start_channel(self._port, channel_idx)
                self._dll.wait(CHANNEL_COMMAND_WAIT_MS)

    def stop_channel(self, channel_name: str) -> None:
        channel_idx = self._resolve_channel(channel_name)
        with self._lock:
            if self._dll is None:
                return
            with self._dll_console_context():
                if self._uid:
                    self._dll.ls_stop_channel_uid(self._uid_bytes, channel_idx)
                else:
                    self._dll.ls_stop_channel(self._port, channel_idx)
                self._dll.wait(CHANNEL_COMMAND_WAIT_MS)

    def start_all(self) -> None:
        with self._lock:
            self._require_connected()
            with self._dll_console_context():
                if self._uid:
                    self._dll.ls_start_all_uid(self._uid_bytes)
                else:
                    self._dll.ls_start_all(self._port)

    def stop_all(self) -> None:
        with self._lock:
            if self._dll is None:
                return
            with self._dll_console_context():
                if self._uid:
                    self._dll.ls_stop_all_uid(self._uid_bytes)
                else:
                    self._dll.ls_stop_all(self._port)

    def configure_channel(
        self,
        channel_name: str,
        *,
        current_ma: Optional[int] = None,
        mode: LightSourceMode | str = LightSourceMode.Square,
        square: Optional[LightSourceSquareConfig] = None,
    ) -> None:
        with self._lock:
            self._require_connected()
            channel_cfg = self._channels.get(channel_name)
            if channel_cfg is None:
                raise KeyError(f"Unknown channel '{channel_name}'. Known: {list(self._channels)}")
            next_current = int(channel_cfg.current_ma if current_ma is None else current_ma)
            next_mode = self._normalize_mode(mode)
            next_square = square if square is not None else self._square
            self._send_channel_settings(
                channel_name=channel_name,
                channel_cfg=ChannelHardwareConfig(index=channel_cfg.index, current_ma=next_current),
                mode=next_mode,
                square=next_square,
            )
            self._channels[channel_name] = ChannelHardwareConfig(index=channel_cfg.index, current_ma=next_current)

    def update_channel_current(self, channel_name: str, current_ma: int) -> None:
        self.configure_channel(channel_name, current_ma=int(current_ma))

    def _attach_signatures(self) -> None:
        self._dll.init.argtypes = [c_int]
        self._dll.wait.argtypes = [c_int]
        self._dll.quit.argtypes = []
        if self._uid:
            required = (
                "available_devices_infos",
                "open_device_uid",
                "close_device_uid",
                "ls_start_all_uid",
                "ls_stop_all_uid",
                "ls_start_channel_uid",
                "ls_stop_channel_uid",
                "ls_send_settings_uid",
            )
            missing = [name for name in required if not hasattr(self._dll, name)]
            if missing:
                raise RuntimeError(
                    "Configured stimulus.uid requires the newer Doric DLL UID API. "
                    f"Missing symbols: {', '.join(missing)}"
                )
            self._dll.available_devices_infos.argtypes = []
            self._dll.open_device_uid.argtypes = [c_char_p]
            self._dll.close_device_uid.argtypes = [c_char_p]
            self._dll.ls_start_all_uid.argtypes = [c_char_p]
            self._dll.ls_stop_all_uid.argtypes = [c_char_p]
            self._dll.ls_start_channel_uid.argtypes = [c_char_p, c_int]
            self._dll.ls_stop_channel_uid.argtypes = [c_char_p, c_int]
            self._dll.ls_send_settings_uid.argtypes = [c_char_p, POINTER(LightSourceSettings)]
        else:
            required = (
                "available_devices_with_ports",
                "open_device",
                "close_device",
                "ls_start_all",
                "ls_stop_all",
                "ls_start_channel",
                "ls_stop_channel",
                "ls_send_settings",
            )
            missing = [name for name in required if not dll_has_symbol(self._dll, name)]
            if missing:
                raise RuntimeError(
                    "Configured stimulus.port requires the legacy Doric port API. "
                    f"Missing symbols: {', '.join(missing)}. "
                    "Set stimulus.uid for newer Doric DLL builds that only expose the UID API."
                )
            self._dll.available_devices_with_ports.argtypes = []
            self._dll.open_device.argtypes = [c_int]
            self._dll.close_device.argtypes = [c_int]
            self._dll.ls_start_all.argtypes = [c_int]
            self._dll.ls_stop_all.argtypes = [c_int]
            self._dll.ls_start_channel.argtypes = [c_int, c_int]
            self._dll.ls_stop_channel.argtypes = [c_int, c_int]
            self._dll.ls_send_settings.argtypes = [c_int, POINTER(LightSourceSettings)]

    def _apply_square_settings(self) -> None:
        for name, channel_cfg in self._channels.items():
            self._send_channel_settings(
                channel_name=name,
                channel_cfg=channel_cfg,
                mode=LightSourceMode.Square,
                square=self._square,
            )

    def _send_channel_settings(
        self,
        *,
        channel_name: str,
        channel_cfg: ChannelHardwareConfig,
        mode: LightSourceMode,
        square: LightSourceSquareConfig,
    ) -> None:
        settings = LightSourceSettings()
        settings.channelIdx = Channel(self._resolve_channel(channel_name))
        settings.mode = mode
        settings.isTTLOutput = bool(square.ttl_output)
        settings.triggerType = TriggerType.Manual
        settings.triggerMode = TriggerMode.Uninterrupted
        settings.ttlModulation.current = int(channel_cfg.current_ma)
        settings.ttlModulation.startingDelayMs = int(square.starting_delay_ms)
        settings.ttlModulation.delayBetweenSeqMs = int(square.delay_between_seq_ms)
        settings.ttlModulation.nbOfSeq = int(square.nb_of_seq)
        settings.ttlModulation.nbOfPulsesPerSeq = int(square.nb_of_pulses_per_seq)
        if mode == LightSourceMode.CW:
            settings.ttlModulation.periodMs = 0.0
            settings.ttlModulation.timeOnMs = 0.0
            settings.isTTLOutput = False
        else:
            settings.ttlModulation.periodMs = float(square.period_ms)
            settings.ttlModulation.timeOnMs = float(square.time_on_ms)
        if self._uid:
            with self._dll_console_context():
                self._dll.ls_send_settings_uid(self._uid_bytes, pointer(settings))
                self._dll.wait(SETTINGS_APPLY_WAIT_MS)
        else:
            with self._dll_console_context():
                self._dll.ls_send_settings(self._port, pointer(settings))
                self._dll.wait(SETTINGS_APPLY_WAIT_MS)

    def _normalize_mode(self, mode: LightSourceMode | str) -> LightSourceMode:
        if isinstance(mode, LightSourceMode):
            return mode
        normalized = str(mode).strip().lower()
        if normalized == "square":
            return LightSourceMode.Square
        if normalized in {"cw", "continuous", "continuous_wave"}:
            return LightSourceMode.CW
        raise ValueError(f"Unsupported light source mode '{mode}'.")

    def _resolve_channel(self, channel_name: str) -> int:
        if channel_name not in self._channels:
            raise KeyError(f"Unknown channel '{channel_name}'. Known: {list(self._channels)}")
        return int(self._channels[channel_name].index)

    def _require_connected(self) -> None:
        if self._dll is None:
            raise RuntimeError("Doric LightSource is not connected.")

    def _dll_console_context(self) -> contextlib.AbstractContextManager[None]:
        if self._verbose:
            return contextlib.nullcontext()
        return _silence_process_console()


@contextlib.contextmanager
def _silence_process_console():
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass

    try:
        stdout_fd = sys.stdout.fileno()
        stderr_fd = sys.stderr.fileno()
    except Exception:
        yield
        return

    saved_stdout = os.dup(stdout_fd)
    saved_stderr = os.dup(stderr_fd)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull_fd, stdout_fd)
        os.dup2(devnull_fd, stderr_fd)
        yield
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass
        os.dup2(saved_stdout, stdout_fd)
        os.dup2(saved_stderr, stderr_fd)
        os.close(saved_stdout)
        os.close(saved_stderr)
        os.close(devnull_fd)
