#!/usr/bin/env python3

import serial
import serial.tools.list_ports
import logging
import json
import socket
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
        self.selected_message_types = set()
        self.log_path = Path('/app/logs/nmea_messages.log')
        self.state_path = Path('/app/logs/state.json')
        self.udp_socket = None
        self.is_streaming = False
        self.streamed_messages = 0  # Add message counter
        self.state = {
            'port': None,
            'baud_rate': 4800,
            'is_streaming': False,
            'selected_message_types': []
        }
        
        # Configure logging
        log_dir = Path('/app/logs')
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Set up file handler for NMEA messages
        fh = logging.FileHandler(self.log_path, mode='a')
        fh.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(message)s')
        fh.setFormatter(formatter)
        self.logger.addHandler(fh)
        
        # Set up file handler for application logs
        app_log_path = log_dir / 'nmea_handler.log'
        app_fh = logging.FileHandler(app_log_path, mode='a')
        app_fh.setLevel(logging.INFO)
        app_fh.setFormatter(formatter)
        self.logger.addHandler(app_fh)

        # Load saved state
        self.load_state()

    def load_state(self):
        """Load saved state from file"""
        try:
            if self.state_path.exists():
                with open(self.state_path, 'r') as f:
                    self.state = json.load(f)
                    self.selected_message_types = set(self.state.get('selected_message_types', []))
        except Exception as e:
            self.logger.error(f"Error loading state: {e}")

    def save_state(self):
        """Save current state to file"""
        try:
            self.state['selected_message_types'] = list(self.selected_message_types)
            with open(self.state_path, 'w') as f:
                json.dump(self.state, f)
        except Exception as e:
            self.logger.error(f"Error saving state: {e}")

    def start_streaming(self):
        """Start UDP streaming"""
        try:
            if not self.udp_socket:
                self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.is_streaming = True
            self.streamed_messages = 0  # Reset counter
            self.state['is_streaming'] = True
            self.save_state()
            self.logger.info(f"UDP streaming started to host.docker.internal:27000")
            self.logger.info(f"Streaming selected message types: {', '.join(sorted(self.selected_message_types))}")
            return True, "Streaming started"
        except Exception as e:
            self.logger.error(f"Error starting UDP stream: {e}")
            return False, str(e)

    def stop_streaming(self):
        """Stop UDP streaming"""
        try:
            if self.udp_socket:
                self.udp_socket.close()
                self.udp_socket = None
            self.is_streaming = False
            self.state['is_streaming'] = False
            self.save_state()
            self.logger.info("UDP streaming stopped")
            return True, "Streaming stopped"
        except Exception as e:
            self.logger.error(f"Error stopping UDP stream: {e}")
            return False, str(e)

    def update_selected_message_types(self, message_types):
        """Update the set of selected message types"""
        old_types = self.selected_message_types
        self.selected_message_types = set(message_types)
        self.save_state()
        self.logger.info(f"Updated streaming message types: {', '.join(sorted(self.selected_message_types))}")
        if self.is_streaming:
            self.logger.info(f"Streaming active with types: {', '.join(sorted(self.selected_message_types))}")

    def stream_message(self, message, msg_type):
        """Stream message via UDP if type is selected"""
        if self.is_streaming and self.udp_socket and msg_type in self.selected_message_types:
            try:
                # Send raw NMEA message with newline
                self.udp_socket.sendto((message + '\n').encode(), ('host.docker.internal', 27000))
                self.streamed_messages += 1
                self.logger.info(f"Streamed message #{self.streamed_messages}: {msg_type} - {message}")
            except Exception as e:
                self.logger.error(f"Error streaming message: {e}")

    def get_ports(self):
        """Get list of available serial ports"""
        ports = []
        # Check common USB serial ports
        for i in range(4):  # Check ttyUSB0 through ttyUSB3
            port = f'/dev/ttyUSB{i}'
            if Path(port).exists():
                ports.append(port)
        
        # Check common ACM ports
        for i in range(2):  # Check ttyACM0 through ttyACM1
            port = f'/dev/ttyAMA{i}'
            if Path(port).exists():
                ports.append(port)
        
        # Also check using pyserial's list_ports
        try:
            for port in serial.tools.list_ports.comports():
                if port.device not in ports:
                    ports.append(port.device)
        except Exception as e:
            self.logger.error(f"Error listing ports: {e}")
        
        return ports

    def connect_serial(self, port, baud_rate):
        """Connect to serial port with specified settings"""
        try:
            if self.serial_connection and self.serial_connection.is_open:
                self.serial_connection.close()
            
            # Verify port exists before attempting connection
            if not Path(port).exists():
                return False, f"Port {port} does not exist"
            
            self.serial_connection = serial.Serial(
                port=port,
                baudrate=baud_rate,
                timeout=1
            )
            # Save state
            self.state['port'] = port
            self.state['baud_rate'] = baud_rate
            self.save_state()
            return True, f"Connected to {port} at {baud_rate} baud"
        except Exception as e:
            return False, str(e)

    def disconnect_serial(self):
        """Disconnect from serial port"""
        try:
            if self.serial_connection and self.serial_connection.is_open:
                self.serial_connection.close()
            # Update state
            self.state['port'] = None
            self.save_state()
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
                # Stream the message if streaming is active and type is selected
                if self.is_streaming and msg_type in self.selected_message_types:
                    self.stream_message(data, msg_type)
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
    try:
        with open('register_service.json', 'r') as f:
            return jsonify(json.load(f))
    except Exception as e:
        app.logger.error(f"Error loading register_service.json: {e}")
        return jsonify({"error": "Failed to load service registration"}), 500

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
    
    baud_rate = data.get('baud_rate', 4800)  # Default to 4800 if not specified
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
    if not nmea_handler.log_path.exists():
        return jsonify({
            "success": False,
            "message": "No log file found. Connect to a device and receive messages to create logs."
        }), 404
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

@app.route('/api/stream/start', methods=['POST'])
def start_streaming():
    """Start UDP streaming"""
    success, message = nmea_handler.start_streaming()
    return jsonify({"success": success, "message": message})

@app.route('/api/stream/stop', methods=['POST'])
def stop_streaming():
    """Stop UDP streaming"""
    success, message = nmea_handler.stop_streaming()
    return jsonify({"success": success, "message": message})

@app.route('/api/stream/status', methods=['GET'])
def get_streaming_status():
    """Get current streaming status"""
    return jsonify({
        "is_streaming": nmea_handler.is_streaming,
        "port": nmea_handler.state['port'],
        "baud_rate": nmea_handler.state['baud_rate'],
        "selected_message_types": list(nmea_handler.selected_message_types),
        "streaming_to": "host.docker.internal:27000" if nmea_handler.is_streaming else None,
        "streamed_messages": nmea_handler.streamed_messages
    })

@app.route('/api/message_types/update', methods=['POST'])
def update_message_types():
    """Update selected message types"""
    data = request.get_json()
    if not data or 'message_types' not in data:
        return jsonify({"success": False, "message": "No message types specified"})
    
    nmea_handler.update_selected_message_types(data['message_types'])
    return jsonify({"success": True, "message": "Message types updated"})

if __name__ == '__main__':
    from waitress import serve
    serve(app, host='0.0.0.0', port=6436)
