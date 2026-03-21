import time
import traceback
import os

import snap7
from snap7 import util

MAX_RETRY_ATTEMPTS = 3

class PLCCommunication:
    def __init__(self, plcIPAddress, dbReadNumber, dbWriteNumber, dbReadStr):
        self.PLC_IP_ADDRESS = plcIPAddress 
        self.DB_READ_NUMBER = dbReadNumber 
        self.DB_WRITE_NUMBER = dbWriteNumber  
        self.DB_READ_STR_NUMBER = dbReadStr 

    def createConnection(self):
        client = None
        try:
            client = snap7.client.Client()
            client.connect(self.PLC_IP_ADDRESS,0,1)
        except Exception as e:
            print("createCOnnection() Exception is : "+ str(e))
        
        return client

    def isPLCConnected(self, clientConn):
        isConnected = False
        try:
            clientConn.get_connected()
            isConnected = True
            print("PLC CONNECT")
        except Exception as e:
            print("isPLCConnected() Exception is : "+ str(e))
            isConnected = False
        
        return isConnected

    def closePLCConnection(self, clientConn):
        try:
            clientConn.destroy()
        except Exception as e:
            print("closePLCConnection() Exception is : "+ str(e))

    ''' Read PLC Functions '''
    def readIntFromPLC(self, clientConn, db_col_start_buffer_pos): 
        row_data = None
        try:
            if self.isPLCConnected(clientConn) is True:
                db = clientConn.db_read(self.DB_READ_NUMBER, db_col_start_buffer_pos, 2) 
                row_data = util.get_int(db, 0)
                print("readIntFromPLC",row_data)
            else:
                print("PLC not connected")
        except Exception as e:
            print("readIntFromPLC() Exception is : "+ str(e))

        return row_data

    def readBoolFromPLC(self, clientConn, db_col_start_buffer_pos):
        time.sleep(0.2)
        row_data = None

        try:
            if self.isPLCConnected(clientConn) is True:
                db = clientConn.db_read(self.DB_READ_NUMBER, db_col_start_buffer_pos, 1)
                row_data = util.get_bool(db, 0 ,0)
            else:
                print("PLC not connected")
        except Exception as e:
            print("readBoolFromPLC() Exception is : "+ str(e))
        
        return row_data

    def readDoubleFromPLC(self, clientConn, db_col_start_buffer_pos):
        row_data = None

        try:
            if self.isPLCConnected(clientConn) is True:
                db = clientConn.db_read(self.DB_READ_NUMBER, db_col_start_buffer_pos, 4) 
                row_data = util.get_dint(db, 0)
            else:
                print("PLC not connected")
        except Exception as e:
            print("readDoubleFromPLC() Exception is : "+ str(e))

        return row_data

    def readStringFromPLC(self, clientConn, db_col_start_buffer_pos):
        row_data = None
        ERROR_CODE=0

        try:
            for _ in range(MAX_RETRY_ATTEMPTS):
                time.sleep(0.2)
                if self.isPLCConnected(clientConn) is True:
                    db = clientConn.db_read(self.DB_READ_STR_NUMBER, db_col_start_buffer_pos, 14)
                    row_data = db[2:].decode("utf-8")
                    print(f"Read String value: {row_data}")
                else:
                    print("PLC isPLCConnected(clientConn) is False ")
                    print("PLC not connected")

        except Exception as e:
            print("readStringFromPLC() Exception is :",e)
            ERROR_CODE=1
            print("readStringFromPLC() Exception is : "+ str(e))
            time.sleep(0.2)

        return row_data,ERROR_CODE

    ''' Write PLC Functions '''        
    def writeBoolToPLC(self, clientConn, db_col_start_buffer_pos , bool_value):
        try:
            for _ in range(MAX_RETRY_ATTEMPTS):
                if self.isPLCConnected(clientConn) is True:
                    data = bytearray(1)
                    util.set_int(data,db_col_start_buffer_pos,0,bool_value)
                    clientConn.db_write(self.DB_WRITE_NUMBER,db_col_start_buffer_pos,data)
                else:
                    print("PLC not connected")
        except Exception as e:
            print("writeBoolToPLC() Exception is : "+ str(e))
            print("writeBoolToPLC() Exception is : "+ str(e))

    def writeIntToPLC(self, clientConn, db_col_start_buffer_pos , int_value):
        try:
            if self.isPLCConnected(clientConn) is True:
                data = bytearray(2)
                util.set_int(data,db_col_start_buffer_pos,int_value)
                clientConn.db_write(self.DB_WRITE_NUMBER,db_col_start_buffer_pos,data)
            else:
                print("PLC not connected")
        except Exception as e:
            print("writeIntToPLC() Exception is : "+ str(e))

    def writeDoubleToPLC(self, clientConn, db_col_start_buffer_pos, double_value):
        try:
            if self.isPLCConnected(clientConn) is True:
                data = bytearray(4)
                util.set_dint(data,db_col_start_buffer_pos,double_value)
                clientConn.db_write(self.DB_WRITE_NUMBER,db_col_start_buffer_pos,data)
            else:
                time.sleep(0.2)
                print("PLC not connected")
        except Exception as e:
            time.sleep(0.2)
            print("writeDoubleToPLC() Exception is : "+ str(e))
            print("writeDoubleToPLC() Exception is : "+ str(e))

    def writeStringToPLC(self, clientConn, db_col_start_buffer_pos,string_value):
        try:
            if self.isPLCConnected(clientConn) is True:
                
                SPACE_START = "   "
                SPACE_END = "                                                     "
                concat_string = SPACE_START + string_value + SPACE_END
                byte_value = concat_string.encode('utf-8')
                clientConn.db_write(self.DB_WRITE_NUMBER,db_col_start_buffer_pos, data=bytearray(byte_value))
            else:
                print("PLC not connected")
        except Exception as e:
            print("writeStringToPLC() Exception is : "+ str(e))


# if __name__=="__main__":
    # plcCommunicationObj = PLCCommunication(configHashmap.get(CONFIG_KEY_NAME.PLC_IP), configHashmap.get(CONFIG_KEY_NAME.READ_INT), configHashmap.get(CONFIG_KEY_NAME.READ_STR), configHashmap.get(CONFIG_KEY_NAME.WRITE_INT)) 
    # clientConn = plcCommunicationObj.createConnection()

    # while True:
    #     time.sleep(2)
    #     try:
    #         start_trigger = plcCommunicationObj.readIntFromPLC(clientConn,0)
            
    #         if start_trigger == 1:
    #             eng_no = plcCommunicationObj.readStringFromPLC(clientConn,0)
    #             real_no = eng_no[0][:-1]
    #             updatePLCStatusTable(1, real_no, 'PLC')

    #         start_trigger = plcCommunicationObj.readIntFromPLC(clientConn,2)
    #         if start_trigger == 2:
    #             eng_no = plcCommunicationObj.readStringFromPLC(clientConn,0)
    #             real_no = eng_no[0][:-1]
    #             updatePLCStatusTable(1, real_no, 'PLC')

    #         algo_status, eng_no = getAlgoStatusTable()
    #         print(f"ALgo final status ----- {algo_status, eng_no}")
    #         if algo_status != '0':
    #             # plcCommunicationObj.writeIntToPLC(clientConn,0,int(algo_status))
    #             plcCommunicationObj.writeIntToPLC(clientConn,0,1)
    #             updatePLCStatusTable(0, "", 'ALGO')

    #         time.sleep(1)

    #     except: pass
    
    
