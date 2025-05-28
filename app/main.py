#!/usr/bin/env python3

import serial
import serial.tools.list_ports
import logging
import json
from pathlib import Path
from litestar import Litestar, get, post, MediaType
from litestar.controller import Controller
from litestar.datastructures import State
from litestar.logging import LoggingConfig
from litestar.static_files.config import StaticFilesConfig

class NMEAController(Controller):
    def __init__(self, *args, **kwargs):
        self.serial_connection = None
        self.logger = logging.getLogger(__name__)
        self.nmea_messages = set()
        self.log_path = Path('/app/logs/nmea_messages.log')
        super().__init__(*args, **kwargs)

    @get("/ports", sync_to_thread=False)
    def get_ports(self) -> dict:
        """Get list of available serial ports"""
        ports = [port.device for port in serial.tools.list_ports.comports()]
        return {"ports": ports}

    @get("/baud_rates", sync_to_thread=False)
    def get_baud_rates(self) -> dict:
        """Get list of common baud rates"""
        rates = [9600, 19200, 38400, 57600, 115200]
        return {"rates": rates}

    @post("/connect", sync_to_thread=True)
    def connect_serial(self, data: dict) -> dict:
        """Connect to serial port with specified settings"""
        try:
            if self.serial_connection and self.serial_connection.is_open:
                self.serial_connection.close()
            
            self.serial_connection = serial.Serial(
                port=data["port"],
                baudrate=data["baud_rate"],
                timeout=1
            )
            return {"status": "connected", "message": f"Connected to {data['port']} at {data['baud_rate']} baud"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @get("/disconnect", sync_to_thread=True)
    def disconnect_serial(self) -> dict:
        """Disconnect from serial port"""
        try:
            if self.serial_connection and self.serial_connection.is_open:
                self.serial_connection.close()
            return {"status": "disconnected", "message": "Disconnected from serial port"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @get("/read", sync_to_thread=True)
    def read_serial(self) -> dict:
        """Read data from serial port"""
        if not self.serial_connection or not self.serial_connection.is_open:
            return {"status": "error", "message": "Not connected"}
        
        try:
            data = self.serial_connection.readline().decode('utf-8', errors='ignore').strip()
            if data.startswith('$'):
                # Parse NMEA message
                msg_type = data.split(',')[0][1:]  # Remove $ and get message type
                self.nmea_messages.add(msg_type)
                return {
                    "status": "success",
                    "raw": data,
                    "type": msg_type,
                    "available_types": list(self.nmea_messages)
                }
            return {"status": "success", "raw": data}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    @post("/log_message", sync_to_thread=True)
    def log_message(self, data: dict) -> dict:
        """Enable/disable logging for specific NMEA message type"""
        try:
            with open(self.log_path, 'a') as f:
                f.write(f"{data['message']}\n")
            return {"status": "success", "message": "Message logged"}
        except Exception as e:
            return {"status": "error", "message": str(e)}

# Configure logging
logging_config = LoggingConfig(
    loggers={
        __name__: dict(
            level='INFO',
            handlers=['queue_listener'],
        )
    },
)

# Create log directory and set permissions
log_dir = Path('/app/logs')
log_dir.mkdir(parents=True, exist_ok=True)
fh = logging.handlers.RotatingFileHandler(log_dir / 'nmea_handler.log', maxBytes=2**16, backupCount=1)

# Create application
app = Litestar(
    route_handlers=[NMEAController],
    static_files_config=[
        StaticFilesConfig(directories=['app/static'], path='/', html_mode=True)
    ],
    logging_config=logging_config,
)

app.logger.addHandler(fh)
