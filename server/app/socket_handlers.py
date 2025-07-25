import logging
from flask import request, flash
from flask_socketio import SocketIO
from flask_login import current_user

from .controller import Controller


class SocketEventHandler:
    _instance: "SocketEventHandler | None" = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, socketio: SocketIO, ctrl: Controller):
        if self._initialized:
            return
        self.socketio = socketio
        self.ctrl = ctrl
        self.logger = logging.getLogger(__name__)
        self._initialized = True
        socketio.on_event('connect',    self.handle_connect)
        socketio.on_event('disconnect', self.handle_disconnect)
        socketio.on_event('image',      self.handle_image)
        socketio.on_event('temphum',    self.handle_temphum)
        socketio.on_event('status',     self.handle_status)
        socketio.on_event('printerAction', self.handle_printer_action)

    def handle_connect(self, auth):
        # Use Flask-Login session cookie for auth instead of API key
        if not current_user.is_authenticated:
            self.logger.warning("Socket connect refused: unauthenticated user")
            return False
        self.logger.info("Client connected: %s", request.sid) # type: ignore

    def handle_disconnect(self, *args):
        """Allow any positional args to avoid signature mismatch."""
        self.logger.info("Client disconnected: %s", request.sid) # type: ignore

    def flash(self, message, category):
        """Emit a flash message to the client."""
        self.socketio.emit('flash', {'category': category, 'message': message})
        self.logger.info("Flash message: %s (%s)", category, message)

    def handle_image(self, *args):
        self.socketio.emit('image')
        self.logger.debug("Emitted 'image' event")

    def handle_temphum(self, data):
        temp, hum = data.get('temperature'), data.get('humidity')
        if temp is None or hum is None:
            self.socketio.emit(
                'error', {'message': 'Invalid temperature/humidity data'})
            self.logger.warning("Bad temphum payload: %s", data)
            return
        saved = self.ctrl.record_temphum(temp, hum)
        self.socketio.emit('temphum2v', {
            'temperature': saved.temperature,
            'humidity':    saved.humidity
        })
        self.logger.debug("Broadcasted temphum: %s", data)

    def handle_status(self, data):
        if data is None:
            self.socketio.emit('error', {'message': 'Invalid status data'})
            self.logger.warning("Bad status payload: %s", data)
            return
        # saved = self.ctrl.update_status(data)
        self.socketio.emit('status2v', data)
        self.logger.debug("Broadcasted status: %s", data)
        
    def handle_client_response(self, result, action):
        if result is None:
            self.socketio.emit('error', {'message': 'Invalid printer result'})
            self.logger.warning("Bad printer result: %s", result)
            return
        elif result == '':
            return
        
        if result:
            self.flash(
                f"Printer action '{action}' completed successfully.",
                'success'
            )
        else:
            self.flash(
                f"Printer action '{action}' failed.",
                'error'
            )

    def handle_printer_action(self, data):
        result = data.get('result', '')
        if not current_user.is_admin and result == '':
            self.flash(
                "You do not have permission to perform printer actions.",
                'error'
            )
            return
        self.logger.info("Received printer action data: %s", data)
        action = data.get('action', '')
        if result != '':
            self.handle_client_response(result, action)
            return
        if action not in ['pause', 'resume', 'stop', 'home', 
                          'timelapse_start', 'timelapse_stop',
                          'run_gcode'
                          ]:
            self.socketio.emit(
                'error', {'message': f'Invalid printer action: {action}'})
            self.logger.warning("Bad printer action: %s", action)
            return
        
        if action == 'run_gcode':
            gcode = data.get('gcode', '')
            if not gcode:
                self.flash(
                    "No G-code provided for run_gcode action",
                    'error'
                )
                self.logger.error("No G-code provided for run_gcode action")
                return
            try:
                result = self.ctrl.record_gcode_command(gcode=gcode)
            except ValueError as e:
                self.logger.exception("Error recording G-code: %s", e)
                return

        self.socketio.emit('printerAction', data)
        self.logger.info("Handled printer action: %s", action)
