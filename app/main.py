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
import threading
import time

app = Flask(__name__, static_folder='static')
CORS(app)

class NMEAHandler:
    # Required NMEA sentences for dashboard display
    # These will be enabled after connecting at 38400 baud
    REQUIRED_SENTENCES = {
        'MWVT': {'interval': 10, 'description': 'True wind (vessel-relative)'},
        'MWD':  {'interval': 10, 'description': 'True wind (north-relative)'},
        'HDT':  {'interval': 10, 'description': 'True heading'},
        'ROT':  {'interval': 10, 'description': 'Rate of turn'},
        'ZDA':  {'interval': 10, 'description': 'Time and date'},
    }
    
    # Connection status phases
    CONN_STATUS_DISCONNECTED = 'disconnected'
    CONN_STATUS_TRYING_4800 = 'trying_4800'
    CONN_STATUS_TRYING_38400 = 'trying_38400'
    CONN_STATUS_SWITCHING_BAUD = 'switching_baud'
    CONN_STATUS_ENABLING_SENTENCES = 'enabling_sentences'
    CONN_STATUS_CONNECTED = 'connected'
    CONN_STATUS_FAILED = 'failed'
    
    def __init__(self):
        self.serial_connection = None
        # Create two separate loggers
        self.nmea_logger = logging.getLogger('nmea')
        self.app_logger = logging.getLogger('app')
        
        self.nmea_messages = set()
        self.selected_message_types = set()
        self.log_path = None
        self.state_path = None
        self.udp_socket = None
        self.is_streaming = False
        self.streamed_messages = 0
        self.message_history = []  # Store recent message history
        self.max_history = 100  # Maximum number of messages to keep in history
        
        # Connection status tracking
        self.connection_status = self.CONN_STATUS_DISCONNECTED
        self.connection_message = ''
        self.detected_baud = None  # Baud rate at which device was initially detected
        
        self.state = {
            'port': None,
            'baud_rate': 4800,
            'is_streaming': False,
            'selected_message_types': []
        }
        
        # Thread control
        self.reader_thread = None
        self.should_stop = False
        
        # Configure logging
        log_dir = Path('/app/logs')
        log_dir.mkdir(parents=True, exist_ok=True)
        
        # Set up file handler for NMEA messages
        self.log_path = log_dir / 'nmea_messages.log'
        nmea_fh = logging.FileHandler(self.log_path, mode='a')
        nmea_fh.setLevel(logging.INFO)
        nmea_formatter = logging.Formatter('%(asctime)s - %(message)s')
        nmea_fh.setFormatter(nmea_formatter)
        self.nmea_logger.addHandler(nmea_fh)
        self.nmea_logger.setLevel(logging.INFO)
        
        # Set up file handler for application logs
        app_log_path = log_dir / 'nmea_handler.log'
        app_fh = logging.FileHandler(app_log_path, mode='a')
        app_fh.setLevel(logging.INFO)
        app_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        app_fh.setFormatter(app_formatter)
        self.app_logger.addHandler(app_fh)
        self.app_logger.setLevel(logging.INFO)

        # State file path
        self.state_path = log_dir / 'state.json'
        
        # Load saved state
        self.load_state()
        
        # Restore previous connection if it exists
        if self.state['port']:
            self.app_logger.info(f"Restoring previous connection to {self.state['port']} at {self.state['baud_rate']} baud")
            success, message = self.connect_serial(self.state['port'], self.state['baud_rate'])
            if success:
                self.app_logger.info("Successfully restored previous connection")
                # Restore streaming state if it was active
                if self.state['is_streaming']:
                    self.app_logger.info("Restoring previous streaming state")
                    self.start_streaming()
            else:
                self.app_logger.error(f"Failed to restore previous connection: {message}")

    def load_state(self):
        """Load saved state from file"""
        try:
            if self.state_path.exists():
                with open(self.state_path, 'r') as f:
                    self.state = json.load(f)
                    self.selected_message_types = set(self.state.get('selected_message_types', []))
                    self.is_streaming = self.state.get('is_streaming', False)
                    self.app_logger.info(f"Loaded saved state: port={self.state['port']}, baud_rate={self.state['baud_rate']}, streaming={self.is_streaming}")
        except Exception as e:
            self.app_logger.error(f"Error loading state: {e}")

    def save_state(self):
        """Save current state to file"""
        try:
            self.state['selected_message_types'] = list(self.selected_message_types)
            self.state['is_streaming'] = self.is_streaming
            with open(self.state_path, 'w') as f:
                json.dump(self.state, f)
            self.app_logger.info(f"Saved state: port={self.state['port']}, baud_rate={self.state['baud_rate']}, streaming={self.is_streaming}")
        except Exception as e:
            self.app_logger.error(f"Error saving state: {e}")

    def start_reader_thread(self):
        """Start the background thread for reading serial data"""
        if self.reader_thread is None or not self.reader_thread.is_alive():
            self.should_stop = False
            self.reader_thread = threading.Thread(target=self._read_serial_loop)
            self.reader_thread.daemon = True
            self.reader_thread.start()
            self.app_logger.info("Started background serial reader thread")

    def stop_reader_thread(self):
        """Stop the background thread"""
        self.should_stop = True
        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=1.0)
            self.app_logger.info("Stopped background serial reader thread")

    def _read_serial_loop(self):
        """Background thread function for reading serial data"""
        while not self.should_stop:
            if self.serial_connection and self.serial_connection.is_open:
                try:
                    data = self.serial_connection.readline().decode('utf-8', errors='ignore').strip()
                    if data.startswith('$'):
                        # Parse NMEA message
                        msg_type = data.split(',')[0][1:]  # Remove $ and get message type
                        self.nmea_messages.add(msg_type)
                        
                        # Add message to history
                        message = {
                            "raw": data,
                            "type": msg_type,
                            "timestamp": datetime.datetime.now().isoformat()
                        }
                        self.message_history.insert(0, message)  # Add to start of list
                        if len(self.message_history) > self.max_history:
                            self.message_history.pop()  # Remove oldest message
                        
                        # Log the message
                        self.log_message(data)
                        # Stream the message if streaming is active and type is selected
                        if self.is_streaming and msg_type in self.selected_message_types:
                            self.stream_message(data, msg_type)
                except Exception as e:
                    self.app_logger.error(f"Error in serial reader thread: {e}")
            time.sleep(0.1)  # Small delay to prevent CPU overuse

    def start_streaming(self):
        """Start UDP streaming"""
        try:
            if not self.udp_socket:
                self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.is_streaming = True
            self.streamed_messages = 0  # Reset counter
            self.state['is_streaming'] = True
            self.save_state()
            self.app_logger.info(
                "UDP streaming started to host.docker.internal:27000 "
                "(typical device IP: 192.168.2.2)"
            )
            self.app_logger.info(f"Streaming selected message types: {', '.join(sorted(self.selected_message_types))}")
            return True, "Streaming started"
        except Exception as e:
            self.app_logger.error(f"Error starting UDP stream: {e}")
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
            self.app_logger.info("UDP streaming stopped")
            return True, "Streaming stopped"
        except Exception as e:
            self.app_logger.error(f"Error stopping UDP stream: {e}")
            return False, str(e)

    def update_selected_message_types(self, message_types):
        """Update the set of selected message types"""
        old_types = self.selected_message_types
        self.selected_message_types = set(message_types)
        self.save_state()
        self.app_logger.info(f"Updated streaming message types: {', '.join(sorted(self.selected_message_types))}")
        if self.is_streaming:
            self.app_logger.info(f"Streaming active with types: {', '.join(sorted(self.selected_message_types))}")

    def stream_message(self, message, msg_type):
        """Stream message via UDP if type is selected"""
        if self.is_streaming and msg_type in self.selected_message_types:
            self.app_logger.info(f"Attempting to stream message type: {msg_type}")
            try:
                # Check if socket is still valid
                if not self.udp_socket:
                    self.app_logger.info("Creating new UDP socket")
                    self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                
                # Send raw NMEA message with newline
                encoded_message = (message + '\n').encode()
                self.app_logger.info(f"Sending UDP packet to host.docker.internal:27000 - Length: {len(encoded_message)} bytes")
                self.udp_socket.sendto(encoded_message, ('host.docker.internal', 27000))
                self.streamed_messages += 1
                self.app_logger.info(f"Successfully streamed message #{self.streamed_messages}: {msg_type} - {message}")
            except Exception as e:
                self.app_logger.error(f"Error streaming message: {e}")
                # Try to recreate socket on error
                try:
                    if self.udp_socket:
                        self.app_logger.info("Closing existing UDP socket due to error")
                        self.udp_socket.close()
                    self.app_logger.info("Creating new UDP socket after error")
                    self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                except Exception as socket_error:
                    self.app_logger.error(f"Failed to recreate socket: {socket_error}")

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
            self.app_logger.error(f"Error listing ports: {e}")
        
        return ports

    def _try_baud_rate(self, port, baud_rate, timeout=3):
        """
        Try to connect at a specific baud rate and wait for valid NMEA data.
        Returns (success, serial_connection or None)
        """
        try:
            self.app_logger.info(f"Trying {port} at {baud_rate} baud...")
            conn = serial.Serial(
                port=port,
                baudrate=baud_rate,
                timeout=1
            )
            
            start_time = time.time()
            while time.time() - start_time < timeout:
                try:
                    data = conn.readline().decode('utf-8', errors='ignore').strip()
                    if data.startswith('$'):
                        # Valid NMEA sentence received
                        msg_type = data.split(',')[0][1:]
                        self.app_logger.info(f"Received valid NMEA at {baud_rate} baud: {msg_type}")
                        return True, conn
                except Exception as e:
                    self.app_logger.error(f"Error reading at {baud_rate} baud: {e}")
                time.sleep(0.1)
            
            # No valid data received, close connection
            conn.close()
            return False, None
            
        except Exception as e:
            self.app_logger.error(f"Failed to open port at {baud_rate} baud: {e}")
            return False, None

    def _switch_to_38400(self, port):
        """
        Switch the device from 4800 to 38400 baud.
        Assumes we're currently connected at 4800 baud.
        Returns (success, message)
        """
        try:
            self.connection_status = self.CONN_STATUS_SWITCHING_BAUD
            self.connection_message = 'Switching to 38400 baud...'
            
            # Step 1: Disable periodic sentences
            self.app_logger.info("Disabling periodic sentences before baud change")
            self.serial_connection.write(b'$PAMTX\r\n')
            time.sleep(0.5)
            
            # Step 2: Send baud rate change command
            self.app_logger.info("Sending baud rate change command to 38400")
            self.serial_connection.write(b'$PAMTC,BAUD,38400\r\n')
            time.sleep(1)
            
            # Step 3: Close current connection
            self.serial_connection.close()
            time.sleep(0.5)
            
            # Step 4: Reopen at 38400 baud
            self.app_logger.info("Reopening connection at 38400 baud")
            self.serial_connection = serial.Serial(
                port=port,
                baudrate=38400,
                timeout=1
            )
            time.sleep(0.5)
            
            # Step 5: Re-enable periodic sentences
            self.app_logger.info("Re-enabling periodic sentences")
            self.serial_connection.write(b'$PAMTX,1\r\n')
            time.sleep(0.3)
            
            # Verify we're receiving data at 38400
            start_time = time.time()
            while time.time() - start_time < 3:
                data = self.serial_connection.readline().decode('utf-8', errors='ignore').strip()
                if data.startswith('$'):
                    self.app_logger.info(f"Confirmed communication at 38400 baud")
                    return True, "Switched to 38400 baud"
                time.sleep(0.1)
            
            return False, "No response after baud rate switch"
            
        except Exception as e:
            self.app_logger.error(f"Error switching baud rate: {e}")
            return False, str(e)

    def enable_required_sentences(self):
        """
        Enable the NMEA sentences required for dashboard display.
        Should only be called when connected at 38400 baud.
        Returns (success, message)
        """
        try:
            self.connection_status = self.CONN_STATUS_ENABLING_SENTENCES
            self.connection_message = 'Enabling required NMEA sentences...'
            
            if not self.serial_connection or not self.serial_connection.is_open:
                return False, "Not connected"
            
            enabled_count = 0
            for sentence_id, config in self.REQUIRED_SENTENCES.items():
                # Format: $PAMTC,EN,<id>,<enable>,<interval>
                # interval is in tenths of a second (10 = 1 second)
                cmd = f'$PAMTC,EN,{sentence_id},1,{config["interval"]}\r\n'
                self.app_logger.info(f"Enabling sentence {sentence_id}: {config['description']}")
                self.serial_connection.write(cmd.encode())
                time.sleep(0.2)  # Small delay between commands
                enabled_count += 1
            
            self.app_logger.info(f"Enabled {enabled_count} required NMEA sentences")
            return True, f"Enabled {enabled_count} sentences"
            
        except Exception as e:
            self.app_logger.error(f"Error enabling sentences: {e}")
            return False, str(e)

    def connect_serial(self, port, baud_rate=None):
        """
        Connect to serial port with automatic baud rate negotiation.
        
        Connection sequence:
        1. Try 4800 baud - if successful, switch to 38400 baud
        2. If 4800 fails, try 38400 baud (device may already be configured)
        3. Toggle between baud rates until successful or max retries
        4. Once at 38400 baud, enable required sentences
        """
        try:
            # Clean up any existing connection
            if self.serial_connection and self.serial_connection.is_open:
                self.stop_reader_thread()
                self.serial_connection.close()
                self.serial_connection = None
            
            # Verify port exists
            if not Path(port).exists():
                self.connection_status = self.CONN_STATUS_FAILED
                self.connection_message = f"Port {port} does not exist"
                return False, self.connection_message
            
            self.detected_baud = None
            max_attempts = 6  # 3 attempts at each baud rate
            baud_rates = [4800, 38400]
            
            # Try to establish connection
            for attempt in range(max_attempts):
                current_baud = baud_rates[attempt % 2]
                
                if current_baud == 4800:
                    self.connection_status = self.CONN_STATUS_TRYING_4800
                    self.connection_message = f'Trying {current_baud} baud (attempt {attempt // 2 + 1}/3)...'
                else:
                    self.connection_status = self.CONN_STATUS_TRYING_38400
                    self.connection_message = f'Trying {current_baud} baud (attempt {attempt // 2 + 1}/3)...'
                
                self.app_logger.info(self.connection_message)
                
                success, conn = self._try_baud_rate(port, current_baud, timeout=3)
                
                if success:
                    self.serial_connection = conn
                    self.detected_baud = current_baud
                    self.state['port'] = port
                    
                    if current_baud == 4800:
                        # Connected at 4800, need to switch to 38400
                        self.app_logger.info("Connected at 4800 baud, switching to 38400...")
                        success, msg = self._switch_to_38400(port)
                        if not success:
                            self.connection_status = self.CONN_STATUS_FAILED
                            self.connection_message = f"Failed to switch baud rate: {msg}"
                            return False, self.connection_message
                    
                    # Now at 38400 baud - enable required sentences
                    success, msg = self.enable_required_sentences()
                    if not success:
                        self.app_logger.warning(f"Failed to enable sentences: {msg}")
                        # Continue anyway - sentences may already be enabled
                    
                    # Start the reader thread
                    self.start_reader_thread()
                    
                    # Update state
                    self.state['baud_rate'] = 38400
                    self.save_state()
                    
                    self.connection_status = self.CONN_STATUS_CONNECTED
                    detected_msg = f" (detected at {self.detected_baud})" if self.detected_baud == 38400 else " (switched from 4800)"
                    self.connection_message = f"Connected to {port} at 38400 baud{detected_msg}"
                    self.app_logger.info(self.connection_message)
                    
                    return True, self.connection_message
            
            # All attempts failed
            self.connection_status = self.CONN_STATUS_FAILED
            self.connection_message = "Could not establish connection after multiple attempts"
            self.app_logger.error(self.connection_message)
            return False, self.connection_message
            
        except Exception as e:
            self.connection_status = self.CONN_STATUS_FAILED
            self.connection_message = str(e)
            self.app_logger.error(f"Error in connect_serial: {e}")
            return False, self.connection_message

    def disconnect_serial(self):
        """Disconnect from serial port"""
        try:
            # Stop streaming first
            if self.is_streaming:
                self.stop_streaming()
            
            # Stop the reader thread
            self.stop_reader_thread()
            
            # Close the serial connection
            if self.serial_connection and self.serial_connection.is_open:
                self.serial_connection.close()
            
            self.serial_connection = None
            
            # Reset state
            self.state['port'] = None
            self.message_history = []  # Clear message history
            self.nmea_messages = set()  # Clear detected message types
            self.detected_baud = None
            self.connection_status = self.CONN_STATUS_DISCONNECTED
            self.connection_message = ''
            self.save_state()
            
            return True, "Disconnected from serial port"
        except Exception as e:
            self.app_logger.error(f"Error disconnecting: {e}")
            return False, str(e)
    
    def get_connection_info(self):
        """Get detailed connection information"""
        is_connected = (self.serial_connection is not None and 
                       self.serial_connection.is_open)
        
        return {
            'connected': is_connected,
            'status': self.connection_status,
            'message': self.connection_message,
            'port': self.serial_connection.port if is_connected else None,
            'baud_rate': self.serial_connection.baudrate if is_connected else 0,
            'detected_baud': self.detected_baud,
            'required_sentences': list(self.REQUIRED_SENTENCES.keys()),
        }

    def read_serial(self):
        """Read data from serial port - returns filtered message history"""
        if not self.serial_connection or not self.serial_connection.is_open:
            return {"status": "error", "message": "Not connected"}
        
        # Filter messages by selected types
        filtered_messages = [
            msg for msg in self.message_history
            if not self.selected_message_types or msg["type"] in self.selected_message_types
        ]
        
        return {
            "status": "success",
            "messages": filtered_messages,
            "available_types": list(self.nmea_messages)
        }

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
            
            # Also log to NMEA logger
            self.nmea_logger.info(message)
            return True, "Message logged"
        except Exception as e:
            self.app_logger.error(f"Error logging NMEA message: {e}")
            return False, str(e)

    def change_baud_rate(self, new_baud_rate):
        """Change the baud rate of the weather station"""
        try:
            if not self.serial_connection or not self.serial_connection.is_open:
                return False, "Not connected to serial port"

            current_baud = self.serial_connection.baudrate
            if current_baud == new_baud_rate:
                return False, f"Already at {new_baud_rate} baud"

            # Step 1: Disable periodic sentences
            self.app_logger.info("Disabling periodic sentences")
            self.serial_connection.write(b'$PAMTX\r\n')
            time.sleep(0.5)  # Wait for command to be processed

            # Step 2: Send baud rate change command
            self.app_logger.info(f"Sending baud rate change command to {new_baud_rate}")
            baud_cmd = f'$PAMTC,BAUD,{new_baud_rate}\r\n'.encode()
            self.serial_connection.write(baud_cmd)
            time.sleep(1)  # Wait for any queued messages

            # Step 3: Close current connection
            self.app_logger.info("Closing current connection")
            self.serial_connection.close()
            time.sleep(0.5)  # Wait for port to fully close

            # Step 4: Reopen at new baud rate
            self.app_logger.info(f"Reopening connection at {new_baud_rate} baud")
            self.serial_connection = serial.Serial(
                port=self.state['port'],
                baudrate=new_baud_rate,
                timeout=1
            )
            time.sleep(0.5)  # Wait for connection to stabilize

            # Step 5: Re-enable periodic sentences
            self.app_logger.info("Re-enabling periodic sentences")
            self.serial_connection.write(b'$PAMTX,1\r\n')

            # Update state
            self.state['baud_rate'] = new_baud_rate
            self.save_state()

            return True, f"Successfully changed baud rate to {new_baud_rate}"
        except Exception as e:
            self.app_logger.error(f"Error changing baud rate: {e}")
            return False, str(e)

