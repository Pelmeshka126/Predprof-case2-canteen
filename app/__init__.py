import os
import secrets

from flask import Flask
from flask import abort
from flask import g
from flask import render_template
from flask import request
from flask import session

from .auth import auth_bp
from .db import close_db, init_app_db
from .routes import routes_bp


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    debug_raw = os.getenv('FLASK_DEBUG', '0').strip().lower()
    debug_enabled = debug_raw in {'1', 'true', 'yes', 'on'}
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'predprof-dev-secret-key')
    app.config['DATABASE'] = os.getenv('DATABASE', 'predprof_case2.db')
    app.config['DEBUG'] = debug_enabled
    if test_config:
        app.config.update(test_config)

    def _ensure_csrf_token() -> str:
        token = session.get('csrf_token')
        if not token:
            token = secrets.token_urlsafe(32)
            session['csrf_token'] = token
        return token

    @app.before_request
    def _validate_csrf_for_post() -> None:
        if request.method != 'POST':
            return
        session_token = session.get('csrf_token')
        request_token = request.form.get('csrf_token', '')
        if not session_token or not request_token or request_token != session_token:
            g.forbidden_reason = (
                'Сессия формы устарела. Обновите страницу и повторите действие.'
            )
            abort(403)

    @app.context_processor
    def _inject_csrf():
        return {'csrf_token': _ensure_csrf_token()}

    @app.errorhandler(403)
    def _forbidden(_error):
        reason = getattr(g, 'forbidden_reason', None)
        return render_template('errors/403.html', forbidden_reason=reason), 403

    init_app_db(app)
    app.teardown_appcontext(close_db)

    app.register_blueprint(auth_bp)
    app.register_blueprint(routes_bp)

    return app
