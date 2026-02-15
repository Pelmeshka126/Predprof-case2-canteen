from datetime import datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for

from .auth import current_user, login_required, role_required
from .db import get_db

routes_bp = Blueprint('routes', __name__)


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
    db = get_db()
    menu_items = db.execute(
        'SELECT * FROM menu_items ORDER BY date DESC, meal_type ASC, id DESC'
    ).fetchall()
    payments = db.execute(
        'SELECT * FROM payments WHERE user_id = ? ORDER BY created_at DESC',
        (current_user()['id'],),
    ).fetchall()
    claims = db.execute(
        'SELECT mc.id, mc.claimed_at, mi.title, mi.meal_type '
        'FROM meal_claims mc JOIN menu_items mi ON mc.menu_item_id = mi.id '
        'WHERE mc.user_id = ? ORDER BY mc.claimed_at DESC',
        (current_user()['id'],),
    ).fetchall()
    feedback_list = db.execute(
        'SELECT f.id, f.rating, f.comment, mi.title, f.created_at '
        'FROM feedback f JOIN menu_items mi ON f.menu_item_id = mi.id '
        'WHERE f.user_id = ? ORDER BY f.created_at DESC',
        (current_user()['id'],),
    ).fetchall()
    return render_template(
        'student/dashboard.html',
        menu_items=menu_items,
        payments=payments,
        claims=claims,
        feedback_list=feedback_list,
    )


@routes_bp.route('/student/pay', methods=['POST'])
@role_required('student')
def student_pay():
    payment_type = request.form.get('payment_type', 'one_time')
    amount_raw = request.form.get('amount', '0').strip()

    if payment_type not in {'one_time', 'subscription'}:
        payment_type = 'one_time'

    try:
        amount = float(amount_raw)
    except ValueError:
        flash('Некорректная сумма платежа.')
        return redirect(url_for('routes.student_dashboard'))

    if amount <= 0:
        flash('Сумма должна быть больше нуля.')
        return redirect(url_for('routes.student_dashboard'))

    db = get_db()
    db.execute(
        'INSERT INTO payments(user_id, payment_type, amount, status, created_at) VALUES (?, ?, ?, ?, ?)',
        (current_user()['id'], payment_type, amount, 'paid', datetime.now().isoformat(timespec='seconds')),
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
    menu_items = db.execute('SELECT * FROM menu_items ORDER BY date DESC, id DESC').fetchall()
    inventory = db.execute('SELECT * FROM inventory ORDER BY product_name ASC').fetchall()
    requests = db.execute(
        'SELECT pr.*, u.name AS cook_name FROM purchase_requests pr '
        'JOIN users u ON u.id = pr.cook_id ORDER BY pr.created_at DESC'
    ).fetchall()
    issues = db.execute(
        'SELECT mi.title, ms.issued_qty, ms.issue_note, ms.issued_at '
        'FROM meal_issues ms JOIN menu_items mi ON mi.id = ms.menu_item_id '
        'WHERE ms.cook_id = ? ORDER BY ms.issued_at DESC',
        (current_user()['id'],),
    ).fetchall()
    return render_template(
        'cook/dashboard.html',
        menu_items=menu_items,
        inventory=inventory,
        requests=requests,
        issues=issues,
    )


@routes_bp.route('/cook/issue', methods=['POST'])
@role_required('cook')
def cook_issue():
    menu_item_id = request.form.get('menu_item_id')
    inventory_id = request.form.get('inventory_id')
    issue_note = request.form.get('issue_note', '').strip()

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

    required_units = issued_qty * 0.2
    if inv['qty'] < required_units:
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
    db.execute('UPDATE inventory SET qty = qty - ? WHERE id = ?', (required_units, inventory_id))
    db.execute('UPDATE menu_items SET available_qty = available_qty - ? WHERE id = ?', (issued_qty, menu_item_id))
    db.commit()
    flash('Выдача и учет остатков сохранены.')
    return redirect(url_for('routes.cook_dashboard'))


@routes_bp.route('/cook/purchase-request', methods=['POST'])
@role_required('cook')
def cook_purchase_request():
    product_name = request.form.get('product_name', '').strip()
    reason = request.form.get('reason', '').strip()

    try:
        qty = float(request.form.get('qty', '0'))
    except ValueError:
        qty = 0

    if not product_name or not reason or qty <= 0:
        flash('Заполните корректно заявку на закупку.')
        return redirect(url_for('routes.cook_dashboard'))

    db = get_db()
    db.execute(
        'INSERT INTO purchase_requests(cook_id, product_name, qty, reason, created_at) VALUES (?, ?, ?, ?, ?)',
        (
            current_user()['id'],
            product_name,
            qty,
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
    pending = db.execute(
        'SELECT pr.*, u.name AS cook_name FROM purchase_requests pr '
        'JOIN users u ON u.id = pr.cook_id ORDER BY pr.created_at DESC'
    ).fetchall()

    total_payments = db.execute('SELECT COALESCE(SUM(amount), 0) AS s FROM payments WHERE status = ?', ('paid',)).fetchone()['s']
    total_claims = db.execute('SELECT COUNT(*) AS c FROM meal_claims').fetchone()['c']
    total_issues = db.execute('SELECT COALESCE(SUM(issued_qty), 0) AS c FROM meal_issues').fetchone()['c']

    report_rows = db.execute(
        """
        SELECT
            mi.title,
            mi.meal_type,
            COUNT(mc.id) AS claims_count,
            COALESCE(AVG(f.rating), 0) AS avg_rating
        FROM menu_items mi
        LEFT JOIN meal_claims mc ON mc.menu_item_id = mi.id
        LEFT JOIN feedback f ON f.menu_item_id = mi.id
        GROUP BY mi.id
        ORDER BY mi.date DESC, mi.id DESC
        """
    ).fetchall()

    return render_template(
        'admin/dashboard.html',
        pending=pending,
        total_payments=round(float(total_payments), 2),
        total_claims=total_claims,
        total_issues=total_issues,
        report_rows=report_rows,
    )


@routes_bp.route('/admin/purchase-request/<int:request_id>/status', methods=['POST'])
@role_required('admin')
def admin_update_request(request_id: int):
    new_status = request.form.get('status', 'pending')
    if new_status not in {'approved', 'rejected'}:
        flash('Некорректный статус заявки.')
        return redirect(url_for('routes.admin_dashboard'))

    db = get_db()
    db.execute(
        'UPDATE purchase_requests SET status = ?, reviewed_by = ? WHERE id = ?',
        (new_status, current_user()['id'], request_id),
    )
    db.commit()
    flash('Статус заявки обновлен.')
    return redirect(url_for('routes.admin_dashboard'))
