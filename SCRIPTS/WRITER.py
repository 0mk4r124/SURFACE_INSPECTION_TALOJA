import os
import traceback
import time

from DEPENDANT.MQTT import MQTT
from DEPENDANT.LOGGING import initializeLogger

from pymodbus.client import ModbusTcpClient

BASE_FILE_PATH = os.environ.get('BASE_FILE_PATH', '/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/')

LOGS_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "LOGS")

plclogger = initializeLogger("WRITER", LOGS_PATH=LOGS_PATH)

class WRITE_REGISTERS:
    PLC_WRITE = 4106
    PLC_STOP_LIGHT = 4247
    
def initClient():
    client = ModbusTcpClient("192.168.2.5", port=502, timeout=3)
    client.connect()
    return client

def writeToPlc(register,val):
    try:
        client=initClient()
        client.write_register(address = register, value = val)
    except Exception as e:
        print(f"WriteToPLC Exception is : {e}")
        plclogger.error(f"{traceback.format_exc()}")

def readFromPlc(register):
    client=initClient()
    value=-1

    try:
        result = client.read_holding_registers(address=register, count=2)
        value=result.getRegister(0)
    except Exception as e:
        print(f"WriteToPLC Exception is : {e}")
        plclogger.error(f"{traceback.format_exc()}")

    return value

def main():
    mq_obj = MQTT("WRITER")
    mq_obj.subscribe("taloja/writer")
    plclogger.info("Starting writer manager")

    print("[PLC_SAMPLER] Ready, waiting for commands …")
        
    while True:
        try:
            data = mq_obj.data

            if data and data.get("_consumed") is not True:
                mq_obj.publish("taloja/health_check", {"module": "writer", "status": "Active"})

                action = data.get("action", "")
                value = data.get("value")
                mq_obj.data = {**data, "_consumed": True}

                if action == "light":
                    writeToPlc(WRITE_REGISTERS.PLC_STOP_LIGHT, value)
                    time.sleep(0.5)
                    plclogger.info(f"Writing {value} - Stop Light")
                    print(f"Writing {value} - Stop Light")
                    
                if action == "diameter":
                    writeToPlc(WRITE_REGISTERS.PLC_WRITE, value)
                    time.sleep(0.5)
                    plclogger.info(f"Writing Diameter")
                    print(f"Writing Diameter")

        except Exception as e:
            mq_obj.publish("taloja/health_check", {"module": "writer", "status": "Inactive", "error": f"{e}"})
            print(f"process_capture() Exception: {e}")
            plclogger.error(traceback.format_exc())
            raise


if __name__=="__main__":
    while True:
        try: main()
        except:
            print("Error in main() !")
            plclogger.critical(f"{traceback.format_exc()}")
            print("Restarting in 5 secs")
            time.sleep(5)
    
