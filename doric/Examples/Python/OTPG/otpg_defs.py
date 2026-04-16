from doric_system_defs import*


class OTPGMode(IntEnum):
    Off = 0
    CW = 1
    Square = 2
    Input = 3


class OTPGSamplingFrequency(IntEnum):
    Freq_10Hz = 0
    Freq_100Hz = 1
    Freq_500Hz = 2
    Freq_1kHz = 3
    Freq_5kHz = 4
    Freq_10kHz = 5


# OTPG Settings
class OTPGSettings(StructureWithEnums):
    _fields_ = [("channelIdx", c_int),
                ("mode", c_int),
                ("triggerSource", c_int),
                ("triggerType", c_int),
                ("triggerMode", c_int),
                ("startingDelayMs", c_uint32),
                ("delayBetweenSeqMs", c_uint32),
                ("periodMs", c_double),
                ("timeOnMs", c_double),
                ("nbOfSeq", c_uint16),
                ("nbOfPulsesPerSeq", c_uint16),
                ("isRepeatableSequence", c_bool),
                ("isInverted", c_bool)]
    _map = {
        "channelIdx": Channel, "mode": OTPGMode, "triggerSource": Channel,
        "triggerType": TriggerType, "triggerMode": TriggerMode
    }

    # Default Values
    def __init__(self):
        self.channelIdx = 0
        self.mode = 0
        self.triggerSource = 255
        self.triggerType = 255
        self.triggerMode = 0
        self.startingDelayMs = 0
        self.delayBetweenSeqMs = 0
        self.periodMs = 100
        self.timeOnMs = 50
        self.nbOfSeq = 0
        self.nbOfPulsesPerSeq = 0
        self.isRepeatableSequence = False
        self.isInverted = False


# Sampling Parameters
class OTPGSamplingParameters(StructureWithEnums):
    _fields_ = [("triggerSource", c_uint8),
                ("samplingFrequency", c_uint8)]
    _map = {
        "triggerSource": Channel, "samplingFrequency": OTPGSamplingFrequency
    }

    # Default Values
    def __init__(self):
        self.triggerSource = Channel.Undefined
        self.samplingFrequency = OTPGSamplingFrequency.Freq_1kHz
