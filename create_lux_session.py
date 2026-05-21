#!/usr/bin/env python3
"""
Run ONCE to create lux_session.json (saved Google OAuth + 2FA session for Lux portal).
Uses a persistent Chrome profile so Google only asks for iPhone approval once.
After running, the install automation reuses this session without re-authenticating.

Usage: python3 create_lux_session.py
"""
import asyncio
import os
from pathlib import Path
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv()

GMAIL_ADDRESS = os.environ['GMAIL_ADDRESS']
GMAIL_APP_PASSWORD = os.environ['GMAIL_APP_PASSWORD']
LUX_GOOGLE_PASSWORD = os.environ['LUX_GOOGLE_PASSWORD']
SESSION_FILE = Path(__file__).parent / 'lux_session.json'
BROWSER_PROFILE_DIR = Path(__file__).parent / 'lux_browser_profile'
LUX_URL = 'https://app.luxfinancial.io/installer/?partnerId=49937750-8974-491e-9d25-b1b2fe86f715&page=1'


def fetch_lux_2fa_code(max_wait: int = 90) -> str:
    """Poll Gmail for the most recent unread Lux 2FA code. Waits up to max_wait seconds."""
    import email as emaillib
    import imaplib
    import time
    deadline = time.time() + max_wait
    print('Waiting for Lux 2FA code in Gmail...')
    while time.time() < deadline:
        try:
            mail = imaplib.IMAP4_SSL('imap.gmail.com')
            mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
            mail.select('inbox')
            _, data = mail.search(None, 'FROM "no-reply@luxfinancial.io" SUBJECT "2FA Code" UNSEEN')
            ids = data[0].split()
            if ids:
                _, raw = mail.fetch(ids[-1], '(RFC822)')
                msg = emaillib.message_from_bytes(raw[0][1])
                body = ''
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == 'text/plain':
                            body = part.get_payload(decode=True).decode('utf-8', errors='ignore')
                            break
                else:
                    body = msg.get_payload(decode=True).decode('utf-8', errors='ignore')
                mail.store(ids[-1], '+FLAGS', '\\Seen')
                mail.logout()
                for line in body.splitlines():
                    line = line.strip()
                    if line.isdigit() and len(line) == 6:
                        print(f'Got 2FA code: {line}')
                        return line
                    if 'code is:' in line.lower():
                        parts = line.split()
                        code = parts[-1].strip()
                        if code.isdigit() and len(code) == 6:
                            print(f'Got 2FA code: {code}')
                            return code
                mail.logout()
            else:
                mail.logout()
        except Exception as e:
            print(f'Gmail check error: {e}')
        time.sleep(5)
    return ''


async def main():
    BROWSER_PROFILE_DIR.mkdir(exist_ok=True)
    async with async_playwright() as p:
        # Persistent context: Chrome remembers Google login after first iPhone approval
        context = await p.chromium.launch_persistent_context(
            str(BROWSER_PROFILE_DIR),
            headless=False,
            viewport={'width': 1600, 'height': 900},
            args=['--no-sandbox'],
        )
        page = await context.new_page()
        await page.bring_to_front()

        print('Navigating to Lux portal...')
        await page.goto(LUX_URL, wait_until='domcontentloaded', timeout=60000)
        await page.wait_for_timeout(3000)

        # If already logged in, skip auth entirely
        if page.url.startswith('https://app.luxfinancial.io'):
            print(f'Already logged in! URL: {page.url}')
            await context.storage_state(path=str(SESSION_FILE))
            print(f'Session saved to {SESSION_FILE}')
            await context.close()
            return

        # Click "Sign in with Google"
        google_btn = page.locator('button:has-text("Google"), a:has-text("Google"), button:has-text("Sign in with Google")').first
        if await google_btn.is_visible(timeout=5000):
            print('Clicking Google sign-in button...')
            await google_btn.click()
            await page.wait_for_timeout(2000)

        # Fill Google credentials (only shown if not already signed in to Google)
        email_input = page.locator('input[type="email"]').first
        if await email_input.is_visible(timeout=5000):
            print('Filling Google credentials...')
            await email_input.fill(GMAIL_ADDRESS)
            await page.locator('#identifierNext, button:has-text("Next")').first.click()
            await page.wait_for_timeout(2000)
            await page.locator('input[type="password"]').first.fill(LUX_GOOGLE_PASSWORD)
            await page.locator('#passwordNext, button:has-text("Next")').first.click()
            await page.wait_for_load_state('domcontentloaded')
            await page.wait_for_timeout(3000)
            print('Google credentials submitted — approve iPhone push if prompted')

        # Wait for either Lux 2FA challenge or the app portal (Google 2FA handled manually)
        print('Waiting for post-Google-auth redirect...')
        for i in range(90):
            await asyncio.sleep(2)
            current_url = page.url
            if current_url.startswith('https://app.luxfinancial.io'):
                print(f'On portal! URL: {current_url}')
                break
            if 'auth/challenge' in current_url or 'challenge' in current_url:
                print(f'Lux 2FA challenge at: {current_url}')
                code_input = page.locator(
                    'input[type="text"][maxlength="6"], input[name*="code" i], '
                    'input[placeholder*="code" i], input[type="number"]'
                ).first
                try:
                    await code_input.wait_for(state='visible', timeout=8000)
                    code = await asyncio.get_event_loop().run_in_executor(None, fetch_lux_2fa_code, 90)
                    if code:
                        await code_input.fill(code)
                        submit = page.locator(
                            'button[type="submit"], button:has-text("Verify"), '
                            'button:has-text("Submit"), button:has-text("Continue")'
                        ).first
                        try:
                            if await submit.is_visible(timeout=3000):
                                await submit.click()
                            else:
                                await code_input.press('Enter')
                        except Exception:
                            await code_input.press('Enter')
                        await page.wait_for_load_state('domcontentloaded')
                        await page.wait_for_timeout(3000)
                        print(f'Lux 2FA submitted: {code}')
                    else:
                        print('ERROR: Could not get Lux 2FA code from Gmail')
                except Exception as e:
                    print(f'Could not fill 2FA input: {e}')
                continue
            if i % 5 == 0:
                print(f'  ({i*2}s) URL: {current_url}')
        else:
            print(f'WARNING: Never reached app.luxfinancial.io — URL: {page.url}')

        await context.storage_state(path=str(SESSION_FILE))
        print(f'Session saved to {SESSION_FILE}')
        await context.close()


asyncio.run(main())
