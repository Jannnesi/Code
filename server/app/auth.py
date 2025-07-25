import json
import logging
from flask import (
    Blueprint, render_template, request, flash,
    redirect, url_for, session, current_app, make_response
)
from flask_login import (
    login_user, logout_user, login_required,
    current_user, UserMixin, AnonymousUserMixin
)
from flask_limiter.errors import RateLimitExceeded
from app import limiter
from .controller import Controller

auth_bp = Blueprint('auth', __name__)
logger = logging.getLogger(__name__)


class AuthUser(UserMixin):
    """Flask-Login user, with admin flag."""

    def __init__(self, username: str, is_admin: bool = False):
        self.id = username
        self.is_admin = is_admin


class AuthAnonymous(AnonymousUserMixin):
    """Anonymous user with is_admin=False."""
    @property
    def is_admin(self):
        return False

def kick_if_expired():
    """Check if the user is temporary and expired."""
    ctrl: Controller = current_app.ctrl
    user = ctrl.get_user_by_username(current_user.get_id(), include_pw=False)
    if user and user.is_expired:
        logout_user()
        flash("Istuntosi on vanhentunut.", "warning")
        return redirect(url_for("auth.login"))

def load_user(user_id: str):
    ctrl: Controller = current_app.ctrl  # type: ignore
    user = ctrl.get_user_by_username(user_id)
    if user:
        is_admin = getattr(user, "is_admin", False)
        logger.debug("Loaded user %s (is_admin=%s)", user_id, is_admin)
        return AuthUser(user_id, is_admin=is_admin)
    logger.warning("User %s not found", user_id)
    return None


@auth_bp.errorhandler(RateLimitExceeded)
def handle_rate_limit(e):
    logger.warning(
        "Rate limit reached for %s on %s",
        request.remote_addr,
        request.endpoint
    )
    return make_response(
        render_template("429.html", retry_after=e.description),
        429
    )


@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit(
    "5/minute;20/hour",
    exempt_when=lambda: (
        current_user.is_admin
        or request.remote_addr in ['192.168.10.50', '192.168.0.3']
    )
)
def login():
    logger.info("Accessed /login via %s", request.method)
    ctrl: Controller = current_app.ctrl  # type: ignore

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        remember = request.form.get('remember') == 'on'
        logger.debug("Login attempt for %s (remember=%s)", username, remember)

        user_obj = ctrl.get_user_by_username(username)
        if user_obj and user_obj.is_temporary and user_obj.expires_at:
            from datetime import datetime
            now = datetime.now(ctrl.finland_tz)
            expires_at = datetime.fromisoformat(user_obj.expires_at)
            if expires_at < now:
                flash('Tämä väliaikainen käyttäjätili on vanhentunut.', 'error')
                logger.warning(
                    "Expired temporary account login attempt for %s", username)
                ctrl.log_message(
                    log_type='auth', message=f"Expired temporary login attempt for {username}")
                return render_template('login.html')

        if ctrl.authenticate_user(username, password):
            session.permanent = remember
            login_user(
                # load_user will restore is_admin from DB on reload
                AuthUser(username, is_admin=getattr(
                    user_obj, "is_admin", False)),
                remember=remember
            )
            logger.info("User %s authenticated, is admin: %s", username, user_obj.is_admin)

            next_page = request.args.get(
                'next') or url_for('web.get_home_page')
            response = redirect(next_page)
            logger.debug(
                "Rate-limit headers — remaining: %s, reset: %s",
                response.headers.get("X-RateLimit-Remaining"),
                response.headers.get("X-RateLimit-Reset")
            )
            return response
        # Authentication failed
        flash('Virheellinen käyttäjätunnus tai salasana', 'error')
        logger.warning("Auth failed for %s", username)
        ctrl.log_message(
            log_type='auth',
            message=f"Failed login attempt for {username}",
        )

    return render_template('login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    username = current_user.get_id()
    logout_user()
    session.pop('_flashes', None)
    logger.info("User %s logged out", username)
    return redirect(url_for('auth.login'))
