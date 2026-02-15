import csv
import io
import json
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from .auth import current_user, login_required, role_required
from .db import get_db
from .utils import ADMIN_ACTION_LABELS
from .utils import ADMIN_TARGET_LABELS
from .utils import MEAL_TYPE_LABELS
from .utils import PAYMENT_STATUS_LABELS
from .utils import PAYMENT_TYPE_LABELS
from .utils import REQUEST_STATUS_LABELS
from .utils import ROLE_LABELS
from .utils import USER_STATUS_LABELS
from .utils import format_date_ru
from .utils import format_datetime_ru
from .utils import now_iso
from .utils import now_moscow

routes_bp = Blueprint('routes', __name__)

MAX_PAYMENT_AMOUNT = Decimal('50000')
MAX_QUANTITY_VALUE = Decimal('10000')
MAX_UNIT_PRICE = Decimal('100000')
LOW_STOCK_THRESHOLD = Decimal('10')


def _parse_positive_decimal(raw_value: str, *, max_value: Decimal, places: int, field_name: str) -> Decimal:
    value = (raw_value or '').strip().replace(',', '.')
    if not value:
        raise ValueError(f'{field_name}: значение не заполнено.')
    if 'e' in value.lower():
        raise ValueError(f'{field_name}: экспоненциальная запись недопустима.')
    try:
        dec = Decimal(value)
    except InvalidOperation as err:
        raise ValueError(f'{field_name}: некорректный формат числа.') from err

    if dec.is_nan() or dec.is_infinite():
        raise ValueError(f'{field_name}: значение недопустимо.')
    if dec <= 0:
        raise ValueError(f'{field_name}: значение должно быть больше нуля.')
    if dec > max_value:
        raise ValueError(f'{field_name}: превышен допустимый максимум ({_format_decimal(max_value)}).')

    quantum = Decimal('1').scaleb(-places)
    return dec.quantize(quantum, rounding=ROUND_HALF_UP)


def _format_decimal(value: Decimal | int | float | str, places: int = 2, trim_trailing: bool = False) -> str:
    try:
        dec = Decimal(str(value))
    except InvalidOperation:
        return str(value)

    quantum = Decimal('1').scaleb(-places)
    normalized = dec.quantize(quantum, rounding=ROUND_HALF_UP)
    out = format(normalized, 'f')
    if trim_trailing and '.' in out:
        out = out.rstrip('0').rstrip('.')
    return out


def _fmt_qty(value: Decimal | int | float | str) -> str:
    return _format_decimal(value, places=3, trim_trailing=True)


def _fmt_money(value: Decimal | int | float | str) -> str:
    return _format_decimal(value, places=2, trim_trailing=False)


def _default_period() -> tuple[str, str]:
    today = now_moscow().date()
    start = today.replace(day=1)
    return start.isoformat(), today.isoformat()


def _resolve_period(raw_from: str | None, raw_to: str | None) -> tuple[str, str, str | None]:
    default_from, default_to = _default_period()
    date_from_raw = (raw_from or '').strip()
    date_to_raw = (raw_to or '').strip()

    if not date_from_raw and not date_to_raw:
        return default_from, default_to, None

    if not date_from_raw:
        date_from_raw = default_from
    if not date_to_raw:
        date_to_raw = default_to

    try:
        from_date = date.fromisoformat(date_from_raw)
        to_date = date.fromisoformat(date_to_raw)
    except ValueError:
        return default_from, default_to, 'Некорректный формат периода. Использован период по умолчанию.'

    if from_date > to_date:
        return default_from, default_to, 'Дата начала больше даты окончания. Использован период по умолчанию.'

    return from_date.isoformat(), to_date.isoformat(), None


