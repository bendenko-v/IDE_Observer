import asyncio
from random import choice

from socketio import AsyncServer

from src.models import Room, StatusEnum, User

from .utils import check_version, emit_log, handle_bad_request, validate_data


async def create_room(sio: AsyncServer, sid: str, data: dict) -> None:
    """
    Create a room.
    Args:
        sio (AsyncServer): The socketio server instance.
        sid (str): The session ID of the user.
        data (dict): The data containing the host name.
    """
    if not await validate_data(sio, data):
        return
    hostname = data.get('name', None)
    user = User(sid, role='host', name=hostname)
    room = Room(host=user)
    room.add_user(user)
    user.room = room.rid
    await sio.emit('room/update', data=room.get_room_data(), to=sid)
    # log
    await emit_log(sio, f'User {sid} created room (id: {room.rid})')


async def join_to_room(sio: AsyncServer, sid: str, data: dict) -> None:
    """
    Join a user to a room.
    Args:
        sio (AsyncServer): The socketio server instance.
        sid (str): The session ID of the user.
        data (dict): The data containing room_id.
    """
    if not await validate_data(sio, data, 'room_id'):
        return

    version = data.get('version')
    await check_version(sio, sid, version)

    room_id = data.get('room_id')
    room = Room.get_room_by_id(room_id)
    username = data.get('name', 'Guest ' + ''.join(choice('0123456789') for _ in range(10)))

    user = User.get_user_by_sid(sid)
    if user and user.room is not None:
        await handle_bad_request(sio, f'User already in room (id: {user.room})')
        return

    user = User(sid, role='client', name=username, room_id=room_id)
    room.add_user(user)

    # Message to student
    await sio.emit('room/join', data={'user_id': user.uid, 'room_id': room_id}, to=sid)

    # Deprecated from v1.1.0
    if room.exercise:
        await sio.emit('exercise', data={'content': room.exercise}, to=sid)
        # log
        await emit_log(sio, f'The exercise was sent to the student (id: {user.uid})!')

    if room.steps:
        await sio.emit('steps/all', data=room.steps, to=sid)
        # log
        await emit_log(sio, f'The steps were sent to the student (id: {user.uid})!')

    # If settings exists, send it to student
    if room.settings:
        await sio.emit('settings', data=room.settings, to=sid)
        # log
        await emit_log(sio, f'The settings were sent to the student (id: {user.uid})!')

    # Update data for teacher
    await sio.emit('room/update', data=room.get_room_data(), to=room.host.sid)
    # log
    await emit_log(sio, f'User {user.uid} has joined the Room with id: {room_id}')


async def rejoin(sio: AsyncServer, sid: str, data: dict, command: str) -> None:
    """
    Rejoin a user to a room (host or student).
    Args:
        sio (AsyncServer): The socketio server instance.
        sid (str): The session ID of the user.
        data (dict): The data containing room_id and user_id.
        command (str): The command to execute ('rejoin' or 'rehost').
    """
    if not await validate_data(sio, data, 'room_id', 'user_id'):
        return

    room_id = data.get('room_id')
    room = Room.get_room_by_id(room_id)

    user_id = data.get('user_id', None)
    user = room.get_user_by_id(user_id)
    if not user:
        await handle_bad_request(sio, f'No such user with id {user_id}!')
        return

    user.set_new_sid(sid)  # Save new socket id to user
    user.status = StatusEnum.ONLINE  # Update user status

    if command == 'rejoin':  # Student rejoin
        # 'room/join' event to the student who has reconnected
        await sio.emit('room/join', data={'user_id': user.uid, 'room_id': room_id}, to=sid)
        # log
        await emit_log(sio, f'Student {user.name} with id {user_id} reconnected!')
    else:  # Teacher rehost
        # Messages to students
        for student in room.users:
            if student.role == 'client':
                await sio.emit('message', {'message': 'The teacher has reconnected!'}, to=student.sid)

        # Send imported steps to host if they exist
        if room.steps:
            await sio.emit('steps/load', data=room.steps, to=room.host.sid)

        # log
        await emit_log(sio, f'Host {user.name} with id {user_id} reconnected!')

    # Update data for teacher
    await sio.emit('room/update', data=room.get_room_data(), to=room.host.sid)


