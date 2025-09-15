import threading
import eventlet
eventlet.monkey_patch()
from .core.controller import Controller
from flask import Flask, send_from_directory
import pathlib
import logging
import signal

from .extensions import csrf, login_manager, socketio
from .logging_handlers import DBLogHandler


def create_app():
    logger = logging.getLogger(__name__)
    logger.info("Starting application")
    logger.debug("Debug logging enabled")

    # ─── Flask app & config ───
    app = Flask(__name__)
    from .config import load_settings
    settings = load_settings()
    app.config.update(settings)
    
    HLS_ROOT = pathlib.Path("/srv/hls")    # parent of “printer1”

    @app.route("/live/<path:filename>")
    def live(filename):
        return send_from_directory(HLS_ROOT, filename)
    
    # ─── Rate limiting ───
    from .security import configure_rate_limiting
    configure_rate_limiting(app)

    # ─── CSRF protection ───
    csrf.init_app(app)
    logger.info("CSRF protection enabled (1 h token lifetime)")

    # ─── Domain-controller ───
    db_path = app.config.get("DB_PATH")
    if not db_path:
        raise RuntimeError("DB_PATH is missing – add to environment.")
    app.ctrl = Controller(db_path)  # type: ignore
    logger.info("Controller init: %s", db_path)
    
    # ─── Route all ERROR+ logs into DB ───
    try:
        db_handler = DBLogHandler(app.ctrl)
        db_handler.setLevel(logging.ERROR)
        # Include exception tracebacks in the stored message
        db_handler.setFormatter(logging.Formatter('%(levelname)s %(name)s: %(message)s'))
        logging.getLogger().addHandler(db_handler)
    except Exception as e:
        logger.warning("Failed to install DBLogHandler: %s", e)
    
    # ─── Seed admin user (Root-Admin) ───
    try:
        app.ctrl.register_user(  # type: ignore
            app.config["WEB_USERNAME"],
            password=app.config["WEB_PASSWORD"],
            is_admin=True,
            is_root_admin=True
        )
        logger.info("Seeded admin user %s (is_admin=True, is_root_admin=True)",
                    app.config["WEB_USERNAME"])
    except ValueError:
        app.ctrl.set_user_as_admin(
            app.config["WEB_USERNAME"], True)  # type: ignore
        logger.info("Ensured %s has is_admin=True (existing)", app.config["WEB_USERNAME"])

    # ─── Flask-Login ───
    login_manager.login_view = "auth.login"  # type: ignore
    login_manager.init_app(app)
    from .blueprints.auth.routes import load_user, AuthAnonymous, kick_if_expired
    app.before_request(kick_if_expired)
    login_manager.user_loader(load_user)
    login_manager.anonymous_user = AuthAnonymous
    logger.info("Login manager ready")

    # ─── Socket.IO ───
    allowed_ws_origins = app.config.get('ALLOWED_WS_ORIGINS', [])
    logger.info("Allowed WebSocket origins: %s", allowed_ws_origins)

    socketio.init_app(
        app,
        async_mode="eventlet",
        message_queue="redis://localhost:6379",
        cors_allowed_origins=allowed_ws_origins,
        max_http_buffer_size=10 * 1024 * 1024,
        ping_interval=10,
        ping_timeout=20,
    )
    def _shutdown_tasks():
        """Run best-effort shutdown side effects outside hub mainloop.

        Avoid doing blocking work directly in the eventlet hub signal context.
        """
        try:
            app.ctrl.log_message("Server shutting down", "system")  # type: ignore
        except Exception as e:
            logging.getLogger(__name__).warning("Shutdown log_message failed: %s", e)
        try:
            socketio.emit('server_shutdown')
            socketio.stop()
        except Exception as e:
            logging.getLogger(__name__).warning("Shutdown emit failed: %s", e)

    def exit_signal(signum, frame):
        # Offload to a greenlet so we don't call blocking functions from the hub mainloop
        try:
            logging.getLogger(__name__).info("Caught exit signal %s", signum)
            eventlet.spawn_n(_shutdown_tasks)
        except Exception:
            # As a last resort, swallow errors to not interfere with Gunicorn shutdown
            pass
        # Let Gunicorn control worker shutdown; avoid calling socketio.stop() here
    signal.signal(signal.SIGTERM, exit_signal)
    signal.signal(signal.SIGINT, exit_signal)
    app.socketio = socketio  # type: ignore
    logger.info("Socket.IO ready (origins: %s)", allowed_ws_origins)
    
    # ─── Services (AC thermostat, Hue, Socket events) ───
    from .services.bootstrap import init_services
    init_services(app)


    # ─── Blueprints ───
    from .blueprints import register_blueprints
    register_blueprints(app)

    # ─── Template asset registry ───
    from .assets import register_assets
    register_assets(app)

    return app, socketio
