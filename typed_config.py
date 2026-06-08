from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from doric_light_source import DEFAULT_DORIC_DLL_DIR


SAFE_DEFAULTS: Dict[str, Any] = {
    "output_directory": "./data",
    "triggers": {"enabled": False},
    "closed_loop": {"rules": []},
    "stimulus": {"enabled": False, "mode": "monitor", "control_mode": None},
    "ttl_capture": {"enabled": False},
}

FORBIDDEN_OVERLAY_PATHS = {
    "devices",
    "output_directory",
    "display",
    "network",
    "stimulus.dll_path",
    "stimulus.discovery",
    "stimulus.port",
    "stimulus.uid",
    "ttl_capture.port",
    "ttl_capture.baudrate",
    "ttl_capture.serial_timeout_seconds",
    "ttl_capture.read_chunk_bytes",
}


def _deep_update(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            base[key] = _deep_update(dict(base[key]), value)
        else:
            base[key] = value
    return base


def _extract_key_line_map(node: Any, prefix: str = "") -> Dict[str, int]:
    if not isinstance(node, yaml.nodes.MappingNode):
        return {}
    found: Dict[str, int] = {}
    for key_node, value_node in node.value:
        key = str(key_node.value)
        path = f"{prefix}.{key}" if prefix else key
        found[path] = key_node.start_mark.line + 1
        found.update(_extract_key_line_map(value_node, path))
    return found


def _load_yaml_mapping(path: str | Path, *, required: bool) -> Tuple[Dict[str, Any], Dict[str, int]]:
    config_path = Path(path)
    if not config_path.exists():
        if required:
            raise FileNotFoundError(str(config_path))
        return {}, {}
    text = config_path.read_text(encoding="utf-8")
    loaded = yaml.safe_load(text) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Config file must contain a YAML mapping at the top level: {config_path}")
    composed = yaml.compose(text)
    line_map = _extract_key_line_map(composed) if composed is not None else {}
    return loaded, line_map


def _iter_overlay_paths(data: Any, prefix: str = "") -> List[str]:
    if not isinstance(data, dict):
        return []
    found: List[str] = []
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        found.append(path)
        found.extend(_iter_overlay_paths(value, path))
    return found


def _validate_overlay_paths(overlay_data: Dict[str, Any]) -> None:
    for path in _iter_overlay_paths(overlay_data):
        if path in FORBIDDEN_OVERLAY_PATHS:
            raise ValueError(
                f"Overlay config may not define machine-local field '{path}'. "
                "Move that value to config.local.yaml."
            )


class DeviceConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: Optional[str] = None
    port: Optional[int] = None
    name: Optional[str] = None


class DisplayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_hz: float = 10.0
    display_interval_seconds: float = 10.0

    @field_validator("refresh_hz", "display_interval_seconds")
    @classmethod
    def _positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("must be greater than 0")
        return float(value)


class NetworkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    socket_timeout_seconds: float = 5.0
    reconnect_delay_seconds: float = 30.0
    stale_timeout_seconds: float = 30.0
    health_check_interval_seconds: float = 10.0

    @field_validator("*")
    @classmethod
    def _positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("must be greater than 0")
        return float(value)


class DataConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    averaging_window_seconds: float = 60.0

    @field_validator("averaging_window_seconds")
    @classmethod
    def _positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("must be greater than 0")
        return float(value)


class TriggerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    mode: Optional[str] = None
    interval_seconds: Optional[float] = None
    clf_window_seconds: Optional[float] = None
    missing_animal_seconds: Optional[float] = None
    classifier: Optional[str] = None
    classifier_config: Dict[str, Any] = Field(default_factory=dict)
    target_channels: List[str] = Field(default_factory=list)

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        lowered = value.strip().lower()
        if lowered not in ("pulse", "window"):
            raise ValueError("must be 'pulse' or 'window'")
        return lowered

    @field_validator("interval_seconds", "clf_window_seconds", "missing_animal_seconds")
    @classmethod
    def _positive_optional(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if value <= 0:
            raise ValueError("must be greater than 0")
        return float(value)

    @field_validator("target_channels")
    @classmethod
    def _normalize_channels(cls, value: List[Any]) -> List[str]:
        return [str(ch).strip() for ch in value if str(ch).strip()]


class ClosedLoopOutputsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    laser_channels: List[str] = Field(default_factory=list)

    @field_validator("laser_channels")
    @classmethod
    def _normalize_channels(cls, value: List[Any]) -> List[str]:
        return [str(ch).strip() for ch in value if str(ch).strip()]


class ClosedLoopClassifierConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plugin: str
    evaluate_interval_seconds: float
    clf_data_input_window_seconds: float
    missing_animal_seconds: Optional[float] = None
    config: Dict[str, Any] = Field(default_factory=dict)
    mode: str = "window"

    @field_validator("plugin")
    @classmethod
    def _normalize_plugin(cls, value: str) -> str:
        stripped = str(value).strip()
        if not stripped:
            raise ValueError("closed_loop.rules[].classifier.plugin must be non-empty.")
        return stripped

    @field_validator("evaluate_interval_seconds", "clf_data_input_window_seconds", "missing_animal_seconds")
    @classmethod
    def _positive_optional(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        if value <= 0:
            raise ValueError("must be greater than 0")
        return float(value)

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, value: str) -> str:
        lowered = str(value).strip().lower()
        if lowered not in ("pulse", "window"):
            raise ValueError("must be 'pulse' or 'window'")
        return lowered


class ClosedLoopRuleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    devices: List[str] = Field(default_factory=list)
    classifier: ClosedLoopClassifierConfig
    outputs: ClosedLoopOutputsConfig
    assigned_animal_ids: List[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _normalize_id(cls, value: str) -> str:
        stripped = str(value).strip()
        if not stripped:
            raise ValueError("closed_loop.rules[].id must be non-empty.")
        return stripped

    @field_validator("devices")
    @classmethod
    def _normalize_devices(cls, value: List[Any]) -> List[str]:
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("assigned_animal_ids")
    @classmethod
    def _normalize_assigned_animals(cls, value: List[Any]) -> List[str]:
        return [str(item).strip().upper() for item in value if str(item).strip()]

    @model_validator(mode="after")
    def _validate(self) -> "ClosedLoopRuleConfig":
        if not self.devices:
            raise ValueError("closed_loop.rules[].devices must be non-empty.")
        if not self.outputs.laser_channels:
            raise ValueError("closed_loop.rules[].outputs.laser_channels must be non-empty.")
        return self


class ClosedLoopConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: List[ClosedLoopRuleConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> "ClosedLoopConfig":
        seen: set[str] = set()
        for rule in self.rules:
            if rule.id in seen:
                raise ValueError(f"closed_loop.rules contains duplicate id '{rule.id}'.")
            seen.add(rule.id)
        return self


class DiscoveryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_ports: List[int] = Field(default_factory=list)
    probe_min_port: int = 1
    probe_max_port: int = 8
    probe_wait_ms: int = 20
    usb_vid: str = ""
    usb_pid: str = ""

    @field_validator("candidate_ports")
    @classmethod
    def _candidate_ports(cls, value: List[Any]) -> List[int]:
        ports = [int(item) for item in value if int(item) > 0]
        return ports

    @model_validator(mode="after")
    def _validate_range(self) -> "DiscoveryConfig":
        if self.probe_min_port <= 0:
            raise ValueError("stimulus.discovery.probe_min_port must be greater than 0.")
        if self.probe_max_port < self.probe_min_port:
            raise ValueError("stimulus.discovery.probe_max_port must be >= stimulus.discovery.probe_min_port.")
        if self.probe_wait_ms <= 0:
            raise ValueError("stimulus.discovery.probe_wait_ms must be greater than 0.")
        self.usb_vid = self.usb_vid.strip().upper()
        self.usb_pid = self.usb_pid.strip().upper()
        return self


class PulseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_ms: Optional[float] = None
    time_on_ms: Optional[float] = None

    @model_validator(mode="after")
    def _validate(self) -> "PulseConfig":
        if self.period_ms is None and self.time_on_ms is None:
            return self
        if self.period_ms is None or self.time_on_ms is None:
            raise ValueError("stimulus.pulse.period_ms and time_on_ms must be provided together.")
        if self.period_ms <= 0 or self.time_on_ms <= 0:
            raise ValueError("stimulus.pulse.period_ms and time_on_ms must be > 0.")
        if self.time_on_ms > self.period_ms:
            raise ValueError("stimulus.pulse.time_on_ms must be <= stimulus.pulse.period_ms.")
        return self


class TrainConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    on_seconds: Optional[float] = None
    off_seconds: Optional[float] = None

    @model_validator(mode="after")
    def _validate(self) -> "TrainConfig":
        if self.on_seconds is None and self.off_seconds is None:
            return self
        if self.on_seconds is None:
            raise ValueError("stimulus.train.on_seconds must be set when stimulus.train is provided.")
        if self.on_seconds <= 0:
            raise ValueError("stimulus.train.on_seconds must be > 0.")
        if self.off_seconds is not None and self.off_seconds < 0:
            raise ValueError("stimulus.train.off_seconds must be >= 0.")
        return self


class StartConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = "immediate"
    timezone: Optional[str] = None
    at_hhmm: Optional[str] = None
    rollback_next_day: bool = False
    delay_seconds: Optional[float] = None

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, value: str) -> str:
        lowered = value.strip().lower()
        if lowered not in ("immediate", "delay", "clock"):
            raise ValueError("must be 'immediate', 'delay', or 'clock'")
        return lowered

    @field_validator("timezone")
    @classmethod
    def _normalize_timezone(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = str(value).strip()
        return stripped or None

    @field_validator("at_hhmm")
    @classmethod
    def _normalize_at_hhmm(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = str(value).strip()
        return stripped or None

    @model_validator(mode="after")
    def _validate(self) -> "StartConfig":
        if self.delay_seconds is not None and self.delay_seconds < 0:
            raise ValueError("stimulus.start.delay_seconds must be >= 0.")
        if self.mode == "immediate":
            return self
        if self.mode == "delay":
            if self.delay_seconds is None:
                raise ValueError("stimulus.start.delay_seconds must be set when stimulus.start.mode='delay'.")
            return self
        if self.at_hhmm is None:
            raise ValueError("stimulus.start.at_hhmm must be set when stimulus.start.mode='clock'.")
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", self.at_hhmm):
            raise ValueError("stimulus.start.at_hhmm must match HH:MM in 24-hour time.")
        if self.timezone is None:
            raise ValueError("stimulus.start.timezone must be set when stimulus.start.mode='clock'.")
        return self


class SquareConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_ms: Optional[float] = None
    time_on_ms: Optional[float] = None
    nb_of_seq: int = 0
    nb_of_pulses_per_seq: int = 0
    starting_delay_ms: int = 0
    delay_between_seq_ms: int = 0
    ttl_output: bool = True

    @model_validator(mode="after")
    def _validate(self) -> "SquareConfig":
        if self.period_ms is not None or self.time_on_ms is not None:
            if self.period_ms is None or self.time_on_ms is None:
                raise ValueError("stimulus.square.period_ms and time_on_ms must be provided together.")
            if self.period_ms <= 0 or self.time_on_ms <= 0:
                raise ValueError("stimulus.square.period_ms and time_on_ms must be > 0.")
            if self.time_on_ms > self.period_ms:
                raise ValueError("stimulus.square.time_on_ms must be <= stimulus.square.period_ms.")
            if (self.time_on_ms / self.period_ms) > 0.9:
                raise ValueError("stimulus.square duty cycle must be <= 0.9 for safety.")
        return self


class ChannelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    current_ma: float

    @model_validator(mode="after")
    def _validate(self) -> "ChannelConfig":
        if self.current_ma < 0:
            raise ValueError("current_ma must be >= 0.")
        if self.current_ma > 200:
            raise ValueError("current_ma must be <= 200.")
        return self


class OpenLoopAssignmentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    device: str
    channel: str
    assigned_animal_ids: List[str] = Field(default_factory=list)

    @field_validator("id", "device", "channel")
    @classmethod
    def _normalize_text(cls, value: str) -> str:
        stripped = str(value).strip()
        if not stripped:
            raise ValueError("must be non-empty")
        return stripped

    @field_validator("assigned_animal_ids")
    @classmethod
    def _normalize_assigned_animals(cls, value: List[Any]) -> List[str]:
        return [str(item).strip().upper() for item in value if str(item).strip()]


class StimulusConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    mode: str = "monitor"
    control_mode: Optional[str] = None
    driver: str = "doric_light_source"
    dll_path: Optional[str] = str(DEFAULT_DORIC_DLL_DIR)
    port: Optional[int] = None
    uid: Optional[str] = None
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    channels: Dict[str, ChannelConfig] = Field(default_factory=dict)
    target_channels: List[str] = Field(default_factory=list)
    open_loop_assignments: List[OpenLoopAssignmentConfig] = Field(default_factory=list)
    run_for_minutes: Optional[float] = None
    start_delay_seconds: Optional[float] = None
    start: StartConfig = Field(default_factory=StartConfig)
    window_on_seconds: Optional[float] = None
    pulse: PulseConfig = Field(default_factory=PulseConfig)
    train: TrainConfig = Field(default_factory=TrainConfig)
    square: SquareConfig = Field(default_factory=SquareConfig)

    @field_validator("mode")
    @classmethod
    def _valid_mode(cls, value: str) -> str:
        lowered = value.strip().lower()
        if lowered not in ("monitor", "laser"):
            raise ValueError("must be 'monitor' or 'laser'")
        return lowered

    @field_validator("control_mode")
    @classmethod
    def _valid_control_mode(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        lowered = value.strip().lower()
        if lowered not in ("closed_loop", "open_loop"):
            raise ValueError("must be 'closed_loop' or 'open_loop'")
        return lowered

    @field_validator("channels", mode="before")
    @classmethod
    def _normalize_channels(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        normalized: Dict[str, Any] = {}
        for name, entry in value.items():
            channel_name = str(name)
            if isinstance(entry, (int, float)):
                entry = {"index": int(entry), "current_ma": 0}
            if not isinstance(entry, dict):
                raise ValueError(f"Channel '{channel_name}' must be a mapping with 'index' and 'current_ma'.")
            d = dict(entry)
            if "index" not in d:
                match = re.fullmatch(r"ch([1-8])", channel_name.strip().lower())
                if match:
                    d["index"] = int(match.group(1))
                else:
                    raise ValueError(
                        f"stimulus.channels.{channel_name}.index is required when the channel name is not in 'chN' form."
                    )
            idx = int(d["index"])
            if idx < 1 or idx > 8:
                raise ValueError(f"stimulus.channels.{channel_name}.index must be between 1 and 8.")
            d["index"] = idx - 1
            normalized[channel_name] = d
        return normalized

    @field_validator("target_channels")
    @classmethod
    def _normalize_target_channels(cls, value: List[Any]) -> List[str]:
        return [str(ch).strip() for ch in value if str(ch).strip()]

    @field_validator("uid")
    @classmethod
    def _normalize_uid(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = str(value).strip()
        return stripped or None

    @model_validator(mode="after")
    def _validate(self) -> "StimulusConfig":
        if self.run_for_minutes is not None and self.run_for_minutes <= 0:
            raise ValueError("stimulus.run_for_minutes must be greater than 0.")
        if self.start_delay_seconds is not None and self.start_delay_seconds < 0:
            raise ValueError("stimulus.start_delay_seconds must be >= 0.")
        if self.start_delay_seconds is not None and self.start.mode != "immediate":
            raise ValueError("Use either legacy stimulus.start_delay_seconds or stimulus.start, not both.")
        if self.window_on_seconds is not None and self.window_on_seconds <= 0:
            raise ValueError("stimulus.window_on_seconds must be greater than 0.")
        if self.mode == "laser" and self.enabled and not (self.dll_path or "").strip():
            raise ValueError("stimulus.dll_path must be set when stimulus.enabled=true and stimulus.mode='laser'.")
        for ch in self.target_channels:
            if ch not in self.channels:
                raise ValueError(f"stimulus.target_channels unknown channel '{ch}'.")
        seen_assignment_ids: set[str] = set()
        seen_assignment_channels: set[str] = set()
        for assignment in self.open_loop_assignments:
            if assignment.id in seen_assignment_ids:
                raise ValueError(f"stimulus.open_loop_assignments contains duplicate id '{assignment.id}'.")
            if assignment.channel in seen_assignment_channels:
                raise ValueError(
                    f"stimulus.open_loop_assignments contains duplicate channel '{assignment.channel}'."
                )
            if assignment.channel not in self.channels:
                raise ValueError(
                    f"stimulus.open_loop_assignments.{assignment.id}.channel unknown channel '{assignment.channel}'."
                )
            seen_assignment_ids.add(assignment.id)
            seen_assignment_channels.add(assignment.channel)
        return self

    def runtime_dict(self) -> Dict[str, Any]:
        data = self.model_dump(mode="python")
        if data.get("open_loop_assignments") and not data.get("target_channels"):
            data["target_channels"] = [
                str(item.get("channel")).strip()
                for item in data.get("open_loop_assignments", [])
                if str(item.get("channel") or "").strip()
            ]
        start_cfg = dict(data.get("start", {}) or {})
        legacy_delay = data.get("start_delay_seconds")
        if legacy_delay is not None:
            start_cfg = {
                "mode": "delay",
                "timezone": None,
                "at_hhmm": None,
                "rollback_next_day": False,
                "delay_seconds": float(legacy_delay),
            }
        data["start"] = start_cfg
        pulse_cfg = data.get("pulse", {})
        square_cfg = data.get("square", {})
        if pulse_cfg.get("period_ms") is not None and pulse_cfg.get("time_on_ms") is not None:
            square_cfg["period_ms"] = float(pulse_cfg["period_ms"])
            square_cfg["time_on_ms"] = float(pulse_cfg["time_on_ms"])
            pulse_duty = square_cfg["time_on_ms"] / square_cfg["period_ms"]
            train_cfg = data.get("train", {})
            train_on = train_cfg.get("on_seconds") or data.get("window_on_seconds")
            train_off = train_cfg.get("off_seconds") or 0.0
            train_cycle = (train_on or 0.0) + train_off
            train_duty = (train_on / train_cycle) if train_on and train_cycle > 0 else 1.0
            pulses_per_on_epoch = (train_on / (square_cfg["period_ms"] / 1000.0)) if train_on else 0.0
            data["derived"] = {
                "pulse_frequency_hz": round(1000.0 / square_cfg["period_ms"], 4),
                "interpulse_ms": round(square_cfg["period_ms"] - square_cfg["time_on_ms"], 4),
                "pulse_duty": round(pulse_duty, 6),
                "train_duty": round(train_duty, 6),
                "effective_duty": round(pulse_duty * train_duty, 6),
                "pulses_per_on_epoch": round(pulses_per_on_epoch, 4),
                "pulses_per_cycle": round(pulses_per_on_epoch, 4),
            }
            data["square"] = square_cfg
        return data


class TTLCaptureConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    port: Optional[str] = None
    baudrate: int = 115200
    serial_timeout_seconds: float = 0.25
    read_chunk_bytes: int = 4096

    @model_validator(mode="after")
    def _validate(self) -> "TTLCaptureConfig":
        if self.baudrate <= 0:
            raise ValueError("ttl_capture.baudrate must be > 0.")
        if self.serial_timeout_seconds <= 0:
            raise ValueError("ttl_capture.serial_timeout_seconds must be > 0.")
        if self.read_chunk_bytes <= 0:
            raise ValueError("ttl_capture.read_chunk_bytes must be > 0.")
        if self.port is not None:
            self.port = str(self.port).strip() or None
        return self


class ResolvedConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    devices: List[DeviceConfigModel] = Field(default_factory=list)
    output_directory: str = "./data"
    session_description: Optional[str] = None
    display: DisplayConfig = Field(default_factory=DisplayConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    triggers: TriggerConfig = Field(default_factory=TriggerConfig)
    closed_loop: ClosedLoopConfig = Field(default_factory=ClosedLoopConfig)
    stimulus: StimulusConfig = Field(default_factory=StimulusConfig)
    ttl_capture: TTLCaptureConfig = Field(default_factory=TTLCaptureConfig)

    @model_validator(mode="after")
    def _validate_cross_section(self) -> "ResolvedConfig":
        trigger_channels = self.triggers.target_channels
        for ch in trigger_channels:
            if ch not in self.stimulus.channels:
                raise ValueError(f"triggers.target_channels unknown channel '{ch}'.")
        device_tokens = {str(device.name).strip() for device in self.devices if device.name}
        device_tokens.update({str(device.host).strip() for device in self.devices if device.host})
        for rule in self.closed_loop.rules:
            for ch in rule.outputs.laser_channels:
                if ch not in self.stimulus.channels:
                    raise ValueError(f"closed_loop.rules.{rule.id}.outputs.laser_channels unknown channel '{ch}'.")
            unknown_devices = [token for token in rule.devices if token not in device_tokens]
            if unknown_devices:
                raise ValueError(
                    f"closed_loop.rules.{rule.id}.devices contains unknown configured device token(s): {', '.join(unknown_devices)}"
                )

        if self.stimulus.enabled and self.stimulus.control_mode is None and self.stimulus.mode == "laser":
            raise ValueError("stimulus.control_mode must be set when stimulus.enabled=true and stimulus.mode='laser'.")

        if self.stimulus.enabled and self.stimulus.control_mode == "open_loop":
            if not self.stimulus.target_channels and not self.stimulus.open_loop_assignments:
                raise ValueError(
                    "stimulus.target_channels or stimulus.open_loop_assignments must be non-empty in open_loop mode."
                )
            unknown_devices = [
                assignment.device
                for assignment in self.stimulus.open_loop_assignments
                if assignment.device not in device_tokens
            ]
            if unknown_devices:
                raise ValueError(
                    "stimulus.open_loop_assignments contains unknown configured device token(s): "
                    + ", ".join(unknown_devices)
                )
            if self.stimulus.run_for_minutes is None:
                raise ValueError("stimulus.run_for_minutes must be set in open_loop mode.")
            if self.stimulus.pulse.period_ms is None or self.stimulus.pulse.time_on_ms is None:
                raise ValueError("stimulus.pulse.period_ms and time_on_ms must be set in open_loop mode.")

        if self.stimulus.enabled and self.stimulus.control_mode == "closed_loop":
            if not self.closed_loop.rules:
                raise ValueError(
                    "closed_loop.rules must be non-empty when stimulus.enabled=true and stimulus.control_mode='closed_loop'."
                )
            if self.stimulus.pulse.period_ms is None or self.stimulus.pulse.time_on_ms is None:
                raise ValueError("stimulus.pulse.period_ms and time_on_ms must be set for enabled stimulation.")

        if self.stimulus.mode == "laser" and not self.ttl_capture.enabled:
            raise ValueError("ttl_capture.enabled must be true when stimulus.mode='laser'.")

        if self.triggers.clf_window_seconds and self.triggers.clf_window_seconds > self.data.averaging_window_seconds:
            print(
                "Warning: triggers.clf_window_seconds "
                f"({self.triggers.clf_window_seconds}s) is larger than data.averaging_window_seconds ({self.data.averaging_window_seconds}s). "
                f"Classifier will use only {self.data.averaging_window_seconds}s (effective window). "
                "Increase data.averaging_window_seconds or lower triggers.clf_window_seconds."
            )
        for rule in self.closed_loop.rules:
            if rule.classifier.clf_data_input_window_seconds > self.data.averaging_window_seconds:
                print(
                    "Warning: closed_loop.rules."
                    f"{rule.id}.classifier.clf_data_input_window_seconds "
                    f"({rule.classifier.clf_data_input_window_seconds}s) is larger than data.averaging_window_seconds "
                    f"({self.data.averaging_window_seconds}s). Classifier will use only {self.data.averaging_window_seconds}s (effective window)."
                )
        for ch_name, ch_cfg in self.stimulus.channels.items():
            if ch_cfg.current_ma == 0:
                print(f"Warning: stimulus.channels.{ch_name}.current_ma is 0; laser output will be off for this channel.")

        return self

    def runtime_dict(self) -> Dict[str, Any]:
        data = self.model_dump(mode="python")
        data["stimulus"] = self.stimulus.runtime_dict()
        return data


def _find_error_source(
    location: str,
    *,
    local_data: Dict[str, Any],
    local_lines: Dict[str, int],
    overlay_data: Dict[str, Any],
    overlay_lines: Dict[str, int],
    overlay_path: Optional[str],
    local_path: str,
) -> str:
    if overlay_data and any(path == location or path.startswith(f"{location}.") for path in _iter_overlay_paths(overlay_data)):
        line = overlay_lines.get(location)
        if line is not None:
            return f"{overlay_path}:{line}"
        return str(overlay_path)
    if local_data and any(path == location or path.startswith(f"{location}.") for path in _iter_overlay_paths(local_data)):
        line = local_lines.get(location)
        if line is not None:
            return f"{local_path}:{line}"
        return local_path
    return "merged runtime config"


def _raise_validation_error(
    exc: ValidationError,
    *,
    local_data: Optional[Dict[str, Any]] = None,
    local_lines: Optional[Dict[str, int]] = None,
    overlay_data: Optional[Dict[str, Any]] = None,
    overlay_lines: Optional[Dict[str, int]] = None,
    overlay_path: Optional[str] = None,
    local_path: str = "config.local.yaml",
) -> None:
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", []))
    message = first.get("msg", "invalid config")
    provided = first.get("input")
    if "stimulus.dll_path must be set" in message and overlay_path:
        overlay_line = overlay_lines.get("stimulus")
        overlay_ref = f"{overlay_path}:{overlay_line}" if overlay_line is not None else str(overlay_path)
        raise ValueError(
            "laser mode was enabled by "
            f"`{overlay_ref}`, but machine-local field `stimulus.dll_path` is missing in `{local_path}`. "
            "Copy `config.example.yaml` to `config.local.yaml` if needed, then set `stimulus.dll_path` there."
        )
    if local_data is not None and local_lines is not None and overlay_data is not None and overlay_lines is not None and location:
        source = _find_error_source(
            location,
            local_data=local_data,
            local_lines=local_lines,
            overlay_data=overlay_data,
            overlay_lines=overlay_lines,
            overlay_path=overlay_path,
            local_path=local_path,
        )
        if provided is not None:
            raise ValueError(f"`{location}` in `{source}`: {message}. Provided value: `{provided!r}`")
        raise ValueError(f"`{location}` in `{source}`: {message}")
    raise ValueError(f"`{location}`: {message}" if location else message)


def load_runtime_config(
    overlay_path: Optional[str] = None,
    *,
    local_path: str = "config.local.yaml",
    require_local: bool = False,
) -> ResolvedConfig:
    config_data = dict(SAFE_DEFAULTS)
    local_data, local_lines = _load_yaml_mapping(local_path, required=require_local)
    config_data = _deep_update(config_data, local_data)
    overlay_data: Dict[str, Any] = {}
    overlay_lines: Dict[str, int] = {}
    if overlay_path:
        overlay_data, overlay_lines = _load_yaml_mapping(overlay_path, required=True)
        _validate_overlay_paths(overlay_data)
        config_data = _deep_update(config_data, overlay_data)
    try:
        return ResolvedConfig.model_validate(config_data)
    except ValidationError as exc:
        _raise_validation_error(
            exc,
            local_data=local_data,
            local_lines=local_lines,
            overlay_data=overlay_data,
            overlay_lines=overlay_lines,
            overlay_path=overlay_path,
            local_path=local_path,
        )
        raise


def load_config(
    overlay_path: Optional[str] = None,
    *,
    local_path: str = "config.local.yaml",
    require_local: bool = False,
) -> Dict[str, Any]:
    return load_runtime_config(
        overlay_path,
        local_path=local_path,
        require_local=require_local,
    ).runtime_dict()


def validate_config(config: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return ResolvedConfig.model_validate(config).runtime_dict()
    except ValidationError as exc:
        _raise_validation_error(exc)
        raise
