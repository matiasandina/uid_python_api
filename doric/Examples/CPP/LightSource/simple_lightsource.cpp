#include <iostream>

/* Include for DoricSystem library */
#include "doric_system_wrapper.h"

int main()
{
	/* Basic infinite square modulation */
	Doric::LightSource::TTLModulation ttlModulation;
	ttlModulation.current = 55;
	ttlModulation.timeOnMs = 500;
	ttlModulation.periodMs = 1000;

	Doric::LightSource::Settings* lightsource_settings = new Doric::LightSource::Settings();
    lightsource_settings->channelIdx = Doric::System::Channel::Channel_1;
    lightsource_settings->mode = Doric::LightSource::Mode::Square;
    lightsource_settings->currentMode = Doric::LightSource::CurrentMode::Normal;

	lightsource_settings->ttlModulation = ttlModulation;

	/* Initialize communication with Doric devices */
	Doric::System::init(true);

	/* Wait for initialization to complete*/
	Doric::System::wait(10000);

	/* List available(s) device(s) */
	Doric::System::available_devices_with_ports();
	
	/* Laser USB port */
	int LASER_port = 5;

	/* Open a device. In this case, a laser is connected on port 5 */
	Doric::System::open_device(LASER_port);

	/* Wait for the device to be initialize */
	Doric::System::wait(5000);

	/* Send the settings created previously to device on port #5 */
	Doric::LightSource::ls_send_settings(LASER_port, lightsource_settings);

	/* Start the channel#1 of the laser (connected on port#5) */
	Doric::LightSource::ls_start_channel(LASER_port, Doric::System::Channel::Channel_1);

	/* Let it run 10 sec */
	Doric::System::wait(10000);

	/* Stop the channel#1 of the laser (connected on port#5) */
	Doric::LightSource::ls_stop_channel(LASER_port, Doric::System::Channel::Channel_1);

	/* Close the laser */
	Doric::System::close_device(LASER_port);

	/* Wait for device to close */
	Doric::System::wait(1000);

	Doric::System::quit();
}
