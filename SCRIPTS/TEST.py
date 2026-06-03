import os
import traceback
import pymssql

# from DEPENDANT.LOGIX import PLCCOMMINCATION
from DEPENDANT.LOGGING import initializeLogger
from DEPENDANT.MQTT import MQTT


BASE_FILE_PATH = os.environ.get('BASE_FILE_PATH', '/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/')
LOGS_PATH = BASE_FILE_PATH + "DEBUGS/LOGS/"

# Initialize logger
logger = initializeLogger("TEST", LOGS_PATH=LOGS_PATH)

FRAME_CAPTURE_SIGNAL = "DataToDeltaPLC"

sql_server_params = {
    "server": "192.168.1.33",
    "port": 1433,
    "user": "saread",
    "password": "saread",
    "database": "L2_Davy"
}

# =========================
# CONNECT TO SQL SERVER
# =========================

def dbconn():
    conn = None
    try:
        conn = pymssql.connect(
            server=sql_server_params["server"],
            user=sql_server_params["user"],
            password=sql_server_params["password"],
            database=sql_server_params["database"],
            port=sql_server_params["port"],
            timeout=5
        )

        print("Connected successfully")

    except Exception as e:
        print("Connection failed :", e)
    
    return conn

def main():
    # Create cursor
    conn = dbconn()
    cursor = conn.cursor()

    # # =========================
    # # FETCH DATA
    # # =========================

    query = "SELECT TOP 10 * FROM VW_LIVE_COIL_DATA"
    cursor.execute(query)

    # # Get column names
    columns = [desc[0] for desc in cursor.description]

    print("\nColumns:\n", columns)
    print("\nData:\n")

    # # Print header
    print("\t".join(columns))

    # # Print rows
    # for row in cursor.fetchall():
    #     print(
    #         "\t".join(
    #             [str(item) if item is not None else "" for item in row]
    #         )
    #     )

    # # Close connection
    cursor.close()
    conn.close()

    mq_obj = MQTT("TEST")
    # diameter, mill_speed, eg_setpoint, width, length = 0, 0, 0, 150, 0

    # while True:
    #     mq_obj.publish("taloja/capture", {"action": "start", "roll_num": "DEMO"})
    #     while True:
    #         mq_obj.publish("taloja/capture", {"diameter": diameter, "mill_speed": mill_speed, "eg_setpoint": eg_setpoint, "width": width, "length": length}) 

    #         diameter += 1
    #         mill_speed += 10
    #         eg_setpoint += 0.05
    #         length += 100

    #         if length>10000000: break
            

    #     mq_obj.publish("taloja/capture", {"action": "stop", "roll_num": "DEMO"})



    # plcobj = PLCCOMMINCATION("192.168.1.13")
    # plcobj.init()

    # while True:
    #     try: 
    #         result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[0]")
    #         print("[0] :", result)
    #     except: pass

    #     try: 
    #         result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[1]")
    #         print("[1] :", result)
    #     except: pass

    #     try: 
    #         result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[2]")
    #         print("[2] :", result)
    #     except: pass

    #     try: 
    #         result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[3]")
    #         print("[3] :", result)
    #     except: pass

    #     try: 
    #         result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[4]")
    #         print("[4] :", result)
    #     except: pass

    #     try: 
    #         result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[5]")
    #         print("[5] :", result)
    #     except: pass

    #     try: 
    #         result = plcobj.read(f"{FRAME_CAPTURE_SIGNAL}[6]")
    #         print("[6] :", result)
    #     except: pass

if __name__ == "__main__":

     while True:
          try: main()
          except Exception as e:
               logger.critical(f"Main Outer Loop Exception is {e}")
               logger.critical(traceback.format_exc())
