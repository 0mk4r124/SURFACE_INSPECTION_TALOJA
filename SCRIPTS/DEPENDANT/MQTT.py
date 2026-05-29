import json
import uuid
import collections
import threading
import paho.mqtt.client as mqtt


class MQTT:

    def __init__(self, name: str, broker: str = "127.0.0.1", port: int = 1883):
        self.name = name
        self.data        = {}
        self.isNewStatus = False
        self._queues: dict[str, collections.deque] = {}
        self._q_lock = threading.Lock()

        try:
            # paho-mqtt >= 2.0
            self.client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION1,
                client_id=f"{name}-{uuid.uuid4().hex[:4]}"
            )
        except AttributeError:
            # paho-mqtt < 2.0 fallback
            self.client = mqtt.Client(
                client_id=f"{name}-{uuid.uuid4().hex[:4]}"
            )

        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message

        print(f"[MQTT-{name}] Connecting to broker {broker}:{port} …")
        self.client.connect(broker, port, 30)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, rc):
        print(f"[MQTT-{self.name}] Connected (rc={rc})")

    def _on_message(self, client, userdata, msg):
        try:
            payload = json.loads(msg.payload.decode())
        except Exception:
            payload = {}

        # 1. Simple single-value store for worker scripts
        self.data        = payload
        self.isNewStatus = True

        # 2. Per-topic queue for the Manager
        with self._q_lock:
            if msg.topic in self._queues:
                self._queues[msg.topic].append(payload)

        print(f"[MQTT-{self.name}] RECEIVED | {msg.topic} | {payload}")

    def publish(self, topic: str, payload: dict, retain: bool = False):
        self.client.publish(topic, json.dumps(payload), retain=retain)

    def subscribe(self, topic: str):
        with self._q_lock:
            if topic not in self._queues:
                self._queues[topic] = collections.deque()
        self.client.subscribe(topic)
        print(f"[MQTT-{self.name}] SUBSCRIBED | {topic}")

    def pop(self, topic: str):
        with self._q_lock:
            q = self._queues.get(topic)
            if q:
                try:
                    return q.popleft()
                except IndexError:
                    pass
        return None

    def peek(self, topic: str):
        with self._q_lock:
            q = self._queues.get(topic)
            if q:
                try:
                    return q[0]
                except IndexError:
                    pass
        return None