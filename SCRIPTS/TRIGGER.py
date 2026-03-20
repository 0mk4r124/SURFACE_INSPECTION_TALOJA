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

               while True:
                    try:
                         trigger = plcobj.read_bit(0, plcobj.FRAME_CAPTURE_SIGNAL)
                         writer.publish( f"taloja/client/write",
                              {
                                   "loc": "client",
                                   "value": trigger
                              }
                         )
                         print(f"Trigger for Delta ----{datetime.now()}------{trigger}--------")

                         if (trigger>585) and (last_trigger<trigger):
                              # start capture
                              print(f"STARTED FRAME CAPTURE")
                              if not start_capture:
                                   start_capture = True
                                   bunch = f"{datetime.now():%Y%m%d_%H%M%S}"

                         if last_trigger>trigger:
                              # stop capture
                              print(f"ENDED FRAME CAPTURE")
                              start_capture = False
                              bunch = "INACTIVE"

                         writer.publish( f"taloja/client/capture",
                              {
                                   "loc": "client",
                                   "value": bunch
                              }
                         )
                         last_trigger = trigger
                    except: plclogger.error(f"{traceback.format_exc()}")

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