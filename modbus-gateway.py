#!/usr/bin/env python
"""
Modbus TCP to Modbus RTU Gateway
================================

Production-safe gateway that bridges Modbus TCP clients to a single RS-485
Modbus RTU bus. Supports multiple concurrent TCP clients while guaranteeing
single-master behavior on the RTU bus.

Threading Model:
----------------
- Main thread: Runs the TCP server, accepts connections
- TCP handler threads: One per connected client (spawned by ThreadingTCPServer)
- RTU worker thread: Single dedicated thread that owns the serial port

Request Flow:
-------------
1. TCP client sends Modbus TCP request
2. TCP handler thread parses request, creates RTURequest object
3. RTURequest is placed in thread-safe queue
4. TCP handler blocks waiting for response (via threading.Event)
5. RTU worker dequeues request, performs serial transaction
6. RTU worker sets response and signals the waiting TCP handler
7. TCP handler sends response back to client

Safety Guarantees:
------------------
- Only the RTU worker thread ever touches the serial port
- Queue serializes all RTU requests (no bus collisions)
- Each TCP client gets its own handler thread (no blocking between clients)
- Transaction IDs preserved end-to-end for each client
- Timeouts prevent indefinite blocking on failures

"""

from __future__ import print_function

import struct
import threading
import time
import logging
import sys

# Python 2/3 compatibility
if sys.version_info[0] >= 3:
    import configparser as ConfigParser
    import socketserver as SocketServer
    from queue import Queue, Empty
else:
    import ConfigParser
    import SocketServer
    from Queue import Queue, Empty

import serial

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(threadName)-12s] %(levelname)-8s %(message)s'
)
logger = logging.getLogger('ModbusGateway')


# ============================================================================
# CRC-16 Calculation (Modbus RTU)
# ============================================================================

def crc16_calculate(data):
    """
    Calculate Modbus CRC-16 for the given data.
    Returns 2-byte CRC in little-endian format (low byte first).
    """
    crc = 0xFFFF
    for byte in data:
        if isinstance(byte, int):
            crc ^= byte
        else:
            crc ^= ord(byte)
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    # Return as bytes (little-endian: low byte, high byte)
    return struct.pack('<H', crc)


def crc16_verify(data):
    """
    Verify CRC-16 of received RTU frame.
    Returns True if CRC is valid.
    """
    if len(data) < 3:
        return False
    message = data[:-2]
    received_crc = data[-2:]
    calculated_crc = crc16_calculate(message)
    return received_crc == calculated_crc


# ============================================================================
# RTU Request Container
# ============================================================================

class RTURequest:
    """
    Container for a single RTU transaction request.

    Each TCP handler creates one of these, enqueues it, and waits on the
    event for the RTU worker to complete the transaction.

    Attributes:
        transaction_id: Modbus TCP transaction ID (for response matching)
        unit_id: Target Modbus slave unit ID
        pdu: Protocol Data Unit (function code + data, no CRC)
        response: RTU response PDU (set by RTU worker)
        error: Error message if transaction failed
        event: Threading event to signal completion
        timeout: How long to wait for RTU response
    """

    def __init__(self, transaction_id, unit_id, pdu, timeout=1.0):
        self.transaction_id = transaction_id
        self.unit_id = unit_id
        self.pdu = pdu
        self.response = None
        self.error = None
        self.event = threading.Event()
        self.timeout = timeout
        self.timestamp = time.time()

    def set_response(self, response):
        """Set successful response and signal completion."""
        self.response = response
        self.event.set()

    def set_error(self, error_msg):
        """Set error and signal completion."""
        self.error = error_msg
        self.event.set()

    def wait(self, timeout=None):
        """
        Wait for RTU worker to complete the transaction.
        Returns True if completed, False if timed out.
        """
        wait_timeout = timeout if timeout is not None else (self.timeout + 1.0)
        return self.event.wait(timeout=wait_timeout)


# ============================================================================
# RTU Worker Thread
# ============================================================================