def _collect_admin_metrics(db, date_from: str, date_to: str) -> dict:
    total_payments_raw = db.execute(
        'SELECT COALESCE(SUM(amount), 0) AS s FROM payments WHERE status = ? '
        'AND substr(created_at, 1, 10) BETWEEN ? AND ?',
        ('paid', date_from, date_to),
    ).fetchone()['s']
    total_claims = db.execute(
        'SELECT COUNT(*) AS c FROM meal_claims WHERE substr(claimed_at, 1, 10) BETWEEN ? AND ?',
        (date_from, date_to),
    ).fetchone()['c']
    unique_students_with_claims = db.execute(
        'SELECT COUNT(DISTINCT user_id) AS c FROM meal_claims WHERE substr(claimed_at, 1, 10) BETWEEN ? AND ?',
        (date_from, date_to),
    ).fetchone()['c']
    total_issues = db.execute(
        'SELECT COALESCE(SUM(issued_qty), 0) AS c FROM meal_issues '
        'WHERE substr(issued_at, 1, 10) BETWEEN ? AND ?',
        (date_from, date_to),
    ).fetchone()['c']
    approved_procurement_cost_raw = db.execute(
        """
        SELECT COALESCE(SUM(qty * unit_price), 0) AS s
        FROM purchase_requests
        WHERE status = ? AND qty > 0 AND unit_price > 0
          AND substr(created_at, 1, 10) BETWEEN ? AND ?
        """,
        ('approved', date_from, date_to),
    ).fetchone()['s']
    approved_requests_count = db.execute(
        """
        SELECT COUNT(*) AS c
        FROM purchase_requests
        WHERE status = ? AND qty > 0 AND unit_price > 0
          AND substr(created_at, 1, 10) BETWEEN ? AND ?
        """,
        ('approved', date_from, date_to),
    ).fetchone()['c']

    total_payments = Decimal(str(total_payments_raw or 0))
    approved_procurement_cost = Decimal(str(approved_procurement_cost_raw or 0))
    operating_balance = total_payments - approved_procurement_cost
    generated_at = now_iso()

    return {
        'generated_at': generated_at,
        'generated_at_display': format_datetime_ru(generated_at),
        'period_from': date_from,
        'period_to': date_to,
        'period_from_display': format_date_ru(date_from),
        'period_to_display': format_date_ru(date_to),
        'total_payments_raw': total_payments,
        'total_claims': total_claims,
        'unique_students_with_claims': unique_students_with_claims,
        'total_issues': total_issues,
        'approved_procurement_cost_raw': approved_procurement_cost,
        'approved_requests_count': approved_requests_count,
        'operating_balance_raw': operating_balance,
        'total_payments': _fmt_money(total_payments),
        'approved_procurement_cost': _fmt_money(approved_procurement_cost),
        'operating_balance': _fmt_money(operating_balance),
    }


def _fetch_report_rows(db, date_from: str, date_to: str) -> list[dict]:
    rows = db.execute(
        """
        SELECT
            mi.id,
            mi.title,
            mi.meal_type,
            mi.date,
            (
                SELECT COUNT(*)
                FROM meal_claims mc
                WHERE mc.menu_item_id = mi.id
                  AND substr(mc.claimed_at, 1, 10) BETWEEN ? AND ?
            ) AS claims_count,
            (
                SELECT COALESCE(SUM(ms.issued_qty), 0)
                FROM meal_issues ms
                WHERE ms.menu_item_id = mi.id
                  AND substr(ms.issued_at, 1, 10) BETWEEN ? AND ?
            ) AS issued_count,
            (
                SELECT COALESCE(AVG(f.rating), 0)
                FROM feedback f
                WHERE f.menu_item_id = mi.id
                  AND substr(f.created_at, 1, 10) BETWEEN ? AND ?
            ) AS avg_rating
        FROM menu_items mi
        WHERE mi.date BETWEEN ? AND ?
        ORDER BY mi.date DESC, mi.id DESC
        """,
        (date_from, date_to, date_from, date_to, date_from, date_to, date_from, date_to),
    ).fetchall()

    result = []
    for row in rows:
        row_dict = dict(row)
        row_dict['date_display'] = format_date_ru(row_dict.get('date'))
        row_dict['meal_type_label'] = MEAL_TYPE_LABELS.get(row_dict['meal_type'], row_dict['meal_type'])
        row_dict['avg_rating_display'] = _fmt_money(row_dict.get('avg_rating', 0))
        result.append(row_dict)
    return result


def _fetch_meal_type_rows(db, date_from: str, date_to: str) -> list[dict]:
    rows = db.execute(
        """
        SELECT
            mt.meal_type,
            (
                SELECT COUNT(*)
                FROM meal_claims mc
                JOIN menu_items m1 ON m1.id = mc.menu_item_id
                WHERE m1.meal_type = mt.meal_type
                  AND substr(mc.claimed_at, 1, 10) BETWEEN ? AND ?
            ) AS total_claims,
            (
                SELECT COALESCE(SUM(ms.issued_qty), 0)
                FROM meal_issues ms
                JOIN menu_items m2 ON m2.id = ms.menu_item_id
                WHERE m2.meal_type = mt.meal_type
                  AND substr(ms.issued_at, 1, 10) BETWEEN ? AND ?
            ) AS total_issues
        FROM (
            SELECT DISTINCT meal_type
            FROM menu_items
            WHERE date BETWEEN ? AND ?
        ) mt
        ORDER BY mt.meal_type ASC
        """,
        (date_from, date_to, date_from, date_to, date_from, date_to),
    ).fetchall()

    result = []
    for row in rows:
        row_dict = dict(row)
        row_dict['meal_type_label'] = MEAL_TYPE_LABELS.get(row_dict['meal_type'], row_dict['meal_type'])
        result.append(row_dict)
    return result


