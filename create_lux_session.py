#!/usr/bin/env python3
"""
Run ONCE to create lux_session.json (saved Google OAuth session for Lux portal).
After running, the install automation reuses this session without re-authenticating.

Usage: python create_lux_session.py
"""
import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv()

GMAIL_ADDRESS = os.environ['GMAIL_ADDRESS']
LUX_GOOGLE_PASSWORD = os.environ['LUX_GOOGLE_PASSWORD']
SESSION_FILE = Path(__file__).parent / 'lux_session.json'
LUX_URL = 'https://app.luxfinancial.io/installer/?partnerId=49937750-8974-491e-9d25-b1b2fe86f715&page=1'

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(viewport={'width': 1600, 'height': 900})
        page = await context.new_page()

        print(f'Navigating to Lux portal...')
        await page.goto(LUX_URL, wait_until='domcontentloaded', timeout=60000)
        await page.wait_for_timeout(3000)

        # Find and click Google sign-in button
        google_btn = page.locator('button:has-text("Google"), a:has-text("Google"), button:has-text("Sign in with Google")').first
        if await google_btn.is_visible():
            print('Clicking Google sign-in button...')
            await google_btn.click()
            await page.wait_for_timeout(2000)
        else:
            print('No Google button found — checking if already logged in or different login flow')

        # Handle Google OAuth popup or redirect
        # Try filling email if on Google login page
        email_input = page.locator('input[type="email"]').first
        if await email_input.is_visible(timeout=5000):
            print('Filling Google email...')
            await email_input.fill(GMAIL_ADDRESS)
            await page.locator('#identifierNext, button:has-text("Next")').first.click()
            await page.wait_for_timeout(2000)
            await page.locator('input[type="password"]').first.fill(LUX_GOOGLE_PASSWORD)
            await page.locator('#passwordNext, button:has-text("Next")').first.click()
            await page.wait_for_load_state('domcontentloaded')
            await page.wait_for_timeout(3000)

        # Wait for user to complete any MFA or CAPTCHA manually (up to 3 min)
        print('Waiting for login to complete (complete any MFA manually if needed)...')
        for i in range(90):
            await asyncio.sleep(2)
            current_url = page.url
            if 'luxfinancial.io' in current_url and 'accounts.google.com' not in current_url:
                print(f'Login confirmed! URL: {current_url}')
                break
            if i % 15 == 0:
                print(f'Waiting... ({i*2}s elapsed)')
        else:
            print('WARNING: Login may not have completed — saving session anyway')

        # Save session state
        await context.storage_state(path=str(SESSION_FILE))
        print(f'Session saved to {SESSION_FILE}')
        await browser.close()

asyncio.run(main())
