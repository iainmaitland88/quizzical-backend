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
    invalid_message = "invalid_message"
    connection_ack = "connection_ack"
    buzzer = "buzzer"
    buzzer_won = "buzzer_won"
    buzzer_lost = "buzzer_lost"
    buzzer_reset = "buzzer_reset"
    user_joined = "user_joined"
    user_left = "user_left"
    user_registration_attempt = "user_registration_attempt"
    user_registration_success = "user_registration_success"
    user_registration_error = "user_registration_error"


class Message(BaseModel):
    message_type: MessageType
    message_data: Optional[dict[Any, Any]]


class Connection:
    def __init__(self, websocket: WebSocket) -> None:
        self.id = str(uuid4())
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

    async def broadcast(
        self, message: Message, excluding: Optional[List[Connection]] = None
    ):
        # TODO handle websockets.exceptions.ConnectionClosedError:
        if not excluding:
            excluding = []

        for connection in [c for c in self.active_connections if c not in excluding]:
            await connection.websocket.send_json(message.dict())


class GameState:
    def __init__(self) -> None:
        self._locked = False
        self._connection_id: Optional[str] = None

    def lock(self, connection_id: str) -> None:
        self._locked = True
        self._connection_id = connection_id

    def unlock(self) -> None:
        self._locked = False

    @property
    def locked(self) -> bool:
        return self._locked is True


connection_manager: ConnectionManager = ConnectionManager()
game_state: GameState = GameState()
players: List[str] = []
players_connection: dict[str, str] = {}


@app.get("/")
async def get():
    return html_response("app/static/index.html")


async def handle_buzzer(connection: Connection, message: Message):
    if not game_state.locked:
        game_state.lock(connection.id)
        await connection_manager.broadcast(
            Message(message_type=MessageType.buzzer_lost), [connection]
        )
        await connection_manager.send_personal_message(
            Message(message_type=MessageType.buzzer_won), connection
        )


async def handle_user_registration_attempt(connection: Connection, message: Message):
    username = message.message_data["username"]

    if username in players:
        await connection_manager.send_personal_message(
            Message(
                message_type=MessageType.user_registration_error,
                message_data={"errors": ["username is taken"]},
            ),
            connection,
        )
    else:
        players.append(username)
        players_connection[connection.id] = username
        await connection_manager.send_personal_message(
            Message(message_type=MessageType.user_registration_success), connection
        )
        await connection_manager.broadcast(
            Message(
                message_type=MessageType.user_joined,
                message_data={"username": username},
            )
        )


async def handle_connection(connection: Connection) -> None:
    try:
        print(f"players={players}")
        print(f"players_connections={players_connection}")

        raw_message = await connection.websocket.receive_text()
        message = Message.parse_raw(raw_message)

        if message.message_type == MessageType.user_registration_attempt:
            await handle_user_registration_attempt(connection, message)

        if message.message_type == MessageType.buzzer:
            await handle_buzzer(connection, message)

        print(f"players={players}")
        print(f"players_connections={players_connection}")
    except ValidationError as e:
        await connection_manager.send_personal_message(
            Message(
                message_type=MessageType.invalid_message,
                message_data={"errors": e.json()},
            ),
            connection,
        )


@app.websocket("/ws/")
async def websocket_endpoint(websocket: WebSocket):
    connection = Connection(websocket)
    await connection_manager.connect(connection)
    await connection_manager.send_personal_message(
        Message(
            message_type=MessageType.connection_ack,
            message_data={"connection_id": connection.id},
        ),
        connection,
    )

    try:
        while True:
            await handle_connection(connection)
    except WebSocketDisconnect:
        connection_manager.disconnect(connection)
        try:
            username = players_connection.pop(connection.id)
            players.remove(username)
            await connection_manager.broadcast(
                Message(
                    message_type=MessageType.user_left,
                    message_data={"username": username},
                )
            )
        except (KeyError, ValueError):
            pass


@app.post("/unlock")
async def unlock():
    game_state.unlock()
    await connection_manager.broadcast(Message(message_type=MessageType.buzzer_reset))
