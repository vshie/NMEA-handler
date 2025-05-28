FROM python:3.11-slim-bullseye

# Install minimal system dependencies
RUN apt-get update && apt-get install -y \
    python3-serial \
    && rm -rf /var/lib/apt/lists/*

# Copy application files
WORKDIR /app
COPY app /app

# Install dependencies
RUN pip install litestar pyserial

# Create logs directory inside the container
RUN mkdir -p /app/logs && chmod 777 /app/logs

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 5000

# Set labels for BlueOS
LABEL org.opencontainers.image.title="NMEA Handler"
LABEL org.opencontainers.image.description="A BlueOS extension for handling NMEA messages from serial devices"
LABEL org.opencontainers.image.vendor="Blue Robotics"

LABEL permissions='\
{\
  "ExposedPorts": {\
    "5000/tcp": {}\
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
      "5000/tcp": [\
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
