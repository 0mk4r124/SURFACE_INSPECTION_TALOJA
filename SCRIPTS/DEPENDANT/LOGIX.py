import time
import traceback
from pycomm3 import LogixDriver

class PLCCOMMINCATION:
     
    def __init__(self, ip_address=None):
        self.plc_ip = ip_address if ip_address is not None else ""
        self.init()

    def init(self):
        try:
            self.plc = LogixDriver(self.plc_ip)
            self.plc.open()
        except Exception as e:
            print(f"PLCCOMMINCATION init() Error occurred while initializing PLC connection: {e}")
            print(traceback.format_exc())
            
            try:
                    self.close()
                    time.sleep(1)
                    self.init()
            except Exception as e:
                    print(f"Error occurred while initializing PLC connection: {e}")
                    print(traceback.format_exc())
                    self.init()

    def read_bit(self, bit, tag):
        try:
            int_value = self.plc.read(tag)
            bit_value = (int_value[1] >> bit) & 0x01
            return bit_value
        
        except Exception as e:
            print(f"PLCCOMMINCATION exception in read_bit :{e}")
            print(traceback.format_exc())

            self.close()
            time.sleep(1)
            self.init()

            return None

    def write_bit(self, tag, bit, value):
        try:
            current_int_value = self.plc.read(tag)
            modified_int_value = current_int_value[1] & ~(0x01 << bit)  # Clear the bit
            modified_int_value |= value << bit  # Set the new bit value
            self.plc.write(tag, modified_int_value)
            current_int_value = self.plc.read(tag)

            return modified_int_value
        
        except Exception as e:
            print(f"Error occurred while writing PLC tag: {e}")
            print(traceback.format_exc())

            self.close()
            time.sleep(1)
            self.init()

            return None

    def close(self):
        try:
            if self.plc is not None: self.plc.close()

        except Exception as e:
            print(f"PLCCOMMINCATION exception in close :{e}")
            print(traceback.format_exc())
