import os
import re
import tempfile
import unittest

from app import create_app
from app.db import get_db
from app.db import init_db


class Case2AppTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='predprof_case2_test_', suffix='.db')
        os.close(fd)
        self.app = create_app({'TESTING': True, 'DATABASE': self.db_path, 'SECRET_KEY': 'test-key'})
        self.client = self.app.test_client()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def _extract_csrf(self, html: str) -> str:
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        self.assertIsNotNone(match)
        return match.group(1)

    def _csrf_from_page(self, path: str) -> str:
        response = self.client.get(path, follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        return self._extract_csrf(response.get_data(as_text=True))

    def register(self, name: str, email: str, password: str, role: str | None = None):
        csrf = self._csrf_from_page('/register')
        payload = {'name': name, 'email': email, 'password': password, 'csrf_token': csrf}
        if role is not None:
            payload['role'] = role
        return self.client.post('/register', data=payload, follow_redirects=True)

    def login(self, email: str, password: str):
        csrf = self._csrf_from_page('/login')
        return self.client.post(
            '/login',
            data={'email': email, 'password': password, 'csrf_token': csrf},
            follow_redirects=True,
        )

    def test_register_always_creates_student_even_with_role_spoof(self):
        self.register('Spoofed Admin', 'spoof@local.test', 'secret123', role='admin')

        with self.app.app_context():
            db = get_db()
            row = db.execute('SELECT role FROM users WHERE email = ?', ('spoof@local.test',)).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row['role'], 'student')

    def test_public_registration_cannot_create_cook_or_admin(self):
        self.register('Cook Try', 'cooktry@local.test', 'secret123', role='cook')
        page = self.login('cooktry@local.test', 'secret123').get_data(as_text=True)
        self.assertIn('Кабинет ученика', page)

    def test_admin_can_change_user_role(self):
        self.register('Role User', 'roleuser@local.test', 'secret123')

        with self.app.app_context():
            db = get_db()
            user = db.execute('SELECT id FROM users WHERE email = ?', ('roleuser@local.test',)).fetchone()
            user_id = user['id']

        self.login('admin@predprof.local', 'admin123')
        csrf = self._csrf_from_page('/admin/users')
        response = self.client.post(
            f'/admin/users/{user_id}/role',
            data={'role': 'cook', 'csrf_token': csrf},
            follow_redirects=True,
        )
        self.assertIn('Роль пользователя обновлена', response.get_data(as_text=True))

        with self.app.app_context():
            db = get_db()
            row = db.execute('SELECT role FROM users WHERE id = ?', (user_id,)).fetchone()
            self.assertEqual(row['role'], 'cook')

    def test_admin_can_block_user_and_blocked_user_cannot_login(self):
        self.register('Blocked User', 'blocked@local.test', 'secret123')

        with self.app.app_context():
            db = get_db()
            user = db.execute('SELECT id FROM users WHERE email = ?', ('blocked@local.test',)).fetchone()
            user_id = user['id']

        self.login('admin@predprof.local', 'admin123')
        csrf = self._csrf_from_page('/admin/users')
        self.client.post(
            f'/admin/users/{user_id}/block',
            data={'action': 'block', 'csrf_token': csrf},
            follow_redirects=True,
        )
        self.client.get('/logout', follow_redirects=True)

        blocked_login = self.login('blocked@local.test', 'secret123').get_data(as_text=True)
        self.assertIn('Пользователь заблокирован администратором', blocked_login)

    def test_csrf_missing_token_rejected(self):
        response = self.client.post('/login', data={'email': 'x', 'password': 'y'}, follow_redirects=True)
        self.assertEqual(response.status_code, 403)

    def test_csrf_invalid_token_rejected_for_authorized_post(self):
        self.login('cook@predprof.local', 'cook123')
        response = self.client.post(
            '/cook/purchase-request',
            data={
                'product_name': 'Тест',
                'qty': '1',
                'unit_price': '50',
                'reason': 'Тест',
                'csrf_token': 'wrong-token',
            },
            follow_redirects=True,
        )
        self.assertEqual(response.status_code, 403)

    def test_cross_role_route_returns_403(self):
        self.login('student@predprof.local', 'student123')
        response = self.client.get('/admin/dashboard', follow_redirects=False)
        self.assertEqual(response.status_code, 403)

    def test_student_can_update_allergies_and_preferences(self):
        self.login('student@predprof.local', 'student123')
        csrf = self._csrf_from_page('/student/dashboard')

        response = self.client.post(
            '/student/profile',
            data={
                'allergies': 'Орехи, лактоза',
                'preferences': 'Без сахара',
                'csrf_token': csrf,
            },
            follow_redirects=True,
        )
        html = response.get_data(as_text=True)
        self.assertIn('Пищевые аллергии и предпочтения сохранены', html)

        with self.app.app_context():
            db = get_db()
            row = db.execute(
                'SELECT allergies, preferences FROM users WHERE email = ?',
                ('student@predprof.local',),
            ).fetchone()
        self.assertEqual(row['allergies'], 'Орехи, лактоза')
        self.assertEqual(row['preferences'], 'Без сахара')

    def test_duplicate_claim_is_blocked(self):
        self.login('student@predprof.local', 'student123')
        page = self.client.get('/student/dashboard').get_data(as_text=True)
        menu_id_match = re.search(r'name="menu_item_id" value="(\d+)"', page)
        self.assertIsNotNone(menu_id_match)
        menu_id = menu_id_match.group(1)
        csrf = self._extract_csrf(page)

        first = self.client.post(
            '/student/claim',
            data={'menu_item_id': menu_id, 'csrf_token': csrf},
            follow_redirects=True,
        )
        self.assertIn('Получение питания отмечено', first.get_data(as_text=True))

        csrf = self._csrf_from_page('/student/dashboard')
        second = self.client.post(
            '/student/claim',
            data={'menu_item_id': menu_id, 'csrf_token': csrf},
            follow_redirects=True,
        )
        self.assertIn('Повторная отметка питания запрещена', second.get_data(as_text=True))

    def test_shortage_of_inventory_is_blocked(self):
        self.login('cook@predprof.local', 'cook123')
        with self.app.app_context():
            db = get_db()
            menu_item = db.execute('SELECT id FROM menu_items ORDER BY id LIMIT 1').fetchone()
            inv = db.execute('SELECT id FROM inventory ORDER BY id LIMIT 1').fetchone()
            db.execute('UPDATE inventory SET qty = 0 WHERE id = ?', (inv['id'],))
            db.commit()

        csrf = self._csrf_from_page('/cook/dashboard')
        response = self.client.post(
            '/cook/issue',
            data={
                'menu_item_id': menu_item['id'],
                'inventory_id': inv['id'],
                'issued_qty': 1,
                'issue_note': 'test',
                'csrf_token': csrf,
            },
            follow_redirects=True,
        )
        self.assertIn('Недостаточно продуктов на складе для выдачи', response.get_data(as_text=True))

    def test_numeric_bounds_and_exponential_are_rejected(self):
        self.login('cook@predprof.local', 'cook123')

        csrf = self._csrf_from_page('/cook/dashboard')
        response = self.client.post(
            '/cook/purchase-request',
            data={
                'product_name': 'Тест',
                'qty': '1e9',
                'unit_price': '10',
                'reason': 'Проверка',
                'csrf_token': csrf,
            },
            follow_redirects=True,
        )
        self.assertIn('экспоненциальная запись недопустима', response.get_data(as_text=True))

        csrf = self._csrf_from_page('/cook/dashboard')
        response = self.client.post(
            '/cook/purchase-request',
            data={
                'product_name': 'Тест',
                'qty': '1',
                'unit_price': '1000000000',
                'reason': 'Проверка',
                'csrf_token': csrf,
            },
            follow_redirects=True,
        )
        self.assertIn('превышен допустимый максимум', response.get_data(as_text=True))

    def test_valid_fractions_are_saved_and_rendered_without_scientific_notation(self):
        self.login('cook@predprof.local', 'cook123')
        csrf = self._csrf_from_page('/cook/dashboard')

        self.client.post(
            '/cook/purchase-request',
            data={
                'product_name': 'Гречка',
                'qty': '12.345',
                'unit_price': '71.50',
                'reason': 'Тест дробей',
                'csrf_token': csrf,
            },
            follow_redirects=True,
        )

        dashboard = self.client.get('/cook/dashboard', follow_redirects=True).get_data(as_text=True)
        self.assertIn('12.345', dashboard)
        self.assertIn('71.50', dashboard)
        self.assertNotIn('e+', dashboard.lower())

    def test_legacy_policy_rejects_approved_rows_with_zero_price(self):
        with self.app.app_context():
            db = get_db()
            db.execute('DROP TRIGGER IF EXISTS purchase_requests_positive_price_insert')
            db.execute('DROP TRIGGER IF EXISTS purchase_requests_positive_price_update')
            db.execute('PRAGMA ignore_check_constraints = ON')
            cook_id = db.execute("SELECT id FROM users WHERE role = 'cook' LIMIT 1").fetchone()['id']
            db.execute(
                'INSERT INTO purchase_requests(cook_id, product_name, qty, unit_price, reason, status, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (cook_id, 'Legacy', 5, 0, 'legacy reason', 'approved', '2026-01-01T00:00:00'),
            )
            db.execute('PRAGMA ignore_check_constraints = OFF')
            db.commit()
            init_db()
            row = db.execute(
                "SELECT status, reason FROM purchase_requests WHERE product_name = 'Legacy' ORDER BY id DESC LIMIT 1"
            ).fetchone()

        self.assertEqual(row['status'], 'rejected')
        self.assertIn('legacy-запись', row['reason'])

    def test_admin_report_has_cost_balance_and_csv_export(self):
        self.login('cook@predprof.local', 'cook123')
        csrf = self._csrf_from_page('/cook/dashboard')
        self.client.post(
            '/cook/purchase-request',
            data={
                'product_name': 'Рис',
                'qty': '5',
                'unit_price': '80',
                'reason': 'Гарнир',
                'csrf_token': csrf,
            },
            follow_redirects=True,
        )
        self.client.get('/logout', follow_redirects=True)

        self.login('admin@predprof.local', 'admin123')
        admin_page = self.client.get('/admin/dashboard').get_data(as_text=True)
        req_id = re.search(r'/admin/purchase-request/(\d+)/status', admin_page)
        self.assertIsNotNone(req_id)

        csrf = self._csrf_from_page('/admin/dashboard')
        self.client.post(
            f'/admin/purchase-request/{req_id.group(1)}/status',
            data={'status': 'approved', 'csrf_token': csrf},
            follow_redirects=True,
        )

        refreshed = self.client.get('/admin/dashboard').get_data(as_text=True)
        self.assertIn('Сумма затрат по одобренным закупкам', refreshed)
        self.assertIn('400.00', refreshed)
        self.assertIn('Баланс (оплаты - затраты)', refreshed)

        csv_response = self.client.get('/admin/report.csv', follow_redirects=True)
        csv_text = csv_response.get_data(as_text=True)
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn('text/csv', csv_response.content_type)
        self.assertIn('generated_at', csv_text)
        self.assertIn('approved_procurement_cost,400.00', csv_text)

    def test_admin_cannot_block_himself(self):
        self.login('admin@predprof.local', 'admin123')

        with self.app.app_context():
            db = get_db()
            admin_id = db.execute("SELECT id FROM users WHERE email = 'admin@predprof.local'").fetchone()['id']

        csrf = self._csrf_from_page('/admin/users')
        response = self.client.post(
            f'/admin/users/{admin_id}/block',
            data={'action': 'block', 'csrf_token': csrf},
            follow_redirects=True,
        )
        self.assertIn('Нельзя заблокировать собственный аккаунт', response.get_data(as_text=True))

        with self.app.app_context():
            db = get_db()
            state = db.execute('SELECT is_active FROM users WHERE id = ?', (admin_id,)).fetchone()['is_active']
        self.assertEqual(state, 1)


if __name__ == '__main__':
    unittest.main()