# Create NMEA handler instance
nmea_handler = NMEAHandler()

@app.route('/')
def index():
    """Serve the main page"""
    return send_from_directory(app.static_folder, 'index.html')

@app.route('/register_service')
def register_service():
    """Provide extension metadata to BlueOS"""
    return jsonify({
        "name": "NMEA Handler",
        "description": "Route nmea data",
        "icon": "mdi-briefcase-minus-outline",
        "company": "Blue Robotics",
        "version": "0.0.1",
        "webpage": "https://github.com/vshie/NMEA-handler",
        "api": "https://github.com/vshie/NMEA-handler"
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
            "baud_rate": nmea_handler.serial_connection.baudrate,
            "detected_baud": nmea_handler.detected_baud
        })
    return jsonify({"serial_port": "Not connected", "baud_rate": 0, "detected_baud": None})

@app.route('/api/connection/status', methods=['GET'])
def get_connection_status():
    """Get detailed connection status information"""
    return jsonify(nmea_handler.get_connection_info())

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

@app.route('/api/serial/change_baud', methods=['POST'])
def change_baud():
    """Change the baud rate of the weather station"""
    data = request.get_json()
    if not data or 'baud_rate' not in data:
        return jsonify({"success": False, "message": "No baud rate specified"})
    
    baud_rate = data['baud_rate']
    if baud_rate not in [4800, 38400]:
        return jsonify({"success": False, "message": "Invalid baud rate. Must be 4800 or 38400"})
    
    success, message = nmea_handler.change_baud_rate(baud_rate)
    return jsonify({"success": success, "message": message})

if __name__ == '__main__':
    from waitress import serve
    serve(app, host='0.0.0.0', port=6436)