def _decorate_purchase_row(row) -> dict:
    row_dict = dict(row)
    qty_dec = Decimal(str(row_dict.get('qty', 0)))
    unit_price_dec = Decimal(str(row_dict.get('unit_price', 0)))
    total_dec = qty_dec * unit_price_dec

    row_dict['qty_display'] = _fmt_qty(qty_dec)
    row_dict['unit_price_display'] = _fmt_money(unit_price_dec)
    row_dict['total_display'] = _fmt_money(total_dec)
    row_dict['status_label'] = REQUEST_STATUS_LABELS.get(row_dict.get('status'), row_dict.get('status'))
    row_dict['created_at_display'] = format_datetime_ru(row_dict.get('created_at'))
    return row_dict


def _decorate_inventory_row(row) -> dict:
    row_dict = dict(row)
    row_dict['qty_display'] = _fmt_qty(row_dict.get('qty', 0))
    return row_dict


def _decorate_user_row(row) -> dict:
    row_dict = dict(row)
    row_dict['role_label'] = ROLE_LABELS.get(row_dict.get('role'), row_dict.get('role'))
    row_dict['is_active_value'] = int(row_dict.get('is_active', 0))
    row_dict['status_label'] = USER_STATUS_LABELS.get(row_dict['is_active_value'], 'Неизвестно')
    row_dict['created_at_display'] = format_datetime_ru(row_dict.get('created_at'))
    return row_dict


def _decorate_admin_action_row(row) -> dict:
    row_dict = dict(row)
    row_dict['created_at_display'] = format_datetime_ru(row_dict.get('created_at'))
    row_dict['action_label'] = ADMIN_ACTION_LABELS.get(row_dict.get('action_type'), row_dict.get('action_type'))
    row_dict['target_type_label'] = ADMIN_TARGET_LABELS.get(row_dict.get('target_type'), row_dict.get('target_type'))
    try:
        details = json.loads(row_dict.get('details_json') or '{}')
    except json.JSONDecodeError:
        details = {'raw': row_dict.get('details_json', '')}
    row_dict['details_pretty'] = json.dumps(details, ensure_ascii=False)
    return row_dict


def _log_admin_action(
    db,
    *,
    admin_id: int,
    action_type: str,
    target_type: str,
    target_id: int,
    details: dict,
) -> None:
    db.execute(
        'INSERT INTO admin_actions(admin_id, action_type, target_type, target_id, details_json, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (
            admin_id,
            action_type,
            target_type,
            target_id,
            json.dumps(details, ensure_ascii=False),
            now_iso(),
        ),
    )


@routes_bp.app_context_processor
def inject_user():
    return {
        'current_user': current_user(),
        'role_labels': ROLE_LABELS,
        'request_status_labels': REQUEST_STATUS_LABELS,
        'meal_type_labels': MEAL_TYPE_LABELS,
    }


@routes_bp.route('/')
@login_required
def index():
    user = current_user()
    if user['role'] == 'student':
        return redirect(url_for('routes.student_dashboard'))
    if user['role'] == 'cook':
        return redirect(url_for('routes.cook_dashboard'))
    return redirect(url_for('routes.admin_dashboard'))


