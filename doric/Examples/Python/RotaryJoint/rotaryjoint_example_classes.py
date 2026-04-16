from rotaryjoint_defs import *


class Example_RotaryJoint_Default_Motor_ON_OFF:
    def __init__(self, dll, portNumber):
        # Power OFF the rotary joint
        dll.rotary_joint_send_motor_power_off(portNumber)

        # Wait for 3 seconds
        dll.wait(3000)

        # Power ON the rotary joint
        dll.rotary_joint_send_motor_power_on(portNumber)


class Example_RotaryJoint_Manual:
    def __init__(self, dll, portNumber):
        # Activate the manual control
        dll.rotary_joint_send_manual_control_activated(portNumber)

        # Send a duty cycle of 70%
        dll.rotary_joint_send_manual_control_duty_cycle(portNumber, c_double(70))

        # Send 'Continuous' mode
        dll.rotary_joint_send_manual_control_mode(portNumber, RotaryJointMotorMode.kMode_Manual)

        # Send 'clockwise' direction 
        dll.rotary_joint_send_manual_control_direction(portNumber, RotaryJointMotorDirection.kDirection_Clockwise)

        # Wait for 5 seconds
        dll.wait(5000)

        # Send 'no direction' direction 
        dll.rotary_joint_send_manual_control_direction(portNumber, RotaryJointMotorDirection.kDirection_NoDirection)

        # Wait for 5 seconds
        dll.wait(5000)

        # Send 'counterclockwise' direction 
        dll.rotary_joint_send_manual_control_direction(portNumber, RotaryJointMotorDirection.kDirection_CounterClockwise)
        
        # Wait for 5 seconds
        dll.wait(5000)

        # Deactivate the manual control
        dll.rotary_joint_send_manual_control_deactivated(portNumber)        


class Example_RotaryJoint_Manual_Continuous:
    def __init__(self, dll, portNumber):
        # Activate the manual control
        dll.rotary_joint_send_manual_control_activated(portNumber)

        # Send a duty cycle of 70%
        dll.rotary_joint_send_manual_control_duty_cycle(portNumber, c_double(70))

        # Send 'Continuous' mode
        dll.rotary_joint_send_manual_control_mode(portNumber, RotaryJointMotorMode.kMode_Continuous)
        
        # Send 'clockwise' direction 
        dll.rotary_joint_send_manual_control_direction(portNumber, RotaryJointMotorDirection.kDirection_Clockwise)

        # Wait for 10 seconds
        dll.wait(10000)

        # Send 'counterclockwise' direction 
        dll.rotary_joint_send_manual_control_direction(portNumber, RotaryJointMotorDirection.kDirection_CounterClockwise)
        
        # Wait for 10 seconds
        dll.wait(10000)

        # Deactivate the manual control
        dll.rotary_joint_send_manual_control_deactivated(portNumber)


class Example_RotaryJoint_Manual_Random:
    def __init__(self, dll, portNumber):
        # Activate the manual control
        dll.rotary_joint_send_manual_control_activated(portNumber)

        # Send a duty cycle of 70%
        dll.rotary_joint_send_manual_control_duty_cycle(portNumber, c_double(70))

        # Send 'Random' mode
        dll.rotary_joint_send_manual_control_mode(portNumber, RotaryJointMotorMode.kMode_Random)
        
        # Wait for 10 seconds
        dll.wait(10000)

        # Deactivate the manual control
        dll.rotary_joint_send_manual_control_deactivated(portNumber)        


class Example_RotaryJoint_Manual_TurnPerSide:
    def __init__(self, dll, portNumber):
        # Activate the manual control
        dll.rotary_joint_send_manual_control_activated(portNumber)

        # Send a duty cycle of 70%
        dll.rotary_joint_send_manual_control_duty_cycle(portNumber, c_double(70))

        # Send number of turns per side
        dll.rotary_joint_send_manual_control_turn_per_side(portNumber, c_uint8(2))

        # Send 'Turn Per Side' mode
        dll.rotary_joint_send_manual_control_mode(portNumber, RotaryJointMotorMode.kMode_TurnPerSide)
        
        # Wait for 15 seconds
        dll.wait(15000)

        # Deactivate the manual control
        dll.rotary_joint_send_manual_control_deactivated(portNumber)        