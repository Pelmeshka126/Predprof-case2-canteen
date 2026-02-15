import sqlite3
from datetime import date
from typing import Any

from flask import current_app, g

from .utils import now_iso


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('student', 'cook', 'admin')),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at TEXT NOT NULL,
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
    qty REAL NOT NULL CHECK(qty >= 0 AND qty <= 10000),
    unit TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS purchase_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cook_id INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    qty REAL NOT NULL CHECK(qty > 0 AND qty <= 10000),
    unit_price REAL NOT NULL CHECK(unit_price >= 0 AND unit_price <= 100000),
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

CREATE TABLE IF NOT EXISTS admin_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_id INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(admin_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
"""


def _ensure_column(db: sqlite3.Connection, table_name: str, column_name: str, column_ddl: str) -> None:
    columns = {
        row['name']
        for row in db.execute(f'PRAGMA table_info({table_name})').fetchall()
    }
    if column_name not in columns:
        db.execute(f'ALTER TABLE {table_name} ADD COLUMN {column_name} {column_ddl}')


def _migration_001_users_hardening(db: sqlite3.Connection) -> None:
    _ensure_column(db, 'users', 'is_active', 'INTEGER NOT NULL DEFAULT 1')
    _ensure_column(db, 'users', 'created_at', "TEXT NOT NULL DEFAULT ''")


def _migration_002_purchase_price_guards(db: sqlite3.Connection) -> None:
    _ensure_column(db, 'purchase_requests', 'unit_price', 'REAL NOT NULL DEFAULT 0')
    db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS purchase_requests_positive_price_insert
        BEFORE INSERT ON purchase_requests
        FOR EACH ROW
        WHEN NEW.unit_price <= 0
        BEGIN
            SELECT RAISE(ABORT, 'purchase_requests.unit_price must be > 0');
        END;
        """
    )
    db.execute(
        """
        CREATE TRIGGER IF NOT EXISTS purchase_requests_positive_price_update
        BEFORE UPDATE ON purchase_requests
        FOR EACH ROW
        WHEN NEW.unit_price <= 0 AND OLD.unit_price > 0
        BEGIN
            SELECT RAISE(ABORT, 'purchase_requests.unit_price must be > 0');
        END;
        """
    )


def _migration_003_admin_actions(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id INTEGER NOT NULL,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(admin_id) REFERENCES users(id)
        )
        """
    )


MIGRATIONS: list[tuple[int, str, Any]] = [
    (1, 'users_hardening', _migration_001_users_hardening),
    (2, 'purchase_price_guards', _migration_002_purchase_price_guards),
    (3, 'admin_actions', _migration_003_admin_actions),
]


def _apply_migrations(db: sqlite3.Connection) -> None:
    db.execute(
        'CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)'
    )

    for version, name, handler in MIGRATIONS:
        exists = db.execute(
            'SELECT 1 FROM schema_migrations WHERE version = ?',
            (version,),
        ).fetchone()
        if exists:
            continue

        handler(db)
        db.execute(
            'INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)',
            (version, name, now_iso()),
        )


def _normalize_runtime_data(db: sqlite3.Connection) -> dict[str, int]:
    now = now_iso()
    db.execute(
        "UPDATE users SET created_at = ? WHERE created_at IS NULL OR TRIM(created_at) = ''",
        (now,),
    )
    db.execute('UPDATE users SET is_active = 1 WHERE is_active IS NULL')
    db.execute('UPDATE users SET is_active = 1 WHERE is_active NOT IN (0, 1)')

    db.execute('UPDATE inventory SET qty = 0 WHERE qty < 0 OR qty > 10000')
    db.execute('UPDATE inventory SET qty = ROUND(qty, 3)')

    db.execute(
        'UPDATE purchase_requests SET qty = 0 '
        'WHERE qty IS NULL OR qty < 0 OR qty > 10000'
    )
    db.execute(
        'UPDATE purchase_requests SET unit_price = 0 '
        'WHERE unit_price IS NULL OR unit_price < 0 OR unit_price > 100000'
    )
    db.execute('UPDATE purchase_requests SET qty = ROUND(qty, 3), unit_price = ROUND(unit_price, 2)')

    legacy_note = '[SYSTEM] Отклонено: legacy-запись с unit_price=0.'
    legacy_fix_cursor = db.execute(
        """
        UPDATE purchase_requests
        SET
            status = 'rejected',
            reason = CASE
                WHEN reason IS NULL OR TRIM(reason) = '' THEN ?
                WHEN instr(reason, ?) > 0 THEN reason
                ELSE reason || ' | ' || ?
            END
        WHERE status = 'approved' AND unit_price = 0
        """,
        (legacy_note, legacy_note, legacy_note),
    )

    return {
        'legacy_rejected_count': max(legacy_fix_cursor.rowcount, 0),
    }


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
    _apply_migrations(db)
    normalization_stats = _normalize_runtime_data(db)

    users_count = db.execute('SELECT COUNT(*) AS c FROM users').fetchone()['c']
    if users_count == 0:
        from werkzeug.security import generate_password_hash

        now = now_iso()
        db.execute(
            'INSERT INTO users(name, email, password_hash, role, is_active, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            ('Администратор (демо)', 'admin@predprof.local', generate_password_hash('admin123'), 'admin', 1, now),
        )
        db.execute(
            'INSERT INTO users(name, email, password_hash, role, is_active, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            ('Повар (демо)', 'cook@predprof.local', generate_password_hash('cook123'), 'cook', 1, now),
        )
        db.execute(
            'INSERT INTO users(name, email, password_hash, role, is_active, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            ('Ученик (демо)', 'student@predprof.local', generate_password_hash('student123'), 'student', 1, now),
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

    legacy_count = normalization_stats['legacy_rejected_count']
    if legacy_count > 0:
        current_app.logger.info('Нормализация legacy-записей: отклонено %s заявок с unit_price=0', legacy_count)


def init_app_db(app) -> None:
    with app.app_context():
        init_db()
