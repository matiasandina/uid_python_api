from doric_system_defs import*

LIGHTSOURCE_MAX_COMPLEX_SEQ = 32


class LightSourceMode(IntEnum):
    Off = 0
    CW = 1
    ExtTTL = 2
    ExtAnalog = 3
    Square = 4
    Complex = 5
    Custom = 6
    MicroscopeFollower = 10


class LightSourceComplexMode(IntEnum):
    Off = 0
    CW = 1
    Square = 2
    Input = 3
    Triangle = 4
    RampUp = 5
    RampDown = 6
    Sine = 7
    Stairs = 8
    Custom = 9
    Delay = 10
    LockIn = 11


class LightSourceCurrentMode(IntEnum):
    Normal = 0
    LowPower = 1
    Overdrive = 2


# TTL Modulation
class LightSourceTTLModulation(Structure):
    _fields_ = [("current", c_uint16),
                ("startingDelayMs", c_uint32),
                ("delayBetweenSeqMs", c_uint32),
                ("periodMs", c_double),
                ("timeOnMs", c_double),
                ("risingTimeMs", c_uint16),
                ("fallingTimeMs", c_uint16),
                ("nbOfSeq", c_uint16),
                ("nbOfPulsesPerSeq", c_uint16)]

    # Default Values
    def __init__(self):
        self.current = 0
        self.startingDelayMs = 0
        self.delayBetweenSeqMs = 0
        self.periodMs = 100
        self.timeOnMs = 50
        self.risingTimeMs = 0
        self.fallingTimeMs = 0
        self.nbOfSeq = 1
        self.nbOfPulsesPerSeq = 0


# Complex Modulation
class LightSourceComplexModulation(StructureWithEnums):
    _fields_ = [("mode", c_int),
                ("current", c_uint16),
                ("delayBetweenSeqMs", c_uint32),
                ("periodMs", c_double),
                ("timeOnMs", c_double),
                ("nbOfSeq", c_uint16),
                ("nbOfPulsesPerSeq", c_uint16),
                ("startingDelayMs", c_uint32)]
    _map = {
        "mode": LightSourceComplexMode
    }

    # Default Values
    def __init__(self):
        self.mode = LightSourceComplexMode.CW
        self.current = 0
        self.delayBetweenSeqMs = 0
        self.periodMs = 10
        self.timeOnMs = 50
        self.nbOfSeq = 1
        self.nbOfPulsesPerSeq = 0
        self.startingDelayMs = 0

# LightSource Settings #
class LightSourceSettings(StructureWithEnums):
    _fields_ = [("channelIdx", c_int),
                ("mode", c_int),
                ("isTTLOutput", c_bool),
                ("triggerType", c_int),
                ("triggerMode", c_int),
                ("isTriggerRepeatable", c_bool),
                ("currentMode", c_int),
                ("customDataPoint", c_uint16 * 1000),
                ("ttlModulation", LightSourceTTLModulation),
                ("nbComplexModulations", c_uint8),
                ("complexModulations", POINTER(LightSourceComplexModulation))]
    _map = {
        "channelIdx": Channel, "mode": LightSourceMode,"triggerType": TriggerType,
        "triggerMode": TriggerMode, "currentMode": LightSourceCurrentMode
    }

    # Default Values
    def __init__(self):
        self.channelIdx = Channel.Channel_1
        self.mode = LightSourceMode.Off
        self.isTTLOutput = False
        self.triggerType = 255
        self.triggerMode = 0
        self.isTriggerRepeatable = False
        self.currentMode = 0
        self.ttlModulation = LightSourceTTLModulation()

        self.customDataPoint = (c_uint16 * 1000)()
        for pointId in range(1000):
            self.customDataPoint[pointId] = 0

        self.nbComplexModulations = 0
        elems = (LIGHTSOURCE_MAX_COMPLEX_SEQ * LightSourceComplexModulation)()
        self.complexModulations = cast(elems, POINTER(LightSourceComplexModulation))

    def addComplexModulation(self, complexModulation):
        self.complexModulations[self.nbComplexModulations] = complexModulation
        self.nbComplexModulations += 1

    def clearAllComplexModulations(self):
        self.nbComplexModulations = 0