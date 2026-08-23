import json
import logging
from typing import Dict, List, Optional
from fastapi import WebSocket

logger = logging.getLogger("connection_manager")


class ConnectionManager:
    def __init__(self):
        # user_id -> { device_id: WebSocket }
        self.active_connections: Dict[str, Dict[str, WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: str, device_id: str):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = {}
        self.active_connections[user_id][device_id] = websocket
        logger.info(f"WebSocket connected for user {user_id}, device {device_id}")

        # Broadcast device connected event to user's other devices
        await self.broadcast_to_user(
            user_id=user_id,
            message={
                "type": "DEVICE_CONNECTED",
                "device_id": device_id,
                "active_devices": list(self.active_connections[user_id].keys())
            },
            exclude_device_id=device_id
        )

    def disconnect(self, user_id: str, device_id: str):
        if user_id in self.active_connections:
            self.active_connections[user_id].pop(device_id, None)
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
        logger.info(f"WebSocket disconnected for user {user_id}, device {device_id}")

    async def broadcast_to_user(
        self,
        user_id: str,
        message: dict,
        exclude_device_id: Optional[str] = None
    ):
        if user_id not in self.active_connections:
            return

        dead_devices = []
        msg_str = json.dumps(message)
        for dev_id, ws in self.active_connections[user_id].items():
            if exclude_device_id and dev_id == exclude_device_id:
                continue
            try:
                await ws.send_text(msg_str)
            except Exception as e:
                logger.warning(f"Failed to send to device {dev_id} ({e}), marking dead.")
                dead_devices.append(dev_id)

        for dev_id in dead_devices:
            self.disconnect(user_id, dev_id)


manager = ConnectionManager()
