# controller.py
import os
import tempfile
from typing import Optional, List
from datetime import datetime, timedelta
from flask_login import current_user
from werkzeug.security import generate_password_hash, check_password_hash
import logging

from models import User, TemperatureHumidity, Status, ImageData, TimelapseConf
from .database import DatabaseManager
import pytz

logger = logging.getLogger(__name__)


class Controller:
    def __init__(self, db_path: str = os.getenv("DB_PATH", os.path.join(tempfile.gettempdir(), "timelapse.db"))):
        self.db = DatabaseManager(db_path)
        self.finland_tz = pytz.timezone('Europe/Helsinki')

    # --- User operations ---
    def register_user(
        self, 
        username: str, 
        password: str | None = None, 
        password_hash: str | None = None, 
        is_admin: bool = False, 
        is_temporary: bool = False, 
        expires_at: str | None = None
        ) -> User:
        """
        Creates the user if it doesn't exist, or returns the existing one.
        Uses INSERT OR IGNORE to avoid UNIQUE errors, then SELECT to fetch.
        """
        logger.info(f"Registering user: {username}")

        # 1) Hash the password up front
        if password_hash is None and password:
            pw_hash = generate_password_hash(password)
        elif password is None and password_hash is None:
            raise ValueError(
                "Either password or password_hash must be provided")
        else:
            pw_hash = password_hash

        # 2) Try to insert; if username exists, this is a no-op
        cursor = self.db.execute_query(
            "INSERT OR IGNORE INTO users (username, password_hash, is_admin, is_temporary, expires_at) VALUES (?, ?, ?, ?, ?)",
            (username, pw_hash, is_admin, is_temporary, expires_at)
        )
        if cursor.rowcount == 1:
            logger.info(f"User '{username}' created successfully.")
        else:
            logger.info(f"User '{username}' already exists, skipping INSERT.")

        # 3) Fetch whatever is now in the table
        row = self.db.fetchone(
            "SELECT id, username, password_hash, is_admin, is_temporary, expires_at FROM users WHERE username = ?",
            (username,)
        )
        if row is None:
            # This really should never happen
            logger.error(
                f"After INSERT OR IGNORE, no row for '{username}' found.")
            raise RuntimeError(f"Failed to retrieve user '{username}'")

        logger.info(f"Returning user '{username}' with id={row['id']}")
        return User(
            id=row['id'],
            username=row['username'],
            password_hash=row['password_hash'],
            is_admin=row['is_admin'],
            is_temporary=row['is_temporary'],
            expires_at=row['expires_at']
        )

    def create_temporary_user(self, username: str, password: str, duration_value: int = 1, duration_unit: str = "hours") -> User:
        """Creates a temporary user with expiration."""
        now = datetime.now(self.finland_tz)
        if duration_unit == "minutes":
            expires = now + timedelta(minutes=duration_value)
        elif duration_unit == "hours":
            expires = now + timedelta(hours=duration_value)
        elif duration_unit == "days":
            expires = now + timedelta(days=duration_value)
        else:
            expires = now + timedelta(hours=duration_value)
        expires_at = expires.isoformat()
        return self.register_user(username, password=password, is_temporary=True, expires_at=expires_at)

    def set_user_as_admin(self, username: str, is_admin: bool) -> None:
        """Sets the is_admin flag for the user with the given username."""
        self.db.execute_query(
            "UPDATE users SET is_admin = ? WHERE username = ?",
            (is_admin, username)
        )

    def authenticate_user(self, username: str, password: str) -> bool:
        row = self.db.fetchone(
            "SELECT password_hash FROM users WHERE username = ?", (username,)
        )
        if row is None:
            return False
        return check_password_hash(row['password_hash'], password)

    def get_all_users(self, exclude_admin: bool = False, exclude_current: bool = False, exclude_expired: bool = False) -> list[User]:
        """Palauttaa listan kaikista käyttäjistä."""
        rows = self.db.fetchall(
            "SELECT id, username, password_hash, is_admin, is_temporary, expires_at FROM users",
            ()
        )
        users = [
            User(id=row['id'], username=row['username'],
                 password_hash=row['password_hash'], is_admin=row['is_admin'], is_temporary=row['is_temporary'], expires_at=row['expires_at'])
            for row in rows
        ]
        if exclude_admin:
            users = [user for user in users if not user.is_admin]
        if exclude_current:
            users = [user for user in users if user.username !=
                     current_user.get_id()]
        if exclude_expired:
            now = datetime.now(self.finland_tz)
            users = [user for user in users if not user.is_temporary or not user.expires_at or datetime.fromisoformat(
                user.expires_at) > now]
        return users

    def get_user_by_username(self, username: str, include_pw: bool = True) -> User | None:
        row = self.db.fetchone(
            "SELECT id, username, password_hash, is_admin, is_temporary, expires_at FROM users WHERE username = ?",
            (username,)
        )
        if not row:
            return None
        return User(
            id=row['id'],
            username=row['username'],
            password_hash=row['password_hash'] if include_pw else None,
            is_admin=row['is_admin'],
            is_temporary=row['is_temporary'],
            expires_at=row['expires_at']
        )

    def delete_user(self, username: str) -> None:
        """Poistaa käyttäjän annetulla käyttäjätunnuksella."""
        self.db.execute_query(
            "DELETE FROM users WHERE username = ?",
            (username,)
        )

    def delete_temporary_users(self) -> None:
        """Deletes all temporary users."""
        self.db.execute_query(
            "DELETE FROM users WHERE is_temporary = 1",
            ()
        )

    def delete_expired_temporary_users(self) -> None:
        """Deletes all expired temporary users."""
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "DELETE FROM users WHERE is_temporary = 1 AND expires_at IS NOT NULL AND expires_at < ?",
            (now,)
        )

    # --- Sensor data operations ---
    def record_temphum(self, temperature: float, humidity: float) -> TemperatureHumidity:
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "INSERT INTO temphum (timestamp, temperature, humidity) VALUES (?, ?, ?)",
            (now, temperature, humidity)
        )
        row = self.db.fetchone(
            "SELECT id, timestamp, temperature, humidity FROM temphum ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            raise RuntimeError("Failed to retrieve inserted temphum record")
        return TemperatureHumidity(
            id=row['id'], timestamp=row['timestamp'],
            temperature=row['temperature'], humidity=row['humidity']
        )

    def get_last_temphum(self) -> Optional[TemperatureHumidity]:
        row = self.db.fetchone(
            "SELECT id, timestamp, temperature, humidity FROM temphum ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            return None
        return TemperatureHumidity(
            id=row['id'], timestamp=row['timestamp'],
            temperature=row['temperature'], humidity=row['humidity']
        )

    def get_temphum_for_date(self, date_str: str) -> List[TemperatureHumidity]:
        """
        Returns all TemperatureHumidity rows whose timestamp falls on `date_str`
        (ISO date, e.g. '2025-04-20'), ordered by timestamp.
        """
        rows = self.db.fetchall(
            """
            SELECT id, timestamp, temperature, humidity
              FROM temphum
             WHERE date(timestamp) = ?
             ORDER BY timestamp
            """,
            (date_str,)
        )
        return [
            TemperatureHumidity(
                id=row['id'],
                timestamp=row['timestamp'],
                temperature=row['temperature'],
                humidity=row['humidity']
            )
            for row in rows
        ]

    def update_status(self, status: str) -> Status:
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "UPDATE status SET timestamp = ?, status = ?",
            (now, status)
        )
        row = self.db.fetchone(
            "SELECT id, timestamp, status FROM status"
        )
        if row is None:
            raise RuntimeError("Failed to retrieve inserted status record")
        return Status(id=row['id'], timestamp=row['timestamp'], status=row['status'])

    def get_last_status(self) -> Optional[Status]:
        row = self.db.fetchone(
            "SELECT id, timestamp, status FROM status"
        )
        if row is None:
            return None
        return Status(id=row['id'], timestamp=row['timestamp'], status=row['status'])

    def record_image(self, image_base64: str) -> ImageData:
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "INSERT INTO images (timestamp, image) VALUES (?, ?)",
            (now, image_base64)
        )
        row = self.db.fetchone(
            "SELECT id, timestamp, image FROM images ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            raise RuntimeError("Failed to retrieve inserted image record")
        return ImageData(id=row['id'], timestamp=row['timestamp'], image=row['image'])

    def get_last_image(self) -> Optional[ImageData]:
        row = self.db.fetchone(
            "SELECT id, timestamp, image FROM images ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            return None
        return ImageData(id=row['id'], timestamp=row['timestamp'], image=row['image'])

    def get_timelapse_conf(self) -> Optional[TimelapseConf]:
        row = self.db.fetchone(
            "SELECT id, image_delay, temphum_delay, status_delay FROM timelapse_conf"
        )
        if row is None:
            return None
        return TimelapseConf(
            id=row['id'],
            image_delay=row['image_delay'],
            temphum_delay=row['temphum_delay'],
            status_delay=row['status_delay']
        )

    def update_timelapse_conf(self, image_delay: int, temphum_delay: int, status_delay: int) -> None:
        self.db.execute_query(
            """
            UPDATE timelapse_conf
               SET image_delay = ?,
                   temphum_delay = ?,
                   status_delay = ?
            """,
            (image_delay, temphum_delay, status_delay)
        )

    def record_gcode_command(self, gcode: str) -> None:
        logger.info(f"Recording G-code command: {gcode}")
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "INSERT INTO gcode_commands (timestamp, gcode) VALUES (?, ?)",
            (now, gcode)
        )

    def get_all_gcode_commands(self) -> List[str]:
        rows = self.db.fetchall(
            "SELECT gcode FROM gcode_commands ORDER BY id DESC"
        )
        gcode_set = set([row['gcode'] for row in rows])
        return list(gcode_set)

    def log_message(self, message: str, log_type: str = 'info') -> None:
        """
        Logs a message with the given type ('info', 'warning', 'error').
        """
        now = datetime.now(self.finland_tz).isoformat()
        self.db.execute_query(
            "INSERT INTO logs (timestamp, type, message) VALUES (?, ?, ?)",
            (now, log_type, message)
        )

    def get_logs(self, limit: int = 100) -> List[dict]:
        """
        Retrieves the most recent log messages.
        """
        rows = self.db.fetchall(
            "SELECT timestamp, type, message FROM logs ORDER BY id DESC LIMIT ?",
            (limit,)
        )
        return [dict(row) for row in rows]
