import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from .auth import current_user, login_required, role_required
from .db import get_db

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


def _decorate_purchase_row(row) -> dict:
    row_dict = dict(row)
    qty_dec = Decimal(str(row_dict.get('qty', 0)))
    unit_price_dec = Decimal(str(row_dict.get('unit_price', 0)))
    total_dec = qty_dec * unit_price_dec

    row_dict['qty_display'] = _fmt_qty(qty_dec)
    row_dict['unit_price_display'] = _fmt_money(unit_price_dec)
    row_dict['total_display'] = _fmt_money(total_dec)
    return row_dict


def _decorate_inventory_row(row) -> dict:
    row_dict = dict(row)
    row_dict['qty_display'] = _fmt_qty(row_dict.get('qty', 0))
    return row_dict


def _collect_admin_metrics(db) -> dict:
    total_payments_raw = db.execute(
        'SELECT COALESCE(SUM(amount), 0) AS s FROM payments WHERE status = ?',
        ('paid',),
    ).fetchone()['s']
    total_claims = db.execute('SELECT COUNT(*) AS c FROM meal_claims').fetchone()['c']
    unique_students_with_claims = db.execute(
        'SELECT COUNT(DISTINCT user_id) AS c FROM meal_claims'
    ).fetchone()['c']
    total_issues = db.execute(
        'SELECT COALESCE(SUM(issued_qty), 0) AS c FROM meal_issues'
    ).fetchone()['c']
    approved_procurement_cost_raw = db.execute(
        """
        SELECT COALESCE(SUM(qty * unit_price), 0) AS s
        FROM purchase_requests
        WHERE status = ? AND qty > 0 AND unit_price > 0
        """,
        ('approved',),
    ).fetchone()['s']
    approved_requests_count = db.execute(
        """
        SELECT COUNT(*) AS c
        FROM purchase_requests
        WHERE status = ? AND qty > 0 AND unit_price > 0
        """,
        ('approved',),
    ).fetchone()['c']

    total_payments = Decimal(str(total_payments_raw or 0))
    approved_procurement_cost = Decimal(str(approved_procurement_cost_raw or 0))
    operating_balance = total_payments - approved_procurement_cost
    generated_at = datetime.now().isoformat(timespec='seconds')

    return {
        'generated_at': generated_at,
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


@routes_bp.app_context_processor
def inject_user():
    return {'current_user': current_user()}


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
        menu_items.append(row_dict)

    payments = db.execute(
        'SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC',
        (student['id'],),
    ).fetchall()
    decorated_payments = []
    for row in payments:
        row_dict = dict(row)
        row_dict['amount_display'] = _fmt_money(row_dict['amount'])
        decorated_payments.append(row_dict)

    claims = db.execute(
        'SELECT mc.id, mc.claimed_at, mi.title, mi.meal_type '
        'FROM meal_claims mc JOIN menu_items mi ON mc.menu_item_id = mi.id '
        'WHERE mc.user_id = ? ORDER BY mc.claimed_at DESC',
        (student['id'],),
    ).fetchall()
    feedback_list = db.execute(
        'SELECT f.id, f.rating, f.comment, mi.title, f.created_at '
        'FROM feedback f JOIN menu_items mi ON f.menu_item_id = mi.id '
        'WHERE f.user_id = ? ORDER BY f.created_at DESC',
        (student['id'],),
    ).fetchall()
    return render_template(
        'student/dashboard.html',
        student=student,
        menu_items=menu_items,
        payments=decorated_payments,
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
            datetime.now().isoformat(timespec='seconds'),
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
        (current_user()['id'], menu_item_id, datetime.now().isoformat(timespec='seconds')),
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
            datetime.now().isoformat(timespec='seconds'),
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
        menu_items.append(row_dict)

    inventory_rows = db.execute('SELECT * FROM inventory ORDER BY product_name ASC').fetchall()
    inventory = [_decorate_inventory_row(row) for row in inventory_rows]

    request_rows = db.execute(
        'SELECT pr.*, u.name AS cook_name FROM purchase_requests pr '
        'JOIN users u ON u.id = pr.cook_id ORDER BY pr.created_at DESC'
    ).fetchall()
    requests = [_decorate_purchase_row(row) for row in request_rows]

    issues = db.execute(
        'SELECT mi.title, ms.issued_qty, ms.issue_note, ms.issued_at '
        'FROM meal_issues ms JOIN menu_items mi ON mi.id = ms.menu_item_id '
        'WHERE ms.cook_id = ? ORDER BY ms.issued_at DESC',
        (current_user()['id'],),
    ).fetchall()
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
            datetime.now().isoformat(timespec='seconds'),
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
            datetime.now().isoformat(timespec='seconds'),
        ),
    )
    db.commit()
    flash('Заявка на закупку отправлена администратору.')
    return redirect(url_for('routes.cook_dashboard'))