class RTUWorker(threading.Thread):
    """
    Dedicated worker thread for RTU serial communication.

    This is the ONLY thread that accesses the serial port, ensuring
    single-master behavior on the RS-485 bus regardless of how many
    TCP clients are connected.

    The worker:
    1. Opens and configures the serial port on startup
    2. Continuously dequeues requests from the request queue
    3. Performs RTU transactions (send request, wait for response)
    4. Returns responses to waiting TCP handlers via RTURequest.event
    5. Handles timeouts and errors gracefully
    """

    def __init__(self, config, request_queue):
        super(RTUWorker, self).__init__(name='RTUWorker')
        self.config = config
        self.request_queue = request_queue
        self.serial = None
        self.running = False
        self.daemon = True  # Allow clean shutdown

        # Configuration with defaults
        self.rtu_timeout = get_config_value(config, "ModbusRTU", "timeout", 1.0, float)
        self.retry_count = get_config_value(config, "ModbusRTU", "retry_count", 3, int)
        self.inter_frame_delay = get_config_value(config, "ModbusRTU", "inter_frame_delay", 0.05, float)

    def setup_serial(self):
        """Configure and open the serial port."""
        self.serial = serial.Serial()
        self.serial.port = get_config_value(self.config, "ModbusRTU", "port", "/dev/ttyUSB0", str)
        self.serial.baudrate = get_config_value(self.config, "ModbusRTU", "baudrate", 9600, int)
        self.serial.stopbits = get_config_value(self.config, "ModbusRTU", "stopbits", 1, int)
        self.serial.parity = get_config_value(self.config, "ModbusRTU", "parity", "N", str)
        self.serial.bytesize = get_config_value(self.config, "ModbusRTU", "bytesize", 8, int)
        self.serial.timeout = self.rtu_timeout

        try:
            self.serial.open()
            logger.info("Serial port opened: %s @ %d baud",
                       self.serial.port, self.serial.baudrate)
            return True
        except serial.SerialException as e:
            logger.error("Failed to open serial port: %s", e)
            return False

    def run(self):
        """Main worker loop - process RTU requests from queue."""
        if not self.setup_serial():
            logger.critical("RTU Worker failed to start - serial port error")
            return

        self.running = True
        logger.info("RTU Worker started")

        while self.running:
            try:
                # Block waiting for requests (with timeout for clean shutdown)
                try:
                    request = self.request_queue.get(timeout=0.5)
                except Empty:
                    continue

                # Process the RTU transaction
                self.process_request(request)
                self.request_queue.task_done()

            except Exception as e:
                logger.exception("RTU Worker error: %s", e)

        # Cleanup
        if self.serial and self.serial.is_open:
            self.serial.close()
            logger.info("Serial port closed")

    def process_request(self, request):
        """
        Execute a single RTU transaction with retries.

        RTU Transaction Lifecycle:
        1. Build RTU frame: Unit ID + PDU + CRC
        2. Flush input buffer (discard any stale data)
        3. Send RTU request frame
        4. Wait for response with timeout
        5. Validate response CRC
        6. Return PDU to TCP handler
        """
        # Build RTU frame: [Unit ID (1)] [PDU (n)] [CRC (2)]
        if isinstance(request.pdu, bytes):
            rtu_frame = bytes([request.unit_id]) + request.pdu
        else:
            rtu_frame = chr(request.unit_id) + request.pdu
        rtu_frame = rtu_frame + crc16_calculate(rtu_frame)

        logger.debug("RTU TX [Unit %d]: %s",
                    request.unit_id,
                    self.format_hex(rtu_frame))

        last_error = None

        for attempt in range(self.retry_count):
            if attempt > 0:
                logger.debug("RTU retry %d/%d for Unit %d",
                           attempt + 1, self.retry_count, request.unit_id)
                time.sleep(self.inter_frame_delay)

            try:
                # Ensure serial port is open
                if not self.serial.is_open:
                    self.serial.open()

                # Clear input buffer
                self.serial.reset_input_buffer()

                # Send request
                if isinstance(rtu_frame, bytes):
                    self.serial.write(rtu_frame)
                else:
                    self.serial.write(rtu_frame.encode('latin-1'))

                # Inter-frame delay (Modbus spec: 3.5 char times minimum)
                time.sleep(self.inter_frame_delay)

                # Read response
                response = self.read_rtu_response(request)

                if response is not None:
                    request.set_response(response)
                    return

            except serial.SerialException as e:
                last_error = "Serial error: {}".format(e)
                logger.warning("RTU serial error (attempt %d): %s", attempt + 1, e)
                # Try to recover serial connection
                try:
                    self.serial.close()
                    time.sleep(0.1)
                    self.serial.open()
                except:
                    pass

            except Exception as e:
                last_error = "RTU error: {}".format(e)
                logger.warning("RTU transaction error (attempt %d): %s", attempt + 1, e)

        # All retries exhausted
        error_msg = last_error or "RTU timeout after {} retries".format(self.retry_count)
        logger.error("RTU transaction failed for Unit %d: %s", request.unit_id, error_msg)
        request.set_error(error_msg)

    def read_rtu_response(self, request):
        """
        Read and parse RTU response frame.

        Modbus RTU response format depends on function code:
        - Normal response: [Unit ID] [FC] [Data...] [CRC]
        - Exception response: [Unit ID] [FC | 0x80] [Exception Code] [CRC]

        Returns PDU (without CRC) on success, None on timeout/error.
        """
        # Read first 2 bytes: Unit ID + Function Code
        header = self.serial.read(2)
        if len(header) < 2:
            logger.debug("RTU response timeout (no header)")
            return None

        if isinstance(header, bytes):
            unit_id = header[0]
            function_code = header[1]
        else:
            unit_id = ord(header[0])
            function_code = ord(header[1])

        # Check for exception response (function code has high bit set)
        if function_code & 0x80:
            # Exception response: read 1 more byte (exception code) + 2 CRC
            remaining = self.serial.read(3)
            if len(remaining) < 3:
                logger.debug("RTU response timeout (exception frame incomplete)")
                return None
            response = header + remaining

        else:
            # Normal response - length depends on function code
            data_length = self.get_response_length(function_code, request.pdu)
            if data_length is None:
                # Variable length - read byte count first
                byte_count_raw = self.serial.read(1)
                if len(byte_count_raw) < 1:
                    logger.debug("RTU response timeout (no byte count)")
                    return None

                if isinstance(byte_count_raw, bytes):
                    byte_count = byte_count_raw[0]
                else:
                    byte_count = ord(byte_count_raw)

                # Read data bytes + CRC
                remaining = self.serial.read(byte_count + 2)
                if len(remaining) < byte_count + 2:
                    logger.debug("RTU response timeout (data incomplete)")
                    return None
                response = header + byte_count_raw + remaining
            else:
                # Fixed length response
                remaining = self.serial.read(data_length + 2)  # +2 for CRC
                if len(remaining) < data_length + 2:
                    logger.debug("RTU response timeout (fixed frame incomplete)")
                    return None
                response = header + remaining

        # Verify CRC
        if not crc16_verify(response):
            logger.warning("RTU response CRC error: %s", self.format_hex(response))
            return None

        logger.debug("RTU RX [Unit %d]: %s", unit_id, self.format_hex(response))

        # Return PDU (strip unit ID prefix and CRC suffix)
        return response[1:-2]

    def get_response_length(self, function_code, request_pdu):
        """
        Determine expected response data length for fixed-length responses.
        Returns None for variable-length responses (those with byte count).

        Function codes with fixed response lengths:
        - FC 05 (Write Single Coil): echo of request (4 bytes)
        - FC 06 (Write Single Register): echo of request (4 bytes)
        - FC 15 (Write Multiple Coils): 4 bytes
        - FC 16 (Write Multiple Registers): 4 bytes

        Function codes with variable response lengths (return None):
        - FC 01, 02, 03, 04: byte count + data
        """
        if function_code in (0x05, 0x06, 0x0F, 0x10):
            return 4  # Fixed 4 data bytes after function code
        return None  # Variable length - has byte count

    def format_hex(self, data):
        """Format bytes as hex string for logging."""
        if isinstance(data, bytes):
            return ':'.join('{:02X}'.format(b) for b in data)
        return ':'.join('{:02X}'.format(ord(c)) for c in data)

    def stop(self):
        """Signal worker to stop."""
        self.running = False


