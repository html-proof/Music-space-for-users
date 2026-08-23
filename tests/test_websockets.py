import json
import pytest
from starlette.testclient import TestClient
from app.main import app


def test_websocket_player_connect_and_ping():
    with TestClient(app) as client:
        with client.websocket_connect("/ws/player/device-ws-1?token=test_token_ws_user") as websocket:
            # Initial message is PLAYBACK_STATE
            initial_msg = websocket.receive_text()
            initial_data = json.loads(initial_msg)
            assert initial_data["type"] == "PLAYBACK_STATE"

            # Send PING
            websocket.send_text(json.dumps({"type": "PING"}))
            pong_msg = websocket.receive_text()
            pong_data = json.loads(pong_msg)
            assert pong_data["type"] == "PONG"