@routes_bp.route('/student/dashboard')
@role_required('student')
def student_dashboard():
    student = current_user()
    db = get_db()

    menu_rows = db.execute(
        'SELECT * FROM menu_items ORDER BY date DESC, meal_type ASC, id DESC'
    ).fetchall()
    menu_items = []
    for row in menu_rows:
        row_dict = dict(row)
        row_dict['price_display'] = _fmt_money(row_dict['price'])
        row_dict['date_display'] = format_date_ru(row_dict['date'])
        row_dict['meal_type_label'] = MEAL_TYPE_LABELS.get(row_dict['meal_type'], row_dict['meal_type'])
        menu_items.append(row_dict)

    payments_rows = db.execute(
        'SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC',
        (student['id'],),
    ).fetchall()
    payments = []
    for row in payments_rows:
        row_dict = dict(row)
        row_dict['amount_display'] = _fmt_money(row_dict['amount'])
        row_dict['payment_type_label'] = PAYMENT_TYPE_LABELS.get(row_dict['payment_type'], row_dict['payment_type'])
        row_dict['status_label'] = PAYMENT_STATUS_LABELS.get(row_dict['status'], row_dict['status'])
        row_dict['created_at_display'] = format_datetime_ru(row_dict['created_at'])
        payments.append(row_dict)

    claims_rows = db.execute(
        'SELECT mc.id, mc.claimed_at, mi.title, mi.meal_type '
        'FROM meal_claims mc JOIN menu_items mi ON mc.menu_item_id = mi.id '
        'WHERE mc.user_id = ? ORDER BY mc.claimed_at DESC',
        (student['id'],),
    ).fetchall()
    claims = []
    for row in claims_rows:
        row_dict = dict(row)
        row_dict['claimed_at_display'] = format_datetime_ru(row_dict['claimed_at'])
        row_dict['meal_type_label'] = MEAL_TYPE_LABELS.get(row_dict['meal_type'], row_dict['meal_type'])
        claims.append(row_dict)

    feedback_rows = db.execute(
        'SELECT f.id, f.rating, f.comment, mi.title, f.created_at '
        'FROM feedback f JOIN menu_items mi ON f.menu_item_id = mi.id '
        'WHERE f.user_id = ? ORDER BY f.created_at DESC',
        (student['id'],),
    ).fetchall()
    feedback_list = []
    for row in feedback_rows:
        row_dict = dict(row)
        row_dict['created_at_display'] = format_datetime_ru(row_dict['created_at'])
        feedback_list.append(row_dict)

    return render_template(
        'student/dashboard.html',
        student=student,
        menu_items=menu_items,
        payments=payments,
        claims=claims,
        feedback_list=feedback_list,
    )


@routes_bp.route('/student/profile', methods=['POST'])
@role_required('student')
def student_profile():
    allergies = request.form.get('allergies', '').strip()
    preferences = request.form.get('preferences', '').strip()

    if len(allergies) > 500 or len(preferences) > 500:
        flash('Слишком длинное описание аллергий или предпочтений.')
        return redirect(url_for('routes.student_dashboard'))

    db = get_db()
    db.execute(
        'UPDATE users SET allergies = ?, preferences = ? WHERE id = ?',
        (allergies, preferences, current_user()['id']),
    )
    db.commit()
    flash('Пищевые аллергии и предпочтения сохранены.')
    return redirect(url_for('routes.student_dashboard'))


@routes_bp.route('/student/pay', methods=['POST'])
@role_required('student')
def student_pay():
    payment_type = request.form.get('payment_type', 'one_time')
    amount_raw = request.form.get('amount', '')

    if payment_type not in {'one_time', 'subscription'}:
        payment_type = 'one_time'

    try:
        amount = _parse_positive_decimal(
            amount_raw,
            max_value=MAX_PAYMENT_AMOUNT,
            places=2,
            field_name='Сумма платежа',
        )
    except ValueError as err:
        flash(str(err))
        return redirect(url_for('routes.student_dashboard'))

    db = get_db()
    db.execute(
        'INSERT INTO payments(user_id, payment_type, amount, status, created_at) VALUES (?, ?, ?, ?, ?)',
        (
            current_user()['id'],
            payment_type,
            float(amount),
            'paid',
            now_iso(),
        ),
    )
    db.commit()
    flash('Оплата успешно проведена (демо-режим).')
    return redirect(url_for('routes.student_dashboard'))


@routes_bp.route('/student/claim', methods=['POST'])
@role_required('student')
def student_claim():
    menu_item_id = request.form.get('menu_item_id')
    if not menu_item_id:
        flash('Не выбрано блюдо для отметки.')
        return redirect(url_for('routes.student_dashboard'))

    db = get_db()
    menu_item = db.execute('SELECT * FROM menu_items WHERE id = ?', (menu_item_id,)).fetchone()
    if menu_item is None:
        flash('Блюдо не найдено.')
        return redirect(url_for('routes.student_dashboard'))

    if menu_item['available_qty'] <= 0:
        flash('Невозможно отметить получение: блюдо закончилось.')
        return redirect(url_for('routes.student_dashboard'))

    already = db.execute(
        'SELECT id FROM meal_claims WHERE user_id = ? AND menu_item_id = ?',
        (current_user()['id'], menu_item_id),
    ).fetchone()
    if already:
        flash('Повторная отметка питания запрещена.')
        return redirect(url_for('routes.student_dashboard'))

    db.execute(
        'INSERT INTO meal_claims(user_id, menu_item_id, claimed_at) VALUES (?, ?, ?)',
        (current_user()['id'], menu_item_id, now_iso()),
    )
    db.execute(
        'UPDATE menu_items SET available_qty = available_qty - 1 WHERE id = ? AND available_qty > 0',
        (menu_item_id,),
    )
    db.commit()
    flash('Получение питания отмечено.')
    return redirect(url_for('routes.student_dashboard'))