# ============================================================================
# Modbus TCP Handler
# ============================================================================

class ModbusGatewayHandler(SocketServer.BaseRequestHandler):
    """
    TCP connection handler - one instance per connected client.

    Each TCP client gets its own handler thread (via ThreadingTCPServer).
    Handlers do NOT access the serial port directly. Instead, they:
    1. Parse incoming Modbus TCP requests
    2. Create RTURequest objects and enqueue them
    3. Wait for RTU worker to complete the transaction
    4. Send responses back to TCP client

    This design allows multiple TCP clients without bus collisions.
    """

    def setup(self):
        """Initialize handler for new connection."""
        self.client_ip = self.client_address[0]
        self.client_port = self.client_address[1]
        logger.info("TCP client connected: %s:%d", self.client_ip, self.client_port)

        # Set socket timeout
        tcp_timeout = get_config_value(self.server.config, "ModbusTCP", "timeout", 60.0, float)
        self.request.settimeout(tcp_timeout)

        # RTU timeout from config
        self.rtu_timeout = get_config_value(self.server.config, "ModbusRTU", "timeout", 1.0, float)

    def handle(self):
        """Main request handling loop for this TCP client."""
        while True:
            try:
                # Read MBAP header (7 bytes)
                mbap_header = self.recv_exact(7)
                if mbap_header is None:
                    break

                # Parse MBAP header
                # [Transaction ID (2)] [Protocol ID (2)] [Length (2)] [Unit ID (1)]
                transaction_id = struct.unpack('>H', mbap_header[0:2])[0]
                protocol_id = struct.unpack('>H', mbap_header[2:4])[0]
                length = struct.unpack('>H', mbap_header[4:6])[0]

                if isinstance(mbap_header[6], int):
                    unit_id = mbap_header[6]
                else:
                    unit_id = ord(mbap_header[6])

                # Validate protocol ID (must be 0 for Modbus)
                if protocol_id != 0:
                    logger.warning("Invalid protocol ID %d from %s",
                                 protocol_id, self.client_ip)
                    continue

                # Read PDU (length - 1 because unit ID already read)
                pdu_length = length - 1
                if pdu_length < 1 or pdu_length > 253:
                    logger.warning("Invalid PDU length %d from %s",
                                 pdu_length, self.client_ip)
                    continue

                pdu = self.recv_exact(pdu_length)
                if pdu is None:
                    break

                logger.debug("TCP RX [TxID %d, Unit %d]: %s",
                           transaction_id, unit_id,
                           self.format_hex(mbap_header + pdu))

                # Create RTU request and enqueue
                rtu_request = RTURequest(
                    transaction_id=transaction_id,
                    unit_id=unit_id,
                    pdu=pdu,
                    timeout=self.rtu_timeout
                )

                self.server.rtu_queue.put(rtu_request)

                # Wait for RTU worker to complete
                if not rtu_request.wait(timeout=self.rtu_timeout + 2.0):
                    logger.warning("RTU request timeout for client %s", self.client_ip)
                    # Send Modbus exception response (Gateway Target Device Failed to Respond)
                    self.send_exception(transaction_id, unit_id, pdu, 0x0B)
                    continue

                # Check for RTU error
                if rtu_request.error:
                    logger.warning("RTU error for client %s: %s",
                                 self.client_ip, rtu_request.error)
                    # Send Modbus exception response
                    self.send_exception(transaction_id, unit_id, pdu, 0x0B)
                    continue

                # Build and send TCP response
                self.send_response(transaction_id, unit_id, rtu_request.response)

            except socket.timeout:
                logger.info("TCP client timeout: %s", self.client_ip)
                break
            except (ConnectionResetError, BrokenPipeError):
                logger.info("TCP client disconnected: %s", self.client_ip)
                break
            except Exception as e:
                logger.exception("Error handling client %s: %s", self.client_ip, e)
                break

    def recv_exact(self, length):
        """
        Receive exactly 'length' bytes from socket.
        Returns None on disconnect or error.
        """
        data = b''
        while len(data) < length:
            try:
                chunk = self.request.recv(length - len(data))
                if not chunk:
                    return None
                data += chunk
            except:
                return None
        return data

    def send_response(self, transaction_id, unit_id, pdu):
        """
        Send Modbus TCP response to client.

        MBAP Header format:
        [Transaction ID (2)] [Protocol ID (2)] [Length (2)] [Unit ID (1)]
        """
        if isinstance(pdu, bytes):
            pdu_bytes = pdu
        else:
            pdu_bytes = pdu.encode('latin-1')

        length = len(pdu_bytes) + 1  # +1 for unit ID

        mbap = struct.pack('>HHHB', transaction_id, 0, length, unit_id)
        response = mbap + pdu_bytes

        logger.debug("TCP TX [TxID %d, Unit %d]: %s",
                   transaction_id, unit_id, self.format_hex(response))

        try:
            self.request.sendall(response)
        except:
            pass

    def send_exception(self, transaction_id, unit_id, request_pdu, exception_code):
        """
        Send Modbus exception response to client.

        Exception PDU: [Function Code | 0x80] [Exception Code]
        """
        if isinstance(request_pdu, bytes):
            function_code = request_pdu[0]
        else:
            function_code = ord(request_pdu[0])

        exception_pdu = struct.pack('BB', function_code | 0x80, exception_code)
        self.send_response(transaction_id, unit_id, exception_pdu)

    def format_hex(self, data):
        """Format bytes as hex string for logging."""
        if isinstance(data, bytes):
            return ':'.join('{:02X}'.format(b) for b in data)
        return ':'.join('{:02X}'.format(ord(c)) for c in data)

    def finish(self):
        """Cleanup when connection closes."""
        logger.info("TCP client disconnected: %s:%d", self.client_ip, self.client_port)


