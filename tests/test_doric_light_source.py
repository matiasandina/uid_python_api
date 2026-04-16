import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from doric_light_source import (
    CHANNEL_COMMAND_WAIT_MS,
    ChannelHardwareConfig,
    DoricLightSource,
    LightSourceSquareConfig,
    SETTINGS_APPLY_WAIT_MS,
    get_doric_dll_search_directories,
    resolve_doric_dll_path,
)


class FakeDllFunction:
    def __init__(self, name, calls):
        self.name = name
        self.calls = calls
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        self.calls.append((self.name, args))
        return 2


class FakeDoricDll:
    def __init__(self):
        self.calls = []
        for name in (
            "init",
            "wait",
            "available_devices_infos",
            "available_devices_with_ports",
            "open_device_uid",
            "open_device",
            "close_device_uid",
            "close_device",
            "quit",
            "ls_start_all",
            "ls_start_all_uid",
            "ls_stop_all",
            "ls_stop_all_uid",
            "ls_start_channel",
            "ls_start_channel_uid",
            "ls_stop_channel",
            "ls_stop_channel_uid",
            "ls_send_settings",
            "ls_send_settings_uid",
        ):
            setattr(self, name, FakeDllFunction(name, self.calls))


class FakeUidOnlyDoricDll:
    def __init__(self):
        self.calls = []
        for name in (
            "init",
            "wait",
            "available_devices_infos",
            "open_device_uid",
            "close_device_uid",
            "quit",
            "ls_start_all_uid",
            "ls_stop_all_uid",
            "ls_start_channel_uid",
            "ls_stop_channel_uid",
            "ls_send_settings_uid",
        ):
            setattr(self, name, FakeDllFunction(name, self.calls))


