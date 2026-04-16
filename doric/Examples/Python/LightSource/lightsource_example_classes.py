from lightsource_defs import*


class Example_Light_CW:
    def __init__(self, dll, portNumber, channelIdx = Channel.Channel_1):
        self.settings = LightSourceSettings()
        self.settings.channelIdx = channelIdx
        self.settings.mode = LightSourceMode.CW
        self.settings.ttlModulation.current = 100
        dll.ls_send_settings(portNumber, pointer(self.settings))


class Example_Light_Square:
    def __init__(self, dll, portNumber, channelIdx = Channel.Channel_1):
        self.settings = LightSourceSettings()
        self.settings.channelIdx = channelIdx
        self.settings.mode = LightSourceMode.Square
        self.settings.isTTLOutput = True
        self.settings.ttlModulation.current = 50
        self.settings.ttlModulation.periodMs = 1000
        self.settings.ttlModulation.timeOnMs = 500
        self.settings.ttlModulation.nbOfSeq = 0
        self.settings.ttlModulation.nbOfPulsesPerSeq = 0
        self.settings.ttlModulation.startingDelayMs = 0
        self.settings.ttlModulation.delayBetweenSeqMs = 0
        dll.ls_send_settings(portNumber, pointer(self.settings))


class Example_Light_ExtTTL:
    def __init__(self, dll, portNumber, channelIdx = Channel.Channel_1):
        self.settings = LightSourceSettings()
        self.settings.channelIdx = channelIdx
        self.settings.mode = LightSourceMode.ExtTTL
        self.settings.ttlModulation.current = 100
        dll.ls_send_settings(portNumber, pointer(self.settings))


class Example_Light_ExtAnalog:
    def __init__(self, dll, portNumber, channelIdx = Channel.Channel_1):
        self.settings = LightSourceSettings()
        self.settings.channelIdx = channelIdx
        self.settings.mode = LightSourceMode.ExtAnalog
        self.settings.ttlModulation.current = 1000
        dll.ls_send_settings(portNumber, pointer(self.settings))


class Example_Light_Triggered:
    def __init__(self, dll, portNumber, channelIdx = Channel.Channel_1):
        self.settings = LightSourceSettings()
        self.settings.channelIdx = channelIdx
        self.settings.mode = LightSourceMode.Square
        self.settings.isTTLOutput = True
        self.settings.ttlModulation.current = 50
        self.settings.ttlModulation.periodMs = 100
        self.settings.ttlModulation.timeOnMs = 50
        self.settings.ttlModulation.nbOfSeq = 5
        self.settings.ttlModulation.nbOfPulsesPerSeq = 5
        self.settings.ttlModulation.delayBetweenSeqMs = 2000
        self.settings.triggerType = TriggerType.Triggered
        self.settings.triggerMode = TriggerMode.Pause
        dll.ls_send_settings(portNumber, pointer(self.settings))


class Example_Light_Gated:
    def __init__(self, dll, portNumber, channelIdx = Channel.Channel_1):
        self.settings = LightSourceSettings()
        self.settings.channelIdx = channelIdx
        self.settings.mode = LightSourceMode.Square
        self.settings.ttlModulation.current = 250
        self.settings.ttlModulation.periodMs = 100
        self.settings.ttlModulation.timeOnMs = 50
        self.settings.ttlModulation.nbOfSeq = 0
        self.settings.ttlModulation.nbOfPulsesPerSeq = 0
        self.settings.ttlModulation.delayBetweenSeqMs = 0
        self.settings.triggerType = TriggerType.Gated
        self.settings.triggerMode = TriggerMode.Restart
        dll.ls_send_settings(portNumber, pointer(self.settings))


class Example_Light_Complex:
    def __init__(self, dll, portNumber, channelIdx = Channel.Channel_1):
        self.settings = LightSourceSettings()
        self.settings.channelIdx = channelIdx
        self.settings.mode = LightSourceMode.Complex

        self.settings.clearAllComplexModulations()

        # Square 
        complexModulationSquare = LightSourceComplexModulation()
        complexModulationSquare.mode = LightSourceComplexMode.Square
        complexModulationSquare.current = 50
        complexModulationSquare.nbOfSeq = 5
        complexModulationSquare.nbOfPulsesPerSeq = 1
        complexModulationSquare.periodMs = 1000
        complexModulationSquare.timeOnMs = 500
        self.settings.addComplexModulation(complexModulationSquare)

        # Delay 
        complexModulationDelay = LightSourceComplexModulation()
        complexModulationDelay.mode = LightSourceComplexMode.Delay
        complexModulationDelay.nbOfSeq = 1
        complexModulationDelay.nbOfPulsesPerSeq = 1
        complexModulationDelay.periodMs = 2000
        complexModulationDelay.startingDelayMs = 500 # <- Start when 1st is done
        self.settings.addComplexModulation(complexModulationDelay)

        # Triangle 
        complexModulationTriangle = LightSourceComplexModulation()
        complexModulationTriangle.mode = LightSourceComplexMode.Triangle
        complexModulationTriangle.current = 100
       
        complexModulationTriangle.nbOfSeq = 3
        complexModulationTriangle.nbOfPulsesPerSeq = 2
        complexModulationTriangle.delayBetweenSeqMs = 1000
        complexModulationTriangle.periodMs = 1500
        complexModulationTriangle.timeOnMs = 1500
        complexModulationTriangle.startingDelayMs = 7000 # <- Start when 2nd is done
        self.settings.addComplexModulation(complexModulationTriangle)

        dll.DoricLightSourceDriver_send_settings(portNumber, pointer(self.settings))


class Example_Light_Custom:
    def __init__(self, dll, portNumber, channelIdx = Channel.Channel_1):
        self.settings = LightSourceSettings()
        self.settings.channelIdx = channelIdx
        self.settings.mode = LightSourceMode.Custom
        self.settings.ttlModulation.periodMs = 2500
        self.settings.ttlModulation.startingDelayMs = 2000
        self.settings.ttlModulation.delayBetweenSeqMs = 500
        self.settings.ttlModulation.nbOfSeq = 6

        # Current values (mA) here
        for pointId in range(1000):
            self.settings.customDataPoint[pointId] = pointId

        dll.DoricLightSourceDriver_send_settings(portNumber, pointer(self.settings))