async def exit_from_room(sio: AsyncServer, sid: str, data: dict) -> None:
    """
    Leave a room.
    Args:
        sio (AsyncServer): The socketio server instance.
        sid (str): The session ID of the user.
        data (dict): The data containing room_id.
    """
    if not await validate_data(sio, data, 'room_id'):
        return

    room_id = data.get('room_id')
    room = Room.get_room_by_id(room_id)
    user = User.get_user_by_sid(sid)

    if not user:
        await handle_bad_request(sio, f'No user registered with sid: {sid}')
        return

    if not room.remove_user_from_room(user.uid):
        await handle_bad_request(sio, f'User is not in room (id: {user.room})')
        return

    await sio.emit('room/update', data=room.get_room_data(), to=room.host.sid)
    # log
    await emit_log(sio, f'User {user.uid} has left the Room with id: {room_id}')


async def close_room(sio: AsyncServer, data: dict) -> None:
    """
    Close a room by host.
    Args:
        sio (AsyncServer): The socketio server instance.
        data (dict): The data containing 'room_id'.
    """
    if not await validate_data(sio, data, 'room_id'):
        return

    room_id = data.get('room_id')
    room = Room.get_room_by_id(room_id)

    for student in room.users:
        if student.role == 'client':
            await sio.emit('room/closed', {'message': 'Room closed!'}, to=student.sid)

    await asyncio.sleep(2)
    Room.delete_room(room_id)  # Delete all users in the room and room itself
    # log
    await emit_log(sio, f'Room with id: {room_id} has been closed!')


async def disconnect_user(sio: AsyncServer, sid: str) -> None:
    """
    Catch user disconnect
    Args:
        sio (AsyncServer): The Socket.IO server instance.
        sid (str): The session ID of the user.
    """
    user = User.get_user_by_sid(sid)
    if user and user.room:
        user.status = StatusEnum.OFFLINE
        room = Room.get_room_by_id(user.room)

        if user.role == 'host':  # teacher disconnected
            for student in room.users:
                if student.role == 'client':
                    await sio.emit('message', {'message': 'STATUS: The teacher is offline!'}, to=student.sid)
            # log
            await emit_log(sio, f'Teacher disconnected (host id: {user.uid}, room id: {room.rid})')
        else:  # student disconnected
            await sio.emit('room/update', data=room.get_room_data(), to=room.host.sid)
            # log
            await emit_log(sio, f'Student disconnected (id: {user.uid}, room id: {room.rid})')


async def disconnect_user_by_host(sio: AsyncServer, sid: str, data: dict) -> None:
    """
    Disconnect user with user_id from data

    Args:
        sio (AsyncServer): The Socket.IO server instance.
        sid (str): The session ID of the user.
        data (dict): The data containing 'user_id'.
    """
    if not await validate_data(sio, data, 'user_id'):
        return

    host = User.get_user_by_sid(sid)
    room = Room.get_room_by_id(host.room)

    user_id = data.get('user_id')
    user_to_kill = room.get_user_by_id(user_id)
    if not user_to_kill:
        await handle_bad_request(sio, f'No such user id {user_id} in the room {host.room}')
        return

    await disconnect_user(sio, user_to_kill.sid)


# JUST FOR TESTS
async def create_test_room(sio: AsyncServer, sid: str) -> None:
    """ Create room for tests """
    room = Room.get_room_by_id(1000)
    if not room:
        user = User(sid, role='host', name='Mama Zmeya')
        test_room = Room(host=user)
        test_room.add_user(user)
        # log
        await emit_log(sio, f'Test room created (id: {test_room.rid})')


# JUST FOR TESTS
async def room_log(sio: AsyncServer, sid: str) -> None:
    """
    Send room log.
    Args:
        sio (AsyncServer): The socketio server instance.
        sid (str): The session ID of the user.
    """
    rooms = [Room.get_room_by_id(room) for room in Room.rooms]
    rooms_data = {
        'total_rooms_count': len(Room.rooms),
        'rooms': [
            {
                'room_id': room.rid, 'users_count': len(room.users), 'host_id': room.host.uid,
            } for room in rooms
        ],
    }

    await sio.emit('room/log', data=rooms_data, to=sid)
