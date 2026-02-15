# Case 2 Coverage Matrix

| Требование кейса | Где реализовано | Как тестируется |
|---|---|---|
| Регистрация и авторизация | `app/auth.py` (`/register`, `/login`, `/logout`) | `test_register_always_creates_student_even_with_role_spoof`, `test_public_registration_cannot_create_cook_or_admin` |
| Саморегистрация только `student` | `app/auth.py` + `app/templates/auth/register.html` | `test_register_always_creates_student_even_with_role_spoof` |
| Разграничение ролей (RBAC) | `app/auth.py` (`role_required`) | `test_cross_role_route_returns_403` |
| Админ-управление пользователями | `app/routes.py` (`/admin/users`, `/admin/users/<id>/role`, `/admin/users/<id>/block`), `app/templates/admin/users.html` | `test_admin_can_change_user_role`, `test_admin_can_block_user_and_blocked_user_cannot_login`, `test_admin_cannot_block_himself` |
| Блокировка пользователя | `app/db.py` (`users.is_active`), `app/auth.py` (check при логине/сессии) | `test_admin_can_block_user_and_blocked_user_cannot_login` |
| CSRF-защита POST | `app/__init__.py` (`before_request`, token in session), все POST-формы в `app/templates/*` | `test_csrf_missing_token_rejected`, `test_csrf_invalid_token_rejected_for_authorized_post` |
| Ученик: профиль питания | `app/routes.py` (`/student/profile`), `app/templates/student/dashboard.html` | `test_student_can_update_allergies_and_preferences` |
| Ученик: отметка получения и запрет дубля | `app/routes.py` (`/student/claim`) | `test_duplicate_claim_is_blocked` |
| Повар: выдача и исключение «нехватка продукта» | `app/routes.py` (`/cook/issue`) | `test_shortage_of_inventory_is_blocked` |
| Повар: заявка на закупку | `app/routes.py` (`/cook/purchase-request`) | `test_valid_fractions_are_saved_and_rendered_without_scientific_notation` |
| Корректная обработка дробей и формат вывода | `app/routes.py` (`_parse_positive_decimal`, `_fmt_qty`, `_fmt_money`) | `test_valid_fractions_are_saved_and_rendered_without_scientific_notation` |
| Отсечение scientific notation / NaN / inf / лимитов | `app/routes.py` (`_parse_positive_decimal`) | `test_numeric_bounds_and_exponential_are_rejected` |
| Legacy policy для `unit_price=0` | `app/db.py` (`init_db` normalization policy) | `test_legacy_policy_rejects_approved_rows_with_zero_price` |
| Админ-отчет: оплаты/получения/выдача/затраты/баланс | `app/routes.py` (`/admin/dashboard`, `_collect_admin_metrics`), `app/templates/admin/dashboard.html` | `test_admin_report_has_cost_balance_and_csv_export` |
| Экспорт отчета в CSV + timestamp | `app/routes.py` (`/admin/report.csv`) | `test_admin_report_has_cost_balance_and_csv_export` |
| Устойчивость таблиц и мобильный скролл | `app/templates/base.html` (`.table-wrap`, `.table-wrap--wide`), шаблоны dashboard | Ручной сценарий в `docs/test_protocol.md` |
