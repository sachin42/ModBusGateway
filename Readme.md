# 🔌 Modbus TCP ↔ RTU Gateway

<div align="center">

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](http://creativecommons.org/licenses/by-nc/4.0/)
[![Python](https://img.shields.io/badge/Python-2.7%20%7C%203.x-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20Raspberry%20Pi-green.svg)](https://www.raspberrypi.org/)

**Production-ready Modbus TCP to Modbus RTU gateway with multi-client support**

[Features](#-features) •
[Installation](#-installation) •
[Configuration](#-configuration) •
[Architecture](#-architecture) •
[Usage](#-usage)

</div>

---

## ✨ Features

| Feature | Description |
|---------|-------------|
| 🔄 **Protocol Translation** | Seamless Modbus TCP ↔ RTU conversion |
| 👥 **Multi-Client Support** | Handle multiple TCP clients simultaneously |
| 🔒 **Thread-Safe** | Single-master guarantee on RS-485 bus |
| ⚡ **Auto-Recovery** | Automatic retry and serial port recovery |
| 🐧 **Systemd Integration** | Auto-start when USB adapter detected |
| 📊 **Comprehensive Logging** | Debug-friendly with thread identification |

## 🏗️ Architecture

```
┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ TCP Client  │  │ TCP Client  │  │ TCP Client  │
│   (SCADA)   │  │   (HMI)     │  │  (Custom)   │
└──────┬──────┘  └──────┬──────┘  └──────┬──────┘
       │                │                │
       │     Modbus TCP (Port 502)       │
       └────────────────┼────────────────┘
                        │
                        ▼
          ┌─────────────────────────┐
          │   🖥️ Modbus Gateway     │
          │  ┌───────────────────┐  │
          │  │ ThreadingTCPServer│  │
          │  └─────────┬─────────┘  │
          │            │            │
          │  ┌─────────▼─────────┐  │
          │  │  📋 Request Queue │  │
          │  └─────────┬─────────┘  │
          │            │            │
          │  ┌─────────▼─────────┐  │
          │  │  🔧 RTU Worker    │  │
          │  │  (Single Thread)  │  │
          │  └─────────┬─────────┘  │
          └────────────┼────────────┘
                       │
                       │  Modbus RTU
                       ▼
          ┌─────────────────────────┐
          │    🔌 RS-485 Bus        │
          │  ┌─────┐ ┌─────┐ ┌─────┐│
          │  │ Dev │ │ Dev │ │ Dev ││
          │  │  1  │ │  2  │ │  N  ││
          │  └─────┘ └─────┘ └─────┘│
          └─────────────────────────┘
```

## 📦 Installation

### Quick Start

```bash
# Clone repository
git clone https://github.com/Bouni/ModBusGateway.git
cd ModBusGateway

# Install dependency
pip install pyserial

# Run directly
python modbus-gateway.py
```

### 🐧 Linux Service Installation (Recommended)

```bash
# Make install script executable
chmod +x install.sh

# Run installer as root
sudo ./install.sh
```

The installer will:
- ✅ Create `/opt/modbus-gateway/` directory
- ✅ Install systemd service
- ✅ Configure udev rules for auto-start
- ✅ Create dedicated `modbus` user

### 🔍 Configure USB Adapter Detection

Find your adapter's vendor/product ID:

```bash
lsusb
# Example: Bus 001 Device 003: ID 1a86:7523 QinHeng Electronics CH340
```

Edit the udev rule to match your adapter:

```bash
sudo nano /etc/udev/rules.d/99-rs485.rules
```

Common adapters:

| Adapter | Vendor ID | Product ID |
|---------|-----------|------------|
| 🔵 FTDI FT232 | `0403` | `6001` |
| 🟢 CH340/CH341 | `1a86` | `7523` |
| 🟡 CP2102/CP2104 | `10c4` | `ea60` |
| 🟣 Prolific PL2303 | `067b` | `2303` |

Reload rules:

```bash
sudo udevadm control --reload-rules
sudo udevadm trigger
```

## ⚙️ Configuration

Edit `modbus-gateway.cfg`:

```ini
# 🌐 TCP Server Settings
[ModbusTCP]
host = 0.0.0.0          # Listen on all interfaces
port = 502              # Standard Modbus TCP port
timeout = 60            # Client idle timeout (seconds)

# 🔌 RTU Serial Settings
[ModbusRTU]
port = /dev/rs485       # Serial port (or /dev/ttyUSB0)
baudrate = 9600         # Baud rate
stopbits = 1            # Stop bits
parity = N              # N=None, E=Even, O=Odd
bytesize = 8            # Data bits
timeout = 1             # RTU response timeout (seconds)
retry_count = 3         # Retries on failure
inter_frame_delay = 0.05 # Delay between frames (seconds)
```

## 🚀 Usage

### Manual Start

```bash
python modbus-gateway.py
```

### Service Commands

```bash
# 📊 Check status
sudo systemctl status modbus-gateway

# ▶️ Start service
sudo systemctl start modbus-gateway

# ⏹️ Stop service
sudo systemctl stop modbus-gateway

# 🔄 Restart service
sudo systemctl restart modbus-gateway

# 📋 View logs (live)
sudo journalctl -u modbus-gateway -f

# 📋 View last 100 log lines
sudo journalctl -u modbus-gateway -n 100
```

## 📡 Supported Modbus Functions

| Code | Function | Status |
|------|----------|--------|
| `0x01` | Read Coils | ✅ Tested |
| `0x02` | Read Discrete Inputs | ✅ Tested |
| `0x03` | Read Holding Registers | ✅ Tested |
| `0x04` | Read Input Registers | ✅ Tested |
| `0x05` | Write Single Coil | ✅ Supported |
| `0x06` | Write Single Register | ✅ Tested |
| `0x0F` | Write Multiple Coils | ✅ Supported |
| `0x10` | Write Multiple Registers | ✅ Supported |

## 🛡️ Safety Features

| Protection | Description |
|------------|-------------|
| 🔒 **Single Master** | Only one RTU worker thread accesses serial port |
| 📋 **Request Queue** | Thread-safe queue serializes all transactions |
| ⏱️ **Timeouts** | Configurable timeouts prevent hung connections |
| 🔄 **Auto-Retry** | Automatic retry on CRC errors or timeouts |
| 🩹 **Recovery** | Serial port auto-recovery on connection loss |
| ⚠️ **Exception Handling** | Proper Modbus exception responses (0x0B) |

## 🗑️ Uninstallation

```bash
sudo ./uninstall.sh
```

## 📚 References

- 📖 [Modbus TCP/IP Specification](https://modbus.org/specs.php)
- 📖 [Modbus RTU Specification](https://modbus.org/specs.php)
- 🔗 [Original Blog Post](http://blog.bouni.de/blog/2016/12/10/modbus-tcp-to-modbus-rtu-gatway-on-a-beaglebone-green/)

## 📄 License

This project is licensed under [CC BY-NC 4.0](http://creativecommons.org/licenses/by-nc/4.0/)

---

<div align="center">

**Made with ❤️ for Industrial Automation**

⭐ Star this repo if you find it useful!

</div>
