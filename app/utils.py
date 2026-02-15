from datetime import datetime
from zoneinfo import ZoneInfo


MSK_TZ = ZoneInfo('Europe/Moscow')

ROLE_LABELS = {
    'student': 'Ученик',
    'cook': 'Повар',
    'admin': 'Администратор',
}

USER_STATUS_LABELS = {
    1: 'Активен',
    0: 'Заблокирован',
}

REQUEST_STATUS_LABELS = {
    'pending': 'На рассмотрении',
    'approved': 'Одобрено',
    'rejected': 'Отклонено',
}

PAYMENT_TYPE_LABELS = {
    'one_time': 'Разовый платеж',
    'subscription': 'Абонемент',
}

PAYMENT_STATUS_LABELS = {
    'paid': 'Оплачено',
}

MEAL_TYPE_LABELS = {
    'breakfast': 'Завтрак',
    'lunch': 'Обед',
}

ADMIN_ACTION_LABELS = {
    'user_role_changed': 'Смена роли пользователя',
    'user_block_state_changed': 'Изменение статуса пользователя',
    'purchase_request_status_changed': 'Изменение статуса заявки',
}

ADMIN_TARGET_LABELS = {
    'user': 'Пользователь',
    'purchase_request': 'Заявка на закупку',
}


def now_moscow() -> datetime:
    return datetime.now(MSK_TZ)


def now_iso() -> str:
    return now_moscow().isoformat(timespec='seconds')


def _parse_iso_datetime(raw_value: str | None) -> datetime | None:
    if not raw_value:
        return None
    value = str(raw_value).strip()
    if not value:
        return None
    if value.endswith('Z'):
        value = value[:-1] + '+00:00'

    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        try:
            dt = datetime.fromisoformat(f'{value}T00:00:00')
        except ValueError:
            return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=MSK_TZ)
    return dt.astimezone(MSK_TZ)


def format_datetime_ru(raw_value: str | None) -> str:
    dt = _parse_iso_datetime(raw_value)
    if dt is None:
        return str(raw_value or '')
    return dt.strftime('%d.%m.%Y %H:%M:%S')


def format_date_ru(raw_value: str | None) -> str:
    dt = _parse_iso_datetime(raw_value)
    if dt is None:
        return str(raw_value or '')
    return dt.strftime('%d.%m.%Y')
