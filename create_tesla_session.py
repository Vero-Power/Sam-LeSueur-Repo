#!/usr/bin/env python3
"""
Run ONCE to set up the Tesla PowerHub persistent browser session.
Opens a visible browser — complete the MFA/CAPTCHA yourself.
After completing login, press ENTER in terminal to save the session.

Usage: python3 create_tesla_session.py
"""
import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv()

TESLA_EMAIL = os.environ.get('TESLA_EMAIL', 'sam@veropwr.com')
TESLA_PASSWORD = os.environ.get('TESLA_PASSWORD', '')
PROFILE_DIR = Path(__file__).parent / '.tesla_browser_profile'
TESLA_LOGIN_URL = 'https://powerhub.energy.tesla.com/login?redirect_to=%2F'
TESLA_BASE_URL = 'https://powerhub.energy.tesla.com'


async def main():
    PROFILE_DIR.mkdir(exist_ok=True)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=False,
            viewport={'width': 1600, 'height': 900},
            args=['--no-sandbox'],
        )
        page = await context.new_page()
        await page.bring_to_front()

        print(f'Navigating to Tesla PowerHub...')
        await page.goto(TESLA_BASE_URL, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(2)

        url = page.url
        if TESLA_BASE_URL in url and 'login' not in url and 'auth.tesla.com' not in url:
            print(f'Already logged in! URL: {url}')
            await context.close()
            return

        print('Starting login...')
        await page.goto(TESLA_LOGIN_URL, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(2)

        # Fill email
        identity = page.locator('input[name="identity"]')
        if await identity.is_visible(timeout=5000):
            print('Filling email...')
            await identity.fill(TESLA_EMAIL)
            await page.locator('button[type="submit"]').first.click()
            await page.wait_for_load_state('domcontentloaded')
            await asyncio.sleep(2)

        # Fill password
        pwd = page.locator('input[type="password"], input[name="credential"]').first
        if await pwd.is_visible(timeout=5000):
            print('Filling password...')
            await pwd.fill(TESLA_PASSWORD)
            await page.locator('button[type="submit"]').first.click()
            await page.wait_for_load_state('domcontentloaded')
            await asyncio.sleep(2)

        # Wait for MFA / CAPTCHA completion — poll until on dashboard (up to 5 min)
        print('\nComplete MFA/CAPTCHA in the browser window...')
        print('Waiting up to 5 minutes for you to finish...')
        logged_in = False
        for i in range(150):  # 150 × 2s = 5 min
            await asyncio.sleep(2)
            url = page.url
            if TESLA_BASE_URL in url and 'login' not in url and 'auth.tesla.com' not in url:
                logged_in = True
                print(f'Logged in! URL: {url}')
                break
            if i % 15 == 0:
                print(f'  ({i*2}s) still waiting... URL: {url}')

        if logged_in:
            await page.screenshot(path='/tmp/tesla_logged_in.png')
            print('Screenshot saved to /tmp/tesla_logged_in.png')
        else:
            print(f'WARNING: Timed out — URL: {page.url}')

        await context.close()
        print('Tesla browser session saved to .tesla_browser_profile/')
        print('The screenshot function will now work without re-login.')


asyncio.run(main())
