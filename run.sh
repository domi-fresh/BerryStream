#!/usr/bin/env bash
# ==============================================================================
# Canopi — All-in-One Automated Setup & Launcher
# Cross-compatible with:
#   - Raspberry Pi 5
#   - Raspberry Pi 4
#   - Raspberry Pi 3 (Model B / B+)
#   - Raspberry Pi Zero 2 / Zero 2W
# ==============================================================================
set -e

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}====================================================${NC}"
echo -e "${BLUE}      Canopi Ultra-Low-Latency Field EVF Setup      ${NC}"
echo -e "${BLUE}====================================================${NC}"

# 1. Require administrative privileges
if [ "$EUID" -ne 0 ]; then
  echo -e "${YELLOW}==> Administrative privileges required. Re-running with sudo...${NC}"
  exec sudo "$0" "$@"
fi

# Locate project directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ARCH="$(uname -m)"
echo -e "${BLUE}==> Detected Hardware Architecture:${NC} ${GREEN}${ARCH}${NC}"

# Detect specific board model
BOARD_MODEL=""
if [ -f /proc/device-tree/model ]; then
  BOARD_MODEL=$(tr -d '\0' < /proc/device-tree/model)
elif [ -f /sys/firmware/devicetree/base/model ]; then
  BOARD_MODEL=$(tr -d '\0' < /sys/firmware/devicetree/base/model)
fi

if [ -n "$BOARD_MODEL" ]; then
  echo -e "${BLUE}==> Detected Board Model:${NC} ${GREEN}${BOARD_MODEL}${NC}"
fi

# Apply safe moderate overclock if board is Raspberry Pi Zero 2 or Zero 2 W
if echo "$BOARD_MODEL" | grep -qi "Zero 2"; then
  echo -e "${YELLOW}==> Raspberry Pi Zero 2 (W) detected. Applying permanent moderate overclock (1.2GHz CPU, 500MHz GPU, over_voltage=2)...${NC}"
  
  # Locate active config.txt (Bookworm uses /boot/firmware/config.txt, older versions use /boot/config.txt)
  BOOT_CONFIG=""
  if [ -f "/boot/firmware/config.txt" ]; then
    BOOT_CONFIG="/boot/firmware/config.txt"
  elif [ -f "/boot/config.txt" ]; then
    BOOT_CONFIG="/boot/config.txt"
  fi

  if [ -n "$BOOT_CONFIG" ]; then
    if grep -q "arm_freq=1200" "$BOOT_CONFIG"; then
      echo -e "${GREEN}==> Overclock settings already present in ${BOOT_CONFIG}.${NC}"
    else
      cat << 'EOF' >> "$BOOT_CONFIG"

# ==============================================================================
# Canopi Performance Tuning (Raspberry Pi Zero 2 / 2W Overclock)
# ==============================================================================
arm_freq=1200
gpu_freq=500
over_voltage=2
EOF
      echo -e "${GREEN}==> Overclock settings successfully configured in ${BOOT_CONFIG} (takes effect upon reboot).${NC}"
    fi
  else
    echo -e "${YELLOW}==> config.txt not found under /boot/firmware or /boot; skipping overclock config.${NC}"
  fi
fi

# 2. Update package lists and install USB tethering dependencies
echo -e "${BLUE}==> Installing host dependencies (usbmuxd, libimobiledevice, v4l-utils)...${NC}"
apt-get update -qq
apt-get install -y --no-install-recommends \
  usbmuxd \
  libimobiledevice6 \
  libimobiledevice-utils \
  v4l-utils \
  curl \
  ca-certificates >/dev/null

# 3. Check and install Docker if not present
if ! command -v docker >/dev/null 2>&1; then
  echo -e "${YELLOW}==> Docker not found. Installing Docker using official automated installer...${NC}"
  curl -fsSL https://get.docker.com | sh
else
  echo -e "${GREEN}==> Docker is already installed.${NC}"
fi

# Ensure Docker service is enabled and started on boot
echo -e "${BLUE}==> Enabling Docker service to start on boot...${NC}"
systemctl enable --now docker >/dev/null 2>&1 || true

# Add the regular user to the docker group if run via sudo
if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
  usermod -aG docker "$SUDO_USER" >/dev/null 2>&1 || true
fi

# 4. Check for Docker Compose (v2 plugin or standalone v1)
DOCKER_COMPOSE_CMD=""
if docker compose version >/dev/null 2>&1; then
  DOCKER_COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DOCKER_COMPOSE_CMD="docker-compose"
else
  echo -e "${YELLOW}==> Installing Docker Compose plugin...${NC}"
  apt-get install -y docker-compose-plugin >/dev/null 2>&1 || apt-get install -y docker-compose >/dev/null 2>&1
  if docker compose version >/dev/null 2>&1; then
    DOCKER_COMPOSE_CMD="docker compose"
  else
    DOCKER_COMPOSE_CMD="docker-compose"
  fi
fi
echo -e "${GREEN}==> Using Compose command:${NC} ${DOCKER_COMPOSE_CMD}"

# 5. Prepare persistent recordings directories
echo -e "${BLUE}==> Ensuring storage directory structure...${NC}"
mkdir -p "$SCRIPT_DIR/recordings/thumbs" "$SCRIPT_DIR/recordings/mp4"
chmod -R 777 "$SCRIPT_DIR/recordings"

# 6. Configure Systemd Service for 100% reliable startup on system boot
SERVICE_FILE="/etc/systemd/system/canopi.service"
echo -e "${BLUE}==> Configuring systemd service (${SERVICE_FILE}) for auto-start on boot...${NC}"

cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=Canopi Ultra-Low-Latency Field EVF Monitor
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=${SCRIPT_DIR}
ExecStart=$(command -v docker) compose up -d
ExecStop=$(command -v docker) compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable canopi.service >/dev/null 2>&1
echo -e "${GREEN}==> Boot auto-start service enabled successfully.${NC}"

# 7. Build and launch container
echo -e "${BLUE}==> Building and launching Canopi container...${NC}"
$DOCKER_COMPOSE_CMD up -d --build

# 8. Success Banner & Instructions
IP_HINT=$(ip -4 addr show usb0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' || echo "172.20.10.2")

echo ""
echo -e "${GREEN}====================================================${NC}"
echo -e "${GREEN}       ✓ Canopi is Ready & Running!                ${NC}"
echo -e "${GREEN}====================================================${NC}"
echo ""
echo -e "1. Connect your iPhone via Lightning/USB-C to the Raspberry Pi."
echo -e "2. Enable ${YELLOW}Personal Hotspot${NC} (select ${YELLOW}USB Only${NC} if prompted)."
echo -e "3. Open Mobile Safari on your iPhone and go to:"
echo ""
echo -e "      ${BLUE}http://${IP_HINT}:8000${NC}"
echo ""
echo -e "   (Alternative direct port if using Wi-Fi: http://<pi-ip>:8000)"
echo ""
echo -e "Tip: In Safari, tap '${YELLOW}Share -> Add to Home Screen${NC}' for a full-screen borderless OLED viewfinder."
echo -e "${GREEN}Canopi will now automatically start on every system boot.${NC}"
echo ""
