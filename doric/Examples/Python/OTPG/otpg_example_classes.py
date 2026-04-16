from otpg_defs import *


class Example_OTPG_CW:
    def __init__(self, dll, portNumber, channelIdx = Channel.Channel_1):
        self.settings = OTPGSettings()
        self.settings.channelIdx = channelIdx
        self.settings.mode = OTPGMode.CW
        self.settings.timeOnMs = 30000
        dll.otpg_send_settings(portNumber, pointer(self.settings))


class Example_OTPG_Square:
    def __init__(self, dll, portNumber, channelIdx = Channel.Channel_1):
        self.settings = OTPGSettings()
        self.settings.channelIdx = channelIdx
        self.settings.mode = OTPGMode.Square
        self.settings.triggerSource = Channel.Undefined
        self.settings.triggerType = TriggerType.Manual
        self.settings.triggerMode = TriggerMode.Uninterrupted
        self.settings.startingDelayMs = 0
        self.settings.delayBetweenSeqMs = 0
        self.settings.periodMs = 100
        self.settings.timeOnMs = 50
        self.settings.nbOfSeq = 0
        self.settings.nbOfPulsesPerSeq = 0
        self.settings.isRepeatableSequence = False
        self.settings.isInverted = False
        dll.otpg_send_settings(portNumber, pointer(self.settings))


class Example_OTPG_Triggered:
    def __init__(self, dll, portNumber, channelIdx = Channel.Channel_1, triggerChannel = Channel.Channel_2):
        # Input Channel
        self.settingsInput = OTPGSettings()
        self.settingsInput.channelIdx = triggerChannel
        self.settingsInput.mode = OTPGMode.Input
        dll.otpg_send_settings(portNumber, pointer(self.settingsInput))

        # Output Channel
        self.settingsOutput = OTPGSettings()
        self.settingsOutput.channelIdx = channelIdx
        self.settingsOutput.mode = OTPGMode.Square
        self.settingsOutput.periodMs = 100
        self.settingsOutput.timeOnMs = 50
        self.settingsOutput.nbOfSeq = 5
        self.settingsOutput.nbOfPulsesPerSeq = 5
        self.settingsOutput.delayBetweenSeqMs = 2000
        self.settingsOutput.triggerSource = triggerChannel
        self.settingsOutput.triggerType = TriggerType.Triggered
        self.settingsOutput.triggerMode = TriggerMode.Restart
        self.settingsOutput.isRepeatableSequence = True
        self.settingsOutput.isInverted = False
        dll.otpg_send_settings(portNumber, pointer(self.settingsOutput))


class Example_OTPG_Gated:
    def __init__(self, dll, portNumber, channelIdx = Channel.Channel_1, triggerChannel = Channel.Channel_2):
        # Input Channel
        self.settingsInput = OTPGSettings()
        self.settingsInput.channelIdx = triggerChannel
        self.settingsInput.mode = OTPGMode.Input
        dll.otpg_send_settings(portNumber, pointer(self.settingsInput))

        # Output Channel
        self.settingsOutput = OTPGSettings()
        self.settingsOutput.channelIdx = channelIdx
        self.settingsOutput.mode = OTPGMode.Square
        self.settingsOutput.periodMs = 100
        self.settingsOutput.timeOnMs = 50
        self.settingsOutput.nbOfSeq = 5
        self.settingsOutput.nbOfPulsesPerSeq = 5
        self.settingsOutput.delayBetweenSeqMs = 2000
        self.settingsOutput.triggerSource = triggerChannel
        self.settingsOutput.triggerType = TriggerType.Gated
        self.settingsOutput.triggerMode = TriggerMode.Restart
        self.settingsOutput.isRepeatableSequence = True
        dll.otpg_send_settings(portNumber, pointer(self.settingsOutput))


class Example_OTPGSamplingParameters_Default:
    def __init__(self, dll, portNumber):
        self.settings = OTPGSamplingParameters()
        self.settings.triggerSource = Channel.Undefined
        self.settings.samplingFrequency = OTPGSamplingFrequency.Freq_1kHz
        dll.otpg_send_sampling_parameters(portNumber, pointer(self.settings))


class Example_OTPGSamplingParameters_Triggered:
    def __init__(self, dll, portNumber):
        self.settings = OTPGSamplingParameters()
        self.settings.triggerSource = Channel.Channel_2
        self.settings.samplingFrequency = OTPGSamplingFrequency.Freq_500Hz
        dll.otpg_send_sampling_parameters(portNumber, pointer(self.settings))


class Example_OTPG_TimeSeries_Default:
    def __init__(self, dll, portNumber):
        self.settings = DoricTimeSeriesProperties()
        self.settings.activeTimeMs = 1000
        self.settings.numberOfSeries = 1
        self.settings.delayBetweenSeriesMs = 1000
        self.settings.isUsingTimeSeries = False
        dll.otpg_send_timeseries_properties(portNumber, pointer(self.settings))


class Example_OTPG_TimeSeriesActive:
    def __init__(self, dll, portNumber):
        self.settings = DoricTimeSeriesProperties()
        self.settings.activeTimeMs = 5000
        self.settings.numberOfSeries = 5
        self.settings.delayBetweenSeriesMs = 2000
        self.settings.isUsingTimeSeries = True
        dll.otpg_send_timeseries_properties(portNumber, pointer(self.settings))
