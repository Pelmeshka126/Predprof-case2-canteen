import sqlite3
from datetime import date
from typing import Any

from flask import current_app, g


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('student', 'cook', 'admin')),
    allergies TEXT DEFAULT '',
    preferences TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS menu_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    meal_type TEXT NOT NULL CHECK(meal_type IN ('breakfast', 'lunch')),
    title TEXT NOT NULL,
    price REAL NOT NULL,
    available_qty INTEGER NOT NULL CHECK(available_qty >= 0)
);

CREATE TABLE IF NOT EXISTS payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    payment_type TEXT NOT NULL CHECK(payment_type IN ('one_time', 'subscription')),
    amount REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'paid',
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS meal_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    menu_item_id INTEGER NOT NULL,
    claimed_at TEXT NOT NULL,
    UNIQUE(user_id, menu_item_id),
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(menu_item_id) REFERENCES menu_items(id)
);

CREATE TABLE IF NOT EXISTS meal_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cook_id INTEGER NOT NULL,
    menu_item_id INTEGER NOT NULL,
    issued_qty INTEGER NOT NULL CHECK(issued_qty > 0),
    issue_note TEXT DEFAULT '',
    issued_at TEXT NOT NULL,
    FOREIGN KEY(cook_id) REFERENCES users(id),
    FOREIGN KEY(menu_item_id) REFERENCES menu_items(id)
);

CREATE TABLE IF NOT EXISTS inventory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name TEXT NOT NULL UNIQUE,
    qty REAL NOT NULL,
    unit TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cook_id INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    qty REAL NOT NULL,
    unit_price REAL NOT NULL DEFAULT 0,
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
    reviewed_by INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY(cook_id) REFERENCES users(id),
    FOREIGN KEY(reviewed_by) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    menu_item_id INTEGER NOT NULL,
    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
    comment TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(menu_item_id) REFERENCES menu_items(id)
);
"""


def _ensure_column(db: sqlite3.Connection, table_name: str, column_name: str, column_ddl: str) -> None:
    columns = {
        row['name']
        for row in db.execute(f'PRAGMA table_info({table_name})').fetchall()
    }
    if column_name not in columns:
        db.execute(f'ALTER TABLE {table_name} ADD COLUMN {column_name} {column_ddl}')


def get_db() -> sqlite3.Connection:
    if 'db' not in g:
        conn = sqlite3.connect(current_app.config['DATABASE'])
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


def close_db(_e: Any = None) -> None:
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    db.executescript(SCHEMA_SQL)
    _ensure_column(db, 'purchase_requests', 'unit_price', 'REAL NOT NULL DEFAULT 0')

    users_count = db.execute('SELECT COUNT(*) AS c FROM users').fetchone()['c']
    if users_count == 0:
        from werkzeug.security import generate_password_hash

        db.execute(
            'INSERT INTO users(name, email, password_hash, role) VALUES (?, ?, ?, ?)',
            ('Admin Demo', 'admin@predprof.local', generate_password_hash('admin123'), 'admin'),
        )
        db.execute(
            'INSERT INTO users(name, email, password_hash, role) VALUES (?, ?, ?, ?)',
            ('Cook Demo', 'cook@predprof.local', generate_password_hash('cook123'), 'cook'),
        )
        db.execute(
            'INSERT INTO users(name, email, password_hash, role) VALUES (?, ?, ?, ?)',
            ('Student Demo', 'student@predprof.local', generate_password_hash('student123'), 'student'),
        )

    menu_count = db.execute('SELECT COUNT(*) AS c FROM menu_items').fetchone()['c']
    if menu_count == 0:
        today = date.today().isoformat()
        menu_seed = [
            (today, 'breakfast', 'Овсяная каша + яблоко', 120.0, 100),
            (today, 'breakfast', 'Омлет + чай', 140.0, 100),
            (today, 'lunch', 'Суп куриный + котлета + гарнир', 230.0, 150),
            (today, 'lunch', 'Паста + салат', 210.0, 120),
        ]
        db.executemany(
            'INSERT INTO menu_items(date, meal_type, title, price, available_qty) VALUES (?, ?, ?, ?, ?)',
            menu_seed,
        )

    inventory_count = db.execute('SELECT COUNT(*) AS c FROM inventory').fetchone()['c']
    if inventory_count == 0:
        inv_seed = [
            ('Курица', 50, 'кг'),
            ('Крупа овсяная', 30, 'кг'),
            ('Яйца', 300, 'шт'),
            ('Макароны', 40, 'кг'),
            ('Овощи', 80, 'кг'),
        ]
        db.executemany('INSERT INTO inventory(product_name, qty, unit) VALUES (?, ?, ?)', inv_seed)

    db.commit()


def init_app_db(app) -> None:
    with app.app_context():
        init_db()