@routes_bp.route('/student/feedback', methods=['POST'])
@role_required('student')
def student_feedback():
    menu_item_id = request.form.get('menu_item_id')
    rating_raw = request.form.get('rating', '5').strip()
    comment = request.form.get('comment', '').strip()

    if not menu_item_id or not comment:
        flash('Для отзыва нужно выбрать блюдо и написать комментарий.')
        return redirect(url_for('routes.student_dashboard'))
    if len(comment) > 500:
        flash('Комментарий слишком длинный.')
        return redirect(url_for('routes.student_dashboard'))

    try:
        rating = int(rating_raw)
    except ValueError:
        rating = 5
    rating = max(1, min(5, rating))

    db = get_db()
    db.execute(
        'INSERT INTO feedback(user_id, menu_item_id, rating, comment, created_at) VALUES (?, ?, ?, ?, ?)',
        (
            current_user()['id'],
            menu_item_id,
            rating,
            comment,
            now_iso(),
        ),
    )
    db.commit()
    flash('Отзыв сохранен.')
    return redirect(url_for('routes.student_dashboard'))


@routes_bp.route('/cook/dashboard')
@role_required('cook')
def cook_dashboard():
    db = get_db()
    menu_rows = db.execute('SELECT * FROM menu_items ORDER BY date DESC, id DESC').fetchall()
    menu_items = []
    for row in menu_rows:
        row_dict = dict(row)
        row_dict['price_display'] = _fmt_money(row_dict['price'])
        row_dict['date_display'] = format_date_ru(row_dict['date'])
        row_dict['meal_type_label'] = MEAL_TYPE_LABELS.get(row_dict['meal_type'], row_dict['meal_type'])
        menu_items.append(row_dict)

    inventory_rows = db.execute('SELECT * FROM inventory ORDER BY product_name ASC').fetchall()
    inventory = [_decorate_inventory_row(row) for row in inventory_rows]

    request_rows = db.execute(
        'SELECT pr.*, u.name AS cook_name FROM purchase_requests pr '
        'JOIN users u ON u.id = pr.cook_id ORDER BY pr.created_at DESC'
    ).fetchall()
    requests = [_decorate_purchase_row(row) for row in request_rows]

    issues_rows = db.execute(
        'SELECT mi.title, ms.issued_qty, ms.issue_note, ms.issued_at '
        'FROM meal_issues ms JOIN menu_items mi ON mi.id = ms.menu_item_id '
        'WHERE ms.cook_id = ? ORDER BY ms.issued_at DESC',
        (current_user()['id'],),
    ).fetchall()
    issues = []
    for row in issues_rows:
        row_dict = dict(row)
        row_dict['issued_at_display'] = format_datetime_ru(row_dict['issued_at'])
        issues.append(row_dict)

    low_stock = [
        row for row in inventory if Decimal(str(row['qty'])) < LOW_STOCK_THRESHOLD
    ]
    return render_template(
        'cook/dashboard.html',
        menu_items=menu_items,
        inventory=inventory,
        low_stock=low_stock,
        requests=requests,
        issues=issues,
    )


@routes_bp.route('/cook/issue', methods=['POST'])
@role_required('cook')
def cook_issue():
    menu_item_id = request.form.get('menu_item_id')
    inventory_id = request.form.get('inventory_id')
    issue_note = request.form.get('issue_note', '').strip()
    if len(issue_note) > 300:
        flash('Комментарий к выдаче слишком длинный.')
        return redirect(url_for('routes.cook_dashboard'))

    try:
        issued_qty = int(request.form.get('issued_qty', '1'))
    except ValueError:
        issued_qty = 0

    if issued_qty <= 0:
        flash('Количество выданных порций должно быть положительным.')
        return redirect(url_for('routes.cook_dashboard'))

    db = get_db()
    menu_item = db.execute('SELECT * FROM menu_items WHERE id = ?', (menu_item_id,)).fetchone()
    inv = db.execute('SELECT * FROM inventory WHERE id = ?', (inventory_id,)).fetchone()

    if menu_item is None or inv is None:
        flash('Неверные данные выдачи.')
        return redirect(url_for('routes.cook_dashboard'))

    required_units = (Decimal(issued_qty) * Decimal('0.2')).quantize(Decimal('0.001'))
    inv_qty = Decimal(str(inv['qty']))
    if inv_qty < required_units:
        flash('Недостаточно продуктов на складе для выдачи.')
        return redirect(url_for('routes.cook_dashboard'))

    if menu_item['available_qty'] < issued_qty:
        flash('Недостаточно готовых блюд в меню.')
        return redirect(url_for('routes.cook_dashboard'))

    db.execute(
        'INSERT INTO meal_issues(cook_id, menu_item_id, issued_qty, issue_note, issued_at) VALUES (?, ?, ?, ?, ?)',
        (
            current_user()['id'],
            menu_item_id,
            issued_qty,
            issue_note,
            now_iso(),
        ),
    )
    db.execute('UPDATE inventory SET qty = qty - ? WHERE id = ?', (float(required_units), inventory_id))
    db.execute('UPDATE menu_items SET available_qty = available_qty - ? WHERE id = ?', (issued_qty, menu_item_id))
    db.commit()
    flash('Выдача и учет остатков сохранены.')
    return redirect(url_for('routes.cook_dashboard'))


