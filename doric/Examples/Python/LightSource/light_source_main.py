from ctypes import*

from lightsource_example_classes import*

import sys
import os

# LIGHT SOURCE USB port #
LIGHT_USB_PORT_NUMBER = 5

mDLL = cdll.LoadLibrary('C:/PATH_TO_THE_DORIC_LIB/DoricSystem.dll')

def main():

    # Initialize communication with Doric devices #
    mDLL.init(True)

    # Wait for initialization to complete #
    mDLL.wait(5000)

    # List available(s) device(s) #
    mDLL.available_devices_with_ports()

    # Open a device. In this case, a laser is connected on port 10 #
    mDLL.open_device(LIGHT_USB_PORT_NUMBER)

    # Wait for the device to be initialized #
    mDLL.wait(5000)

    ##################################################################
    ######## LIST OF MULTIPLE DIFFERENT EXAMPLE OF LIGHT USAGE #######
    ##################################################################

    # # Example_Light_CW(mDLL, LIGHT_USB_PORT_NUMBER, Channel.Channel_1)
    # # Example_Light_ExtTTL(mDLL, LIGHT_USB_PORT_NUMBER, Channel.Channel_1)
    # # Example_Light_ExtAnalog(mDLL, LIGHT_USB_PORT_NUMBER, Channel.Channel_1)
    # # Example_Light_Triggered(mDLL, LIGHT_USB_PORT_NUMBER, Channel.Channel_1)
    #
    # # Example_Light_Gated(mDLL, LIGHT_USB_PORT_NUMBER, Channel.Channel_1)
    # # Example_Light_Complex(mDLL, LIGHT_USB_PORT_NUMBER, Channel.Channel_1)
    # #
    # # Example_Light_Custom(mDLL, LIGHT_USB_PORT_NUMBER, Channel.Channel_1)

    # Simply create a square signal (see lightsource_example_classes.py for more information or examples) #
    Example_Light_Square(mDLL, LIGHT_USB_PORT_NUMBER, Channel.Channel_1)

    # Wait for the settings to be sent to the device #
    mDLL.wait(1000)

    # Start the laser (all channels) (connected on port#5) #
    mDLL.ls_start_all(LIGHT_USB_PORT_NUMBER)

    # Let it run 10 sec #
    mDLL.wait(10000)

    # Stop the laser (all channels) (connected on port#5) #
    mDLL.ls_stop_all(LIGHT_USB_PORT_NUMBER)

    # Close the laser #
    mDLL.close_device(LIGHT_USB_PORT_NUMBER)

    # Wait for device to close #
    mDLL.wait(1000)

    # Quit the library #
    mDLL.quit()

    try:
        sys.exit(0)
    except SystemExit:
        os._exit(0)

    while (True): continue


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Interrupted (keyboard)')

        # Stop the laser (all channels) (connected on port#5) #
        mDLL.ls_stop_all(LIGHT_USB_PORT_NUMBER)

        # Close the laser #
        mDLL.close_device(LIGHT_USB_PORT_NUMBER)

        # Wait for device to close #
        mDLL.wait(1000)

        # Quit the library #
        mDLL.quit()

        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)