# ============================================================================
# Threaded TCP Server
# ============================================================================

class ThreadedModbusServer(SocketServer.ThreadingMixIn, SocketServer.TCPServer):
    """
    Multi-threaded TCP server for Modbus gateway.

    ThreadingMixIn creates a new thread for each client connection,
    allowing multiple TCP clients to be served concurrently.

    The server holds references to:
    - config: Configuration parser
    - rtu_queue: Thread-safe queue for RTU requests
    - rtu_worker: The dedicated RTU worker thread
    """

    allow_reuse_address = True
    daemon_threads = True  # Handler threads are daemonic

    def __init__(self, address, handler, config):
        # Python 2/3 compatible initialization
        SocketServer.TCPServer.__init__(self, address, handler)

        self.config = config
        self.rtu_queue = Queue()
        self.rtu_worker = None

    def start_rtu_worker(self):
        """Start the dedicated RTU worker thread."""
        self.rtu_worker = RTUWorker(self.config, self.rtu_queue)
        self.rtu_worker.start()

        # Wait briefly to ensure worker started successfully
        time.sleep(0.5)
        if not self.rtu_worker.is_alive():
            raise RuntimeError("RTU Worker failed to start")

        logger.info("RTU Worker thread started")

    def shutdown(self):
        """Graceful shutdown of server and RTU worker."""
        logger.info("Shutting down gateway...")

        # Stop RTU worker
        if self.rtu_worker:
            self.rtu_worker.stop()
            self.rtu_worker.join(timeout=5.0)

        # Stop TCP server
        SocketServer.TCPServer.shutdown(self)
        logger.info("Gateway shutdown complete")


