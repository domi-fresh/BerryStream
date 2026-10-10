FROM mwader/static-ffmpeg:latest AS ffmpeg-bin
FROM alexxit/go2rtc:latest AS go2rtc-bin
FROM python:3.11-alpine

# Install only lightweight v4l-utils and certificates (<1MB, zero heavy codec dependencies)
RUN apk add --no-cache v4l-utils ca-certificates

WORKDIR /app

# Copy pre-compiled, multi-arch static binaries
COPY --from=go2rtc-bin /usr/local/bin/go2rtc /usr/local/bin/go2rtc
COPY --from=ffmpeg-bin /ffmpeg /usr/local/bin/ffmpeg
COPY --from=ffmpeg-bin /ffprobe /usr/local/bin/ffprobe

COPY go2rtc.yaml /app/go2rtc.yaml
COPY web/ /app/web/
COPY server.py /app/server.py
COPY entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
