from pycomm3 import LogixDriver
import time
import logging
import traceback
import os
import pymysql
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

from DEPENDANT.MQTT import MQTT

BASE_DIR = "/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/"
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "passwd": "insightzz@123",
    "db": "SURFACE_INSPECTION_TALOJA"
}
# Persistent Roll Counter
COUNTER_FILE = os.path.join(BASE_DIR, "SCRIPTS", "roll_counter.txt")

plclogger = None
manual_trigger = False

def get_next_roll_id():
    today = datetime.now().strftime("%Y%m%d")
    counter = 1
    
    if os.path.exists(COUNTER_FILE):
        try:
            with open(COUNTER_FILE, "r") as f:
                line = f.read().strip()
                last_date, last_count = line.split(",")
                if last_date == today:
                    counter = int(last_count) + 1
        except Exception:
            pass
                
    try:
        with open(COUNTER_FILE, "w") as f:
            f.write(f"{today},{counter}")
    except Exception as e:
        if plclogger: plclogger.error(f"Failed to write counter file: {e}")
        
    now_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"Roll_{now_str}_{counter:03d}"

def log_mill_event(roll_id, event_type):
    # event_type: 'start' or 'stop'
    db = None
    try:
        db = pymysql.connect(**DB_CONFIG)
        cur = db.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if event_type == 'start':
            query = "INSERT INTO mill_runs (roll_id, mill_start_time, CREATED_AT) VALUES (%s, %s, %s)"
            cur.execute(query, (roll_id, now, now))
        else:
            query = "UPDATE mill_runs SET mill_stop_time = %s WHERE roll_id = %s AND mill_stop_time IS NULL ORDER BY id DESC LIMIT 1"
            cur.execute(query, (now, roll_id))
            
        db.commit()
    except Exception as e:
        if plclogger: plclogger.error(f"Database logging failed: {e}")
    finally:
        if db: db.close()
          
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

class PLCCOMMINCATION:
     def __init__(self):
          self.plc_ip = '192.168.1.13'
          self.FRAME_CAPTURE_SIGNAL = "DataToDeltaPLC"

     def init(self):
          try:
               self.plc = LogixDriver(self.plc_ip)
               self.plc.open()
          except Exception as e:
               plclogger.critical(f"PLCCOMMINCATION init() Error occurred while initializing PLC connection: {e}")
               plclogger.critical(traceback.format_exc())
               
               try:
                    self.close()
                    time.sleep(1)
                    self.init()
               except Exception as e:
                    plclogger.critical(f"Error occurred while initializing PLC connection: {e}")
                    self.init()

     def read_bit(self, bit, tag):
          try:
               int_value = self.plc.read(tag)
               return int(int_value[1])
          except Exception as e:
               plclogger.critical(f"PLCCOMMINCATION exception in read_bit :{e}")
               plclogger.critical(traceback.format_exc())
               self.close()
               time.sleep(1)
               self.init()

     def close(self):
          try:
               if self.plc is not None: self.plc.close()
          except Exception as e: print(e)

