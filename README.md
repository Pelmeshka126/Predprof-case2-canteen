# Predprof 2025/2026 - Кейс №2 «Управление столовой»

Веб-приложение для практического тура (профиль «Информационные технологии») на стеке `Flask + SQLite + server-rendered templates`.

## Реализовано

- Роли и доступ:
  - публичная регистрация только для `student`;
  - админ-управление пользователями: просмотр, смена роли, блокировка/разблокировка;
  - проверка активности пользователя (`is_active`) при логине и доступе;
  - стабильные ответы `403` для запретных маршрутов.
- Безопасность:
  - единый CSRF token в сессии;
  - валидация CSRF для всех POST-роутов.
- Ученик:
  - профиль питания (аллергии/предпочтения), меню, отметка получения,
  - оплата (демо), отзывы.
- Повар:
  - учет выдачи, контроль остатков, ручная корректировка склада,
  - заявки на закупку с валидируемыми `qty` и `unit_price`.
- Администратор:
  - согласование заявок,
  - сводная статистика,
  - отчет в интерфейсе,
  - экспорт отчета в CSV (`/admin/report.csv`).
- Числа и legacy-данные:
  - `Decimal`-валидация, запрет `e/E`, `NaN`, `inf`, лимиты диапазонов;
  - форматированные отображения без scientific notation;
  - политика legacy: `approved + unit_price=0` => `rejected` с системной пометкой.

## Быстрый запуск (WSL/Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Открыть: `http://127.0.0.1:5000/login`

## Демо-аккаунты

- `admin@predprof.local / admin123`
- `cook@predprof.local / cook123`
- `student@predprof.local / student123`

## Тесты

```bash
python -m unittest discover -s tests -v
```

Текущий набор: 15 интеграционных тестов (`tests/test_case2_app.py`).

## Структура проекта

- `app.py` — точка входа
- `app/__init__.py` — app factory, CSRF, обработчик 403
- `app/auth.py` — регистрация/вход/доступ
- `app/routes.py` — бизнес-логика по ролям, отчеты, CSV
- `app/db.py` — схема, миграционно-безопасная инициализация, legacy-normalization
- `app/templates/` — UI
- `tests/test_case2_app.py` — интеграционные тесты
- `docs/full_case_text.md` — полный текст кейса
- `docs/case2_coverage.md` — покрытие требований
- `docs/demo_script.md` — сценарий видео-демо
- `docs/test_protocol.md` — протокол ручного тестирования
- `docs/official_requirements.md` — оргданные и источники

## Репозиторий

- `https://github.com/Pelmeshka126/Predprof-case2-canteen`
