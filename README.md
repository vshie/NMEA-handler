# NMEA Handler

A BlueOS extension for handling NMEA messages from serial devices. This extension provides a web interface for configuring serial connections and monitoring/logging NMEA messages.

## Features

- Serial port configuration (port selection and baud rate)
- Real-time display of raw serial traffic
- NMEA message parsing and display
- Selective message logging with checkbox controls
- Persistent logging to file

## Installation

This extension can be installed through the BlueOS Extensions Manager:

1. Open BlueOS web interface
2. Navigate to Extensions Manager
3. Search for "NMEA Handler"
4. Click Install

## Usage

1. Open the NMEA Handler extension from the BlueOS sidebar
2. Select the appropriate serial port and baud rate from the dropdown menus
3. Click "Connect" to establish the serial connection
4. View incoming NMEA messages in the text window
5. Use checkboxes to enable/disable logging for specific message types
6. Logs are stored in `/app/logs/nmea_messages.log`

## Development

### Building from Source

```bash
# Clone the repository
git clone https://github.com/vshie/NMEA-handler.git
cd NMEA-handler

# Build the Docker image
docker build -t vshie/nmea-handler:latest .
```

### Local Testing

1. Build the Docker image
2. Install the extension manually through BlueOS Extensions Manager
3. Configure the extension with your serial device settings

## License

MIT License - see LICENSE file for details

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
