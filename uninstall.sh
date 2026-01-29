#!/bin/bash
#
# Modbus Gateway Uninstallation Script
#

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

INSTALL_DIR="/opt/modbus-gateway"
SERVICE_FILE="/etc/systemd/system/modbus-gateway.service"
UDEV_RULE="/etc/udev/rules.d/99-rs485.rules"

echo -e "${YELLOW}================================${NC}"
echo -e "${YELLOW}Modbus Gateway Uninstaller${NC}"
echo -e "${YELLOW}================================${NC}"
echo

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: Please run as root (sudo ./uninstall.sh)${NC}"
    exit 1
fi

# Stop and disable service
echo -e "${YELLOW}Stopping service...${NC}"
systemctl stop modbus-gateway.service 2>/dev/null || true
systemctl disable modbus-gateway.service 2>/dev/null || true
echo -e "${GREEN}Service stopped.${NC}"
echo

# Remove files
echo -e "${YELLOW}Removing files...${NC}"
rm -f "$SERVICE_FILE"
rm -f "$UDEV_RULE"
rm -rf "$INSTALL_DIR"
echo -e "${GREEN}Files removed.${NC}"
echo

# Reload systemd and udev
echo -e "${YELLOW}Reloading systemd and udev...${NC}"
systemctl daemon-reload
udevadm control --reload-rules
echo -e "${GREEN}Reload complete.${NC}"
echo

# Optionally remove user
read -p "Remove 'modbus' user? (y/N): " remove_user
if [[ "$remove_user" =~ ^[Yy]$ ]]; then
    userdel modbus 2>/dev/null || true
    echo -e "${GREEN}User removed.${NC}"
fi
echo

echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}Uninstallation Complete!${NC}"
echo -e "${GREEN}================================${NC}"
