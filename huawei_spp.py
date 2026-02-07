import socket
import struct
import time
import logging

logger = logging.getLogger(__name__)

def crc16_xmodem(data: bytes) -> bytes:
    """Calculate CRC16 - XMODEM (poly=0x1021, init=0x0000)"""
    crc = 0x0000
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return struct.pack(">H", crc)

class HuaweiSPPClient:
    def __init__(self, address):
        self.address = address
        self.sock = None
        self.connected = False

    def connect(self):
        """Connect to the device via RFCOMM channel 1"""
        if self.connected:
            return True
        
        try:
            self.sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
            self.sock.settimeout(5)
            logger.debug(f"Connecting to {self.address} port 1...")
            self.sock.connect((self.address, 1))
            self.connected = True
            logger.info("SPP Connected")
            return True
        except Exception as e:
            logger.error(f"SPP Connection failed: {e}")
            if self.sock:
                self.sock.close()
            self.connected = False
            return False

    def disconnect(self):
        if self.sock:
            try:
                self.sock.close()
            except:
                pass
        self.sock = None
        self.connected = False

    def send_packet(self, cmd_id: bytes, params: list = None):
        """
        Build and send a packet
        params: list of tuples (type, value_bytes)
        """
        if not self.connected:
            raise Exception("Not connected")

        # Payload construction
        # Format: Type(1b) + Length(1b) + Value
        payload = b""
        if params:
            for p_type, p_value in params:
                payload += struct.pack("B", p_type)
                payload += struct.pack("B", len(p_value))
                payload += p_value

        # Packet Header
        # 0x5A + Length(2b) + 0x00 + CmdID(2b) + Payload
        # Length excludes Head(1)
        # Length = 1 (Reserved 0x00) + 2 (CmdID) + len(payload)
        
        length = 1 + 2 + len(payload)
        
        packet = b"\x5A" + struct.pack(">H", length) + b"\x00" + cmd_id + payload
        
        # Calculate Checksum (CRC16 of everything before it)
        checksum = crc16_xmodem(packet)
        packet += checksum
        
        logger.debug(f"Sending SPP: {packet.hex()}")
        self.sock.send(packet)

    def _read_exact(self, n):
        data = b""
        while len(data) < n:
            try:
                chunk = self.sock.recv(n - len(data))
                if not chunk:
                    break
                data += chunk
            except Exception as e:
                logger.error(f"Socket read error: {e}")
                break
        return data

    def receive_packet(self):
        if not self.connected:
            raise Exception("Not connected")
            
        # Read Header: 5A + Len(2) + 00
        try:
            header = self._read_exact(4)
        except socket.timeout:
            return None
            
        if len(header) < 4 or header[0] != 0x5A:
            valid_len = len(header)
            if valid_len > 0:
                 logger.debug(f"Invalid header received: {header.hex()}")
            return None
            
        length = struct.unpack(">H", header[1:3])[0]
        
        # Remaining body = Length - 1 (for the 00 byte we read) + 2 (CRC)
        remaining_len = length - 1 + 2
        
        try:
            body_crc = self._read_exact(remaining_len)
        except socket.timeout:
            logger.warning("Socket timeout while reading body")
            return None
            
        if len(body_crc) < remaining_len:
            logger.warning(f"Incomplete packet body. Expected {remaining_len}, got {len(body_crc)}")
            return None
            
        full_data = header + body_crc
        
        # Verify CRC (Optional but good)
        # Checksum is last 2 bytes
        # Packet content for CRC calc includes everything except last 2 bytes
        # packet_for_crc = full_data[:-2]
        # received_crc = full_data[-2:]
        # calc_crc = crc16_xmodem(packet_for_crc)
        # if calc_crc != received_crc:
        #    logger.warning("CRC Mismatch")
        
        return full_data

    def get_battery(self):
        """Active query for battery"""
        # CMD_BATTERY_READ = 0x01 0x08
        params = [
            (1, b""), 
            (2, b""), 
            (3, b"")
        ]
        self.send_packet(b"\x01\x08", params)
        
        # Loop to find the correct response
        start_time = time.time()
        while time.time() - start_time < 3.0: # 3s timeout
            resp = self.receive_packet()
            if resp:
                # Check for Cmd ID 0x0108
                if len(resp) >= 6 and resp[4:6] == b"\x01\x08":
                    result = self.parse_battery_response(resp)
                    if result:
                        return result
                else:
                    if len(resp) >= 6:
                        logger.debug(f"Skipping non-battery packet, info: {resp[4:6].hex()}")
            else:
                # If receive_packet returns None (timeout or invalid), we continue attempting 
                # or break effectively if socket is dead?
                # Using short internal timeouts in recv helps yield here.
                # But our socket timeout is 5s. 
                # If we want responsive loop, we rely on recv returning when data comes.
                pass
                
        logger.warning("Battery query timed out or no valid response found")
        return None

    def parse_battery_response(self, data):
        # Parse logic
        # Skip 5A + Len(2) + 00(1) + Cmd(2) = 6 bytes header
        # Payload starts at 6
        # Parse TLVs
        # Cmd should be 01 08 (or response ID?)
        # Response ID is usually same as Request? Or `01 14` etc?
        # OpenFreebuds: `GET_BATTERY_RESP_BASE = bytes.fromhex("5a0014000108 01 01 40...")`
        # Cmd ID in response is `01 08` (Same as request).
        
        if len(data) < 8: return None
        
        idx = 6
        res = {}
        # Simple loop
        while idx < len(data) - 2: # Exclude CRC
            t = data[idx]
            l = data[idx+1]
            v = data[idx+2 : idx+2+l]
            
            if t == 1: res['global'] = int.from_bytes(v, 'big')
            if t == 2: # L R Case
                if len(v) >= 3:
                    res['left'] = v[0]
                    res['right'] = v[1]
                    res['case'] = v[2]
            
            idx += 2 + l
            
        return res

    def set_low_latency(self, enabled: bool):
        # CMD_LOW_LATENCY = 0x2B 0x6C
        # Write: Change RQ
        # Param 1 = 1/0
        val = b"\x01" if enabled else b"\x00"
        self.send_packet(b"\x2b\x6c", [(1, val)])
        # Expect response?
        return self.receive_packet()

