import os
import traceback
import time

from datetime import datetime

from DEPENDANT.LOGIX import PLCCOMMINCATION
from DEPENDANT.MQTT import MQTT
from DEPENDANT.LOGGING import initializeLogger

BASE_FILE_PATH = os.environ.get('BASE_FILE_PATH', '/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/')

TEMP_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "TEMP_IMG")
RAW_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "RAW_IMG")
LOGS_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "LOGS")

# Initialize logger
logger = initializeLogger("TRIGGER", LOGS_PATH=LOGS_PATH)

FRAME_CAPTURE_SIGNAL = "DataToDeltaPLC"

def main():
     mq_obj = MQTT("TRIGGER")
     logger.info("Starting trigger manager")

     plcobj = PLCCOMMINCATION("192.168.1.13")
     plcobj.init()

     while True:
          try: 
               mq_obj.publish("taloja/health_check", {"module": "trigger", "status": "Active"})
               diameter = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[0]")
               mq_obj.publish("taloja/writer", {"action": "diameter", "value": diameter})

               mill_speed = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[1]") 
               eg_setpoint = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[3]") 
               width = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[4]") 
               length = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[5]") 

               mill_run = plcobj.read_bit(0, f"{FRAME_CAPTURE_SIGNAL}[10]") 
               ironing_raised = plcobj.read_bit(1, f"{FRAME_CAPTURE_SIGNAL}[10]")

               if ((mill_run == 1) or (mill_run == '1')) and diameter > 570:
                    roll_num = f'ROLL_{datetime.now().strftime("%Y%m%d%H%M%S")}'
                    mq_obj.publish("taloja/capture", {"action": "start", "roll_num": roll_num})
                    print(f"Trigger recieved - {roll_num}")
                    # time.sleep(2)

                    while True:

                         diameter = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[0]")
                         mill_speed = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[1]") 
                         eg_setpoint = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[2]") 
                         width = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[3]") 
                         length = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[4]")
                         mq_obj.publish("taloja/writer", {"action": "diameter", "value": diameter})
                         mq_obj.publish("taloja/capture", {"diameter": diameter, "mill_speed": mill_speed, "eg_setpoint": eg_setpoint, "width": width, "length": length}) 
                         mq_obj.publish("main/trigger", {"mill_run": mill_run, "ironing_raised": ironing_raised, "roll_num": roll_num, "diameter": diameter, "mill_speed": mill_speed, "eg_setpoint": eg_setpoint, "width": width, "length": length}) 
                         mq_obj.publish("taloja/health_check", {"module": "trigger", "status": "Active"})

                         ironing_raised = plcobj.read_bit(1, f"{FRAME_CAPTURE_SIGNAL}[10]")

                         if ironing_raised == 1:
                              mq_obj.publish("taloja/capture", {"action": "stop", "roll_num": roll_num})
                              mq_obj.publish("taloja/writer", {"action": "light", "value": 1})
                              time.sleep(5)
                              logger.info(f"diameter = {diameter} | mill_speed = {mill_speed} | eg_setpoint = {eg_setpoint} | width = {width} | length = {length}")
                              logger.info(f"mill_run = {mill_run} | ironing_raised = {ironing_raised}")
                              break
                         else:
                              mq_obj.publish("taloja/writer", {"action": "light", "value": 0})

                         logger.info(f"diameter = {diameter} | mill_speed = {mill_speed} | eg_setpoint = {eg_setpoint} | width = {width} | length = {length}")
                         logger.info(f"mill_run = {mill_run} | ironing_raised = {ironing_raised}")
                         # time.sleep(1)
                        
               if ironing_raised == 1: mq_obj.publish("taloja/writer", {"action": "light", "value": 1})
               if ironing_raised == 0: mq_obj.publish("taloja/writer", {"action": "light", "value": 0})

               mq_obj.publish("main/trigger", {"roll_num": "--", "mill_run": mill_run, "ironing_raised": ironing_raised}) 

               logger.info(f"diameter = {diameter} | mill_speed = {mill_speed} | eg_setpoint = {eg_setpoint} | width = {width} | length = {length}")
               logger.info(f"mill_run = {mill_run} | ironing_raised = {ironing_raised}")
               print(f"diameter = {diameter} | mill_run = {mill_run} | ironing_raised = {ironing_raised}")
               time.sleep(2)

          except Exception as e:
               mq_obj.publish("taloja/health_check", {"module": "trigger", "status": "Inactive", "error": f"{e}"})
               print(f"Error : {e}")
               logger.critical(f"{traceback.format_exc()}")


if __name__ == "__main__":
     while True:
          try: main()
          except:
               print("Error in main() !")
               logger.critical(f"{traceback.format_exc()}")
               print("Restarting in 5 secs")
               time.sleep(5)