@routes_bp.route('/cook/inventory/update', methods=['POST'])
@role_required('cook')
def cook_inventory_update():
    inventory_id = request.form.get('inventory_id')
    operation = request.form.get('operation', 'set')
    try:
        delta = _parse_positive_decimal(
            request.form.get('delta_qty', ''),
            max_value=MAX_QUANTITY_VALUE,
            places=3,
            field_name='Количество',
        )
    except ValueError as err:
        flash(str(err))
        return redirect(url_for('routes.cook_dashboard'))

    db = get_db()
    inv = db.execute('SELECT * FROM inventory WHERE id = ?', (inventory_id,)).fetchone()
    if inv is None:
        flash('Позиция склада не найдена.')
        return redirect(url_for('routes.cook_dashboard'))

    old_qty = Decimal(str(inv['qty']))
    new_qty = old_qty
    if operation == 'add':
        new_qty = old_qty + delta
    elif operation == 'subtract':
        new_qty = old_qty - delta
    else:
        flash('Некорректная операция изменения остатков.')
        return redirect(url_for('routes.cook_dashboard'))

    if new_qty < 0:
        flash('Остаток не может быть отрицательным.')
        return redirect(url_for('routes.cook_dashboard'))

    if new_qty > MAX_QUANTITY_VALUE:
        flash(f'Остаток не может быть больше {_fmt_qty(MAX_QUANTITY_VALUE)}.')
        return redirect(url_for('routes.cook_dashboard'))

    db.execute('UPDATE inventory SET qty = ? WHERE id = ?', (float(new_qty), inventory_id))
    db.commit()
    flash('Остатки обновлены.')
    return redirect(url_for('routes.cook_dashboard'))


@routes_bp.route('/cook/purchase-request', methods=['POST'])
@role_required('cook')
def cook_purchase_request():
    product_name = request.form.get('product_name', '').strip()
    reason = request.form.get('reason', '').strip()

    if not product_name or not reason:
        flash('Заполните корректно заявку на закупку.')
        return redirect(url_for('routes.cook_dashboard'))
    if len(product_name) > 120 or len(reason) > 500:
        flash('Слишком длинное название продукта или обоснование.')
        return redirect(url_for('routes.cook_dashboard'))

    try:
        qty = _parse_positive_decimal(
            request.form.get('qty', ''),
            max_value=MAX_QUANTITY_VALUE,
            places=3,
            field_name='Количество',
        )
        unit_price = _parse_positive_decimal(
            request.form.get('unit_price', ''),
            max_value=MAX_UNIT_PRICE,
            places=2,
            field_name='Цена за единицу',
        )
    except ValueError as err:
        flash(str(err))
        return redirect(url_for('routes.cook_dashboard'))

    db = get_db()
    db.execute(
        'INSERT INTO purchase_requests(cook_id, product_name, qty, unit_price, reason, created_at) '
        'VALUES (?, ?, ?, ?, ?, ?)',
        (
            current_user()['id'],
            product_name,
            float(qty),
            float(unit_price),
            reason,
            now_iso(),
        ),
    )
    db.commit()
    flash('Заявка на закупку отправлена администратору.')
    return redirect(url_for('routes.cook_dashboard'))


