import os
import tempfile
import unittest

from app import create_app
from app.db import get_db


class Case2AppTest(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(prefix='predprof_case2_test_', suffix='.db')
        os.close(fd)
        self.app = create_app({'TESTING': True, 'DATABASE': self.db_path, 'SECRET_KEY': 'test-key'})
        self.client = self.app.test_client()

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    def login(self, email: str, password: str):
        return self.client.post(
            '/login',
            data={'email': email, 'password': password},
            follow_redirects=True,
        )

    def test_student_can_update_allergies_and_preferences(self):
        response = self.login('student@predprof.local', 'student123')
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
            '/student/profile',
            data={
                'allergies': 'Орехи, лактоза',
                'preferences': 'Без сахара',
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
        import re

        menu_id_match = re.search(r'name="menu_item_id" value="(\d+)"', page)
        self.assertIsNotNone(menu_id_match)
        menu_id = menu_id_match.group(1)

        first = self.client.post('/student/claim', data={'menu_item_id': menu_id}, follow_redirects=True)
        self.assertIn('Получение питания отмечено', first.get_data(as_text=True))

        second = self.client.post('/student/claim', data={'menu_item_id': menu_id}, follow_redirects=True)
        self.assertIn('Повторная отметка питания запрещена', second.get_data(as_text=True))

    def test_cook_can_update_inventory_and_create_purchase_request_with_price(self):
        self.login('cook@predprof.local', 'cook123')

        with self.app.app_context():
            db = get_db()
            inv = db.execute('SELECT id, qty FROM inventory ORDER BY id LIMIT 1').fetchone()
            inv_id = inv['id']
            old_qty = float(inv['qty'])

        response = self.client.post(
            '/cook/inventory/update',
            data={'inventory_id': inv_id, 'operation': 'add', 'delta_qty': '2.5'},
            follow_redirects=True,
        )
        self.assertIn('Остатки обновлены', response.get_data(as_text=True))

        with self.app.app_context():
            db = get_db()
            inv_after = db.execute('SELECT qty FROM inventory WHERE id = ?', (inv_id,)).fetchone()
            self.assertAlmostEqual(float(inv_after['qty']), old_qty + 2.5, places=3)

        response = self.client.post(
            '/cook/purchase-request',
            data={
                'product_name': 'Мука',
                'qty': '10',
                'unit_price': '45.5',
                'reason': 'Для выпечки',
            },
            follow_redirects=True,
        )
        self.assertIn('Заявка на закупку отправлена администратору', response.get_data(as_text=True))

        with self.app.app_context():
            db = get_db()
            req = db.execute('SELECT unit_price FROM purchase_requests ORDER BY id DESC LIMIT 1').fetchone()
        self.assertAlmostEqual(float(req['unit_price']), 45.5, places=3)

    def test_admin_report_has_cost_and_balance(self):
        self.login('cook@predprof.local', 'cook123')
        self.client.post(
            '/cook/purchase-request',
            data={
                'product_name': 'Рис',
                'qty': '5',
                'unit_price': '80',
                'reason': 'Гарнир',
            },
            follow_redirects=True,
        )
        self.client.get('/logout', follow_redirects=True)

        self.login('admin@predprof.local', 'admin123')
        admin_page = self.client.get('/admin/dashboard').get_data(as_text=True)

        import re

        req_id = re.search(r'/admin/purchase-request/(\d+)/status', admin_page)
        self.assertIsNotNone(req_id)

        self.client.post(
            f'/admin/purchase-request/{req_id.group(1)}/status',
            data={'status': 'approved'},
            follow_redirects=True,
        )

        refreshed = self.client.get('/admin/dashboard').get_data(as_text=True)
        self.assertIn('Сумма затрат по одобренным закупкам', refreshed)
        self.assertIn('Баланс (оплаты - затраты)', refreshed)

    def test_exponential_and_too_large_values_are_rejected(self):
        self.login('cook@predprof.local', 'cook123')

        response = self.client.post(
            '/cook/purchase-request',
            data={
                'product_name': 'Тест',
                'qty': '1e9',
                'unit_price': '10',
                'reason': 'Проверка',
            },
            follow_redirects=True,
        )
        self.assertIn('экспоненциальная запись недопустима', response.get_data(as_text=True))

        response = self.client.post(
            '/cook/purchase-request',
            data={
                'product_name': 'Тест',
                'qty': '1',
                'unit_price': '999999',
                'reason': 'Проверка',
            },
            follow_redirects=True,
        )
        self.assertIn('превышен допустимый максимум', response.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