class ResolveDoricDllPathTests(unittest.TestCase):
    def test_resolve_doric_dll_path_returns_absolute_path_for_relative_input(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            repo_root = root / "repo"
            dll_dir = repo_root / "vendor" / "DoricSystemDLL" / "x64" / "release"
            dll_dir.mkdir(parents=True)
            dll_path = dll_dir / "DoricSystem.dll"
            dll_path.write_bytes(b"")

            previous_cwd = Path.cwd()
            os.chdir(repo_root)
            try:
                resolved = resolve_doric_dll_path("./vendor/DoricSystemDLL")
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(resolved, dll_path.resolve())

    def test_resolve_doric_dll_path_expands_environment_variable_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dll_dir = root / "Downloads" / "DoricSystemDLL-2024" / "x64" / "Release"
            dll_dir.mkdir(parents=True)
            dll_path = dll_dir / "DoricSystem.dll"
            dll_path.write_bytes(b"")

            with mock.patch.dict(os.environ, {"USERPROFILE": str(root)}, clear=False):
                resolved = resolve_doric_dll_path(r"%USERPROFILE%/Downloads/DoricSystemDLL-2024")

        self.assertEqual(resolved, dll_path)

    def test_resolve_doric_dll_path_prefers_x64_api_lib_layout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            api_dir = root / "DoricSystemDLL" / "DoricSystemDLL-2026-April" / "API" / "lib"
            x64_path = api_dir / "x64" / "DoricSystem.dll"
            x86_path = api_dir / "x86" / "DoricSystem.dll"
            x64_path.parent.mkdir(parents=True)
            x86_path.parent.mkdir(parents=True)
            x64_path.write_bytes(b"")
            x86_path.write_bytes(b"")

            resolved = resolve_doric_dll_path(str(root / "DoricSystemDLL"))

        self.assertEqual(resolved, x64_path)

    def test_get_doric_dll_search_directories_includes_api_dependency_dirs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            api_root = root / "DoricSystemDLL" / "DoricSystemDLL-2026-April" / "API"
            package_root = api_root.parent
            dll_path = api_root / "lib" / "x64" / "release" / "DoricSystem.dll"
            dependency_path = api_root / "bin" / "x64" / "release" / "Qt6Core.dll"
            package_dependency_path = package_root / "runtime" / "x64" / "release" / "support.dll"
            wrong_arch_path = api_root / "bin" / "x86" / "release" / "Qt6Core.dll"
            wrong_build_path = api_root / "bin" / "x64" / "debug" / "Qt6Core.dll"
            dll_path.parent.mkdir(parents=True)
            dependency_path.parent.mkdir(parents=True)
            package_dependency_path.parent.mkdir(parents=True)
            wrong_arch_path.parent.mkdir(parents=True)
            wrong_build_path.parent.mkdir(parents=True)
            dll_path.write_bytes(b"")
            dependency_path.write_bytes(b"")
            package_dependency_path.write_bytes(b"")
            wrong_arch_path.write_bytes(b"")
            wrong_build_path.write_bytes(b"")

            directories = get_doric_dll_search_directories(dll_path)

        self.assertIn(dll_path.parent, directories)
        self.assertIn(dependency_path.parent, directories)
        self.assertIn(package_dependency_path.parent, directories)
        self.assertNotIn(wrong_arch_path.parent, directories)
        self.assertNotIn(wrong_build_path.parent, directories)


class DoricLightSourceUidTests(unittest.TestCase):
    def test_uid_path_uses_uid_dll_functions_without_port(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dll_path = Path(tmpdir) / "DoricSystem.dll"
            dll_path.write_bytes(b"")
            fake_dll = FakeUidOnlyDoricDll()
            square = LightSourceSquareConfig(
                period_ms=50,
                time_on_ms=10,
                nb_of_seq=0,
                nb_of_pulses_per_seq=0,
                starting_delay_ms=0,
                delay_between_seq_ms=0,
            )
            laser = DoricLightSource(
                dll_path=str(dll_path),
                port=None,
                uid="c006855a20e326fe",
                channels={"ch1": ChannelHardwareConfig(index=0, current_ma=10)},
                square=square,
            )

            with mock.patch("doric_light_source.CDLL", return_value=fake_dll):
                laser.connect()
                laser.start_channel("ch1")
                laser.stop_channel("ch1")
                laser.close()

        names = [name for name, _args in fake_dll.calls]
        self.assertIn("available_devices_infos", names)
        self.assertIn("open_device_uid", names)
        self.assertIn("ls_send_settings_uid", names)
        self.assertIn("ls_start_channel_uid", names)
        self.assertIn("ls_stop_channel_uid", names)
        self.assertIn("close_device_uid", names)
        self.assertNotIn("open_device", names)
        self.assertIn(("wait", (SETTINGS_APPLY_WAIT_MS,)), fake_dll.calls)
        self.assertIn(("wait", (CHANNEL_COMMAND_WAIT_MS,)), fake_dll.calls)

    def test_requires_uid_or_port(self):
        square = LightSourceSquareConfig(
            period_ms=50,
            time_on_ms=10,
            nb_of_seq=0,
            nb_of_pulses_per_seq=0,
            starting_delay_ms=0,
            delay_between_seq_ms=0,
        )
        with self.assertRaisesRegex(ValueError, "either uid or port"):
            DoricLightSource(
                dll_path="C:/Doric/DoricSystem.dll",
                port=None,
                uid=None,
                channels={"ch1": ChannelHardwareConfig(index=0, current_ma=10)},
                square=square,
            )

    def test_port_path_requires_legacy_port_symbols(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dll_path = Path(tmpdir) / "DoricSystem.dll"
            dll_path.write_bytes(b"")
            fake_dll = FakeUidOnlyDoricDll()
            square = LightSourceSquareConfig(
                period_ms=50,
                time_on_ms=10,
                nb_of_seq=0,
                nb_of_pulses_per_seq=0,
                starting_delay_ms=0,
                delay_between_seq_ms=0,
            )
            laser = DoricLightSource(
                dll_path=str(dll_path),
                port=1,
                uid=None,
                channels={"ch1": ChannelHardwareConfig(index=0, current_ma=10)},
                square=square,
            )

            with mock.patch("doric_light_source.CDLL", return_value=fake_dll):
                with self.assertRaisesRegex(RuntimeError, "legacy Doric port API"):
                    laser.connect()

    def test_configure_channel_resends_settings_without_reconnect(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            dll_path = Path(tmpdir) / "DoricSystem.dll"
            dll_path.write_bytes(b"")
            fake_dll = FakeDoricDll()
            square = LightSourceSquareConfig(
                period_ms=50,
                time_on_ms=10,
                nb_of_seq=0,
                nb_of_pulses_per_seq=0,
                starting_delay_ms=0,
                delay_between_seq_ms=0,
            )
            laser = DoricLightSource(
                dll_path=str(dll_path),
                port=None,
                uid="c006855a20e326fe",
                channels={"ch1": ChannelHardwareConfig(index=0, current_ma=10)},
                square=square,
            )

            with mock.patch("doric_light_source.CDLL", return_value=fake_dll):
                laser.connect()
                laser.configure_channel("ch1", current_ma=55, mode="cw")
                laser.start_channel("ch1")
                laser.stop_channel("ch1")
                laser.close()

        names = [name for name, _args in fake_dll.calls]
        self.assertEqual(names.count("open_device_uid"), 1)
        self.assertGreaterEqual(names.count("ls_send_settings_uid"), 2)
        self.assertIn("ls_start_channel_uid", names)
        self.assertIn("ls_stop_channel_uid", names)
        self.assertGreaterEqual(fake_dll.calls.count(("wait", (SETTINGS_APPLY_WAIT_MS,))), 2)
        self.assertGreaterEqual(fake_dll.calls.count(("wait", (CHANNEL_COMMAND_WAIT_MS,))), 2)


if __name__ == "__main__":
    unittest.main()
