import os
import re
import tempfile
import unittest
from unittest.mock import patch

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

    def logout(self):
        return self.client.get('/logout', follow_redirects=True)

    def _admin_actions_count(self, action_type: str) -> int:
        with self.app.app_context():
            db = get_db()
            row = db.execute(
                'SELECT COUNT(*) AS c FROM admin_actions WHERE action_type = ?',
                (action_type,),
            ).fetchone()
            return int(row['c'])

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

    def test_registration_page_is_russian_and_has_no_role_selector(self):
        page = self.client.get('/register').get_data(as_text=True)
        self.assertIn('Саморегистрация доступна только для роли', page)
        self.assertNotIn('name="role"', page)

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

    def test_cross_role_route_returns_403_with_russian_message(self):
        self.login('student@predprof.local', 'student123')
        response = self.client.get('/admin/dashboard', follow_redirects=False)
        self.assertEqual(response.status_code, 403)
        page = self.client.get('/admin/dashboard', follow_redirects=True).get_data(as_text=True)
        self.assertIn('Доступ запрещен', page)
        self.assertNotIn('Forbidden', page)

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
        self.assertIn('Пищевые аллергии и предпочтения сохранены', response.get_data(as_text=True))
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
        menu_id = re.search(r'name="menu_item_id" value="(\d+)"', page).group(1)
        csrf = self._extract_csrf(page)

        first = self.client.post('/student/claim', data={'menu_item_id': menu_id, 'csrf_token': csrf}, follow_redirects=True)
        self.assertIn('Получение питания отмечено', first.get_data(as_text=True))

        csrf = self._csrf_from_page('/student/dashboard')
        second = self.client.post('/student/claim', data={'menu_item_id': menu_id, 'csrf_token': csrf}, follow_redirects=True)
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

    def test_numeric_exponential_is_rejected(self):
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

    def test_numeric_too_large_is_rejected(self):
        self.login('cook@predprof.local', 'cook123')
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

    def test_admin_can_change_user_role(self):
        self.register('Role User', 'roleuser@local.test', 'secret123')
        with self.app.app_context():
            db = get_db()
            user_id = db.execute('SELECT id FROM users WHERE email = ?', ('roleuser@local.test',)).fetchone()['id']

        self.login('admin@predprof.local', 'admin123')
        csrf = self._csrf_from_page('/admin/users')
        response = self.client.post(
            f'/admin/users/{user_id}/role',
            data={'role': 'cook', 'csrf_token': csrf},
            follow_redirects=True,
        )
        self.assertIn('Роль пользователя обновлена', response.get_data(as_text=True))

    def test_admin_cannot_demote_himself(self):
        self.login('admin@predprof.local', 'admin123')
        with self.app.app_context():
            db = get_db()
            admin_id = db.execute("SELECT id FROM users WHERE email = 'admin@predprof.local'").fetchone()['id']

        csrf = self._csrf_from_page('/admin/users')
        response = self.client.post(
            f'/admin/users/{admin_id}/role',
            data={'role': 'cook', 'csrf_token': csrf},
            follow_redirects=True,
        )
        self.assertIn('Нельзя понизить собственную роль', response.get_data(as_text=True))

    def test_admin_role_unchanged_message(self):
        self.login('admin@predprof.local', 'admin123')
        with self.app.app_context():
            db = get_db()
            student_id = db.execute("SELECT id FROM users WHERE role = 'student' LIMIT 1").fetchone()['id']

        csrf = self._csrf_from_page('/admin/users')
        response = self.client.post(
            f'/admin/users/{student_id}/role',
            data={'role': 'student', 'csrf_token': csrf},
            follow_redirects=True,
        )
        self.assertIn('Роль пользователя не изменилась', response.get_data(as_text=True))

    def test_admin_can_block_user_and_blocked_user_cannot_login(self):
        self.register('Blocked User', 'blocked@local.test', 'secret123')
        with self.app.app_context():
            db = get_db()
            user_id = db.execute('SELECT id FROM users WHERE email = ?', ('blocked@local.test',)).fetchone()['id']

        self.login('admin@predprof.local', 'admin123')
        csrf = self._csrf_from_page('/admin/users')
        self.client.post(
            f'/admin/users/{user_id}/block',
            data={'action': 'block', 'csrf_token': csrf},
            follow_redirects=True,
        )
        self.logout()

        blocked_login = self.login('blocked@local.test', 'secret123').get_data(as_text=True)
        self.assertIn('Пользователь заблокирован администратором', blocked_login)

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

    def test_admin_block_state_unchanged_message(self):
        self.login('admin@predprof.local', 'admin123')
        with self.app.app_context():
            db = get_db()
            target = db.execute("SELECT id FROM users WHERE role = 'student' LIMIT 1").fetchone()['id']

        csrf = self._csrf_from_page('/admin/users')
        response = self.client.post(
            f'/admin/users/{target}/block',
            data={'action': 'unblock', 'csrf_token': csrf},
            follow_redirects=True,
        )
        self.assertIn('Статус пользователя не изменился', response.get_data(as_text=True))

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
                (cook_id, 'Legacy', 5, 0, 'legacy reason', 'approved', '2026-01-01T00:00:00+03:00'),
            )
            db.execute('PRAGMA ignore_check_constraints = OFF')
            db.commit()
            init_db()
            row = db.execute(
                "SELECT status, reason FROM purchase_requests WHERE product_name = 'Legacy' ORDER BY id DESC LIMIT 1"
            ).fetchone()

        self.assertEqual(row['status'], 'rejected')
        self.assertIn('legacy-запись', row['reason'])

    def test_admin_actions_logged_for_role_change(self):
        before = self._admin_actions_count('user_role_changed')
        self.register('Action Role', 'action-role@local.test', 'secret123')
        with self.app.app_context():
            db = get_db()
            target_id = db.execute('SELECT id FROM users WHERE email = ?', ('action-role@local.test',)).fetchone()['id']

        self.login('admin@predprof.local', 'admin123')
        csrf = self._csrf_from_page('/admin/users')
        self.client.post(
            f'/admin/users/{target_id}/role',
            data={'role': 'cook', 'csrf_token': csrf},
            follow_redirects=True,
        )
        after = self._admin_actions_count('user_role_changed')
        self.assertEqual(after, before + 1)

    def test_admin_actions_logged_for_block_unblock(self):
        before = self._admin_actions_count('user_block_state_changed')
        self.register('Action Block', 'action-block@local.test', 'secret123')
        with self.app.app_context():
            db = get_db()
            target_id = db.execute('SELECT id FROM users WHERE email = ?', ('action-block@local.test',)).fetchone()['id']

        self.login('admin@predprof.local', 'admin123')
        csrf = self._csrf_from_page('/admin/users')
        self.client.post(
            f'/admin/users/{target_id}/block',
            data={'action': 'block', 'csrf_token': csrf},
            follow_redirects=True,
        )
        csrf = self._csrf_from_page('/admin/users')
        self.client.post(
            f'/admin/users/{target_id}/block',
            data={'action': 'unblock', 'csrf_token': csrf},
            follow_redirects=True,
        )
        after = self._admin_actions_count('user_block_state_changed')
        self.assertEqual(after, before + 2)

    def test_admin_actions_logged_for_purchase_status_change(self):
        before = self._admin_actions_count('purchase_request_status_changed')

        self.login('cook@predprof.local', 'cook123')
        csrf = self._csrf_from_page('/cook/dashboard')
        self.client.post(
            '/cook/purchase-request',
            data={
                'product_name': 'Тест логов',
                'qty': '2',
                'unit_price': '10',
                'reason': 'Проверка логов',
                'csrf_token': csrf,
            },
            follow_redirects=True,
        )
        self.logout()

        self.login('admin@predprof.local', 'admin123')
        page = self.client.get('/admin/dashboard').get_data(as_text=True)
        req_id = re.search(r'/admin/purchase-request/(\d+)/status', page).group(1)
        csrf = self._csrf_from_page('/admin/dashboard')
        self.client.post(
            f'/admin/purchase-request/{req_id}/status',
            data={'status': 'approved', 'csrf_token': csrf},
            follow_redirects=True,
        )
        after = self._admin_actions_count('purchase_request_status_changed')
        self.assertEqual(after, before + 1)

    def test_admin_report_csv_has_bom_semicolon_and_period_fields(self):
        self.login('admin@predprof.local', 'admin123')
        response = self.client.get('/admin/report.csv?date_from=2026-02-01&date_to=2026-02-28', follow_redirects=True)
        text = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(text.startswith('\ufeff'))
        self.assertIn('Раздел;Метрика;Значение', text)
        self.assertIn('Параметры;Период с;01.02.2026', text)
        self.assertIn('Параметры;Период по;28.02.2026', text)

    def test_admin_report_period_filter_changes_totals(self):
        with self.app.app_context():
            db = get_db()
            student_id = db.execute("SELECT id FROM users WHERE role = 'student' LIMIT 1").fetchone()['id']
            db.execute(
                'INSERT INTO payments(user_id, payment_type, amount, status, created_at) VALUES (?, ?, ?, ?, ?)',
                (student_id, 'one_time', 111.0, 'paid', '2026-02-05T10:00:00+03:00'),
            )
            db.execute(
                'INSERT INTO payments(user_id, payment_type, amount, status, created_at) VALUES (?, ?, ?, ?, ?)',
                (student_id, 'one_time', 222.0, 'paid', '2026-01-10T10:00:00+03:00'),
            )
            db.commit()

        self.login('admin@predprof.local', 'admin123')
        feb_csv = self.client.get('/admin/report.csv?date_from=2026-02-01&date_to=2026-02-28').get_data(as_text=True)
        jan_csv = self.client.get('/admin/report.csv?date_from=2026-01-01&date_to=2026-01-31').get_data(as_text=True)

        self.assertIn('Оплаты;Сумма оплат;111.00', feb_csv)
        self.assertIn('Оплаты;Сумма оплат;222.00', jan_csv)

    def test_admin_dashboard_and_csv_have_consistent_procurement_cost(self):
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
        self.logout()

        self.login('admin@predprof.local', 'admin123')
        admin_page = self.client.get('/admin/dashboard').get_data(as_text=True)
        req_id = re.search(r'/admin/purchase-request/(\d+)/status', admin_page).group(1)
        csrf = self._csrf_from_page('/admin/dashboard')
        self.client.post(
            f'/admin/purchase-request/{req_id}/status',
            data={'status': 'approved', 'csrf_token': csrf},
            follow_redirects=True,
        )

        dashboard = self.client.get('/admin/dashboard').get_data(as_text=True)
        html_match = re.search(r'Сумма затрат по одобренным закупкам:</strong>\s*([0-9.\-]+)', dashboard)
        self.assertIsNotNone(html_match)
        html_cost = html_match.group(1)

        csv_text = self.client.get('/admin/report.csv').get_data(as_text=True)
        csv_match = re.search(r'Закупки;Сумма затрат;([0-9.\-]+)', csv_text)
        self.assertIsNotNone(csv_match)
        self.assertEqual(html_cost, csv_match.group(1))

    def test_invalid_period_falls_back_to_default_and_shows_message(self):
        self.login('admin@predprof.local', 'admin123')
        page = self.client.get('/admin/dashboard?date_from=bad&date_to=2026-01-01', follow_redirects=True).get_data(as_text=True)
        self.assertIn('Некорректный формат периода', page)
        self.assertIn('Период:', page)

    def test_user_list_displays_russian_role_and_status_labels(self):
        self.login('admin@predprof.local', 'admin123')
        page = self.client.get('/admin/users').get_data(as_text=True)
        self.assertIn('Администратор', page)
        self.assertIn('Активен', page)

    def test_dashboard_displays_russian_datetime_format(self):
        self.login('admin@predprof.local', 'admin123')
        page = self.client.get('/admin/dashboard').get_data(as_text=True)
        self.assertRegex(page, r'\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2}')

    def test_schema_migrations_versions_exist(self):
        with self.app.app_context():
            db = get_db()
            rows = db.execute('SELECT version FROM schema_migrations ORDER BY version ASC').fetchall()
            versions = [int(r['version']) for r in rows]
        self.assertEqual(versions, [1, 2, 3])

    def test_env_config_can_enable_debug_and_set_database(self):
        fd, env_db_path = tempfile.mkstemp(prefix='predprof_env_test_', suffix='.db')
        os.close(fd)
        try:
            with patch.dict(
                os.environ,
                {'FLASK_DEBUG': '1', 'SECRET_KEY': 'env-secret', 'DATABASE': env_db_path},
                clear=False,
            ):
                app = create_app({'TESTING': True})
                self.assertTrue(app.config['DEBUG'])
                self.assertEqual(app.config['SECRET_KEY'], 'env-secret')
                self.assertEqual(app.config['DATABASE'], env_db_path)
        finally:
            if os.path.exists(env_db_path):
                os.remove(env_db_path)

    def test_default_debug_is_disabled_when_env_not_set(self):
        fd, env_db_path = tempfile.mkstemp(prefix='predprof_env_test_', suffix='.db')
        os.close(fd)
        try:
            env = dict(os.environ)
            env.pop('FLASK_DEBUG', None)
            env['DATABASE'] = env_db_path
            with patch.dict(os.environ, env, clear=True):
                app = create_app({'TESTING': True, 'SECRET_KEY': 'x'})
                self.assertFalse(bool(app.config.get('DEBUG')))
        finally:
            if os.path.exists(env_db_path):
                os.remove(env_db_path)

    def test_local_run_script_exists_and_is_executable(self):
        self.assertTrue(os.path.exists('scripts/run_local.sh'))
        self.assertTrue(os.access('scripts/run_local.sh', os.X_OK))


if __name__ == '__main__':
    unittest.main()
