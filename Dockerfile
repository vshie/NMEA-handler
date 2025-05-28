FROM python:3.11-slim-bullseye

# Install minimal system dependencies
RUN apt-get update && apt-get install -y \
    python3-serial \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Create logs directory inside the container
RUN mkdir -p /app/logs && chmod 777 /app/logs

# Copy app files
COPY app/ .

# Install Python dependencies directly (without upgrading pip)
# Install each package separately to avoid hash verification issues
RUN pip install --no-cache-dir flask==2.0.1 && \
    pip install --no-cache-dir pyserial==3.5 && \
    pip install --no-cache-dir requests==2.28.1 && \
    pip install --no-cache-dir Werkzeug==2.0.3 && \
    pip install --no-cache-dir Jinja2==3.0.3 && \
    pip install --no-cache-dir MarkupSafe==2.0.1 && \
    pip install --no-cache-dir itsdangerous==2.0.1

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=app.py

# Expose port
EXPOSE 6436

# Set labels for BlueOS
LABEL org.opencontainers.image.title="NMEA Handler"
LABEL org.opencontainers.image.description="A BlueOS extension for handling NMEA messages from serial devices"
LABEL org.opencontainers.image.vendor="Blue Robotics"

LABEL permissions='\
{\
  "ExposedPorts": {\
    "6436/tcp": {}\
  },\
  "HostConfig": {\
    "CpuPeriod": 100000,\
    "CpuQuota": 100000,\
    "Binds": [\
      "/usr/blueos/extensions/nmea-handler:/app/logs",\
      "/dev/ttyUSB0:/dev/ttyUSB0",\
      "/dev/ttyUSB1:/dev/ttyUSB1",\
      "/dev/ttyUSB2:/dev/ttyUSB2",\
      "/dev/ttyUSB3:/dev/ttyUSB3",\
      "/dev/ttyACM0:/dev/ttyACM0",\
      "/dev/ttyACM1:/dev/ttyACM1"\
    ],\
    "ExtraHosts": ["host.docker.internal:host-gateway"],\
    "PortBindings": {\
      "6436/tcp": [\
        {\
          "HostPort": ""\
        }\
      ]\
    },\
    "NetworkMode": "host",\
    "Privileged": true\
  }\
}'

LABEL type="extension"
LABEL requirements="{}"
LABEL name="nmea_handler"
LABEL description="NMEA Message Handler"
LABEL author="Blue Robotics"
LABEL website="https://github.com/vshie/NMEA-handler"
LABEL icon="mdi-serial-port"
LABEL display_name="NMEA Handler"
LABEL display_icon="mdi-serial-port"
LABEL display_description="Monitor and log NMEA messages from serial devices"
LABEL display_category="Sensors"
LABEL display_order="10"

# Run the application
CMD ["python", "main.py"]
