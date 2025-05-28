FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    pyserial \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY app/pyproject.toml .
RUN pip install --no-cache-dir .

# Copy application code
COPY app/ .

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

# Run the application
CMD ["python", "main.py"]
