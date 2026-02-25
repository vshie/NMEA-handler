# Airmar 300WX WeatherStation - BlueOS Extension

A BlueOS extension for the Airmar 300WX WeatherStation. Connects via NMEA 0183 serial, auto-negotiates 38400 baud for full bandwidth, and streams wind data to Cockpit via WebSocket.

## Features

- Automatic serial port detection and baud rate negotiation (4800 → 38400)
- Real-time dashboard with wind, heading, atmosphere, GPS, and attitude data
- Sparkline history graphs for all sensor channels
- Per-sentence enable/disable and transmission interval control
- Bandwidth usage indicator (percentage of serial bus capacity)
- Raw message view with one card per message type and live Hz rate
- UDP streaming of NMEA sentences (autopilot integration)
- Cockpit data-lake WebSocket streaming of wind data
- Persistent NMEA message and application logs with download/delete

## Installation

### From BlueOS Extensions Manager

1. Open BlueOS web interface
2. Navigate to Extensions Manager
3. Search for "Airmar 300WX"
4. Click Install

### Manual Install

1. In BlueOS, go to Extensions Manager > Installed > "+"
2. Enter:
   - **Extension Identifier**: `bluerobotics.airmar-300wx`
   - **Extension Name**: `Airmar 300WX`
   - **Docker image**: `vshie/nmea-handler`
   - **Docker tag**: `latest`

## Custom Settings (Permissions)

When manually installing, paste this into the **Custom settings** field:

```json
{
  "ExposedPorts": {
    "6436/tcp": {},
    "8765/tcp": {}
  },
  "HostConfig": {
    "CpuPeriod": 100000,
    "CpuQuota": 100000,
    "Binds": [
      "/usr/blueos/extensions/nmea-handler:/app/logs",
      "/dev:/dev"
    ],
    "ExtraHosts": ["host.docker.internal:host-gateway"],
    "NetworkMode": "host",
    "Privileged": true
  }
}
```

## Cockpit WebSocket Streaming

The extension streams live wind data to Cockpit's data-lake via WebSocket. This allows wind speed and direction to be displayed in Cockpit widgets, mini-widgets, and HUD overlays.

### Variables Streamed

| Variable | Source | Description |
|---|---|---|
| `wind-direction-true` | `$WIMWD` | True wind direction relative to north (degrees) |
| `wind-speed-kts` | `$WIMWD` | True wind speed (knots) |
| `wind-apparent-speed-kts` | `$WIMWV` (R) | Apparent wind speed (knots) |
| `wind-apparent-angle` | `$WIMWV` (R) | Apparent wind angle relative to vessel (degrees) |

### Setting Up Cockpit Connection

1. Open **Cockpit > Menu > Settings > General**
2. Scroll to **Generic WebSocket connections**
3. Add the URL: `ws://{{ vehicle-address }}:8765`
   (e.g., `ws://192.168.2.2:8765`)

Once connected, the wind variables will appear in Cockpit's data-lake and can be assigned to any widget or HUD element.

## Log Files

Log files are stored in the BlueOS extensions directory:

- Location: `/usr/blueos/extensions/nmea-handler/`
- Files:
  - `nmea_messages.log` — Raw NMEA message history
  - `300wx.log` — Application operational log

These logs persist across container restarts and can be managed from the Logs tab in the extension UI.

## Usage

1. Open the Airmar 300WX extension from the BlueOS sidebar
2. The extension auto-connects to the last used serial port on startup
3. Select a serial port from the dropdown or device identification list
4. Click "Connect" — the extension negotiates 38400 baud automatically
5. View live sensor data on the Dashboard tab
6. Configure sentence enable/disable on the Sentences tab
7. View per-message-type data and Hz rates on the Raw Messages tab

## Development

### Building from Source

```bash
git clone https://github.com/vshie/NMEA-handler.git
cd NMEA-handler
docker build -t vshie/nmea-handler:latest .
```

### Local Testing

```bash
docker-compose up --build
```

Then visit `http://localhost:6436` in your browser.

### GitHub Actions

The CI/CD pipeline requires these GitHub Secrets and Variables:

**Secrets:**
- `DOCKER_USERNAME` — Docker Hub username
- `DOCKER_PASSWORD` — Docker Hub access token (Read & Write)

**Variables:**
- `IMAGE_NAME` — Docker repository name (default: `nmea-handler`)
- `MY_NAME` — Author name
- `MY_EMAIL` — Author email
- `ORG_NAME` — Maintainer organization name
- `ORG_EMAIL` — Maintainer organization email

## License

MIT License - see LICENSE file for details
