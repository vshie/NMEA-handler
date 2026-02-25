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
    pip install --no-cache-dir itsdangerous==2.0.1 && \
    pip install --no-cache-dir flask-cors==3.0.10 && \
    pip install --no-cache-dir waitress==2.1.2 && \
    pip install --no-cache-dir websockets

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV FLASK_APP=app.py

# Expose ports (6436 = web UI, 8765 = Cockpit data-lake WebSocket)
EXPOSE 6436
EXPOSE 8765

# BlueOS extension metadata
LABEL org.blueos.type="tool"
LABEL org.blueos.version="0.1"
LABEL org.blueos.requirements="core >= 1.1"
LABEL org.blueos.name="Airmar 300WX"
LABEL org.blueos.description="Airmar 300WX WeatherStation interface for BlueOS"
LABEL org.blueos.icon="mdi-weather-windy"
LABEL org.blueos.category="Sensors"
LABEL org.blueos.order="10"

ARG IMAGE_NAME
LABEL permissions='\
{\
  "ExposedPorts": {\
    "6436/tcp": {},\
    "8765/tcp": {}\
  },\
  "HostConfig": {\
    "CpuPeriod": 100000,\
    "CpuQuota": 100000,\
    "Binds": [\
      "/usr/blueos/extensions/nmea-handler:/app/logs",\
      "/dev:/dev"\
    ],\
    "ExtraHosts": ["host.docker.internal:host-gateway"],\
    "NetworkMode": "host",\
    "Privileged": true\
  }\
}'

ARG AUTHOR
ARG AUTHOR_EMAIL
LABEL org.blueos.authors='[\
    {\
        "name": "Tony White",\
        "email": "tony@bluerobotics.com"\
    }\
]'

ARG MAINTAINER
ARG MAINTAINER_EMAIL
LABEL org.blueos.company='\
{\
    "about": "Airmar 300WX WeatherStation for BlueOS",\
    "name": "Blue Robotics",\
    "email": "support@bluerobotics.com"\
}'

ARG REPO
ARG OWNER
LABEL org.blueos.readme=''
LABEL org.blueos.links='\
{\
    "source": "https://github.com/vshie/NMEA-handler"\
}'

# Run the application
CMD ["python", "main.py"]
