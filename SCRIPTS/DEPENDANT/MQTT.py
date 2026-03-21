import json
import paho.mqtt.client as mqtt
import uuid
import time
import json

class MQTT:
    def __init__(self, name, broker="127.0.0.1", port=1883):
        self.name = name
        self.data = {}
        self.isNewStatus = False
        self.client = mqtt.Client(client_id=f"{name}-{uuid.uuid4().hex[:4]}")

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        print(f"[MQTT-{name}] Connecting to broker...")
        self.client.connect(broker, port, 30)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, rc):
        print(f"[MQTT-{self.name}] Connected (rc={rc})")

    def _on_message(self, client, userdata, msg):
        self.data = json.loads(msg.payload.decode())
        print(
            f"[MQTT-{self.name}] RECEIVED | {msg.topic} | {self.data}"
        )

    def publish(self, topic, payload, retain=False):
        msg = json.dumps(payload)
        self.client.publish(topic, msg, retain=retain)
        # print(f"[MQTT-{self.name}] PUBLISHED | {topic} | {msg}")

    def subscribe(self, topic):
        self.client.subscribe(topic)
        print(f"[MQTT-{self.name}] SUBSCRIBED | {topic}")