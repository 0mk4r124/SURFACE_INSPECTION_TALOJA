import os
import time
import traceback

from pymodbus.client import ModbusTcpClient
from datetime import datetime

from DEPENDANT.MQTT import MQTT
from DEPENDANT.LOGGING import initializeLogger


BASE_FILE_PATH = os.environ.get(
    "BASE_FILE_PATH",
    "/home/hindalco_surface_inspection/insightzz/SURFACE_INSPECTION_TALOJA/"
)

PLC_IP = "192.168.2.5"
PLC_PORT = 502
PLC_TIMEOUT = 3
LOGS_PATH = os.path.join(BASE_FILE_PATH, "DEBUGS", "LOGS")

logger = initializeLogger(
    "WRITER", LOGS_PATH=os.path.join(BASE_FILE_PATH, "DEBUGS", "LOGS")
)


class WRITE_REGISTERS:
    PLC_WRITE = 4106
    PLC_STOP_LIGHT = 4247


class PLCWriter:

    def __init__(self):
        self.client = None
        self.last_diameter = None
        self.last_light = None
        self.connect()

    def connect(self):

        try:
            if self.client:
                self.client.close()
        except: pass

        self.client = ModbusTcpClient(
            PLC_IP,
            port=PLC_PORT,
            timeout=PLC_TIMEOUT
        )

        self.client.connect()
        logger.info("Connected to Modbus PLC")

    def write_register(self, register, value):

        try:

            if not self.client.connected:
                self.connect()

            self.client.write_register(
                address=register,
                value=int(float(value))
            )

            return True

        except Exception:
            logger.error(traceback.format_exc())
            
            try:self.connect()
            except:pass
            
            return False

    def update(self, diameter, light):

        try:

            if diameter is not None:
                self.write_register(
                    WRITE_REGISTERS.PLC_WRITE,
                    diameter
                )
                logger.info(f"WROTE | {WRITE_REGISTERS.PLC_WRITE} {diameter}")

            if light is not None:
                self.write_register(
                    WRITE_REGISTERS.PLC_STOP_LIGHT,
                    light
                )
                logger.info(f"WROTE | {WRITE_REGISTERS.PLC_STOP_LIGHT} {light}")

        except Exception:
            logger.error(traceback.format_exc())


def main():

    mq = MQTT("WRITER")
    mq.subscribe("taloja/writer")
    plc_writer = PLCWriter()

    logger.info("Writer service started")

    while True:

        try:
            mq.publish("taloja/health", {"service": "WRITER"})
            data = mq.pop("taloja/writer")

            if not data:
                time.sleep(0.05)
                continue

            diameter = data.get("diameter")
            light = data.get("light")
            
            plc_writer.update(diameter, light)
            print(f"DIAMETER : {diameter} | LIGHT : {light} | {datetime.now()}")

        except Exception:
            logger.error(traceback.format_exc())
            time.sleep(1)


if __name__ == "__main__":

    while True:

        try:main()
        except Exception:
            logger.critical(traceback.format_exc())
            print("Error in main()")
            print("Restarting in 5 seconds")

            time.sleep(5)