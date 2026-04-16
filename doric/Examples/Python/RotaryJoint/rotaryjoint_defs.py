from doric_system_defs import*


class RotaryJointSamplingRate(IntEnum):
    kFreq_10Hz = 0
    kFreq_30Hz = 1
    kFreq_60Hz = 2
    kFreq_120Hz = 3
    kFreq_300Hz = 4
    kFreq_600Hz = 5
    kFreq_1200Hz = 6


class RotaryJointMotorSpeedFactor(IntEnum):
    kSPEED_FACTOR_FULL = 1
    kSPEED_FACTOR_NORMAL = 2
    kSPEED_FACTOR_HALF = 4


class RotaryJointMotorDirection(IntEnum):
    kDirection_NoDirection = 0
    kDirection_Clockwise = 1
    kDirection_CounterClockwise = 2


class RotaryJointMotorMode(IntEnum):
    kMode_Manual = 0
    kMode_Continuous = 1
    kMode_TurnPerSide = 2
    kMode_Random = 3


# Rotary Joint Settings
class RotaryJointSettings(StructureWithEnums):
    _fields_ = [("samplingRate", c_int),
                ("motorSpeed", c_int)]
    _map = {
        "samplingRate": RotaryJointSamplingRate, "motorSpeed": RotaryJointMotorSpeedFactor
    }

    # Default Values
    def __init__(self):
        self.samplingRate = 6
        self.motorSpeed = 2
