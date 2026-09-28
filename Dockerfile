FROM alexxit/go2rtc:latest AS go2rtc-bin
FROM python:3.11-alpine

# Install v4l-utils and ffmpeg for recording
RUN apk add --no-cache v4l-utils ffmpeg ca-certificates

WORKDIR /app

COPY --from=go2rtc-bin /usr/local/bin/go2rtc /usr/local/bin/go2rtc

COPY go2rtc.yaml /app/go2rtc.yaml
COPY web/ /app/web/
COPY server.py /app/server.py
COPY entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