@routes_bp.route('/admin/dashboard')
@role_required('admin')
def admin_dashboard():
    date_from, date_to, period_error = _resolve_period(
        request.args.get('date_from'),
        request.args.get('date_to'),
    )
    if period_error:
        flash(period_error)

    db = get_db()
    metrics = _collect_admin_metrics(db, date_from, date_to)

    purchase_request_rows = db.execute(
        'SELECT pr.*, u.name AS cook_name FROM purchase_requests pr '
        'JOIN users u ON u.id = pr.cook_id ORDER BY pr.created_at DESC'
    ).fetchall()
    purchase_requests = [_decorate_purchase_row(row) for row in purchase_request_rows]

    report_rows = _fetch_report_rows(db, date_from, date_to)
    meal_type_rows = _fetch_meal_type_rows(db, date_from, date_to)

    action_rows = db.execute(
        'SELECT aa.*, u.name AS admin_name FROM admin_actions aa '
        'JOIN users u ON u.id = aa.admin_id ORDER BY aa.created_at DESC LIMIT 20'
    ).fetchall()
    admin_actions = [_decorate_admin_action_row(row) for row in action_rows]

    return render_template(
        'admin/dashboard.html',
        purchase_requests=purchase_requests,
        total_payments=metrics['total_payments'],
        total_claims=metrics['total_claims'],
        unique_students_with_claims=metrics['unique_students_with_claims'],
        total_issues=metrics['total_issues'],
        approved_procurement_cost=metrics['approved_procurement_cost'],
        approved_requests_count=metrics['approved_requests_count'],
        operating_balance=metrics['operating_balance'],
        report_generated_at=metrics['generated_at_display'],
        report_rows=report_rows,
        meal_type_rows=meal_type_rows,
        period_from=date_from,
        period_to=date_to,
        period_from_display=metrics['period_from_display'],
        period_to_display=metrics['period_to_display'],
        admin_actions=admin_actions,
    )


@routes_bp.route('/admin/users')
@role_required('admin')
def admin_users():
    db = get_db()
    users_rows = db.execute(
        'SELECT id, name, email, role, is_active, created_at FROM users ORDER BY id ASC'
    ).fetchall()
    users = [_decorate_user_row(row) for row in users_rows]
    return render_template('admin/users.html', users=users)


@routes_bp.route('/admin/users/<int:user_id>/role', methods=['POST'])
@role_required('admin')
def admin_update_user_role(user_id: int):
    new_role = request.form.get('role', '').strip()
    if new_role not in {'student', 'cook', 'admin'}:
        flash('Некорректная роль.')
        return redirect(url_for('routes.admin_users'))

    db = get_db()
    target_user = db.execute(
        'SELECT id, role, name FROM users WHERE id = ?',
        (user_id,),
    ).fetchone()
    if target_user is None:
        flash('Пользователь не найден.')
        return redirect(url_for('routes.admin_users'))

    me = current_user()
    if me and me['id'] == user_id and new_role != 'admin':
        flash('Нельзя понизить собственную роль текущей сессии.')
        return redirect(url_for('routes.admin_users'))

    old_role = target_user['role']
    if old_role == new_role:
        flash('Роль пользователя не изменилась.')
        return redirect(url_for('routes.admin_users'))

    db.execute('UPDATE users SET role = ? WHERE id = ?', (new_role, user_id))
    _log_admin_action(
        db,
        admin_id=current_user()['id'],
        action_type='user_role_changed',
        target_type='user',
        target_id=user_id,
        details={
            'пользователь': target_user['name'],
            'было': ROLE_LABELS.get(old_role, old_role),
            'стало': ROLE_LABELS.get(new_role, new_role),
        },
    )
    db.commit()
    flash('Роль пользователя обновлена.')
    return redirect(url_for('routes.admin_users'))


@routes_bp.route('/admin/users/<int:user_id>/block', methods=['POST'])
@role_required('admin')
def admin_block_user(user_id: int):
    action = request.form.get('action', '').strip()
    if action not in {'block', 'unblock'}:
        flash('Некорректное действие блокировки.')
        return redirect(url_for('routes.admin_users'))

    db = get_db()
    target_user = db.execute(
        'SELECT id, is_active, name FROM users WHERE id = ?',
        (user_id,),
    ).fetchone()
    if target_user is None:
        flash('Пользователь не найден.')
        return redirect(url_for('routes.admin_users'))

    me = current_user()
    if me and me['id'] == user_id and action == 'block':
        flash('Нельзя заблокировать собственный аккаунт.')
        return redirect(url_for('routes.admin_users'))

    old_state = int(target_user['is_active'])
    next_state = 0 if action == 'block' else 1
    if old_state == next_state:
        flash('Статус пользователя не изменился.')
        return redirect(url_for('routes.admin_users'))

    db.execute('UPDATE users SET is_active = ? WHERE id = ?', (next_state, user_id))
    _log_admin_action(
        db,
        admin_id=current_user()['id'],
        action_type='user_block_state_changed',
        target_type='user',
        target_id=user_id,
        details={
            'пользователь': target_user['name'],
            'было': USER_STATUS_LABELS.get(old_state, str(old_state)),
            'стало': USER_STATUS_LABELS.get(next_state, str(next_state)),
        },
    )
    db.commit()
    flash('Статус пользователя обновлен.')
    return redirect(url_for('routes.admin_users'))


