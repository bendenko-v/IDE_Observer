import logging

import socketio

from models import Room

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
file_handler = logging.FileHandler('../log.log')
file_handler.setLevel(logging.ERROR)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)


async def validate_data(sio: socketio.AsyncServer, data, *keys) -> bool:
    """
    Validate incoming data
    Args:
        sio: SocketIO server
        data: JSON object
        *keys: list of keys
    Returns:
        True if data is valid
    """
    if not isinstance(data, dict):
        await handle_bad_request(sio, 'Data should be a JSON object')
        return False

    for key in keys:
        if key not in data:
            await handle_bad_request(sio, f'{key} is not present in data')
            return False
        if key in ('content', 'files_to_ignore'):
            return True
        if key == 'accepted' and not isinstance(data[key], bool):
            await handle_bad_request(sio, f'{key} should be a boolean')
            return False
        if not isinstance(data[key], int):
            await handle_bad_request(sio, f'{key} should be an integer')
            return False
        if key == 'room_id' and not Room.get_room_by_id(data[key]):
            await handle_bad_request(sio, f'No room with id: {data[key]}')
            return False
    return True


async def emit_log(sio: socketio.AsyncServer, message: str) -> None:
    """
    Emit a log message.
    Args:
        sio (socketio.AsyncServer): The Socket.IO server instance.
        message (str): The log message to emit.
    """
    await sio.emit('log', data={'message': message})


async def handle_bad_request(sio: socketio.AsyncServer, message: str) -> None:
    """
    Handle bad request by emitting an error event and logging the error message.
    Args:
        sio (socketio.AsyncServer): The Socket.IO server instance.
        message (str): The error message.
    """
    await sio.emit('error', data={'message': f'400 BAD REQUEST. {message}'})
    logger.error(f'Bad request occurred: {message}')