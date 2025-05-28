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
RUN pip install --no-cache-dir litestar==0.27.0 && \
    pip install --no-cache-dir pyserial==3.5 && \
    pip install --no-cache-dir msgspec==0.18.6 && \
    pip install --no-cache-dir typing-extensions==4.9.0 && \
    pip install --no-cache-dir anyio==4.3.0 && \
    pip install --no-cache-dir click==8.1.7 && \
    pip install --no-cache-dir h11==0.14.0 && \
    pip install --no-cache-dir httptools==0.6.1 && \
    pip install --no-cache-dir python-dotenv==1.0.1 && \
    pip install --no-cache-dir pyyaml==6.0.2 && \
    pip install --no-cache-dir uvicorn==0.27.1

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
