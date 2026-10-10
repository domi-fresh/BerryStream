FROM alexxit/go2rtc:latest AS go2rtc-bin
FROM python:3.11-alpine

# Install v4l-utils and ffmpeg for recording with automatic retry on transient network/DNS issues
RUN set -e; \
    for i in 1 2 3 4 5; do \
        apk add --no-cache v4l-utils ffmpeg ca-certificates && break || \
        (echo "Transient network/DNS issue during apk add, retrying ($i/5) in 3s..." && sleep 3); \
    done

WORKDIR /app

COPY --from=go2rtc-bin /usr/local/bin/go2rtc /usr/local/bin/go2rtc

COPY go2rtc.yaml /app/go2rtc.yaml
COPY web/ /app/web/
COPY server.py /app/server.py
COPY entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
