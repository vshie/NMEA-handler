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
import re
import threading
import time

app = Flask(__name__, static_folder='static')
CORS(app)

class NMEAHandler:
    # Complete registry of all WX200 supported NMEA sentences
    # Based on WX Series NMEA 0183 Developers Technical Manual
    SUPPORTED_SENTENCES = {
        'DTM':   {'name': 'Datum Reference', 'description': 'GPS datum reference', 'default_enabled': False, 'default_interval': 10},
        'GGA':   {'name': 'GPS Fix Data', 'description': 'Position, altitude, satellites, fix quality', 'default_enabled': True, 'default_interval': 10},
        'GLL':   {'name': 'Geographic Position', 'description': 'Latitude/Longitude position', 'default_enabled': False, 'default_interval': 10},
        'GSA':   {'name': 'DOP & Satellites', 'description': 'GPS dilution of precision and active satellites', 'default_enabled': False, 'default_interval': 10},
        'GSV':   {'name': 'Satellites in View', 'description': 'Satellite details (azimuth, elevation, SNR)', 'default_enabled': False, 'default_interval': 10},
        'HDG':   {'name': 'Heading (Magnetic)', 'description': 'Magnetic heading with deviation and variation', 'default_enabled': True, 'default_interval': 10},
        'HDT':   {'name': 'Heading (True)', 'description': 'True heading relative to north', 'default_enabled': False, 'default_interval': 10},
        'MDA':   {'name': 'Meteorological Composite', 'description': 'Pressure, temperature, humidity, dew point, wind', 'default_enabled': True, 'default_interval': 10},
        'MWD':   {'name': 'Wind Direction (True)', 'description': 'True wind direction and speed relative to north', 'default_enabled': False, 'default_interval': 10},
        'MWVR': {'name': 'Wind (Apparent)', 'description': 'Relative/apparent wind speed and angle', 'default_enabled': True, 'default_interval': 10},
        'MWVT': {'name': 'Wind (True)', 'description': 'True/theoretical wind speed and angle', 'default_enabled': False, 'default_interval': 10},
        'RMC':   {'name': 'Recommended Minimum', 'description': 'Position, speed, course, date, magnetic variation', 'default_enabled': True, 'default_interval': 10},
        'ROT':   {'name': 'Rate of Turn', 'description': 'Rate of turn in degrees per minute', 'default_enabled': False, 'default_interval': 10},
        'THS':   {'name': 'True Heading & Status', 'description': 'True heading with mode indicator', 'default_enabled': False, 'default_interval': 10},
        'VTG':   {'name': 'Course Over Ground', 'description': 'Course and speed over ground', 'default_enabled': False, 'default_interval': 10},
        'VWR':   {'name': 'Relative Wind (Alt)', 'description': 'Relative wind speed and angle (alternative format)', 'default_enabled': False, 'default_interval': 10},
        'VWT':   {'name': 'True Wind (Alt)', 'description': 'True wind speed and angle (alternative format)', 'default_enabled': True, 'default_interval': 10},
        'XDRA': {'name': 'Transducer A', 'description': 'Wind chill, heat index, station pressure', 'default_enabled': True, 'default_interval': 10},
        'XDRB': {'name': 'Transducer B', 'description': 'Pitch and roll angles', 'default_enabled': True, 'default_interval': 10},
        'XDRC': {'name': 'Transducer C', 'description': 'X, Y, Z accelerometer readings', 'default_enabled': False, 'default_interval': 10},
        'XDRD': {'name': 'Transducer D', 'description': 'Compensated rate gyros (roll, pitch, yaw)', 'default_enabled': True, 'default_interval': 10},
        'XDRE': {'name': 'Transducer E', 'description': 'Raw rate gyros (roll, pitch, yaw)', 'default_enabled': False, 'default_interval': 10},
        'XDRH': {'name': 'Transducer H', 'description': 'Heater temperatures and voltages', 'default_enabled': False, 'default_interval': 10},
        'XDRR': {'name': 'Transducer R', 'description': 'Rain accumulation, duration, rate', 'default_enabled': False, 'default_interval': 10},
        'XDRT': {'name': 'Transducer T', 'description': 'Internal temperatures and voltages', 'default_enabled': False, 'default_interval': 10},
        'XDRW': {'name': 'Transducer W', 'description': 'Raw/unfiltered wind measurements', 'default_enabled': False, 'default_interval': 10},
        'ZDA':   {'name': 'Time & Date', 'description': 'UTC time and date', 'default_enabled': False, 'default_interval': 10},
    }
    
    # Sentences auto-enabled on connection for dashboard display
    # These are enabled in addition to device defaults
    REQUIRED_SENTENCES = ['MWVT', 'MWD', 'HDT', 'ROT', 'ZDA']
    
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
        self.messages_received = 0  # Total NMEA messages received since connection
        self.connected_since = None  # epoch seconds when connection established
        self.message_history = []  # Store recent message history
        self.max_history = 100  # Maximum number of messages to keep in history
        # Last-seen timestamps for configured sentence IDs (used for UI sync)
        # { sentence_id: epoch_seconds_last_seen }
        self.sentence_last_seen = {}
        # Serial health/debug stats (helps diagnose "returned no data" bursts)
        self._serial_health_lock = threading.Lock()
        self.serial_health = {
            'last_good_nmea_ts': None,
            'last_read_attempt_ts': None,
            'last_raw_len': 0,
            'last_in_waiting': None,
            'read_timeouts': 0,
            'empty_reads': 0,
            'nodata_exceptions': 0,
            'other_read_exceptions': 0,
            'checksum_mismatch': 0,
            'checksum_missing': 0,
            'unmapped_messages': 0,
            'last_unmapped_type': None,
        }
        
        # Connection status tracking
        self.connection_status = self.CONN_STATUS_DISCONNECTED
        self.connection_message = ''
        self.detected_baud = None  # Baud rate at which device was initially detected
        
        self.state = {
            'port': None,
            'baud_rate': 4800,
            'is_streaming': False,
            'selected_message_types': [],
            'sentence_config': {}  # { sentence_id: { "enabled": bool, "interval": int (tenths) } }
        }
        # Lock so only one consumer reads from serial (reader thread vs sentence query)
        self._serial_lock = threading.Lock()
        # Throttle "no data" serial read errors (log at most once per 30s)
        self._last_serial_read_error_log = 0
        
        # Aggregated sensor data for dashboard display
        self.sensor_data = {
            'wind_apparent': {
                'speed_kts': None,
                'angle': None,
                'source': None,
                'timestamp': None
            },
            'wind_true': {
                'speed_kts': None,
                'angle': None,  # Relative to vessel
                'direction_true': None,  # Relative to north
                'direction_magnetic': None,
                'source': None,
                'timestamp': None
            },
            'atmosphere': {
                'temperature_c': None,
                'humidity_pct': None,
                'pressure_bar': None,
                'dew_point_c': None,
                'source': None,
                'timestamp': None
            },
            'attitude': {
                'heading_true': None,
                'heading_magnetic': None,
                'pitch_deg': None,
                'roll_deg': None,
                'rate_of_turn': None,
                'source': None,
                'timestamp': None
            },
            'gps': {
                'latitude': None,
                'longitude': None,
                'altitude_m': None,
                'satellites': None,
                'fix_quality': None,
                'speed_kts': None,
                'course_true': None,
                'source': None,
                'timestamp': None
            },
            'time': {
                'utc_time': None,
                'utc_date': None,
                'source': None,
                'timestamp': None
            }
        }
        
        # Historical sensor data for sparklines (15 minutes = 900 seconds)
        self.history_duration = 900  # seconds
        self.sensor_history = {
            'wind_apparent_speed': [],
            'wind_apparent_angle': [],
            'wind_true_speed': [],
            'wind_true_direction': [],
            'temperature': [],
            'humidity': [],
            'pressure': [],
            'heading': [],
            'pitch': [],
            'roll': [],
            'rate_of_turn': [],
            'gps_speed': [],
            'gps_course': [],
            'satellites': []
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
                # Streaming is always started in connect_serial on success
            else:
                self.app_logger.error(f"Failed to restore previous connection: {message}")

    def load_state(self):
        """Load saved state from file"""
        try:
            if self.state_path.exists():
                with open(self.state_path, 'r') as f:
                    loaded = json.load(f)
                self.state.update(loaded)
                if 'sentence_config' not in self.state or not isinstance(self.state['sentence_config'], dict):
                    self.state['sentence_config'] = {}
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
            if 'sentence_config' not in self.state:
                self.state['sentence_config'] = {}
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

    def _split_nmea_sentences(self, data):
        """Split a read buffer into individual NMEA sentences (one per yield).
        Handles multiple sentences in one read (e.g. $HCHDG,...*7C$WIMWV,...*2C) or
        multiple lines (e.g. $HCHDG,...\\r\\n$WIMWV,...)."""
        if not data or '$' not in data:
            return
        # Normalize line breaks and split into lines
        for line in re.split(r'[\r\n]+', data):
            line = line.strip()
            if not line:
                continue
            # One line may contain multiple $... sentences if device concatenated them
            for part in line.split('$'):
                part = part.strip()
                if not part or ',' not in part:
                    continue
                yield '$' + part

    def _map_msg_to_sentence_id(self, raw_line, msg_type):
        """Map an incoming NMEA msg to a device sentence_id (SUPPORTED_SENTENCES key).
        Returns None if no reliable mapping exists."""
        try:
            if not msg_type:
                return None

            # WIMWV maps to MWVR/MWVT depending on R/T field
            if msg_type == 'WIMWV':
                fields = raw_line.split('*')[0].split(',')
                ref = fields[2] if len(fields) > 2 else ''
                if ref == 'T':
                    return 'MWVT'
                # Default to apparent/relative
                return 'MWVR'

            # YXXDR is the XDR sentence; for our dashboard we use pitch/roll (type B)
            if msg_type == 'YXXDR':
                fields = raw_line.split('*')[0].split(',')
                names = set()
                i = 1
                while i + 3 < len(fields):
                    name = fields[i + 3]
                    if name:
                        names.add(name)
                    i += 4
                if 'PTCH' in names or 'ROLL' in names:
                    return 'XDRB'
                return None

            # Most sentences map by the 3-letter sentence code (talker prefix ignored)
            code = msg_type[-3:] if len(msg_type) >= 3 else None
            if code and code in self.SUPPORTED_SENTENCES:
                return code
            return None
        except Exception:
            return None

    def _nmea_checksum_ok(self, raw_line):
        """Return True if checksum matches, False if mismatch, None if no checksum present."""
        try:
            if not raw_line or '*' not in raw_line or not raw_line.startswith('$'):
                return None
            body, chk = raw_line[1:].split('*', 1)
            chk = chk.strip()
            if len(chk) < 2:
                return None
            expected = int(chk[:2], 16)
            calc = 0
            for b in body.encode('ascii', errors='ignore'):
                calc ^= b
            return calc == expected
        except Exception:
            return None

    def get_serial_health(self):
        """Return current serial health/debug stats."""
        with self._serial_health_lock:
            h = dict(self.serial_health)
        now = time.time()
        h['seconds_since_last_good_nmea'] = (now - h['last_good_nmea_ts']) if h.get('last_good_nmea_ts') else None
        h['connected_since'] = self.connected_since
        h['port'] = self.state.get('port')
        h['baud_rate'] = self.state.get('baud_rate')
        return h

    def _read_serial_loop(self):
        """Background thread function for reading serial data. Processes messages as fast as
        they arrive; only sleeps when read returns no data to avoid busy-loop on USB quirks."""
        while not self.should_stop:
            if self.serial_connection and self.serial_connection.is_open:
                got_data = False
                try:
                    with self._serial_health_lock:
                        self.serial_health['last_read_attempt_ts'] = time.time()
                    with self._serial_lock:
                        raw = self.serial_connection.readline().decode('utf-8', errors='ignore').strip()
                        try:
                            with self._serial_health_lock:
                                self.serial_health['last_in_waiting'] = getattr(self.serial_connection, 'in_waiting', None)
                        except Exception:
                            pass
                    with self._serial_health_lock:
                        self.serial_health['last_raw_len'] = len(raw) if raw else 0
                        if not raw:
                            self.serial_health['read_timeouts'] += 1
                    for data in self._split_nmea_sentences(raw):
                        got_data = True
                        self.messages_received += 1
                        # Parse NMEA message type (letters only; avoids HCHDG31.0 from truncated lines)
                        raw_type = data.split(',')[0].lstrip('$').strip()
                        m = re.match(r'^[A-Z]+', raw_type) if raw_type else None
                        msg_type = m.group(0) if m else (raw_type or '')
                        self.nmea_messages.add(msg_type)
                        # Checksum quality metrics (helps spot flaky links)
                        chk_ok = self._nmea_checksum_ok(data)
                        with self._serial_health_lock:
                            if chk_ok is True:
                                self.serial_health['last_good_nmea_ts'] = time.time()
                            elif chk_ok is False:
                                self.serial_health['checksum_mismatch'] += 1
                                # still treat as received; it's useful for diagnosis
                                self.serial_health['last_good_nmea_ts'] = time.time()
                            else:
                                self.serial_health['checksum_missing'] += 1
                                self.serial_health['last_good_nmea_ts'] = time.time()
                        # Track sentence last-seen for UI auto-sync
                        sentence_id = self._map_msg_to_sentence_id(data, msg_type)
                        if sentence_id:
                            self.sentence_last_seen[sentence_id] = time.time()
                        else:
                            with self._serial_health_lock:
                                self.serial_health['unmapped_messages'] += 1
                                self.serial_health['last_unmapped_type'] = msg_type
                        # New message types are selected for streaming by default
                        if msg_type not in self.selected_message_types:
                            self.selected_message_types.add(msg_type)
                            self.state['selected_message_types'] = list(self.selected_message_types)
                            self.save_state()
                        
                        # Update aggregated sensor data
                        self._parse_nmea_for_dashboard(data, msg_type)
                        
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
                    err_str = str(e)
                    # Throttle "read but no data" to avoid log flood and possible I/O contention
                    if "returned no data" in err_str or "multiple access" in err_str:
                        with self._serial_health_lock:
                            self.serial_health['nodata_exceptions'] += 1
                        now = time.time()
                        if now - self._last_serial_read_error_log >= 30:
                            self._last_serial_read_error_log = now
                            health = self.get_serial_health()
                            self.app_logger.warning(
                                "Serial read: %s (throttled). port=%s baud=%s in_waiting=%s "
                                "since_last_good_nmea=%s timeouts=%s empty=%s nodata_ex=%s cksum_mismatch=%s",
                                err_str,
                                health.get('port'),
                                health.get('baud_rate'),
                                health.get('last_in_waiting'),
                                health.get('seconds_since_last_good_nmea'),
                                health.get('read_timeouts'),
                                health.get('empty_reads'),
                                health.get('nodata_exceptions'),
                                health.get('checksum_mismatch'),
                            )
                        time.sleep(0.02)  # Brief sleep only on spurious read so we don't tight-loop
                    else:
                        with self._serial_health_lock:
                            self.serial_health['other_read_exceptions'] += 1
                        self.app_logger.error(f"Error in serial reader thread: {e}")
                if not got_data and not self.should_stop:
                    # No message this iteration (timeout or empty line); short sleep to avoid busy-wait
                    with self._serial_health_lock:
                        self.serial_health['empty_reads'] += 1
                    time.sleep(0.01)
            else:
                time.sleep(0.2)  # Not connected; sleep before rechecking

    def _parse_nmea_for_dashboard(self, raw_data, msg_type):
        """
        Parse NMEA message and update aggregated sensor data for dashboard.
        """
        try:
            # Remove checksum if present
            data = raw_data.split('*')[0]
            fields = data.split(',')
            timestamp = datetime.datetime.now().isoformat()
            
            # WIMWV - Wind Speed and Angle (Relative or True)
            if msg_type == 'WIMWV':
                # $WIMWV,angle,R/T,speed,unit,status*CC
                if len(fields) >= 6 and fields[5] == 'A':  # Valid data
                    angle = float(fields[1]) if fields[1] else None
                    speed = float(fields[3]) if fields[3] else None
                    reference = fields[2]  # R=Relative, T=True/Theoretical
                    
                    if reference == 'R':
                        self.sensor_data['wind_apparent']['angle'] = angle
                        self.sensor_data['wind_apparent']['speed_kts'] = speed
                        self.sensor_data['wind_apparent']['source'] = 'WIMWV'
                        self.sensor_data['wind_apparent']['timestamp'] = timestamp
                        self._record_history('wind_apparent_speed', speed)
                        self._record_history('wind_apparent_angle', angle)
                    elif reference == 'T':
                        self.sensor_data['wind_true']['angle'] = angle
                        self.sensor_data['wind_true']['speed_kts'] = speed
                        self.sensor_data['wind_true']['source'] = 'WIMWV'
                        self.sensor_data['wind_true']['timestamp'] = timestamp
                        self._record_history('wind_true_speed', speed)
            
            # WIMWD - Wind Direction and Speed (True, relative to north)
            elif msg_type == 'WIMWD':
                # $WIMWD,dir_true,T,dir_mag,M,speed_kts,N,speed_ms,M*CC
                if len(fields) >= 8:
                    dir_true = float(fields[1]) if fields[1] else None
                    dir_mag = float(fields[3]) if fields[3] else None
                    speed_kts = float(fields[5]) if fields[5] else None
                    
                    self.sensor_data['wind_true']['direction_true'] = dir_true
                    self.sensor_data['wind_true']['direction_magnetic'] = dir_mag
                    if speed_kts is not None:
                        self.sensor_data['wind_true']['speed_kts'] = speed_kts
                    self.sensor_data['wind_true']['source'] = 'WIMWD'
                    self.sensor_data['wind_true']['timestamp'] = timestamp
                    self._record_history('wind_true_direction', dir_true)
                    self._record_history('wind_true_speed', speed_kts)
            
            # WIMDA - Meteorological Composite
            elif msg_type == 'WIMDA':
                # $WIMDA,baro_in,I,baro_bar,B,air_temp,C,water_temp,C,humidity,%,dew_point,C,...
                if len(fields) >= 12:
                    pressure_bar = float(fields[3]) if fields[3] else None
                    temp_c = float(fields[5]) if fields[5] else None
                    humidity = float(fields[9]) if fields[9] else None
                    dew_point = float(fields[11]) if fields[11] else None
                    
                    self.sensor_data['atmosphere']['pressure_bar'] = pressure_bar
                    self.sensor_data['atmosphere']['temperature_c'] = temp_c
                    self.sensor_data['atmosphere']['humidity_pct'] = humidity
                    self.sensor_data['atmosphere']['dew_point_c'] = dew_point
                    self.sensor_data['atmosphere']['source'] = 'WIMDA'
                    self.sensor_data['atmosphere']['timestamp'] = timestamp
                    self._record_history('temperature', temp_c)
                    self._record_history('humidity', humidity)
                    self._record_history('pressure', pressure_bar)
            
            # HCHDT - Heading True
            elif msg_type == 'HCHDT':
                # $HCHDT,heading,T*CC
                if len(fields) >= 2 and fields[1]:
                    heading = float(fields[1])
                    self.sensor_data['attitude']['heading_true'] = heading
                    self.sensor_data['attitude']['source'] = 'HCHDT'
                    self.sensor_data['attitude']['timestamp'] = timestamp
                    self._record_history('heading', heading)
            
            # HCHDG / CHDG - Heading Magnetic
            elif msg_type in ('HCHDG', 'CHDG'):
                # $HCHDG,heading,dev,E/W,var,E/W*CC
                if len(fields) >= 2 and fields[1]:
                    heading = float(fields[1])
                    self.sensor_data['attitude']['heading_magnetic'] = heading
                    if self.sensor_data['attitude']['source'] != 'HCHDT':
                        self.sensor_data['attitude']['source'] = msg_type
                        self.sensor_data['attitude']['timestamp'] = timestamp
            
            # YXXDR - Transducer Measurements (Pitch/Roll from type B)
            elif msg_type == 'YXXDR':
                # Parse in groups of 4: type, value, unit, name
                i = 1
                while i + 3 < len(fields):
                    name = fields[i + 3] if i + 3 < len(fields) else ''
                    value = fields[i + 1] if i + 1 < len(fields) and fields[i + 1] else None
                    
                    if value is not None:
                        value = float(value)
                        if name == 'PTCH':
                            self.sensor_data['attitude']['pitch_deg'] = value
                            self.sensor_data['attitude']['source'] = 'YXXDR'
                            self.sensor_data['attitude']['timestamp'] = timestamp
                            self._record_history('pitch', value)
                        elif name == 'ROLL':
                            self.sensor_data['attitude']['roll_deg'] = value
                            self.sensor_data['attitude']['source'] = 'YXXDR'
                            self.sensor_data['attitude']['timestamp'] = timestamp
                            self._record_history('roll', value)
                    i += 4
            
            # TIROT - Rate of Turn
            elif msg_type == 'TIROT':
                # $TIROT,rate,status*CC
                if len(fields) >= 3 and fields[2] == 'A' and fields[1]:
                    rate = float(fields[1])
                    self.sensor_data['attitude']['rate_of_turn'] = rate
                    self._record_history('rate_of_turn', rate)
                    if 'source' not in self.sensor_data['attitude'] or self.sensor_data['attitude']['source'] not in ['HCHDT', 'YXXDR']:
                        self.sensor_data['attitude']['source'] = 'TIROT'
                        self.sensor_data['attitude']['timestamp'] = timestamp
            
            # GPGGA - GPS Fix Data
            elif msg_type == 'GPGGA':
                # $GPGGA,time,lat,N/S,lon,E/W,quality,sats,hdop,alt,M,...
                if len(fields) >= 10:
                    lat = self._parse_nmea_coord(fields[2], fields[3]) if fields[2] else None
                    lon = self._parse_nmea_coord(fields[4], fields[5]) if fields[4] else None
                    fix_quality = int(fields[6]) if fields[6] else 0
                    satellites = int(fields[7]) if fields[7] else None
                    altitude = float(fields[9]) if fields[9] else None
                    
                    quality_names = {0: 'Invalid', 1: 'GPS Fix', 2: 'DGPS', 4: 'RTK Fixed', 5: 'RTK Float'}
                    
                    self.sensor_data['gps']['latitude'] = lat
                    self.sensor_data['gps']['longitude'] = lon
                    self.sensor_data['gps']['fix_quality'] = quality_names.get(fix_quality, str(fix_quality))
                    self.sensor_data['gps']['satellites'] = satellites
                    self.sensor_data['gps']['altitude_m'] = altitude
                    self.sensor_data['gps']['source'] = 'GPGGA'
                    self.sensor_data['gps']['timestamp'] = timestamp
                    self._record_history('satellites', satellites)
            
            # GPVTG - Course Over Ground and Ground Speed
            elif msg_type == 'GPVTG':
                # $GPVTG,track_true,T,track_mag,M,speed_kts,N,speed_kmh,K,mode*CC
                if len(fields) >= 6:
                    course = float(fields[1]) if fields[1] else None
                    speed_kts = float(fields[5]) if fields[5] else None
                    
                    self.sensor_data['gps']['course_true'] = course
                    self.sensor_data['gps']['speed_kts'] = speed_kts
                    self._record_history('gps_speed', speed_kts)
                    self._record_history('gps_course', course)
                    if self.sensor_data['gps']['source'] != 'GPGGA':
                        self.sensor_data['gps']['source'] = 'GPVTG'
                        self.sensor_data['gps']['timestamp'] = timestamp
            
            # GPZDA - Time and Date
            elif msg_type == 'GPZDA':
                # $GPZDA,hhmmss,dd,mm,yyyy,tz_h,tz_m*CC
                if len(fields) >= 5:
                    time_str = fields[1]
                    day = fields[2]
                    month = fields[3]
                    year = fields[4]
                    
                    if time_str and len(time_str) >= 6:
                        utc_time = f"{time_str[0:2]}:{time_str[2:4]}:{time_str[4:6]}"
                        self.sensor_data['time']['utc_time'] = utc_time
                    
                    if day and month and year:
                        utc_date = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
                        self.sensor_data['time']['utc_date'] = utc_date
                    
                    self.sensor_data['time']['source'] = 'GPZDA'
                    self.sensor_data['time']['timestamp'] = timestamp
                    
        except Exception as e:
            # Silently ignore parse errors to not spam logs
            pass

    def _record_history(self, key, value):
        """Record a value to the history buffer for sparklines."""
        if value is None:
            return
        
        now = time.time()
        self.sensor_history[key].append({'t': now, 'v': value})
        
        # Prune old entries (older than 15 minutes)
        cutoff = now - self.history_duration
        self.sensor_history[key] = [
            entry for entry in self.sensor_history[key] 
            if entry['t'] >= cutoff
        ]

    def get_sensor_history(self):
        """Return the sensor history for sparklines."""
        # Return history with relative timestamps (seconds ago)
        now = time.time()
        result = {}
        for key, entries in self.sensor_history.items():
            result[key] = [
                {'t': round(now - entry['t']), 'v': entry['v']}
                for entry in entries
            ]
        return result

    def _parse_nmea_coord(self, coord_str, direction):
        """Convert NMEA coordinate (DDMM.MMMM) to decimal degrees."""
        if not coord_str:
            return None
        try:
            # Find decimal point position
            dot_pos = coord_str.index('.')
            degrees = int(coord_str[:dot_pos - 2])
            minutes = float(coord_str[dot_pos - 2:])
            decimal = degrees + (minutes / 60)
            
            if direction in ['S', 'W']:
                decimal = -decimal
            
            return round(decimal, 6)
        except:
            return None

    def get_sensor_data(self):
        """Return the current aggregated sensor data for dashboard."""
        return self.sensor_data

    def start_streaming(self):
        """Start UDP streaming (idempotent: does not reset counter if already streaming)."""
        try:
            if not self.udp_socket:
                self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            if not self.is_streaming:
                self.streamed_messages = 0  # Reset counter only when actually starting
            self.is_streaming = True
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

    def get_device_ids(self):
        """Get mapping of /dev/serial/by-id/ names to their device paths with USB port info"""
        devices = []
        
        # First, build a map of device -> by-path info for USB port location
        by_path_map = {}
        try:
            serial_by_path = Path('/dev/serial/by-path')
            if serial_by_path.exists() and serial_by_path.is_dir():
                for link in serial_by_path.iterdir():
                    try:
                        if link.is_symlink():
                            real_device = str(link.resolve())
                            path_name = link.name
                            usb_port = self._parse_usb_port(path_name)
                            by_path_map[real_device] = {
                                'path_name': path_name,
                                'usb_port': usb_port
                            }
                    except Exception as e:
                        self.app_logger.error(f"Error reading by-path symlink {link}: {e}")
        except Exception as e:
            self.app_logger.error(f"Error reading /dev/serial/by-path: {e}")
        
        # Now read by-id and combine with by-path info
        try:
            serial_by_id = Path('/dev/serial/by-id')
            if serial_by_id.exists() and serial_by_id.is_dir():
                for link in serial_by_id.iterdir():
                    try:
                        if link.is_symlink():
                            real_device = str(link.resolve())
                            by_id_name = link.name
                            # Clean up name for display
                            display_name = by_id_name.replace('usb-', '').replace('-if00-port0', '').replace('_', ' ')
                            
                            # Get USB port info from by-path map
                            path_info = by_path_map.get(real_device, {})
                            usb_port = path_info.get('usb_port', {'position': 'unknown', 'label': 'Unknown', 'type': 'unknown'})
                            
                            devices.append({
                                'device': real_device,
                                'by_id_name': by_id_name,
                                'display_name': display_name,
                                'usb_port': usb_port,
                                'path_name': path_info.get('path_name', '')
                            })
                    except Exception as e:
                        self.app_logger.error(f"Error reading symlink {link}: {e}")
        except Exception as e:
            self.app_logger.error(f"Error reading /dev/serial/by-id: {e}")
        
        # Sort by device name for consistent ordering
        devices.sort(key=lambda x: x['device'])
        return devices

    # BlueOS-style path-to-position map (PR 3403). Keys are substrings to match in the
    # normalized by-path name; sorted longest-first so e.g. 1.1.3 matches before 1.3.
    # Layout: top-left, top-right / bottom-left, bottom-right (ethernet on left).
    _USB_PATH_POSITION_MAP = sorted([
        # Pi4 - full path keys (BlueOS convention)
        ('platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.3', 'top-left'),
        ('platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.4', 'bottom-left'),
        ('platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.1', 'top-right'),
        ('platform-fd500000.pcie-pci-0000:01:00.0-usb-0:1.2', 'bottom-right'),
        # Pi4 - kernel 1.1.x style (must match before single 1.1 / 1.2 / 1.3)
        ('-usb-0:1.1.3', 'top-left'),
        ('-usb-0:1.1.2', 'bottom-left'),
        ('-usb-0:1.1.4', 'bottom-left'),
        ('-usb-0:1.3:', 'top-right'),
        ('-usb-0:1.3-', 'top-right'),
        ('-usb-0:1.2:', 'bottom-right'),
        ('-usb-0:1.2-', 'bottom-right'),
        ('-usb-0:1.4', 'bottom-left'),
        ('-usb-0:1.1', 'top-right'),
        # Pi3
        ('platform-3f980000.usb-usb-0:1.5:1', 'bottom-right'),
        ('platform-3f980000.usb-usb-0:1.4:1', 'top-right'),
        ('platform-3f980000.usb-usb-0:1.3:1', 'bottom-left'),
        ('platform-3f980000.usb-usb-0:1.2:1', 'top-left'),
        # Pi5
        ('platform-xhci-hcd.1-usb-0:2', 'bottom-right'),
        ('platform-xhci-hcd.0-usb-0:2', 'top-right'),
        ('platform-xhci-hcd.1-usb-0:1', 'top-left'),
        ('platform-xhci-hcd.0-usb-0:1', 'bottom-left'),
    ], key=lambda p: -len(p[0]))

    def _parse_usb_port(self, path_name):
        """
        Parse the by-path symlink name to physical USB port position (BlueOS-style).
        Uses prefix/contains matching so path format variations still resolve.
        Detects hub connections and returns overlay info when present.
        """
        try:
            import re
            # Normalize like BlueOS: strip -port0 so we match on the USB root
            usb_root = path_name.split('-port0')[0] if path_name else ''
            # Extract bus path for display (e.g. 0:1.1.3)
            bus_match = re.search(r'usb-(\d+:\d+(?:\.\d+)*)', path_name or '')
            bus_path = bus_match.group(1) if bus_match else (usb_root or '')

            # Hub detection (BlueOS overlay): ...usb-0:1.4.3:1.0... -> "hub port 3"
            hub_info = None
            hub_match = re.search(r'usb-0:(?:[0-9]+\.)+([0-9]+):1\.0', path_name or '')
            if hub_match:
                hub_info = f"Via hub, port {hub_match.group(1)}"

            # First matching key wins (map is longest-first for specificity)
            for key, position in self._USB_PATH_POSITION_MAP:
                if key in usb_root:
                    label = position.replace('-', ' ').title()
                    result = {
                        'position': position,
                        'label': label,
                        'type': 'usb3' if 'right' in position else 'usb2',  # Pi4: right = USB3
                        'bus': bus_path,
                    }
                    if hub_info:
                        result['hub_info'] = hub_info
                    self.app_logger.info(f"Parsing USB path: {path_name} -> {position} ({bus_path})")
                    return result

            self.app_logger.info(f"Parsing USB path: {path_name} -> no match (usb_root={usb_root})")
            result = {'position': 'unknown', 'label': 'Unknown', 'type': 'unknown', 'bus': bus_path}
            if hub_info:
                result['hub_info'] = hub_info
            return result
        except Exception as e:
            self.app_logger.error(f"Error parsing USB port from {path_name}: {e}")
            return {'position': 'unknown', 'label': 'Unknown', 'type': 'unknown', 'bus': ''}

    def _try_baud_rate(self, port, baud_rate, timeout=3):
        """
        Try to connect at a specific baud rate and wait for valid NMEA data.
        Sends enable periodic ($PAMTX,1) as soon as the port is open so a stopped
        device can start sending before we check for incoming data.
        At 4800 baud we require at least 5 messages before considering connected.
        Returns (success, serial_connection or None)
        """
        min_messages = 5 if baud_rate == 4800 else 1
        try:
            self.app_logger.info(f"Trying {port} at {baud_rate} baud...")
            conn = serial.Serial(
                port=port,
                baudrate=baud_rate,
                timeout=1,
                exclusive=True
            )
            # Start periodic messages before checking for data (device may have been stopped)
            conn.write(b'$PAMTX,1\r\n')
            time.sleep(0.3)
            
            start_time = time.time()
            valid_count = 0
            while time.time() - start_time < timeout:
                try:
                    data = conn.readline().decode('utf-8', errors='ignore').strip()
                    if data.startswith('$'):
                        valid_count += 1
                        msg_type = data.split(',')[0][1:]
                        self.app_logger.info(f"Received valid NMEA at {baud_rate} baud: {msg_type} ({valid_count}/{min_messages})")
                        if valid_count >= min_messages:
                            return True, conn
                except Exception as e:
                    self.app_logger.error(f"Error reading at {baud_rate} baud: {e}")
                time.sleep(0.1)
            
            # Not enough valid data received, close connection
            conn.close()
            return False, None
            
        except Exception as e:
            self.app_logger.error(f"Failed to open port at {baud_rate} baud: {e}")
            return False, None

    def _switch_to_38400(self, port):
        """
        Switch the device from 4800 to 38400 baud.
        Assumes we're currently connected at 4800 baud. After sending the baud change
        command we keep reading at 4800; when messages stop or become garbled we
        close and reopen at 38400.
        Returns (success, message)
        """
        try:
            self.connection_status = self.CONN_STATUS_SWITCHING_BAUD
            self.connection_message = 'Switching to 38400 baud...'
            
            # Step 1: Ensure periodic sentences are on at 4800 (device may have been stopped)
            self.app_logger.info("Enabling periodic sentences at 4800 baud")
            self.serial_connection.write(b'$PAMTX,1\r\n')
            time.sleep(0.3)
            
            # Step 2: Send baud rate change command (device keeps sending at 4800 until it switches)
            self.app_logger.info("Sending baud rate change command to 38400")
            self.serial_connection.write(b'$PAMTC,BAUD,38400\r\n')
            time.sleep(1)
            
            # Step 3: Continue reading at 4800 until messages stop or become garbled
            self.app_logger.info("Watching for device baud switch (messages stop or garbled at 4800)...")
            self.serial_connection.timeout = 0.5
            no_data_deadline = time.time() + 2.5  # if no data for 2.5s, assume switched
            while time.time() < no_data_deadline:
                raw = self.serial_connection.readline()
                if raw:
                    no_data_deadline = time.time() + 2.5  # got something, extend deadline
                    line = raw.decode('utf-8', errors='ignore').strip()
                    if line and not line.startswith('$'):
                        self.app_logger.info("Device appears to have switched (garbled at 4800)")
                        break
                time.sleep(0.05)
            self.serial_connection.timeout = 1
            
            # Step 4: Close and reopen at 38400
            self.serial_connection.close()
            time.sleep(0.5)
            
            self.app_logger.info("Reopening connection at 38400 baud")
            self.serial_connection = serial.Serial(
                port=port,
                baudrate=38400,
                timeout=1,
                exclusive=True
            )
            self.state['baud_rate'] = 38400
            time.sleep(0.5)
            
            # Step 5: Verify we're receiving valid NMEA at 38400
            start_time = time.time()
            while time.time() - start_time < 5:
                data = self.serial_connection.readline().decode('utf-8', errors='ignore').strip()
                if data.startswith('$'):
                    self.app_logger.info("Confirmed communication at 38400 baud")
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
        Always enables periodic transmission first in case the device was previously stopped.
        Returns (success, message)
        """
        try:
            self.connection_status = self.CONN_STATUS_ENABLING_SENTENCES
            self.connection_message = 'Enabling required NMEA sentences...'
            
            if not self.serial_connection or not self.serial_connection.is_open:
                return False, "Not connected"
            
            # Ensure periodic sentences are on (device may have been stopped by a previous session)
            self.app_logger.info("Enabling periodic sentences")
            self.serial_connection.write(b'$PAMTX,1\r\n')
            time.sleep(0.3)
            
            enabled_count = 0
            for sentence_id in self.REQUIRED_SENTENCES:
                config = self.SUPPORTED_SENTENCES.get(sentence_id, {})
                interval = config.get('default_interval', 10)
                description = config.get('description', sentence_id)
                
                # Format: $PAMTC,EN,<id>,<enable>,<interval>
                # interval is in tenths of a second (10 = 1 second)
                cmd = f'$PAMTC,EN,{sentence_id},1,{interval}\r\n'
                self.app_logger.info(f"Enabling sentence {sentence_id}: {description}")
                self.serial_connection.write(cmd.encode())
                time.sleep(0.2)  # Small delay between commands
                enabled_count += 1
            
            self.app_logger.info(f"Enabled {enabled_count} required NMEA sentences")
            return True, f"Enabled {enabled_count} sentences"
            
        except Exception as e:
            self.app_logger.error(f"Error enabling sentences: {e}")
            return False, str(e)

    def configure_sentence(self, sentence_id, enabled, interval=None):
        """
        Configure a single NMEA sentence on the device.
        
        Args:
            sentence_id: The sentence identifier (e.g., 'MWD', 'XDRA')
            enabled: True to enable, False to disable
            interval: Transmission interval in tenths of seconds (default: 10 = 1 second)
        
        Returns (success, message)
        """
        try:
            if not self.serial_connection or not self.serial_connection.is_open:
                return False, "Not connected"
            
            if sentence_id not in self.SUPPORTED_SENTENCES:
                return False, f"Unknown sentence: {sentence_id}"
            
            if interval is None:
                interval = self.SUPPORTED_SENTENCES[sentence_id].get('default_interval', 10)
            
            enable_flag = 1 if enabled else 0
            cmd = f'$PAMTC,EN,{sentence_id},{enable_flag},{interval}\r\n'
            
            self.app_logger.info(f"Configuring sentence {sentence_id}: enabled={enabled}, interval={interval/10}s")
            with self._serial_lock:
                self.serial_connection.write(cmd.encode())
                time.sleep(0.2)
            
            if 'sentence_config' not in self.state:
                self.state['sentence_config'] = {}
            self.state['sentence_config'][sentence_id] = {'enabled': enabled, 'interval': interval}
            self.save_state()
            action = "Enabled" if enabled else "Disabled"
            return True, f"{action} {sentence_id}"
            
        except Exception as e:
            self.app_logger.error(f"Error configuring sentence {sentence_id}: {e}")
            return False, str(e)

    def configure_sentences_batch(self, changes):
        """Configure multiple sentences in one locked session.
        changes: list of dicts: {sentence_id, enabled, interval} where interval is tenths-of-seconds or None."""
        try:
            if not self.serial_connection or not self.serial_connection.is_open:
                return False, "Not connected"
            if not changes:
                return True, "No changes to apply"
            errors = []
            applied = 0
            with self._serial_lock:
                for ch in changes:
                    sentence_id = ch.get('sentence_id')
                    enabled = bool(ch.get('enabled', True))
                    interval = ch.get('interval', None)
                    if not sentence_id or sentence_id not in self.SUPPORTED_SENTENCES:
                        errors.append(f"{sentence_id or '(missing id)'}: unknown sentence")
                        continue
                    if interval is None:
                        interval = self.SUPPORTED_SENTENCES[sentence_id].get('default_interval', 10)
                    enable_flag = 1 if enabled else 0
                    cmd = f'$PAMTC,EN,{sentence_id},{enable_flag},{interval}\r\n'
                    try:
                        self.serial_connection.write(cmd.encode())
                        if 'sentence_config' not in self.state:
                            self.state['sentence_config'] = {}
                        self.state['sentence_config'][sentence_id] = {'enabled': enabled, 'interval': interval}
                        applied += 1
                        time.sleep(0.15)
                    except Exception as e:
                        errors.append(f"{sentence_id}: {e}")
            if applied > 0:
                self.save_state()
            if errors:
                self.app_logger.warning(f"Batch configure had errors: {errors}")
                return False, f"Applied {applied}/{len(changes)} changes; errors: " + "; ".join(errors[:3])
            return True, f"Applied {applied} sentence changes"
        except Exception as e:
            self.app_logger.error(f"Error batch configuring sentences: {e}")
            return False, str(e)

    def query_sentence_config(self):
        """
        Query the device for current sentence configuration.
        Sends $PAMTC,EN,Q and parses the response.
        Holds the serial read lock so the reader thread does not consume response lines.
        
        Returns (success, config_dict or error_message)
        """
        try:
            if not self.serial_connection or not self.serial_connection.is_open:
                return False, "Not connected"
            
            with self._serial_lock:
                # Clear any pending data
                self.serial_connection.reset_input_buffer()
                
                # Send query command
                self.app_logger.info("Querying device sentence configuration")
                self.serial_connection.write(b'$PAMTC,EN,Q\r\n')
                
                # Collect responses (device sends multiple $PAMTR,EN lines)
                config = {}
                start_time = time.time()
                timeout = 5  # 5 second timeout
                
                while time.time() - start_time < timeout:
                    try:
                        line = self.serial_connection.readline().decode('utf-8', errors='ignore').strip()
                    except Exception as read_err:
                        # USB serial often raises "returned no data" spuriously; retry
                        if "returned no data" in str(read_err) or "multiple access" in str(read_err):
                            time.sleep(0.05)
                            continue
                        raise
                    if not line:
                        time.sleep(0.05)
                        continue
                    if line.startswith('$PAMTR,EN,'):
                        # Parse: $PAMTR,EN,<total>,<num>,<id>,<enabled>,<interval> or $PAMTR,EN,<id>,<enabled>,<interval>
                        parts = line.split(',')
                        if len(parts) >= 6:
                            # If 7+ parts: total,num,id,enabled,interval at 2,3,4,5,6
                            if len(parts) >= 7 and parts[4] in self.SUPPORTED_SENTENCES:
                                sentence_id, enabled_str, interval_str = parts[4], parts[5], parts[6]
                            else:
                                sentence_id, enabled_str, interval_str = parts[3], parts[4], parts[5]
                            enabled = enabled_str == '1'
                            interval = int(interval_str) if interval_str.isdigit() else 10
                            if sentence_id in self.SUPPORTED_SENTENCES:
                                config[sentence_id] = {'enabled': enabled, 'interval': interval}
                                self.app_logger.info(f"  {sentence_id}: enabled={enabled}, interval={interval/10}s")
                    
                    if len(config) >= len(self.SUPPORTED_SENTENCES):
                        break
                    time.sleep(0.05)
            
            if config:
                return True, config
            else:
                return False, "No configuration response received"
            
        except Exception as e:
            self.app_logger.error(f"Error querying sentence config: {e}")
            return False, str(e)

    def save_sentence_config(self):
        """
        Save current sentence configuration to device EEPROM.
        Sends $PAMTC,EN,S command.
        
        Returns (success, message)
        """
        try:
            if not self.serial_connection or not self.serial_connection.is_open:
                return False, "Not connected"
            
            self.app_logger.info("Saving sentence configuration to device EEPROM")
            self.serial_connection.write(b'$PAMTC,EN,S\r\n')
            time.sleep(0.5)
            
            return True, "Configuration saved to device EEPROM"
            
        except Exception as e:
            self.app_logger.error(f"Error saving sentence config: {e}")
            return False, str(e)

    def load_sentence_defaults(self):
        """
        Load factory default sentence configuration from device ROM to RAM.
        Sends $PAMTC,EN,LD command.
        
        Returns (success, message)
        """
        try:
            if not self.serial_connection or not self.serial_connection.is_open:
                return False, "Not connected"
            
            self.app_logger.info("Loading factory default sentence configuration")
            self.serial_connection.write(b'$PAMTC,EN,LD\r\n')
            time.sleep(0.5)
            
            return True, "Factory defaults loaded"
            
        except Exception as e:
            self.app_logger.error(f"Error loading sentence defaults: {e}")
            return False, str(e)

    def get_sentences_info(self):
        """
        Get information about all supported sentences.
        Returns list of sentence info dictionaries.
        """
        sentences = []
        for sentence_id, config in self.SUPPORTED_SENTENCES.items():
            sentences.append({
                'id': sentence_id,
                'name': config['name'],
                'description': config['description'],
                'default_enabled': config['default_enabled'],
                'default_interval': config['default_interval'],
                'required': sentence_id in self.REQUIRED_SENTENCES
            })
        
        # Sort by ID
        sentences.sort(key=lambda x: x['id'])
        return sentences

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
            # If caller requests 38400 (e.g. saved state), try 38400 first
            baud_rates = [38400, 4800] if baud_rate == 38400 else [4800, 38400]
            
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
                            # Device may have switched anyway; close and try 38400 next
                            self.app_logger.warning(f"Switch to 38400 failed: {msg}; will try 38400 directly")
                            if self.serial_connection and self.serial_connection.is_open:
                                self.serial_connection.close()
                            self.serial_connection = None
                            continue
                    
                    # Now at 38400 baud - enable required sentences
                    success, msg = self.enable_required_sentences()
                    if not success:
                        self.app_logger.warning(f"Failed to enable sentences: {msg}")
                        # Continue anyway - sentences may already be enabled
                    # Apply saved sentence config so device matches last UI state after restart
                    saved = self.state.get('sentence_config') or {}
                    if saved:
                        changes = [{'sentence_id': sid, 'enabled': c['enabled'], 'interval': c.get('interval')}
                                  for sid, c in saved.items() if sid in self.SUPPORTED_SENTENCES]
                        if changes:
                            apply_ok, _ = self.configure_sentences_batch(changes)
                            if apply_ok:
                                self.app_logger.info("Restored saved sentence configuration to device")
                    
                    # Start the reader thread
                    self.start_reader_thread()
                    
                    # Update state
                    self.state['baud_rate'] = 38400
                    self.save_state()
                    self.connected_since = time.time()
                    
                    self.connection_status = self.CONN_STATUS_CONNECTED
                    detected_msg = f" (detected at {self.detected_baud})" if self.detected_baud == 38400 else " (switched from 4800)"
                    self.connection_message = f"Connected to {port} at 38400 baud{detected_msg}"
                    self.app_logger.info(self.connection_message)
                    
                    # Always start streaming on successful connection so the extension can work without opening the UI
                    if not self.selected_message_types:
                        default_types = {'HCHDG', 'CHDG', 'HCHDT', 'WIMWD', 'WIMWV', 'GPGGA', 'GPGA', 'WIMDA'}
                        self.selected_message_types = default_types
                        self.save_state()
                    self.start_streaming()
                    
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
            self.sentence_last_seen = {}
            self.connected_since = None
            self.messages_received = 0
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
            'required_sentences': list(self.REQUIRED_SENTENCES),
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
            "available_types": list(self.nmea_messages),
            "now": time.time(),
            "connected_since": self.connected_since,
            "observed_sentence_last_seen": self.sentence_last_seen
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
                timeout=1,
                exclusive=True
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

@app.route('/api/serial/device-ids', methods=['GET'])
def get_device_ids():
    """Get device ID mappings from /dev/serial/by-id/"""
    return jsonify({"devices": nmea_handler.get_device_ids()})

@app.route('/api/serial/select', methods=['POST'])
def select_port():
    """Select and connect to a serial port"""
    data = request.get_json()
    if not data or 'port' not in data:
        return jsonify({"success": False, "message": "No port specified"})
    
    # Prefer saved baud when reconnecting to same port (e.g. after disconnect)
    if data['port'] == nmea_handler.state.get('port'):
        baud_rate = data.get('baud_rate') or nmea_handler.state.get('baud_rate', 4800)
    else:
        baud_rate = data.get('baud_rate', 4800)
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

@app.route('/api/sensor/state', methods=['GET'])
def get_sensor_state():
    """Get aggregated sensor data for dashboard display"""
    return jsonify(nmea_handler.get_sensor_data())

@app.route('/api/sensor/history', methods=['GET'])
def get_sensor_history():
    """Get historical sensor data for sparklines (15 min)"""
    return jsonify(nmea_handler.get_sensor_history())

# ============== Sentence Configuration API ==============

@app.route('/api/sentences', methods=['GET'])
def get_sentences():
    """Get list of all supported NMEA sentences with their info and saved config (persisted across restarts)."""
    return jsonify({
        "sentences": nmea_handler.get_sentences_info(),
        "connected": nmea_handler.serial_connection is not None and nmea_handler.serial_connection.is_open,
        "saved_config": nmea_handler.state.get('sentence_config') or {}
    })

@app.route('/api/sentences/configure', methods=['POST'])
def configure_sentence():
    """Configure a single NMEA sentence (enable/disable, set interval).
    interval: seconds (0.1–5), converted to device tenths-of-seconds internally."""
    data = request.get_json()
    if not data or 'sentence_id' not in data:
        return jsonify({"success": False, "message": "No sentence_id specified"})
    
    sentence_id = data['sentence_id']
    enabled = data.get('enabled', True)
    interval_raw = data.get('interval', None)
    # Convert seconds (0.1–5) to tenths (1–50) if provided
    interval = None
    if interval_raw is not None:
        try:
            sec = float(interval_raw)
            sec = max(0.1, min(5.0, sec))
            interval = int(round(sec * 10))
        except (TypeError, ValueError):
            pass
    
    success, message = nmea_handler.configure_sentence(sentence_id, enabled, interval)
    return jsonify({"success": success, "message": message})

@app.route('/api/sentences/configure-batch', methods=['POST'])
def configure_sentences_batch():
    """Configure multiple NMEA sentences in one call.
    Body: { changes: [{sentence_id, enabled, interval(seconds 0.1–5)}] }"""
    data = request.get_json() or {}
    changes_in = data.get('changes', [])
    if not isinstance(changes_in, list) or not changes_in:
        return jsonify({"success": False, "message": "No changes provided"})
    # Convert interval seconds -> tenths for device
    changes = []
    for ch in changes_in:
        if not isinstance(ch, dict):
            continue
        sentence_id = ch.get('sentence_id')
        enabled = ch.get('enabled', True)
        interval_raw = ch.get('interval', None)
        interval = None
        if interval_raw is not None:
            try:
                sec = float(interval_raw)
                sec = max(0.1, min(5.0, sec))
                interval = int(round(sec * 10))
            except (TypeError, ValueError):
                interval = None
        changes.append({"sentence_id": sentence_id, "enabled": enabled, "interval": interval})
    success, message = nmea_handler.configure_sentences_batch(changes)
    return jsonify({"success": success, "message": message})

@app.route('/api/sentences/query', methods=['POST'])
def query_sentences():
    """Query the device for current sentence configuration"""
    success, result = nmea_handler.query_sentence_config()
    if success:
        return jsonify({"success": True, "config": result})
    else:
        return jsonify({"success": False, "message": result})

@app.route('/api/sentences/save', methods=['POST'])
def save_sentences():
    """Save current sentence configuration to device EEPROM"""
    success, message = nmea_handler.save_sentence_config()
    return jsonify({"success": success, "message": message})

@app.route('/api/sentences/load-defaults', methods=['POST'])
def load_sentence_defaults():
    """Load factory default sentence configuration"""
    success, message = nmea_handler.load_sentence_defaults()
    return jsonify({"success": success, "message": message})

# ============== End Sentence Configuration API ==============

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

@app.route('/api/logs/app', methods=['GET'])
def download_app_logs():
    """Download application log file"""
    app_log_path = Path('/app/logs/nmea_handler.log')
    if not app_log_path.exists():
        return jsonify({
            "success": False,
            "message": "No application log file found."
        }), 404
    return send_file(app_log_path, as_attachment=True)

@app.route('/api/logs/info', methods=['GET'])
def get_logs_info():
    """Get information about log files"""
    log_dir = Path('/app/logs')
    logs = []
    
    for log_file in [nmea_handler.log_path, log_dir / 'nmea_handler.log']:
        if log_file.exists():
            stat = log_file.stat()
            logs.append({
                'name': log_file.name,
                'path': str(log_file),
                'size_bytes': stat.st_size,
                'size_human': _format_size(stat.st_size),
                'modified': datetime.datetime.fromtimestamp(stat.st_mtime).isoformat(),
                'modified_human': _format_time_ago(stat.st_mtime)
            })
        else:
            logs.append({
                'name': log_file.name,
                'path': str(log_file),
                'size_bytes': 0,
                'size_human': '0 B',
                'modified': None,
                'modified_human': 'Not created'
            })
    
    return jsonify({
        'log_directory': str(log_dir),
        'logs': logs
    })

@app.route('/api/logs/preview', methods=['GET'])
def get_log_preview():
    """Get the last N lines of a log file"""
    log_type = request.args.get('type', 'nmea')  # 'nmea' or 'app'
    lines = int(request.args.get('lines', 50))
    
    if log_type == 'nmea':
        log_path = nmea_handler.log_path
    else:
        log_path = Path('/app/logs/nmea_handler.log')
    
    if not log_path.exists():
        return jsonify({'lines': [], 'total_lines': 0})
    
    try:
        with open(log_path, 'r') as f:
            all_lines = f.readlines()
            total = len(all_lines)
            preview_lines = all_lines[-lines:] if total > lines else all_lines
            return jsonify({
                'lines': [line.rstrip() for line in preview_lines],
                'total_lines': total,
                'showing': len(preview_lines)
            })
    except Exception as e:
        return jsonify({'error': str(e), 'lines': [], 'total_lines': 0})

def _format_size(size_bytes):
    """Format bytes to human readable size"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"

def _format_time_ago(timestamp):
    """Format timestamp to human readable time ago"""
    now = datetime.datetime.now()
    dt = datetime.datetime.fromtimestamp(timestamp)
    diff = now - dt
    
    if diff.days > 0:
        return f"{diff.days}d ago"
    elif diff.seconds >= 3600:
        return f"{diff.seconds // 3600}h ago"
    elif diff.seconds >= 60:
        return f"{diff.seconds // 60}m ago"
    else:
        return "Just now"

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
        "streamed_messages": nmea_handler.streamed_messages,
        "messages_received": nmea_handler.messages_received,
        "serial_health": nmea_handler.get_serial_health(),
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
