from flask import Flask

from .auth import auth_bp
from .db import close_db, init_app_db
from .routes import routes_bp


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config['SECRET_KEY'] = 'predprof-dev-secret-key'
    app.config['DATABASE'] = 'predprof_case2.db'
    if test_config:
        app.config.update(test_config)

    init_app_db(app)
    app.teardown_appcontext(close_db)

    app.register_blueprint(auth_bp)
    app.register_blueprint(routes_bp)

    return app
