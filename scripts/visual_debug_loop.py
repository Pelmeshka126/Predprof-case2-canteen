#!/usr/bin/env python3
from pathlib import Path
import os

from playwright.sync_api import sync_playwright

BASE_URL = os.getenv('BASE_URL', 'http://127.0.0.1:5000')
OUT_DIR = Path(os.getenv('OUT_DIR', 'output/playwright_v3'))
OUT_DIR.mkdir(parents=True, exist_ok=True)

VIEWPORTS = [
    (360, 760),
    (390, 844),
    (768, 1024),
    (1024, 1366),
    (1280, 800),
]


def login(page, email: str, password: str) -> None:
    page.goto(f'{BASE_URL}/login', wait_until='networkidle')
    page.fill('input[name="email"]', email)
    page.fill('input[name="password"]', password)
    page.click('form button[type="submit"]')
    page.wait_for_load_state('networkidle')


def capture_flow(page, width: int, height: int) -> None:
    suffix = f'{width}x{height}'

    login(page, 'student@predprof.local', 'student123')
    page.screenshot(path=str(OUT_DIR / f'student_dashboard_{suffix}.png'), full_page=True)
    page.goto(f'{BASE_URL}/logout', wait_until='networkidle')

    login(page, 'cook@predprof.local', 'cook123')
    page.screenshot(path=str(OUT_DIR / f'cook_dashboard_{suffix}.png'), full_page=True)
    page.goto(f'{BASE_URL}/logout', wait_until='networkidle')

    login(page, 'admin@predprof.local', 'admin123')
    page.screenshot(path=str(OUT_DIR / f'admin_dashboard_{suffix}.png'), full_page=True)
    page.goto(f'{BASE_URL}/admin/users', wait_until='networkidle')
    page.screenshot(path=str(OUT_DIR / f'admin_users_{suffix}.png'), full_page=True)


if __name__ == '__main__':
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for width, height in VIEWPORTS:
            page = browser.new_page(viewport={'width': width, 'height': height})
            capture_flow(page, width, height)
            page.close()
        browser.close()

    print(f'Скриншоты сохранены: {OUT_DIR}')
