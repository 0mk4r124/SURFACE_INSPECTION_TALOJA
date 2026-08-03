import os
import traceback
import time
import pymysql

from datetime import datetime

from DEPENDANT.LOGIX import PLCCOMMINCATION
from DEPENDANT.MQTT import MQTT
from DEPENDANT.LOGGING import initializeLogger

BASE_FILE_PATH = os.environ.get('BASE_FILE_PATH', '/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/')

TEMP_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "TEMP_IMG")
RAW_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "RAW_IMG")
LOGS_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "LOGS")

# Database configuration
db_user = os.environ.get('db_user', "root")
db_pass = os.environ.get('db_pass', "insightzz@123")
db_host = os.environ.get('db_host', "127.0.0.1")
db_name = os.environ.get('db_name', "SURFACE_INSPECTION_TALOJA")

# Initialize logger
logger = initializeLogger("TRIGGER", LOGS_PATH=LOGS_PATH)

FRAME_CAPTURE_SIGNAL = "DataToDeltaPLC"

def getDBconn():
    conn = None
    try: 
        conn = pymysql.connect(
            host= db_host,
            user= db_user,
            password= db_pass,
            database= db_name
        )
    except Exception as e:
        print(f"DB Error: {e}")
        logger.error(f"{traceback.format_exc()}")

    return conn

def get_coil_id_from_db_sync_and_save(conn, cycle_uid):
    if conn is None:
        return ""
    
    cursor = None
    try:
          cursor = conn.cursor()
          cursor.execute("SELECT coil_id FROM DB_COIL_ID_SYNC ORDER BY id DESC LIMIT 1")
          sync_row = cursor.fetchone()

          if sync_row and sync_row[0]:
               coil_id = sync_row[0]

               # Insert a new MILL_RUNS row for this cycle using the synced coil_id
               try:
                    insert_sql = (
                         "INSERT INTO MILL_RUNS (cycle_uid, coil_id, created_at, status) "
                         "VALUES (%s, %s, NOW(), %s)"
                    )
                    cursor.execute(insert_sql, (cycle_uid, coil_id, 'RUNNING'))
                    conn.commit()
               except Exception:
                    # If insert fails, log and continue returning the coil_id
                    logger.error(f"Failed to insert MILL_RUNS row for {cycle_uid}: {traceback.format_exc()}")

               return coil_id
    except Exception:
        logger.error(f"Failed to fetch coil_id for {cycle_uid}: {traceback.format_exc()}")
    finally:
        if cursor:
            cursor.close()
            
    return ""

def main():
     mq_obj = MQTT("TRIGGER")
     mq_obj.subscribe("taloja/writer")
     logger.info("Starting trigger manager")

     plcobj = PLCCOMMINCATION("192.168.1.13")
     plcobj.init()
     time.sleep(4)

     while True:
          try: 
               diameter = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[0]")
               mq_obj.publish("taloja/writer", {"diameter": diameter})

               mill_speed = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[1]") 
               eg_setpoint = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[3]") 
               width = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[4]") 
               length = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[5]") 

               mill_run = plcobj.read_bit(0, f"{FRAME_CAPTURE_SIGNAL}[10]") 
               ironing_raised = plcobj.read_bit(1, f"{FRAME_CAPTURE_SIGNAL}[10]")

               if ((mill_run == 1) or (mill_run == '1')) and diameter > 570:
                    roll_num = f'ROLL_{datetime.now().strftime("%Y%m%d%H%M%S")}'
                    conn = getDBconn()
                    coil_id = get_coil_id_from_db_sync_and_save(conn, roll_num)
                    if not (("STCS" in coil_id) or ("STCL" in coil_id)): 
                         print(f"Ignoring trigger as coil_id is not valid: {coil_id} == STCS/STCL")    
                         logger.debug(f"Ignoring trigger as coil_id is not valid: {coil_id} == STCS/STCL")        
                         continue
                    mq_obj.publish("taloja/capture", {"action": "start", "roll_num": roll_num})
                    print(f"Trigger recieved - {roll_num} - {coil_id} - {datetime.now()}")
                    logger.info(f"Trigger recieved - {roll_num} - {coil_id}")
                    time.sleep(2)

                    while True:

                         diameter = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[0]")
                         mill_speed = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[1]") 
                         eg_setpoint = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[2]") 
                         width = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[3]") 
                         length = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[4]")
                         mq_obj.publish("taloja/health", {"service": "TRIGGER"})
                         mq_obj.publish("taloja/writer", {"diameter": diameter})
                         mq_obj.publish("taloja/capture", {"diameter": diameter, "mill_speed": mill_speed, "eg_setpoint": eg_setpoint, "width": width, "length": length}) 

                         ironing_raised = plcobj.read_bit(1, f"{FRAME_CAPTURE_SIGNAL}[10]")

                         if ironing_raised == 1:
                              mq_obj.publish("taloja/capture", {"action": "stop", "roll_num": roll_num})
                              mq_obj.publish("taloja/writer", {"light": 1})
                              time.sleep(5)
                              # logger.info(f"diameter = {diameter} | mill_speed = {mill_speed} | eg_setpoint = {eg_setpoint} | width = {width} | length = {length}")
                              # logger.info(f"mill_run = {mill_run} | ironing_raised = {ironing_raised}")
                              break
                         else:
                              mq_obj.publish("taloja/writer", {"light": 0})

                         # logger.info(f"diameter = {diameter} | mill_speed = {mill_speed} | eg_setpoint = {eg_setpoint} | width = {width} | length = {length}")
                         # logger.info(f"mill_run = {mill_run} | ironing_raised = {ironing_raised}")
                         # time.sleep(1)
                        
               if ironing_raised == 1:
                    mq_obj.publish("taloja/writer", {"light": 1})
               if ironing_raised == 0:
                    mq_obj.publish("taloja/writer", {"light": 0})

               # logger.info(f"diameter = {diameter} | mill_speed = {mill_speed} | eg_setpoint = {eg_setpoint} | width = {width} | length = {length}")
               # logger.info(f"mill_run = {mill_run} | ironing_raised = {ironing_raised}")
               print(f"diameter = {diameter} | mill_run = {mill_run} | ironing_raised = {ironing_raised} - {datetime.now()}")
               mq_obj.publish("taloja/health", {"service": "TRIGGER"})
               time.sleep(2)

          except Exception as e:
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