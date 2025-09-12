"""Security-related application setup (rate limiting, request filters, API key auth)."""

from __future__ import annotations

import logging
from flask import request, current_app, jsonify, g
from functools import wraps
from .extensions import limiter

logger = logging.getLogger(__name__)


def configure_rate_limiting(app) -> None:
    """Initialize Flask-Limiter and register a whitelist request filter.

    Whitelist supports:
      - Exact IP match, e.g. "127.0.0.1"
      - Wildcard suffix, e.g. "192.168.10.*"
      - CIDR ranges, e.g. "192.168.10.0/24"
    """
    limiter.init_app(app)

    try:
        from ipaddress import ip_address, ip_network
    except Exception:
        ip_address = ip_network = None  # type: ignore

    def _rate_limit_whitelist_filter() -> bool:
        try:
            addr_raw = request.headers.get('X-Forwarded-For', request.remote_addr)
            if not addr_raw:
                return False
            addr = str(addr_raw).split(',')[0].strip()
            wl = current_app.config.get('whitelist', []) or []
            for item in wl:
                s = str(item).strip()
                if not s:
                    continue
                if s.endswith('.*'):
                    if addr.startswith(s[:-1]):
                        return True
                    continue
                if '/' in s and ip_address and ip_network:
                    try:
                        if ip_address(addr) in ip_network(s, strict=False):
                            return True
                        continue
                    except Exception:
                        pass
                if addr == s:
                    return True
            return False
        except Exception:
            return False

    try:
        limiter.request_filter(_rate_limit_whitelist_filter)  # type: ignore[attr-defined]
    except Exception:
        pass

    logger.info(
        "Rate limiting enabled (whitelist size: %d)",
        len(app.config.get('whitelist', []) or [])
    )


def require_api_key(view_func):
    """Decorator to protect endpoints with an API key.

    Accepts token via:
      - Authorization: Bearer <token>
      - X-API-Key: <token>
      - api_key query parameter (for GET/testing)
    Uses Controller.verify_api_key_token and stores metadata in g.api_key on success.
    """
    @wraps(view_func)
    def _wrapped(*args, **kwargs):
        try:
            token = None
            auth = request.headers.get('Authorization')
            if auth and isinstance(auth, str) and auth.lower().startswith('bearer '):
                token = auth.split(' ', 1)[1].strip()
            if not token:
                token = request.headers.get('X-API-Key')
            if not token:
                token = request.args.get('api_key')
            if not token:
                return jsonify({
                    'ok': False,
                    'error': 'missing_api_key',
                    'message': 'Provide Bearer token or X-API-Key.'
                }), 401
            ctrl = getattr(current_app, 'ctrl', None)
            if ctrl is None:
                return jsonify({'ok': False, 'error': 'server_not_ready'}), 503
            meta = ctrl.verify_api_key_token(token)
            if not meta:
                return jsonify({'ok': False, 'error': 'invalid_api_key'}), 401
            g.api_key = meta
            return view_func(*args, **kwargs)
        except Exception as e:
            logger.exception("API key auth error: %s", e)
            return jsonify({'ok': False, 'error': 'auth_error'}), 500
    return _wrapped
