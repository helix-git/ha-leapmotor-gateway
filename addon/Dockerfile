# Alpine 3.23 ships Python 3.12, the minimum for leapmotor-api. Multi-arch base for CI builds,
# The Supervisor uses this default for local builds as well.
ARG BUILD_FROM=ghcr.io/home-assistant/base:3.23@sha256:1c7a8c7321c15cdc327c264232a76e6fbfdaf7f2b1734a8d8da6fcc994f66015
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8 PYTHONUNBUFFERED=1
RUN apk add --no-cache python3 py3-pip
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --require-hashes --no-cache-dir --break-system-packages -r /tmp/requirements.txt

# The service does not run as root (run.sh starts it with s6-setuidgid).
RUN adduser -D -H -s /sbin/nologin gateway

COPY app /app
COPY run.sh /run.sh
# The integration travels with the app. run.sh installs it (install_integration.sh).
COPY integration /integration
COPY install_integration.sh /install_integration.sh
RUN chmod a+x /run.sh
WORKDIR /app
# If the service stops answering, the Supervisor sees the container unhealthy and restarts it.
HEALTHCHECK --interval=60s --timeout=10s --start-period=60s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8789/health', timeout=5)" || exit 1
CMD [ "/run.sh" ]
