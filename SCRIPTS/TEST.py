import os
import traceback

from DEPENDANT.LOGIX import PLCCOMMINCATION
from DEPENDANT.LOGGING import initializeLogger


BASE_FILE_PATH = os.environ.get('BASE_FILE_PATH', '/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/')
LOGS_PATH = BASE_FILE_PATH + "DEBUGS/LOGS/"

# Initialize logger
logger = initializeLogger("TEST", LOGS_PATH=LOGS_PATH)

FRAME_CAPTURE_SIGNAL = "DataToDeltaPLC"

def main():

    plcobj = PLCCOMMINCATION("192.168.1.13")
    plcobj.init()

    while True:
        try: 
            result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[0]")
            print("[0] :", result)
        except: pass

        try: 
            result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[1]")
            print("[1] :", result)
        except: pass

        try: 
            result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[2]")
            print("[2] :", result)
        except: pass

        try: 
            result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[3]")
            print("[3] :", result)
        except: pass

        try: 
            result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[4]")
            print("[4] :", result)
        except: pass

        try: 
            result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[5]")
            print("[5] :", result)
        except: pass

        try: 
            result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[6]")
            print("[6] :", result)
        except: pass

if __name__ == "__main__":

     while True:
          try: main()
          except Exception as e:
               logger.critical(f"Main Outer Loop Exception is {e}")
               logger.critical(traceback.format_exc())
