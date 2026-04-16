#include <iostream>

#include "doric_system_wrapper.h"

int main()
{
	/* Create an OTPG settings*/
	Doric::OTPG::Settings* otpg_settings = new Doric::OTPG::Settings();

	/* Configure a square mode on channel #1 of OTPG */
	otpg_settings->channelIdx = Doric::System::Channel::Channel_1;
	otpg_settings->mode = Doric::OTPG::Mode::Square;

	/* Configure a manual triggering (start with OTPG start) */
	otpg_settings->triggerMode = Doric::System::TriggerMode::Uninterrupted;
	otpg_settings->triggerType = Doric::System::TriggerType::Manual;
	otpg_settings->triggerSource = Doric::System::Channel::Undefined;

	/* Sequence(s) will be repeated once done & trigger received */
	otpg_settings->isRepeatableSequence = false;
	otpg_settings->isInverted = false;

	/* Initialize communication with Doric devices */
	Doric::System::init(true);

	/* Wait for initialization to complete*/
	Doric::System::wait(5000);

	/* List available(s) device(s) */
	Doric::System::available_devices_with_ports();

	/* OTPG USB port */
	int OTPG_port = 10;

	/* Open a device. In this case, a OTPG is connected on port 10 */
	Doric::System::open_device(OTPG_port);

	/* Wait for the device to be initialize */
	Doric::System::wait(7000);

	/* Send the settings created previously to device on port #10 */
	Doric::OTPG::otpg_send_settings(OTPG_port, otpg_settings);

	/* Wait for the settings to be initialize */
	Doric::System::wait(1000);

	/* Start the OTPG (connected on port#10) */
	Doric::OTPG::otpg_start_all(OTPG_port);

	/* Let it run 10 sec */
	Doric::System::wait(10000);

	/* Stop the OTPG (connected on port#10) */
	Doric::OTPG::otpg_stop_all(OTPG_port);

	/* Close the OTPG */
	Doric::System::close_device(OTPG_port);

	/* Wait for device to close */
	Doric::System::wait(1000);

	Doric::System::quit();
}