def main():
     try:
          writer = MQTT("writer_monitor")
          last_trigger = 0
          start_capture = False
          bunch = "INACTIVE"
          if manual_trigger:
               last_update = 'FALSE'
               trigger = True
               while True:
                    print("MANUAL TRIGGER")
                    time.sleep(2)
          else:
               plcobj = PLCCOMMINCATION()
               plcobj.init()

               last_mill_run = 0
               current_roll_id = None

               while True:
                    try:
                         # Read status and diameter
                         # DataToDeltaPLC[10]: bit 0 -> mill_is_run, bit 1 -> ironing_raised
                         # DataToDeltaPLC[0]: diameter value
                         # Datatodelataplc[0] - diameter
                         # Datatodelataplc[1] - Mill speed 
                         # Datatodelataplc[3] - exit gauge setpoint
                         # Datatodelataplc[4] - Width
                         # Datatodelataplc[5] - coil length 
                         # Datatodelataplc[10].0 - Mill is Run 
                         # Datatodelataplc[10].1 - Ironing roll Raised Done

                         # writer.publish( f"taloja/client/write",
                         #                {
                         #                     "loc": "stop_light",
                         #                     "value": 1
                         #                }
                         #           )

                         # time.sleep(2)
                         millSpeed = int(plcobj.plc.read(f"{plcobj.FRAME_CAPTURE_SIGNAL}[1]").value)
                         exitGauge = int(plcobj.plc.read(f"{plcobj.FRAME_CAPTURE_SIGNAL}[3]").value)
                         width = int(plcobj.plc.read(f"{plcobj.FRAME_CAPTURE_SIGNAL}[4]").value)
                         coilLength = int(plcobj.plc.read(f"{plcobj.FRAME_CAPTURE_SIGNAL}[5]").value)

                         print(f"Mill Speed : {millSpeed}, Exit Gauge : {exitGauge}, Width : {width}, Coil Length : {coilLength}")

                         # Read bit word
                         bit_rec = plcobj.plc.read(f"{plcobj.FRAME_CAPTURE_SIGNAL}[10]")
                         status_raw = int(bit_rec.value)

                         # Extract bits
                         mill_is_run = (status_raw >> 0) & 1
                         ironing_raised = (status_raw >> 1) & 1

                         print(f"{datetime.now()}")
                         print("mill_is_run", mill_is_run)
                         print("ironing_raised",ironing_raised)

                         
                         # status_raw = int(plcobj.plc.read(f"{plcobj.FRAME_CAPTURE_SIGNAL}[10]").value)
                         # mill_is_run = (status_raw >> 0) & 1
                         # ironing_raised = (status_raw >> 1) & 1
                         
                         diameter = int(plcobj.plc.read(f"{plcobj.FRAME_CAPTURE_SIGNAL}[0]").value)

                         # 1. Mill Run Time Tracking
                         if mill_is_run == 1 and last_mill_run == 0:
                              print(f"MILL STARTED")
                              # We'll generate the Roll ID when capture actually starts, 
                              # but we log the mill start event now.
                              log_mill_event("SESSION_START", "start") 
                         
                         if mill_is_run == 0 and last_mill_run == 1:
                              print(f"MILL STOPPED")
                              log_mill_event(current_roll_id if current_roll_id else "SESSION_START", "stop")
                              if start_capture:
                                   print(f"ENDED FRAME CAPTURE (Mill Stop)")
                                   start_capture = False
                                   bunch = "INACTIVE"
                                   current_roll_id = None

                         # 2. PLC Signal-Based Trigger & Frame Capture
                         # Start condition: mill_is_run=1, ironing_raised=0 (raw=1)
                         if mill_is_run == 1 and ironing_raised == 0:
                              if not start_capture:
                                   print(f"STARTED FRAME CAPTURE - PLC Signal (raw=1)")
                                   start_capture = True
                                   current_roll_id = get_next_roll_id()
                                   bunch = current_roll_id
                                   log_mill_event(current_roll_id, "start")

                         # Stop condition: mill_is_run=0, ironing_raised=1 (raw=2)
                         elif mill_is_run == 0 and ironing_raised == 1:
                              # if start_capture:
                              print(f"ENDED FRAME CAPTURE - PLC Signal (raw=2)")
                              writer.publish( f"taloja/client/write",
                                   {
                                        "loc": "stop_light",
                                        "value": 1
                                   }
                              )
                              time.sleep(2)
                         elif ironing_raised == 0:
                              # if start_capture:
                              print(f"ENDED FRAME CAPTURE - PLC Signal (raw=2)")
                              writer.publish( f"taloja/client/write",
                                   {
                                        "loc": "stop_light",
                                        "value": 0
                                   }
                              )
                              time.sleep(2)
                              # log_mill_event(current_roll_id, "stop")
                              # start_capture = False
                              # bunch = "INACTIVE"
                              # current_roll_id = None
                         # elif mill_is_run == 0 :
                         #      # if start_capture and current_roll_id:
                         #      print(f"ENDED FRAME CAPTURE - Stop Light ")
                         #      writer.publish( f"taloja/client/write",
                         #           {
                         #                "loc": "stop_light",
                         #                "value": 1
                         #           }
                         #      )
                         #      time.sleep(0.5)
                         #      log_mill_event(current_roll_id, "stop")
                         #      start_capture = False
                         #      bunch = "INACTIVE"
                         #      current_roll_id = None
                         
                         # else:
                         #      # If mill stops outside of the specific (0,1) state, ensure capture stops
                         #      if not mill_is_run and start_capture:
                         #           print(f"ENDED FRAME CAPTURE - Mill Stop")
                         #           log_mill_event(current_roll_id, "stop")
                         #           start_capture = False
                         #           bunch = "INACTIVE"
                         #           current_roll_id = None

                         # MQTT Updates
                         writer.publish( f"taloja/client/write",
                              {
                                   "loc": "client",
                                   "value": diameter
                              }
                         )
                         
                         writer.publish( f"taloja/client/capture",
                              {
                                   "loc": "client",
                                   "value": bunch
                              }
                         )

                         # if mill_is_run or diameter > 0:
                         #      print(f"PLC ----{datetime.now()}------diam={diameter}, mill={mill_is_run}, capture={bunch}--------")

                         last_trigger = diameter
                         last_mill_run = mill_is_run
                         time.sleep(1)

                    except Exception:
                         plclogger.error(f"{traceback.format_exc()}")
                         time.sleep(1) # Wait a bit on error

                    time.sleep(0.1)
     except Exception as e:
          plclogger.error(f"initializeLogger() Exception is {e}")
          plclogger.error(traceback.format_exc())
   
if __name__ == "__main__":
     initializeLogger()

     while True:
          try: main()
          except Exception as e:
               plclogger.critical(f"Main Outer Loop Exception is {e}")
               plclogger.critical(traceback.format_exc())