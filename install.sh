#!/bin/bash
#
# Modbus Gateway Installation Script
# For Raspberry Pi / Linux with systemd
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

INSTALL_DIR="/opt/modbus-gateway"
SERVICE_FILE="/etc/systemd/system/modbus-gateway.service"
UDEV_RULE="/etc/udev/rules.d/99-rs485.rules"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}Modbus Gateway Installer${NC}"
echo -e "${GREEN}================================${NC}"
echo

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Error: Please run as root (sudo ./install.sh)${NC}"
    exit 1
fi

# Check for required files
echo -e "${YELLOW}Checking required files...${NC}"
for file in modbus-gateway.py modbus-gateway.cfg modbus-gateway.service 99-rs485.rules; do
    if [ ! -f "$SCRIPT_DIR/$file" ]; then
        echo -e "${RED}Error: Missing $file${NC}"
        exit 1
    fi
done
echo -e "${GREEN}All files found.${NC}"
echo

# Install Python dependencies
echo -e "${YELLOW}Installing Python dependencies...${NC}"
if command -v pip3 &> /dev/null; then
    pip3 install pyserial
elif command -v pip &> /dev/null; then
    pip install pyserial
else
    echo -e "${RED}Error: pip not found. Install python3-pip first.${NC}"
    exit 1
fi
echo -e "${GREEN}Dependencies installed.${NC}"
echo

# Create modbus user if it doesn't exist
echo -e "${YELLOW}Creating modbus user...${NC}"
if ! id "modbus" &>/dev/null; then
    useradd -r -s /bin/false -G dialout modbus
    echo -e "${GREEN}User 'modbus' created.${NC}"
else
    # Ensure user is in dialout group
    usermod -a -G dialout modbus
    echo -e "${GREEN}User 'modbus' already exists.${NC}"
fi
echo

# Create installation directory
echo -e "${YELLOW}Creating installation directory...${NC}"
mkdir -p "$INSTALL_DIR"
echo -e "${GREEN}Directory created: $INSTALL_DIR${NC}"
echo

# Copy files
echo -e "${YELLOW}Copying files...${NC}"
cp "$SCRIPT_DIR/modbus-gateway.py" "$INSTALL_DIR/"
cp "$SCRIPT_DIR/modbus-gateway.cfg" "$INSTALL_DIR/"
chown -R modbus:dialout "$INSTALL_DIR"
chmod 755 "$INSTALL_DIR/modbus-gateway.py"
chmod 644 "$INSTALL_DIR/modbus-gateway.cfg"
echo -e "${GREEN}Files copied to $INSTALL_DIR${NC}"
echo

# Install systemd service
echo -e "${YELLOW}Installing systemd service...${NC}"
cp "$SCRIPT_DIR/modbus-gateway.service" "$SERVICE_FILE"
chmod 644 "$SERVICE_FILE"
echo -e "${GREEN}Service installed.${NC}"
echo

# Install udev rule
echo -e "${YELLOW}Installing udev rule...${NC}"
cp "$SCRIPT_DIR/99-rs485.rules" "$UDEV_RULE"
chmod 644 "$UDEV_RULE"
echo -e "${GREEN}Udev rule installed.${NC}"
echo

# Reload systemd and udev
echo -e "${YELLOW}Reloading systemd and udev...${NC}"
systemctl daemon-reload
udevadm control --reload-rules
udevadm trigger
echo -e "${GREEN}Reload complete.${NC}"
echo

# Enable service
echo -e "${YELLOW}Enabling service...${NC}"
systemctl enable modbus-gateway.service
echo -e "${GREEN}Service enabled.${NC}"
echo

# Print summary
echo -e "${GREEN}================================${NC}"
echo -e "${GREEN}Installation Complete!${NC}"
echo -e "${GREEN}================================${NC}"
echo
echo -e "Files installed:"
echo -e "  - $INSTALL_DIR/modbus-gateway.py"
echo -e "  - $INSTALL_DIR/modbus-gateway.cfg"
echo -e "  - $SERVICE_FILE"
echo -e "  - $UDEV_RULE"
echo
echo -e "${YELLOW}IMPORTANT: Edit the udev rule to match your USB adapter:${NC}"
echo -e "  sudo nano $UDEV_RULE"
echo
echo -e "To find your adapter's vendor/product ID:"
echo -e "  lsusb"
echo -e "  udevadm info -a -n /dev/ttyUSB0 | grep -E 'idVendor|idProduct'"
echo
echo -e "${YELLOW}After editing the udev rule, reload:${NC}"
echo -e "  sudo udevadm control --reload-rules"
echo -e "  sudo udevadm trigger"
echo
echo -e "Service commands:"
echo -e "  sudo systemctl status modbus-gateway"
echo -e "  sudo systemctl start modbus-gateway"
echo -e "  sudo systemctl stop modbus-gateway"
echo -e "  sudo journalctl -u modbus-gateway -f"
echo
echo -e "Configuration file:"
echo -e "  sudo nano $INSTALL_DIR/modbus-gateway.cfg"
echo
echo -e "${GREEN}The service will auto-start when /dev/rs485 is detected.${NC}"
