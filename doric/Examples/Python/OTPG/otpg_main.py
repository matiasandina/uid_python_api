from ctypes import*

from otpg_example_classes import*

import sys
import os

# OTPG USB port #
OTPG_USB_PORT_NUMBER = 10

mDLL = cdll.LoadLibrary('C:/PATH_TO_THE_DORIC_LIB/DoricSystem.dll')

def main():

    # Initialize communication with Doric devices #
    mDLL.init(True)

    # Wait for initialization to complete #
    mDLL.wait(3000)

    # List available(s) device(s) #
    mDLL.available_devices_with_ports()

    # Open a device. In this case, a OTPG is connected on port 10 #
    mDLL.open_device(OTPG_USB_PORT_NUMBER)

    # Wait for the device to be initialized #
    mDLL.wait(3000)

    ##################################################################
    ######## LIST OF MULTIPLE DIFFERENT EXAMPLE OF OTPG USAGE ########
    ##################################################################

    # # Example_OTPG_CW(mDLL, OTPG_USB_PORT_NUMBER, Channel.Channel_1)
    # # Example_OTPG_Square(mDLL, OTPG_USB_PORT_NUMBER, Channel.Channel_1)
    # # Example_OTPG_Triggered(mDLL, OTPG_USB_PORT_NUMBER, Channel.Channel_1, Channel.Channel_2)
    # # Example_OTPG_Gated(mDLL, OTPG_USB_PORT_NUMBER, Channel.Channel_1, Channel.Channel_2)
    #
    # # Example_OTPG_Square(mDLL, OTPG_USB_PORT_NUMBER, Channel.Channel_1)
    # # Example_OTPG_CW(mDLL, OTPG_USB_PORT_NUMBER, Channel.Channel_2)
    # #
    # # Example_OTPGSamplingParameters_Default(mDLL, OTPG_USB_PORT_NUMBER)
    # # Example_OTPGSamplingParameters_Triggered(mDLL, OTPG_USB_PORT_NUMBER)
    # #
    # # Example_OTPG_TimeSeries_Default(mDLL, OTPG_USB_PORT_NUMBER)
    # # Example_OTPG_TimeSeries_Active(mDLL, OTPG_USB_PORT_NUMBER)

    # Simply create a square signal (see otpg_example_classes.py for more information or examples) #
    Example_OTPG_Square(mDLL, OTPG_USB_PORT_NUMBER, Channel.Channel_1)

    # Wait for the settings to be sent to the device #
    mDLL.wait(1000)

    # Start the OTPG (connected on port#10) #
    mDLL.otpg_start_all(OTPG_USB_PORT_NUMBER)

    # Let it run 10 sec #
    mDLL.wait(10000)

    # Stop the OTPG (connected on port#10) #
    mDLL.otpg_stop_all(OTPG_USB_PORT_NUMBER)

    # Close the OTPG #
    mDLL.close_device(OTPG_USB_PORT_NUMBER)

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

        # Stop the OTPG (connected on port#10) #
        mDLL.otpg_stop_all(OTPG_USB_PORT_NUMBER)

        # Close the OTPG #
        mDLL.close_device(OTPG_USB_PORT_NUMBER)

        # Wait for device to close #
        mDLL.wait(1000)

        # Quit the library #
        mDLL.quit()

        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)