@routes_bp.route('/admin/report.csv')
@role_required('admin')
def admin_report_csv():
    date_from, date_to, _period_error = _resolve_period(
        request.args.get('date_from'),
        request.args.get('date_to'),
    )

    db = get_db()
    metrics = _collect_admin_metrics(db, date_from, date_to)
    report_rows = _fetch_report_rows(db, date_from, date_to)
    meal_type_rows = _fetch_meal_type_rows(db, date_from, date_to)

    output = io.StringIO(newline='')
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Раздел', 'Метрика', 'Значение'])
    writer.writerow(['Параметры', 'Сформирован', metrics['generated_at_display']])
    writer.writerow(['Параметры', 'Период с', metrics['period_from_display']])
    writer.writerow(['Параметры', 'Период по', metrics['period_to_display']])
    writer.writerow(['Оплаты', 'Сумма оплат', _fmt_money(metrics['total_payments_raw'])])
    writer.writerow(['Посещаемость', 'Количество получений', metrics['total_claims']])
    writer.writerow(['Посещаемость', 'Уникальных учеников', metrics['unique_students_with_claims']])
    writer.writerow(['Выдача', 'Выдано порций', metrics['total_issues']])
    writer.writerow(['Закупки', 'Одобренных заявок', metrics['approved_requests_count']])
    writer.writerow(['Закупки', 'Сумма затрат', _fmt_money(metrics['approved_procurement_cost_raw'])])
    writer.writerow(['Финансы', 'Баланс', _fmt_money(metrics['operating_balance_raw'])])

    writer.writerow([])
    writer.writerow(['Отчет по блюдам'])
    writer.writerow(['Дата', 'Блюдо', 'Тип', 'Получений', 'Выдач', 'Средняя оценка'])
    for row in report_rows:
        writer.writerow(
            [
                row['date_display'],
                row['title'],
                row['meal_type_label'],
                row['claims_count'],
                row['issued_count'],
                row['avg_rating_display'],
            ]
        )

    writer.writerow([])
    writer.writerow(['Сводка по типам питания'])
    writer.writerow(['Тип', 'Получений', 'Выдач'])
    for row in meal_type_rows:
        writer.writerow([row['meal_type_label'], row['total_claims'], row['total_issues']])

    csv_data = '\ufeff' + output.getvalue()
    safe_stamp = metrics['generated_at'].replace(':', '-').replace('+', '_')
    return Response(
        csv_data,
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename=otchet_admin_{safe_stamp}.csv'},
    )


@routes_bp.route('/admin/purchase-request/<int:request_id>/status', methods=['POST'])
@role_required('admin')
def admin_update_request(request_id: int):
    new_status = request.form.get('status', 'pending')
    if new_status not in {'approved', 'rejected'}:
        flash('Некорректный статус заявки.')
        return redirect(url_for('routes.admin_dashboard'))

    db = get_db()
    req = db.execute(
        'SELECT id, unit_price, qty, status, product_name FROM purchase_requests WHERE id = ?',
        (request_id,),
    ).fetchone()
    if req is None:
        flash('Заявка не найдена.')
        return redirect(url_for('routes.admin_dashboard'))
    if new_status == 'approved':
        unit_price = Decimal(str(req['unit_price'] or 0))
        qty = Decimal(str(req['qty'] or 0))
        if unit_price <= 0 or qty <= 0:
            flash('Нельзя одобрить заявку с невалидной ценой или количеством.')
            return redirect(url_for('routes.admin_dashboard'))

    old_status = req['status']
    db.execute(
        'UPDATE purchase_requests SET status = ?, reviewed_by = ? WHERE id = ?',
        (new_status, current_user()['id'], request_id),
    )
    _log_admin_action(
        db,
        admin_id=current_user()['id'],
        action_type='purchase_request_status_changed',
        target_type='purchase_request',
        target_id=request_id,
        details={
            'продукт': req['product_name'],
            'было': REQUEST_STATUS_LABELS.get(old_status, old_status),
            'стало': REQUEST_STATUS_LABELS.get(new_status, new_status),
        },
    )
    db.commit()
    flash('Статус заявки обновлен.')
    return redirect(url_for('routes.admin_dashboard'))
