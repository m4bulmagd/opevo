def test_websocket_requires_auth_message_before_events(client) -> None:
    with client.websocket_connect("/ws") as websocket:
        websocket.send_json({"type": "ping"})
        message = websocket.receive_json()

    assert message["type"] == "error"
