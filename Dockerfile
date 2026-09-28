FROM alexxit/go2rtc:latest AS go2rtc-bin
FROM python:3.11-alpine

# Install v4l-utils for device inspection
RUN apk add --no-cache v4l-utils ca-certificates

WORKDIR /app

# Copy the go2rtc binary from the official image
COPY --from=go2rtc-bin /usr/local/bin/go2rtc /usr/local/bin/go2rtc

# Copy application assets
COPY go2rtc.yaml /app/go2rtc.yaml
COPY web/ /app/web/
COPY entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
