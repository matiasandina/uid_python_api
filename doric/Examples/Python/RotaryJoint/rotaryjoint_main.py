from ctypes import*

from rotaryjoint_example_classes import *

import sys
import os

# Rotary Joint USB port #
ROTARY_JOINT_USB_PORT_NUMBER = 10

mDLL = cdll.LoadLibrary('C:/PATH_TO_THE_DORIC_LIB/DoricSystem.dll')

def main():

    # Initialize communication with Doric devices #
    mDLL.init(True)

    # Wait for initialization to complete #
    mDLL.wait(3000)

    # List available(s) device(s) #
    mDLL.available_devices_with_ports()

    # Open a device. In this case, a Rotary Joint is connected on port 10 #
    mDLL.open_device(ROTARY_JOINT_USB_PORT_NUMBER)

    # Wait for the device to be initialized #
    mDLL.wait(3000)

    ##################################################################
    #### LIST OF MULTIPLE DIFFERENT EXAMPLE OF ROTARY JOINT USAGE ####
    ##################################################################

    # Example_RotaryJoint_Default_Motor_ON_OFF(mDLL, ROTARY_JOINT_USB_PORT_NUMBER)
    # Example_RotaryJoint_Manual(mDLL, ROTARY_JOINT_USB_PORT_NUMBER)
    # Example_RotaryJoint_Manual_Continuous(mDLL, ROTARY_JOINT_USB_PORT_NUMBER)
    # Example_RotaryJoint_Manual_Random(mDLL, ROTARY_JOINT_USB_PORT_NUMBER)
    Example_RotaryJoint_Manual_TurnPerSide(mDLL, ROTARY_JOINT_USB_PORT_NUMBER)

    # Close the Rotary Joint #
    mDLL.close_device(ROTARY_JOINT_USB_PORT_NUMBER)

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

        # Close the Rotary Joint #
        mDLL.close_device(ROTARY_JOINT_USB_PORT_NUMBER)

        # Wait for device to close #
        mDLL.wait(1000)

        # Quit the library #
        mDLL.quit()

        try:
            sys.exit(0)
        except SystemExit:
            os._exit(0)
