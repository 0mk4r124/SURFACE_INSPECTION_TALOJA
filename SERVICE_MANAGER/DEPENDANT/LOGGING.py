import logging
import os
import traceback

from logging.handlers import TimedRotatingFileHandler

def initializeLogger(logger_name, LOGS_PATH="LOGS"):
    logger = None

    try:
        LOG_BACKUP_COUNT = 2
        logger = logging.getLogger(logger_name)
        logger.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        log_file = os.path.join(LOGS_PATH, f"{logger_name}.log")
        file_handler = TimedRotatingFileHandler(log_file, when='midnight', backupCount=LOG_BACKUP_COUNT)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.debug(f"Logger initialized for {logger_name}")
    except Exception as e:
        print(f"initializeLogger() Exception for {logger_name}: {e}")
        print(traceback.format_exc())

    return logger