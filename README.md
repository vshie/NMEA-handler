# Airmar 300WX WeatherStation - BlueOS Extension

A BlueOS extension for the Airmar 300WX WeatherStation. Connects via NMEA 0183 serial, auto-negotiates **115200** baud (with `$PAMTC,BAUD,...,CFG` so the default persists across power cycles), and streams wind data to Cockpit via WebSocket.

## Features

- Automatic serial port detection and baud rate negotiation (4800 → 115200, default saved on device)
- **Auto-reconnect on extension restart**: last serial port, baud hint, and “stay at 4800” preference are stored in `state.json` under the mounted logs directory; a background thread connects at startup without using the UI. **Disconnect** in the UI clears the saved port so the next restart will scan ports instead.
- Real-time dashboard with wind, heading, atmosphere, GPS, and attitude data (including apparent/true wind roses and speed–time heatmaps)
- Sparkline history graphs for all sensor channels
- Per-sentence enable/disable and transmission interval control
- Bandwidth usage indicator (percentage of serial bus capacity)
- Raw message view with one card per message type and live Hz rate
- UDP streaming to ArduPilot — selectable Wind Vane or GPS + Heading mode
- Cockpit data-lake WebSocket streaming of wind, GPS, and heading data
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
   - **Docker image**: `vshie/airmar-wx`
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
      "/usr/blueos/extensions/airmar-wx:/app/logs",
      "/dev:/dev"
    ],
    "ExtraHosts": ["host.docker.internal:host-gateway"],
    "NetworkMode": "host",
    "Privileged": true
  }
}
```

## ArduPilot UDP Streaming

The extension forwards NMEA sentences to the autopilot via UDP on port 27000. In the **Sentences** tab, choose one of two modes:

| Mode | Sentences | ArduPilot Use |
|---|---|---|
| **Wind Vane** (default) | `$WIMWV` | NMEA wind vane — set wind vane type to **NMEA** in ArduRover parameters |
| **GPS + Heading** | `$GPGGA`, `$GPRMC`, `$GPVTG`, `$HCHDT` | External GPS & heading source |

Only one mode can be active at a time — sending both wind and GPS data on the same port can confuse the autopilot.

See [ArduRover Wind Vane docs](https://ardupilot.org/rover/docs/wind-vane.html) for autopilot configuration.

## Cockpit WebSocket Streaming

The extension streams live wind, GPS, and heading data to Cockpit's data-lake via WebSocket (port 8765). All variables are sent **regardless of the ArduPilot UDP mode**.

### Variables Streamed

| Variable | Source | Description |
|---|---|---|
| `wind-direction-true` | `$WIMWD` | True wind direction relative to north (degrees) |
| `wind-speed-kts` | `$WIMWD` | True wind speed (knots) |
| `heading-true` | `$HCHDT` | True heading from compass (degrees) |
| `gps-latitude` | `$GPGGA` | GPS latitude (decimal degrees) |
| `gps-longitude` | `$GPGGA` | GPS longitude (decimal degrees) |
| `gps-altitude-m` | `$GPGGA` | GPS altitude (metres) |
| `gps-satellites` | `$GPGGA` | Number of GPS satellites in use |
| `gps-fix-quality` | `$GPGGA` | GPS fix quality (0=none, 1=GPS, 2=DGPS) |
| `gps-course-true` | `$GPVTG` | Course over ground (degrees true) |
| `gps-speed-kts` | `$GPVTG` | Speed over ground (knots) |

### Setting Up Cockpit Connection

1. Open **Cockpit > Menu > Settings > General**
2. Scroll to **Generic WebSocket connections**
3. Add the URL: `ws://{{ vehicle-address }}:8765`
   (e.g., `ws://192.168.2.2:8765`)

Once connected, all variables appear in Cockpit's data-lake and can be assigned to any widget, mini-widget, or HUD overlay.

## Log Files

Log files are stored in the BlueOS extensions directory:

- Location: `/usr/blueos/extensions/airmar-wx/`
- Files:
  - `nmea_messages.log` — Raw NMEA message history
  - `300wx.log` — Application operational log

These logs persist across container restarts and can be managed from the Logs tab in the extension UI.

## Usage

1. Open the Airmar 300WX extension from the BlueOS sidebar
2. The extension auto-connects to the last used serial port on startup
3. Select a serial port from the dropdown or device identification list
4. Click "Connect" — the extension negotiates 115200 baud automatically and stores that as the sensor default
5. View live sensor data on the Dashboard tab
6. Configure sentence enable/disable on the Sentences tab
7. View per-message-type data and Hz rates on the Raw Messages tab

## Development

### Building from Source

```bash
git clone https://github.com/vshie/Airmar-WX.git
cd Airmar-WX
docker build -t vshie/airmar-wx:latest .
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
- `IMAGE_NAME` — Docker repository name (default: `airmar-wx`)
- `MY_NAME` — Author name
- `MY_EMAIL` — Author email
- `ORG_NAME` — Maintainer organization name
- `ORG_EMAIL` — Maintainer organization email

## License

MIT License - see LICENSE file for details
