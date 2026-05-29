import os
import timer
import traceback

from DEPENDANT.MQTT import MQTT
from

from pymodbus.client import ModbusTcpClient

BASE_DIR = "/home/hindalco_surface_inspection/insightzz/STANDARD_FRAMEWORK/"
plclogger = None
dbConnection = None
manual_trigger = False
          
def initializeLogger():
     global plclogger

     try:
          ''' Initializing Logger '''
          LOG_BACKUP_COUNT = 5
          plclogger = logging.getLogger(__name__)
          plclogger.setLevel(logging.DEBUG)
          formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
          
          # Create a TimedRotatingFileHandler for daily rotation
          log_file = BASE_DIR + "DEBUGS/LOGS/" + os.path.basename(__file__[:-2])+"log"
          file_handler = TimedRotatingFileHandler(log_file, when='midnight', backupCount=LOG_BACKUP_COUNT)
          file_handler.setLevel(logging.DEBUG)
          file_handler.setFormatter(formatter)
          plclogger.addHandler(file_handler)
          plclogger.debug("ANODE TRACKING PLC Module Initialized")

     except Exception as e:
          print(f"initializeLogger() Exception is {e}")
          print(traceback.format_exc())

class WRITE_REGISTERS:
    POE_RESTART = 4097
    PLC_WRITE = 4106
    PLC_STOP_LIGHT = 4247
    COBOT_STOP_FEEDBACK = 4099

class READ_REGISTERS:
    POE_RESTART = 4097
    PLC_READ = 4106
    COBOT_STOP_FEEDBACK = 4099
    
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
        plclogger.error(f"WriteToPLC Exception is : {e}")

def readFromPlc(register):
    client=initClient()
    result = client.read_holding_registers(address=register, count=2) 
    result_new = client.read_input_registers(address=register, count=2)  
    
    value=-1
    try:
        value=result.getRegister(0)
    except Exception as e:
        plclogger.error(f"readFromPLC() Exception is {e}")

    return value

def mainFunction():
    try:
        value = 100
        reciever = MQTT("write_monitor")
        reciever.subscribe("taloja/client/write")
        
        while True:
            data = reciever.data
            if any(list(data.keys())):
                print(data)
                loc = data['loc']
                value = int(data["value"])
                if loc == "stop_light":
                    writeToPlc(WRITE_REGISTERS.PLC_STOP_LIGHT, value)
                else:
                    writeToPlc(WRITE_REGISTERS.PLC_WRITE, value)

                    
                # print("Value is Written ")
            # value = readFromPlc(READ_REGISTERS.PLC_READ)
            # print(f"Value is {value} ")
            # writeToPlc(WRITE_REGISTERS.PLC_WRITE, 100)
            # print("Value is Written ")

            # time.sleep(0.5)
            
    except Exception as e:
        plclogger.error(f"mainFunction() Inside While Exception is : {e}")

if __name__=="__main__":
    initializeLogger()
    mainFunction()
    
