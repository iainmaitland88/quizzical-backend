from enum import Enum
from typing import Any, List, Optional
from uuid import uuid4

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ValidationError

app = FastAPI()


def html_response(path: str) -> HTMLResponse:
    with open(path) as f:
        return HTMLResponse(f.read())


class MessageType(str, Enum):
    connection_ack = "connection_ack"
    buzzer = "buzzer"
    game_state_locked = "game_state_locked"
    game_state_unlocked = "game_state_unlocked"
    user_joined = "user_joined"
    user_left = "user_left"


class Message(BaseModel):
    message_type: MessageType
    message_data: Optional[dict[Any, Any]]


class Connection:
    def __init__(self, user_id: str, websocket: WebSocket) -> None:
        self.user_id = user_id
        self.websocket = websocket


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[Connection] = []

    async def connect(self, connection: Connection):
        await connection.websocket.accept()
        self.active_connections.append(connection)

    def disconnect(self, connection: Connection):
        self.active_connections.remove(connection)

    async def send_personal_message(self, message: Message, connection: Connection):
        # TODO handle websockets.exceptions.ConnectionClosedError:
        await connection.websocket.send_json(message.dict())

    async def broadcast(self, message: Message):
        # TODO handle websockets.exceptions.ConnectionClosedError:
        for connection in self.active_connections:
            await connection.websocket.send_json(message.dict())


class GameState:
    def __init__(self) -> None:
        self._locked = False
        self._user_id: Optional[str] = None

    def lock(self, user_id: str) -> None:
        self._locked = True
        self._user_id = user_id

    def unlock(self) -> None:
        self._locked = False

    @property
    def locked(self) -> bool:
        return self._locked is True


connection_manager = ConnectionManager()
game_state = GameState()


@app.get("/")
async def get():
    return html_response("app/static/index.html")


async def handle_buzzer(connection: Connection, message: Message):
    if not game_state.locked:
        game_state.lock(connection.user_id)
        message = Message(
            message_type=MessageType.game_state_locked,
            message_data={"user_id": connection.user_id},
        )
        await connection_manager.broadcast(message)


async def handle_connection(connection: Connection) -> None:
    try:
        raw_message = await connection.websocket.receive_text()
        message = Message.parse_raw(raw_message)
        if message.message_type == MessageType.buzzer:
            await handle_buzzer(connection, message)
    except ValidationError as e:
        await connection_manager.send_personal_message(e.json(), connection)


@app.websocket("/ws/")
async def websocket_endpoint(websocket: WebSocket):
    user_id = str(uuid4())
    connection = Connection(user_id, websocket)
    await connection_manager.connect(connection)
    await connection_manager.send_personal_message(
        Message(
            message_type=MessageType.connection_ack, message_data={"user_id": user_id}
        ),
        connection,
    )
    await connection_manager.broadcast(
        Message(message_type=MessageType.user_joined, message_data={"user_id": user_id})
    )

    try:
        while True:
            await handle_connection(connection)
    except WebSocketDisconnect:
        connection_manager.disconnect(connection)
        await connection_manager.broadcast(
            Message(
                message_type=MessageType.user_left, message_data={"user_id": user_id}
            )
        )


@app.post("/unlock")
async def unlock():
    game_state.unlock()
    await connection_manager.broadcast(
        Message(message_type=MessageType.game_state_unlocked)
    )
