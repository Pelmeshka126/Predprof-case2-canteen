from functools import wraps

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .utils import now_iso

auth_bp = Blueprint('auth', __name__)


def current_user():
    user_id = session.get('user_id')
    if not user_id:
        return None
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if user is None:
        session.clear()
        return None
    if 'is_active' in user.keys() and int(user['is_active']) == 0:
        session.clear()
        return None
    return user


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get('user_id') is None:
            return redirect(url_for('auth.login'))
        user = current_user()
        if user is None:
            flash('Аккаунт заблокирован или недоступен.')
            return redirect(url_for('auth.login'))
        return view(*args, **kwargs)

    return wrapped


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if user is None:
                return redirect(url_for('auth.login'))
            if user['role'] not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        if not name or not email or not password:
            flash('Заполните все обязательные поля.')
            return render_template('auth/register.html')
        if len(name) > 120 or len(email) > 254:
            flash('Слишком длинные имя или email.')
            return render_template('auth/register.html')
        if len(password) < 6 or len(password) > 128:
            flash('Пароль должен быть длиной от 6 до 128 символов.')
            return render_template('auth/register.html')

        db = get_db()
        exists = db.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
        if exists:
            flash('Электронная почта уже занята.')
            return render_template('auth/register.html')

        db.execute(
            'INSERT INTO users(name, email, password_hash, role, is_active, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (
                name,
                email,
                generate_password_hash(password),
                'student',
                1,
                now_iso(),
            ),
        )
        db.commit()
        flash('Регистрация успешна. Войдите в систему.')
        return redirect(url_for('auth.login'))

    return render_template('auth/register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        db = get_db()
        user = db.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()

        if user is None or not check_password_hash(user['password_hash'], password):
            flash('Неверная электронная почта или пароль.')
            return render_template('auth/login.html')
        if int(user['is_active']) == 0:
            flash('Пользователь заблокирован администратором.')
            return render_template('auth/login.html')

        session.clear()
        session['user_id'] = user['id']
        flash('Вход выполнен.')
        return redirect(url_for('routes.index'))

    return render_template('auth/login.html')


@auth_bp.route('/logout')
def logout():
    session.clear()
    flash('Вы вышли из системы.')
    return redirect(url_for('auth.login'))
