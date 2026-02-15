# Матрица покрытия требований кейса №2

| Требование | Где реализовано | Как проверяется |
|---|---|---|
| Регистрация/авторизация | `app/auth.py` (`/register`, `/login`, `/logout`) | `test_register_always_creates_student_even_with_role_spoof`, `test_public_registration_cannot_create_cook_or_admin` |
| Саморегистрация только ученика | `app/auth.py`, `app/templates/auth/register.html` | `test_registration_page_is_russian_and_has_no_role_selector` |
| Ролевой доступ (RBAC) | `app/auth.py` (`role_required`) | `test_cross_role_route_returns_403_with_russian_message` |
| CSRF на всех POST | `app/__init__.py` + все POST-формы в шаблонах | `test_csrf_missing_token_rejected`, `test_csrf_invalid_token_rejected_for_authorized_post` |
| Блокировка пользователя | `users.is_active`, `app/auth.py` | `test_admin_can_block_user_and_blocked_user_cannot_login` |
| Админ-управление ролями | `app/routes.py` (`/admin/users/<id>/role`) | `test_admin_can_change_user_role`, `test_admin_cannot_demote_himself`, `test_admin_role_unchanged_message` |
| Админ-блокировка/разблокировка | `app/routes.py` (`/admin/users/<id>/block`) | `test_admin_cannot_block_himself`, `test_admin_block_state_unchanged_message` |
| Профиль питания ученика | `app/routes.py` (`/student/profile`) | `test_student_can_update_allergies_and_preferences` |
| Отметка получения и запрет дубля | `app/routes.py` (`/student/claim`) | `test_duplicate_claim_is_blocked` |
| Исключение «нехватка продукта» | `app/routes.py` (`/cook/issue`) | `test_shortage_of_inventory_is_blocked` |
| Заявка на закупку | `app/routes.py` (`/cook/purchase-request`) | `test_valid_fractions_are_saved_and_rendered_without_scientific_notation` |
| Границы numeric-полей | `app/routes.py` (`_parse_positive_decimal`) | `test_numeric_exponential_is_rejected`, `test_numeric_too_large_is_rejected` |
| Legacy policy (`unit_price=0`) | `app/db.py` (`_normalize_runtime_data`) | `test_legacy_policy_rejects_approved_rows_with_zero_price` |
| Отчетность в UI | `app/routes.py` (`/admin/dashboard`, `_collect_admin_metrics`) | `test_admin_dashboard_and_csv_have_consistent_procurement_cost`, `test_admin_report_period_filter_changes_totals` |
| CSV-экспорт (BOM, `;`, период) | `app/routes.py` (`/admin/report.csv`) | `test_admin_report_csv_has_bom_semicolon_and_period_fields` |
| Фильтр периода | `app/routes.py` (`_resolve_period`) | `test_invalid_period_falls_back_to_default_and_shows_message` |
| Аудит админ-действий | `admin_actions`, `_log_admin_action` | `test_admin_actions_logged_for_role_change`, `test_admin_actions_logged_for_block_unblock`, `test_admin_actions_logged_for_purchase_status_change` |
| Версионные миграции | `schema_migrations`, `MIGRATIONS` в `app/db.py` | `test_schema_migrations_versions_exist` |
| Русификация UI и корректный формат даты/времени | `app/templates/*`, `app/utils.py` | `test_user_list_displays_russian_role_and_status_labels`, `test_dashboard_displays_russian_datetime_format` |
| Runtime-конфиг через env | `app/__init__.py`, `app.py` | `test_env_config_can_enable_debug_and_set_database`, `test_default_debug_is_disabled_when_env_not_set` |
| Локальный запуск скриптом | `scripts/run_local.sh` | `test_local_run_script_exists_and_is_executable` |