# ============================================================================
# Main Entry Point
# ============================================================================

import socket  # Needed for exception types in handler

def load_config(config_file='modbus-gateway.cfg'):
    """Load configuration from file."""
    config = ConfigParser.RawConfigParser()
    config.read(config_file)
    return config


def get_config_value(config, section, option, default, value_type=str):
    """Get config value with default fallback."""
    try:
        if value_type == int:
            return config.getint(section, option)
        elif value_type == float:
            return config.getfloat(section, option)
        else:
            return config.get(section, option)
    except (ConfigParser.NoSectionError, ConfigParser.NoOptionError):
        return default


def main():
    """
    Main entry point for Modbus Gateway.

    Architecture Summary:
    ---------------------
    This gateway safely bridges multiple Modbus TCP clients to a single
    RS-485 Modbus RTU bus using the following design:

    1. SINGLE-MASTER GUARANTEE:
       Only one thread (RTUWorker) ever accesses the serial port.
       All TCP handlers must go through the request queue.
       This prevents bus collisions and multi-master issues.

    2. THREAD-SAFE QUEUEING:
       TCP handlers create RTURequest objects and enqueue them.
       The queue serializes all RTU transactions.
       Each request has an Event for synchronization.

    3. SCALABILITY:
       New TCP clients don't slow down existing ones (beyond queue time).
       RTU bus bandwidth is the only bottleneck.
       Timeouts prevent hung clients from blocking others.

    4. RELIABILITY:
       Automatic retry on RTU timeout or CRC error.
       Serial port recovery on connection loss.
       Proper Modbus exception responses on failure.
    """

    # Load configuration
    config = load_config()

    # Server address
    host = get_config_value(config, 'ModbusTCP', 'host', '0.0.0.0', str)
    port = get_config_value(config, 'ModbusTCP', 'port', 502, int)
    address = (host, port)

    # RTU settings for logging
    rtu_port = get_config_value(config, 'ModbusRTU', 'port', '/dev/ttyUSB0', str)
    rtu_baud = get_config_value(config, 'ModbusRTU', 'baudrate', 9600, int)

    logger.info("=" * 60)
    logger.info("Modbus TCP/RTU Gateway")
    logger.info("=" * 60)
    logger.info("TCP Server: %s:%d", host, port)
    logger.info("RTU Port: %s @ %d baud", rtu_port, rtu_baud)
    logger.info("=" * 60)

    # Create and start server
    try:
        server = ThreadedModbusServer(address, ModbusGatewayHandler, config)
        server.start_rtu_worker()

        logger.info("Gateway ready - accepting connections")
        server.serve_forever()

    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received")
        server.shutdown()
    except Exception as e:
        logger.critical("Gateway error: %s", e)
        raise


if __name__ == "__main__":
    main()
