"""Flask blueprints grouped by area (web, api, auth).

Provides a single registration function to keep app factory clean.
"""

from __future__ import annotations

from flask import Flask


def register_blueprints(app: Flask) -> None:
    """Register all application blueprints."""
    from .auth.routes import auth_bp
    from .web.routes import web_bp
    from .api.routes import api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp)

