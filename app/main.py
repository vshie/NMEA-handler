#!/usr/bin/env python3

import serial
import serial.tools.list_ports
import logging
import json
from pathlib import Path
from flask import Flask, jsonify, request, send_file, send_from_directory
from flask_cors import CORS
import datetime

app = Flask(__name__, static_folder='static')
CORS(app)

class NMEAHander:
    def __init__(self):
        self.serial_connection = None
        self.logger = logging.getLogger(__name__)
        self.nmea_messages = set()
        self.log_path = Path('/app/logs/nmea_messages.log')
        
        # Configure logging
        log_dir = Path('/app/logs')
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Set up file handler for NMEA messages
        fh = logging.FileHandler(self.log_path, mode='a')  # Use append mode
        fh.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(message)s')
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)
        
        # Set up file handler for application logs
        app_log_path = log_dir / 'nmea_handler.log'
        app_fh = logging.FileHandler(app_log_path, mode='a')  # Use append mode
        app_fh.setLevel(logging.INFO)
        app_fh.setFormatter(formatter)
        self.logger.addHandler(app_fh)

    def get_ports(self):
        """Get list of available serial ports"""
        return [port.device for port in serial.tools.list_ports.comports()]

    def connect_serial(self, port, baud_rate):
        """Connect to serial port with specified settings"""
        try:
            if self.serial_connection and self.serial_connection.is_open:
                self.serial_connection.close()
            
            self.serial_connection = serial.Serial(
                port=port,
                baudrate=baud_rate,
                timeout=1
            )
            return True, f"Connected to {port} at {baud_rate} baud"
        except Exception as e:
            return False, str(e)

    def disconnect_serial(self):
        """Disconnect from serial port"""
        try:
            if self.serial_connection and self.serial_connection.is_open:
                self.serial_connection.close()
            return True, "Disconnected from serial port"
        except Exception as e:
            return False, str(e)

    def read_serial(self):
        """Read data from serial port"""
        if not self.serial_connection or not self.serial_connection.is_open:
            return {"status": "error", "message": "Not connected"}
        
        try:
            data = self.serial_connection.readline().decode('utf-8', errors='ignore').strip()
            if data.startswith('$'):
                # Parse NMEA message
                msg_type = data.split(',')[0][1:]  # Remove $ and get message type
                self.nmea_messages.add(msg_type)
                # Log the message
                self.log_message(data)
                return {
                    "status": "success",
                    "raw": data,
                    "type": msg_type,
                    "available_types": list(self.nmea_messages)
                }
            return {"status": "success", "raw": data}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def log_message(self, message):
        """Log NMEA message"""
        try:
            # Ensure the log file exists
            if not self.log_path.exists():
                self.log_path.touch()
            
            # Write the message with timestamp
            timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            with open(self.log_path, 'a') as f:
                f.write(f"{timestamp} - {message}\n")
            
            return True, "Message logged"
        except Exception as e:
            return False, str(e)

# Create NMEA handler instance
nmea_handler = NMEAHander()

@app.route('/')
def index():
    """Serve the main page"""
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/register_service')
def register_service():
    """Provide extension metadata to BlueOS"""
    return jsonify({
        "name": "NMEA Handler",
        "version": "0.1",
        "description": "Monitor and log NMEA messages from serial devices",
        "icon": "mdi-enterprise",
        "author": "Tony White",
        "website": "https://github.com/vshie/NMEA-handler",
        "api": "0.1",
        "frontend": {
            "name": "NMEA Handler",
            "icon": "mdi-enterprise",
            "description": "Monitor and log NMEA messages from serial devices",
            "category": "Sensors",
            "order": 10
        },
        "services": [
            {
                "name": "NMEA Handler",
                "icon": "mdi-enterprise",
                "description": "Monitor and log NMEA messages from serial devices",
                "category": "Sensors",
                "order": 10,
                "url": "/",
                "type": "tool",
                "version": "0.1",
                "api": "0.1",
                "requirements": "core >= 1.1"
            }
        ]
    })

@app.route('/docs')
def docs():
    """Serve API documentation"""
    return jsonify({
        "openapi": "3.0.0",
        "info": {
            "title": "NMEA Handler API",
            "version": "0.1",
            "description": "API for monitoring and logging NMEA messages"
        },
        "paths": {
            "/api/serial/ports": {
                "get": {
                    "summary": "Get available serial ports",
                    "responses": {
                        "200": {
                            "description": "List of available ports"
                        }
                    }
                }
            }
        }
    })

@app.route('/v1.0/ui/')
def ui():
    """Serve the UI"""
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/api/serial/ports', methods=['GET'])
def get_ports():
    """Get list of available serial ports"""
    return jsonify({"ports": nmea_handler.get_ports()})

@app.route('/api/serial/select', methods=['POST'])
def select_port():
    """Select and connect to a serial port"""
    data = request.get_json()
    if not data or 'port' not in data:
        return jsonify({"success": False, "message": "No port specified"})
    
    baud_rate = data.get('baud_rate', 9600)  # Default to 9600 if not specified
    success, message = nmea_handler.connect_serial(data['port'], baud_rate)
    return jsonify({"success": success, "message": message})

@app.route('/api/serial/disconnect', methods=['POST'])
def disconnect_port():
    """Disconnect from serial port"""
    success, message = nmea_handler.disconnect_serial()
    return jsonify({"success": success, "message": message})

@app.route('/api/serial', methods=['GET'])
def get_serial_info():
    """Get current serial port information"""
    if nmea_handler.serial_connection and nmea_handler.serial_connection.is_open:
        return jsonify({
            "serial_port": nmea_handler.serial_connection.port,
            "baud_rate": nmea_handler.serial_connection.baudrate
        })
    return jsonify({"serial_port": "Not connected", "baud_rate": 0})

@app.route('/api/read', methods=['GET'])
def read_serial():
    """Read data from serial port"""
    return jsonify(nmea_handler.read_serial())

@app.route('/api/log_message', methods=['POST'])
def log_message():
    """Log NMEA message"""
    data = request.get_json()
    if not data or 'message' not in data:
        return jsonify({"success": False, "message": "No message specified"})
    
    success, message = nmea_handler.log_message(data['message'])
    return jsonify({"success": success, "message": message})

@app.route('/api/logs', methods=['GET'])
def download_logs():
    """Download log file"""
    return send_file(nmea_handler.log_path, as_attachment=True)

@app.route('/api/logs/delete', methods=['POST'])
def delete_logs():
    """Delete log file"""
    try:
        if nmea_handler.log_path.exists():
            nmea_handler.log_path.unlink()
        return jsonify({"success": True, "message": "Logs deleted successfully"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

if __name__ == '__main__':
    from waitress import serve
    serve(app, host='0.0.0.0', port=6436)
