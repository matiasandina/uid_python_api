from ctypes import Structure, Array, c_int, c_bool, c_uint16, c_uint32
from enum import IntEnum


class StructureWithEnums(Structure):
    _map = {}

    def __getattribute__(self, name):
        _map = Structure.__getattribute__(self, "_map")
        value = Structure.__getattribute__(self, name)
        if name in _map:
            enum_class = _map[name]
            if isinstance(value, Array):
                return [enum_class(x) for x in value]
            return enum_class(value)
        return value


class TriggerType(IntEnum):
    Triggered = 0
    Gated = 1
    Manual = 255


class TriggerMode(IntEnum):
    Uninterrupted = 0
    Pause = 1
    Continue = 2
    Restart = 3


class Channel(IntEnum):
    Channel_1 = 0
    Channel_2 = 1
    Channel_3 = 2
    Channel_4 = 3
    Channel_5 = 4
    Channel_6 = 5
    Channel_7 = 6
    Channel_8 = 7
    Undefined = 255


class DoricTimeSeriesProperties(Structure):
    _fields_ = [
        ("activeTimeMs", c_uint32),
        ("numberOfSeries", c_uint16),
        ("delayBetweenSeriesMs", c_uint32),
        ("isUsingTimeSeries", c_bool),
    ]
