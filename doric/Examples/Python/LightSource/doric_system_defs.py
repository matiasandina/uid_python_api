from ctypes import*
from enum import IntEnum

class StructureWithEnums(Structure):
    """Add missing enum feature to ctypes Structures.
    """
    _map = {}

    def __getattribute__(self, name):
        _map = Structure.__getattribute__(self, '_map')
        value = Structure.__getattribute__(self, name)
        if name in _map:
            EnumClass = _map[name]
            if isinstance(value, Array):
                return [EnumClass(x) for x in value]
            else:
                return EnumClass(value)
        else:
            return value

    def __str__(self):
        result = []
        result.append("struct {0} {{".format(self.__class__.__name__))
        for field in self._fields_:
            attr, attrType = field
            if attr in self._map:
                attrType = self._map[attr]
            value = getattr(self, attr)
            result.append("    {0} [{1}] = {2!r};".format(attr, attrType.__name__, value))
        result.append("};")
        return '\n'.join(result)

    __repr__ = __str__

class TriggerType(IntEnum):
    Triggered = 0
    Gated = 1
    Manual = 255

class TriggerMode(IntEnum):
    Uninterrupted = 0
    Pause = 1
    Continue = 2
    Restart = 3

# *** Zero based channel
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

# Doric TimeSeries
class DoricTimeSeriesProperties(Structure):
    _fields_ = [("activeTimeMs", c_uint32),
                ("numberOfSeries", c_uint16),
                ("delayBetweenSeriesMs", c_uint32),
                ("isUsingTimeSeries", c_bool)]

    # Default Values
    def __init__(self):
        self.activeTimeMs = 1000
        self.numberOfSeries = 1
        self.delayBetweenSeriesMs = 1000
        self.isUsingTimeSeries = False