@routes_bp.route('/admin/dashboard')
@role_required('admin')
def admin_dashboard():
    db = get_db()
    metrics = _collect_admin_metrics(db)
    purchase_request_rows = db.execute(
        'SELECT pr.*, u.name AS cook_name FROM purchase_requests pr '
        'JOIN users u ON u.id = pr.cook_id ORDER BY pr.created_at DESC'
    ).fetchall()
    purchase_requests = [_decorate_purchase_row(row) for row in purchase_request_rows]

    report_rows = db.execute(
        """
        SELECT
            mi.title,
            mi.meal_type,
            COUNT(mc.id) AS claims_count,
            COALESCE(SUM(ms.issued_qty), 0) AS issued_count,
            COALESCE(AVG(f.rating), 0) AS avg_rating
        FROM menu_items mi
        LEFT JOIN meal_claims mc ON mc.menu_item_id = mi.id
        LEFT JOIN meal_issues ms ON ms.menu_item_id = mi.id
        LEFT JOIN feedback f ON f.menu_item_id = mi.id
        GROUP BY mi.id
        ORDER BY mi.date DESC, mi.id DESC
        """
    ).fetchall()

    meal_type_rows = db.execute(
        """
        SELECT
            mi.meal_type,
            COUNT(DISTINCT mc.id) AS total_claims,
            COALESCE(SUM(ms.issued_qty), 0) AS total_issues
        FROM menu_items mi
        LEFT JOIN meal_claims mc ON mc.menu_item_id = mi.id
        LEFT JOIN meal_issues ms ON ms.menu_item_id = mi.id
        GROUP BY mi.meal_type
        ORDER BY mi.meal_type ASC
        """
    ).fetchall()

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
        report_generated_at=metrics['generated_at'],
        report_rows=report_rows,
        meal_type_rows=meal_type_rows,
    )


@routes_bp.route('/admin/users')
@role_required('admin')
def admin_users():
    db = get_db()
    users = db.execute(
        'SELECT id, name, email, role, is_active, created_at FROM users ORDER BY id ASC'
    ).fetchall()
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
        'SELECT id, role FROM users WHERE id = ?',
        (user_id,),
    ).fetchone()
    if target_user is None:
        flash('Пользователь не найден.')
        return redirect(url_for('routes.admin_users'))

    me = current_user()
    if me and me['id'] == user_id and new_role != 'admin':
        flash('Нельзя понизить собственную роль текущей сессии.')
        return redirect(url_for('routes.admin_users'))

    db.execute('UPDATE users SET role = ? WHERE id = ?', (new_role, user_id))
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
        'SELECT id, is_active FROM users WHERE id = ?',
        (user_id,),
    ).fetchone()
    if target_user is None:
        flash('Пользователь не найден.')
        return redirect(url_for('routes.admin_users'))

    me = current_user()
    if me and me['id'] == user_id and action == 'block':
        flash('Нельзя заблокировать собственный аккаунт.')
        return redirect(url_for('routes.admin_users'))

    next_state = 0 if action == 'block' else 1
    db.execute('UPDATE users SET is_active = ? WHERE id = ?', (next_state, user_id))
    db.commit()
    flash('Статус пользователя обновлен.')
    return redirect(url_for('routes.admin_users'))


@routes_bp.route('/admin/report.csv')
@role_required('admin')
def admin_report_csv():
    db = get_db()
    metrics = _collect_admin_metrics(db)

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['section', 'metric', 'value'])
    writer.writerow(['meta', 'generated_at', metrics['generated_at']])
    writer.writerow(['payments', 'total_paid', _fmt_money(metrics['total_payments_raw'])])
    writer.writerow(['attendance', 'total_claims', metrics['total_claims']])
    writer.writerow(
        ['attendance', 'unique_students_with_claims', metrics['unique_students_with_claims']]
    )
    writer.writerow(['issuance', 'total_issues', metrics['total_issues']])
    writer.writerow(
        ['procurement', 'approved_requests_count', metrics['approved_requests_count']]
    )
    writer.writerow(
        ['procurement', 'approved_procurement_cost', _fmt_money(metrics['approved_procurement_cost_raw'])]
    )
    writer.writerow(['finance', 'operating_balance', _fmt_money(metrics['operating_balance_raw'])])

    report_rows = db.execute(
        """
        SELECT
            mi.title,
            mi.meal_type,
            COUNT(mc.id) AS claims_count,
            COALESCE(SUM(ms.issued_qty), 0) AS issued_count,
            COALESCE(AVG(f.rating), 0) AS avg_rating
        FROM menu_items mi
        LEFT JOIN meal_claims mc ON mc.menu_item_id = mi.id
        LEFT JOIN meal_issues ms ON ms.menu_item_id = mi.id
        LEFT JOIN feedback f ON f.menu_item_id = mi.id
        GROUP BY mi.id
        ORDER BY mi.date DESC, mi.id DESC
        """
    ).fetchall()
    writer.writerow([])
    writer.writerow(['meal_title', 'meal_type', 'claims_count', 'issued_count', 'avg_rating'])
    for row in report_rows:
        writer.writerow(
            [row['title'], row['meal_type'], row['claims_count'], row['issued_count'], _fmt_money(row['avg_rating'])]
        )

    csv_data = output.getvalue()
    safe_stamp = metrics['generated_at'].replace(':', '-')
    return Response(
        csv_data,
        mimetype='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename=admin_report_{safe_stamp}.csv'},
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
        'SELECT id, unit_price, qty FROM purchase_requests WHERE id = ?',
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

    db.execute(
        'UPDATE purchase_requests SET status = ?, reviewed_by = ? WHERE id = ?',
        (new_status, current_user()['id'], request_id),
    )
    db.commit()
    flash('Статус заявки обновлен.')
    return redirect(url_for('routes.admin_dashboard'))
