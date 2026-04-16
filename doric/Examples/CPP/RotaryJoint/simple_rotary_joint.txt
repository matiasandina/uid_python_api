#include <iostream>

#include "doric_system_wrapper.h"

int main()
{
	/* Create a Rotary Joint settings*/
	Doric::RotaryJoint::Settings* rj_settings = new Doric::RotaryJoint::Settings();

	/* Configure a Rotary Joint for normal speed */
	rj_settings->samplingRate = Doric::RotaryJoint::RotaryJoint::kFreq_120Hz;
	rj_settings->motorSpeed = Doric::RotaryJoint::MotorSpeedFactor::kSPEED_FACTOR_NORMAL;

	/* Initialize communication with Doric devices */
	Doric::System::init(true);

	/* Wait for initialization to complete*/
	Doric::System::wait(5000);

	/* List available(s) device(s) */
	Doric::System::available_devices_with_ports();

	/* Rotary Joint USB port */
	int RJ_port = 10;

	/* Open a device. In this case, a Rotary Joint is connected on port 10 */
	Doric::System::open_device(RJ_port);

	/* Wait for the device to be initialize */
	Doric::System::wait(7000);

	/* Send the settings created previously to device on port #10 */
	Doric::RotaryJoint::rotary_joint_send_settings(RJ_port, rj_settings);

	/* Wait for the settings to be initialize */
	Doric::System::wait(1000);

	/* Start the Rotary Joint (connected on port#10) */
	Doric::RotaryJoint::rotary_joint_send_motor_power_on(RJ_port);
	
	/* Set maximum duty cycle to the Rotary Joint */
	Doric::RotaryJoint::rotary_joint_send_manual_control_duty_cycle(RJ_port, 100);
	
	/* Activate manual control */
	Doric::RotaryJoint::rotary_joint_send_manual_control_activated(RJ_port);
	
	/* Set the manual control (to fully manual) */
	Doric::RotaryJoint::rotary_joint_send_manual_control_mode(RJ_port, Doric::RotaryJoint::MotorMode::kMode_Manual);
	
	/* Make it turn clockwise */
	Doric::RotaryJoint::rotary_joint_send_manual_control_direction(RJ_port, Doric::RotaryJoint::MotorDirection::kDirection_Clockwise);

	/* Let it run 10 sec */
	Doric::System::wait(10000);
	
	/* Make it turn counter-clockwise */
	Doric::RotaryJoint::rotary_joint_send_manual_control_direction(RJ_port, Doric::RotaryJoint::MotorDirection::kDirection_CounterClockwise);

	/* Let it run 10 sec */
	Doric::System::wait(10000);

	/* Stop the Rotary Joint (connected on port#10) */
	Doric::RotaryJoint::rotary_joint_send_motor_power_on(RJ_port);

	/* Close the Rotary Joint */
	Doric::System::close_device(RJ_port);

	/* Wait for device to close */
	Doric::System::wait(1000);

	Doric::System::quit();
}