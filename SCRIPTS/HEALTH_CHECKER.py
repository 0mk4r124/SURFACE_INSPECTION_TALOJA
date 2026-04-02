import platform
import subprocess
import time
import pymysql
import os
import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

# Global Configuration HashMap
configHashMap = {}
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.xml")

class CONFIG_KEY_NAME:
    LOG_FILE_PATH = "LOG_FILE_PATH"
    LOG_BACKUP_COUNT = "LOG_BACKUP_COUNT"
    DB_HOST = "DB_HOST"
    DB_USER = "DB_USER"
    DB_PASS = "DB_PASS"
    DB_NAME = "DB_NAME"
    DEVICES = "DEVICES"

def loadConfiguration():
    """Reads database and device configurations from config.xml, matching the reference style"""
    global configHashMap
    try:
        if not os.path.exists(CONFIG_PATH):
            print(f"Critical: Config file not found at {CONFIG_PATH}")
            return False

        config_parse = ET.parse(CONFIG_PATH)
        config_root = config_parse.getroot()

        # Loading from SYSTEM_PATH (index 0)
        configHashMap[CONFIG_KEY_NAME.LOG_FILE_PATH] = config_root[0][0].text
        configHashMap[CONFIG_KEY_NAME.LOG_BACKUP_COUNT] = int(config_root[0][1].text)

        # Loading from DATABASE_DETAILS (index 1)
        configHashMap[CONFIG_KEY_NAME.DB_HOST] = config_root[1][0].text
        configHashMap[CONFIG_KEY_NAME.DB_USER] = config_root[1][1].text
        configHashMap[CONFIG_KEY_NAME.DB_PASS] = config_root[1][2].text
        configHashMap[CONFIG_KEY_NAME.DB_NAME] = config_root[1][3].text

        # Loading from DEVICES (index 2)
        devices_list = []
        for dev in config_root[2]:
            ip_val = dev[1].text
            devices_list.append({
                "name": dev[0].text,
                "ip": ip_val if ip_val and ip_val.lower() != "none" else None
            })
        
        configHashMap[CONFIG_KEY_NAME.DEVICES] = devices_list
        return True
    except Exception as e:
        print(f"loadConfiguration() Exception: {e}")
        return False

def setup_logger():
    """Initializes the logger using settings from configHashMap"""
    log_dir = configHashMap.get(CONFIG_KEY_NAME.LOG_FILE_PATH)
    backup_count = configHashMap.get(CONFIG_KEY_NAME.LOG_BACKUP_COUNT, 7)
    
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
        
    log_file = os.path.join(log_dir, "health_checker.log")
    handler = TimedRotatingFileHandler(log_file, when="midnight", interval=1, backupCount=backup_count)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[handler, logging.StreamHandler()]
    )
    return logging.getLogger(__name__)

def ping_device(ip, logger):
    """Returns 'ACTIVE', 'INACTIVE', or 'N/A'"""
    if ip is None:
        return "N/A"

    param = "-n" if platform.system().lower() == "windows" else "-c"
    command = ["ping", param, "1", ip]

    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=2)
        return "ACTIVE" if result.returncode == 0 else "INACTIVE"
    except Exception as e:
        logger.error(f"Error pinging {ip}: {e}")
        return "INACTIVE"

def update_db_status(ip, status, device_name, logger):
    """Updates the health status in the database using configHashMap details"""
    db = None
    try:
        db = pymysql.connect(
            host=configHashMap.get(CONFIG_KEY_NAME.DB_HOST),
            user=configHashMap.get(CONFIG_KEY_NAME.DB_USER),
            passwd=configHashMap.get(CONFIG_KEY_NAME.DB_PASS),
            db=configHashMap.get(CONFIG_KEY_NAME.DB_NAME)
        )
        cur = db.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Check if record exists for this device name (DEVICE_TYPE)
        cur.execute("SELECT ID FROM HEALTH_STATUS WHERE DEVICE_TYPE = %s", (device_name,))
        row = cur.fetchone()
        
        if row:
            update_query = """
                UPDATE HEALTH_STATUS 
                SET STATUS = %s, LAST_PING = %s, IP = %s, LOCATION = %s 
                WHERE DEVICE_TYPE = %s
            """
            cur.execute(update_query, (status, now, ip, "TALOJA", device_name))
        else:
            insert_query = """
                INSERT INTO HEALTH_STATUS (LOCATION, IP, STATUS, LAST_PING, DEVICE_TYPE) 
                VALUES (%s, %s, %s, %s, %s)
            """
            cur.execute(insert_query, ("TALOJA", ip, status, now, device_name))
            
        db.commit()
        logger.info(f"DB Update: {device_name} -> {status}")
    except Exception as e:
        logger.error(f"Database update failed for {device_name}: {e}")
    finally:
        if db:
            db.close()

def main():
    if not loadConfiguration():
        return

    logger = setup_logger()
    logger.info("==========================================")
    logger.info("SERVICE START: HEALTH CHECKER (REFERENCE STYLE)")
    logger.info("==========================================")
    
    while True:
        try:
            # Re-load config each cycle to allow dynamic updates
            if not loadConfiguration():
                logger.warning("FAILED TO LOAD CONFIG, Retrying in 10s...")
                time.sleep(10)
                continue

            results = {}
            for device in configHashMap.get(CONFIG_KEY_NAME.DEVICES, []):
                name = device['name']
                ip = device['ip']
                
                status = ping_device(ip, logger)
                results[name] = status
                update_db_status(ip, status, name, logger)
            
            logger.info(f"Check Complete: {results}")
            
        except Exception as e:
            logger.error(f"Main loop critical error: {e}")
        
        time.sleep(10)

if __name__ == "__main__":
    main()
