import cv2
import os
import time
import traceback

import numpy as np

from hikvisionapi import Client

class IPCamera:

    def __init__(self, config: dict):
        self.config = config
        self.client = None
        self.channel = int(self.config.get("channel", 101))
        self._is_connected = False


    def initialize(self):
        try:
            ip = self.config.get("ip")
            user = self.config.get("user")
            password = self.config.get("password")
            if not (ip and user and password): raise ValueError("IP, user and password must be provided in config")

            self.client = Client(f"http://{ip}", user, password)

            try:
                _ = self.client.Streaming.channels[self.channel].picture(method='get', type='opaque_data')
                self._is_connected = True
                return True
            except Exception:
                # still consider client created, but mark not connected
                self._is_connected = False
                return False
            
        except Exception as e:
            print(f"IPCamera initialize error: {e}")
            print(traceback.format_exc())
            self.client = None
            self._is_connected = False

        return False
    
    def capture(self, save=True, timeout=10):
        if not self.client:
            print("IPCamera: client not initialized")
            return None

        try:
            vid = self.client.Streaming.channels[self.channel].picture(method='get', type='opaque_data')
            bytes_buf = b''
            img = None
            for chunk in vid.iter_content(chunk_size=1024):
                bytes_buf += chunk
                a = bytes_buf.find(b'\xff\xd8')
                b = bytes_buf.find(b'\xff\xd9')
                if a != -1 and b != -1:
                    jpg = bytes_buf[a:b+2]
                    bytes_buf = bytes_buf[b+2:]
                    img = cv2.imdecode(np.frombuffer(jpg, dtype=np.uint8), cv2.IMREAD_COLOR)
                    break


            if img is None:
                print("IPCamera: failed to decode image")
                return None

            # Optionally save
            if save:
                save_path = self.config.get("save_path")
                name = self.config.get("name", "ipcam")
                if save_path:
                    os.makedirs(save_path, exist_ok=True)
                    filename = os.path.join(save_path, f"{name}_{int(time.time()*1000)}.jpg")
                    cv2.imwrite(filename, img)
            return img


        except Exception as e:
            print(f"IPCamera capture error: {e}")
            print(traceback.format_exc())
            self._is_connected = False
        return None
    
    def deinitialize(self):
        try:
            self.client = None
            self._is_connected = False
            return True
        except Exception as e:
            print(f"IPCamera deinitialize error: {e}")
            return False

