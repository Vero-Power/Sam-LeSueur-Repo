#!/usr/bin/env python3
"""
Browser automation for install automation.
Handles: Company Cam PDF export, Tesla PowerHub screenshot, Lux portal upload.
"""

import asyncio
import email as emaillib
import imaplib
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional, List
from playwright.async_api import async_playwright
from dotenv import load_dotenv

load_dotenv()

log = logging.getLogger(__name__)

GMAIL_ADDRESS = os.environ.get('GMAIL_ADDRESS', '')
LUX_GOOGLE_PASSWORD = os.environ.get('LUX_GOOGLE_PASSWORD', '')
TESLA_PASSWORD = os.environ.get('TESLA_PASSWORD', '')
CC_PASSWORD = os.environ.get('CC_PASSWORD', 'Firstblood84')

DIR = Path(__file__).parent
SESSION_FILE = DIR / 'lux_session.json'
LUX_URL = 'https://app.luxfinancial.io/installer/?partnerId=49937750-8974-491e-9d25-b1b2fe86f715&page=1'
GMAIL_APP_PASSWORD = os.environ.get('GMAIL_APP_PASSWORD', '')


def _fetch_lux_2fa_code(max_wait: int = 60) -> Optional[str]:
    """Poll Gmail via IMAP for the Lux Financial 2FA code. Waits up to max_wait seconds."""
    deadline = time.time() + max_wait
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
                        log.info(f'Lux 2FA code found: {line}')
                        return line
                    if 'code is:' in line.lower():
                        parts = line.split()
                        code = parts[-1].strip()
                        if code.isdigit() and len(code) == 6:
                            log.info(f'Lux 2FA code found: {code}')
                            return code
            else:
                mail.logout()
        except Exception as e:
            log.warning(f'Gmail 2FA fetch error: {e}')
        time.sleep(5)
    log.error('Timed out waiting for Lux 2FA code in Gmail')
    return None


async def export_cc_checklist_pdf(cc_project_id: str) -> Optional[bytes]:
    """Log into Company Cam, export VERO SOLAR INSTALLER CHECKLIST as PDF.

    Flow:
      1. Login via direct email/password (Company Cam supports this natively).
      2. Navigate to /projects/{id}/todos and find the VERO SOLAR INSTALLER CHECKLIST link.
      3. Navigate to the checklist detail page.
      4. Click '...' (data-testid='project__item__more-menu-trigger') → 'Export to PDF'.
      5. In the Export modal, click 'Export'. This triggers an async server-side PDF generation
         that saves the result to the project's Files section.
      6. Poll GET /v1/locations/{project_id}/documents until the new PDF appears.
      7. Download the PDF bytes from its S3 URL.
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(accept_downloads=True, viewport={'width': 1600, 'height': 900})
        page = await context.new_page()

        try:
            # --- Step 1: Login ---
            log.info("Navigating to Company Cam sign-in page")
            await page.goto('https://app.companycam.com/users/sign_in', wait_until='domcontentloaded', timeout=60000)

            email_input = page.locator('input[type="email"], input[name="email"], input[placeholder*="email" i]').first
            if await email_input.is_visible():
                log.info("Direct login form — filling credentials")
                await email_input.fill(GMAIL_ADDRESS)
                password_input = page.locator('input[type="password"]').first
                if await password_input.is_visible():
                    await password_input.fill(CC_PASSWORD)
                    submit_btn = page.locator('button[type="submit"], input[type="submit"]').first
                    if await submit_btn.is_visible():
                        await submit_btn.click()
                    else:
                        await password_input.press('Enter')
                    await page.wait_for_load_state('domcontentloaded')
                else:
                    next_btn = page.locator('button[type="submit"], button:has-text("Continue"), button:has-text("Next")').first
                    await next_btn.click()
                    await page.wait_for_load_state('domcontentloaded')
                    password_input = page.locator('input[type="password"]').first
                    await password_input.fill(CC_PASSWORD)
                    await password_input.press('Enter')
                    await page.wait_for_load_state('domcontentloaded')
            else:
                log.info("No direct email field — trying Google OAuth")
                google_btn = page.locator('a:has-text("Google"), button:has-text("Google"), a:has-text("Sign in with Google")').first
                if await google_btn.is_visible():
                    async with context.expect_page() as popup_info:
                        await google_btn.click()
                    popup = await popup_info.value
                    await popup.wait_for_load_state('domcontentloaded')
                    await popup.locator('input[type="email"]').fill(GMAIL_ADDRESS)
                    await popup.locator('#identifierNext, button:has-text("Next")').first.click()
                    await popup.wait_for_load_state('domcontentloaded')
                    await popup.locator('input[type="password"]').fill(LUX_GOOGLE_PASSWORD or CC_PASSWORD)
                    await popup.locator('#passwordNext, button:has-text("Next")').first.click()
                    await popup.wait_for_load_state('domcontentloaded')
                    await page.wait_for_load_state('domcontentloaded')

            current_url = page.url
            log.info(f"After login, URL: {current_url}")
            if 'sign_in' in current_url or 'login' in current_url:
                log.error("Login failed — still on sign-in page")
                return None

            # --- Step 2: Find the VERO SOLAR INSTALLER CHECKLIST link ---
            todos_url = f'https://app.companycam.com/projects/{cc_project_id}/todos'
            log.info(f"Navigating to {todos_url}")
            await page.goto(todos_url, wait_until='domcontentloaded', timeout=60000)
            await page.wait_for_timeout(3000)

            log.info("Looking for VERO SOLAR INSTALLER CHECKLIST link")
            checklist_link = None
            all_links = await page.locator('a[data-testid="taskListTable__taskListTitleLink"]').all()
            log.info(f"Found {len(all_links)} checklist links")

            for link in all_links:
                text = (await link.inner_text()).strip()
                log.info(f"  Checklist: {text}")
                if 'VERO SOLAR INSTALLER CHECKLIST' in text.upper():
                    href = await link.get_attribute('href')
                    row = link.locator('xpath=ancestor::tr[1]')
                    row_text = await row.inner_text()
                    if '/11 completed' in row_text:
                        checklist_link = href
                        log.info(f"Selected completed checklist: {text} -> {href}")
                        break
                    elif checklist_link is None:
                        checklist_link = href

            if not checklist_link:
                log.error("Could not find VERO SOLAR INSTALLER CHECKLIST link")
                return None

            # --- Step 3: Navigate to checklist detail page ---
            checklist_url_full = f'https://app.companycam.com{checklist_link}'
            log.info(f"Navigating to checklist detail: {checklist_url_full}")
            await page.goto(checklist_url_full, wait_until='domcontentloaded', timeout=60000)
            await page.wait_for_timeout(3000)

            # --- Step 4: Note the current latest document ID before export ---
            # This lets us identify the newly created document after export
            docs_before = await page.evaluate(f'''async () => {{
                const resp = await fetch('/v1/locations/{cc_project_id}/documents?order=DESC', {{
                    headers: {{ 'Accept': 'application/json' }}
                }});
                if (resp.ok) return await resp.json();
                return [];
            }}''')
            latest_id_before = docs_before[0]['id'] if docs_before else 0
            log.info(f"Latest document ID before export: {latest_id_before}")

            # --- Step 5: Click '...' → 'Export to PDF' → 'Export' in modal ---
            log.info("Clicking '...' more-menu button")
            more_btn = page.locator('[data-testid="project__item__more-menu-trigger"]').first
            if not await more_btn.is_visible():
                log.error("More-menu button not found")
                return None
            await more_btn.click()
            await page.wait_for_timeout(500)

            log.info("Clicking 'Export to PDF'")
            await page.locator('button:has-text("Export to PDF")').first.click()
            await page.wait_for_timeout(1000)

            log.info("Clicking 'Export' in modal to trigger async PDF generation")
            await page.locator('button:has-text("Export"):not(:has-text("to PDF"))').first.click()
            await page.wait_for_timeout(2000)

            # --- Step 6: Poll for the new document (up to 120s) ---
            log.info("Polling for new document to appear in Files...")
            pdf_url = None
            for attempt in range(24):  # 24 × 5s = 120s max
                await asyncio.sleep(5)
                docs = await page.evaluate(f'''async () => {{
                    const resp = await fetch('/v1/locations/{cc_project_id}/documents?order=DESC', {{
                        headers: {{ 'Accept': 'application/json' }}
                    }});
                    if (resp.ok) return await resp.json();
                    return [];
                }}''')
                if docs and docs[0]['id'] != latest_id_before:
                    newest = docs[0]
                    name = newest.get('name', '')
                    log.info(f"New document found: {name} (id={newest['id']})")
                    if 'VERO SOLAR INSTALLER CHECKLIST' in name.upper() or 'Exported' in name:
                        pdf_url = newest.get('url') or newest.get('download_url')
                        log.info(f"PDF URL: {pdf_url[:80]}...")
                        break
                    else:
                        log.info(f"New doc is not the checklist PDF: {name}")
                        latest_id_before = docs[0]['id']
                log.info(f"Attempt {attempt + 1}/24 — no new document yet")

            if not pdf_url:
                log.error("Timed out waiting for exported PDF to appear in documents")
                return None

            # --- Step 7: Download the PDF bytes ---
            log.info("Downloading PDF from S3...")
            pdf_bytes_b64 = await page.evaluate(f'''async () => {{
                const resp = await fetch('{pdf_url}');
                if (!resp.ok) return null;
                const buf = await resp.arrayBuffer();
                const bytes = new Uint8Array(buf);
                let binary = '';
                for (let i = 0; i < bytes.byteLength; i++) binary += String.fromCharCode(bytes[i]);
                return btoa(binary);
            }}''')

            if not pdf_bytes_b64:
                log.error("Failed to download PDF from S3")
                return None

            import base64
            pdf_bytes = base64.b64decode(pdf_bytes_b64)
            log.info(f"PDF export successful: {len(pdf_bytes)} bytes")
            return pdf_bytes

        except Exception as e:
            log.exception(f"export_cc_checklist_pdf failed: {e}")
            return None
        finally:
            await context.close()
            await browser.close()


TESLA_EMAIL = os.environ.get('TESLA_EMAIL', 'sam@veropwr.com')
TESLA_USER_DATA_DIR = str(DIR / '.tesla_browser_profile')
TESLA_SESSION_FILE = DIR / 'tesla_session.json'
TESLA_LOGIN_URL = 'https://powerhub.energy.tesla.com/login?redirect_to=%2F'
TESLA_BASE_URL = 'https://powerhub.energy.tesla.com'


async def _tesla_ensure_login(page, context) -> bool:
    """Ensure we are logged into Tesla PowerHub.

    Attempts login using stored credentials.  If a CAPTCHA / MFA step blocks
    the automated flow the function opens the browser in a visible window and
    waits up to 3 minutes for the user to complete authentication manually.

    Returns True if logged in, False on timeout.
    """
    await page.goto(TESLA_BASE_URL, wait_until='domcontentloaded', timeout=30000)
    await asyncio.sleep(2)
    url = page.url

    # Already on the dashboard
    if TESLA_BASE_URL in url and 'login' not in url and 'auth.tesla.com' not in url:
        log.info("Tesla PowerHub: already logged in via saved session")
        return True

    log.info("Tesla PowerHub: session expired or missing — starting login flow")

    # Navigate to login page
    await page.goto(TESLA_LOGIN_URL, wait_until='domcontentloaded', timeout=30000)
    await asyncio.sleep(2)

    # Phase 1: fill identity (email) on powerhub.energy.tesla.com/login
    identity = page.locator('input[name="identity"]')
    if await identity.is_visible():
        log.info("Filling email field")
        await identity.fill(TESLA_EMAIL)
        submit = page.locator('button[type="submit"]').first
        await submit.click()
        await page.wait_for_load_state('domcontentloaded')
        await asyncio.sleep(2)

    # Phase 2: fill password on auth.tesla.com
    pwd = page.locator('input[type="password"], input[name="credential"]').first
    if await pwd.is_visible():
        log.info("Filling password field")
        await pwd.fill(TESLA_PASSWORD)
        submit = page.locator('button[type="submit"]').first
        await submit.click()
        await page.wait_for_load_state('domcontentloaded')
        await asyncio.sleep(2)

    # Phase 3: check for CAPTCHA / MFA — wait for manual completion (up to 3 min)
    for i in range(90):
        await asyncio.sleep(2)
        url = page.url
        if TESLA_BASE_URL in url and 'login' not in url and 'auth.tesla.com' not in url:
            log.info(f"Tesla PowerHub: login confirmed — URL: {url}")
            # Persist session for next run
            try:
                import json as _json
                state = await context.storage_state()
                TESLA_SESSION_FILE.write_text(_json.dumps(state))
                log.info(f"Tesla session saved ({len(state.get('cookies', []))} cookies)")
            except Exception as e:
                log.warning(f"Could not save Tesla session: {e}")
            return True
        if i % 15 == 0:
            log.info(f"Tesla login waiting ({i * 2}s elapsed)… manual CAPTCHA/MFA may be needed")

    log.error("Tesla PowerHub: login timed out after 3 minutes")
    return False


async def screenshot_tesla_commissioning(customer_address: str) -> Optional[bytes]:
    """Tesla commissioning data is now retrieved via the PowerHub API in install_automation.py.

    Browser-based Tesla automation has been replaced by direct API access using
    get_tesla_commissioning_data() in install_automation.py, which calls the
    Tesla GridLogic API (gridlogic-api.sn.tesla.services) with client credentials.
    This avoids CAPTCHA issues entirely.

    Returns None — callers should use get_tesla_commissioning_data() instead.
    """
    log.info(
        'screenshot_tesla_commissioning: Tesla is now handled via API in install_automation.py. '
        'Use get_tesla_commissioning_data() instead.'
    )
    return None


async def _lux_google_auth(page) -> None:
    """Click Google sign-in on Lux login page and complete Google OAuth."""
    on_lux_login = 'account.luxfinancial.io/auth/login' in page.url
    if on_lux_login:
        google_btn = page.locator('button:has-text("Google"), a:has-text("Google"), button:has-text("Sign in with Google")').first
        try:
            if await google_btn.is_visible(timeout=5000):
                await google_btn.click()
                await page.wait_for_load_state('domcontentloaded')
                await page.wait_for_timeout(2000)
        except Exception:
            pass

    email_input = page.locator('input[type="email"]').first
    if await email_input.is_visible(timeout=5000):
        await email_input.fill(GMAIL_ADDRESS)
        await page.locator('#identifierNext, button:has-text("Next")').first.click()
        await page.wait_for_timeout(2000)
        await page.locator('input[type="password"]').first.fill(LUX_GOOGLE_PASSWORD)
        await page.locator('#passwordNext, button:has-text("Next")').first.click()
        await page.wait_for_load_state('domcontentloaded')
        await page.wait_for_timeout(3000)


async def _lux_handle_2fa(page) -> None:
    """If Lux shows a 2FA input, fetch the code from Gmail and submit it."""
    # Look for a 6-digit code input
    code_input = page.locator('input[type="text"][maxlength="6"], input[name*="code" i], input[placeholder*="code" i]').first
    try:
        visible = await code_input.is_visible(timeout=4000)
    except Exception:
        visible = False

    if not visible:
        return

    log.info('Lux 2FA prompt detected — fetching code from Gmail...')
    code = await asyncio.get_event_loop().run_in_executor(None, _fetch_lux_2fa_code, 90)
    if not code:
        log.error('Could not get Lux 2FA code from Gmail')
        return

    await code_input.fill(code)
    submit = page.locator('button[type="submit"], button:has-text("Verify"), button:has-text("Submit"), button:has-text("Continue")').first
    try:
        if await submit.is_visible(timeout=3000):
            await submit.click()
            await page.wait_for_load_state('domcontentloaded')
            await page.wait_for_timeout(3000)
    except Exception:
        await code_input.press('Enter')
        await page.wait_for_timeout(3000)
    log.info(f'Lux 2FA code submitted: {code}')


BROWSER_PROFILE_DIR = DIR / 'lux_browser_profile'


async def _lux_ensure_on_portal(page, context) -> bool:
    """Handle auth redirects and get to app.luxfinancial.io. Returns True if on portal."""
    for _ in range(60):
        await asyncio.sleep(2)
        url = page.url
        if url.startswith('https://app.luxfinancial.io'):
            return True
        if 'account.luxfinancial.io/auth/login' in url or 'accounts.google.com' in url:
            log.info(f'Auth redirect — filling Google credentials: {url}')
            await _lux_google_auth(page)
            await page.wait_for_timeout(3000)
        if 'auth/challenge' in url:
            log.info(f'Lux 2FA challenge: {url}')
            await _lux_handle_2fa(page)
        log.info(f'Waiting for portal... URL: {url}')
    log.error(f'Never reached app.luxfinancial.io — URL: {page.url}')
    return False


async def upload_to_lux_portal(customer_name: str, files: List) -> bool:
    """Log into Lux portal, find job, upload files to the correct document sections.

    files: list of (filename, bytes, section_name) tuples.
    section_name must match the Lux portal option text exactly, e.g.:
      'Bill of Materials', 'CAD/Plan Set', 'Installation Photos'
    Falls back to (filename, bytes) format for backward compatibility (uses 'Bill of Materials').
    """
    if not BROWSER_PROFILE_DIR.exists() and not SESSION_FILE.exists():
        log.error('No lux_browser_profile/ and no lux_session.json — run create_lux_session.py first')
        return False

    async with async_playwright() as p:
        if BROWSER_PROFILE_DIR.exists():
            context = await p.chromium.launch_persistent_context(
                str(BROWSER_PROFILE_DIR),
                headless=True,
                viewport={'width': 1920, 'height': 1080},
                args=['--no-sandbox'],
            )
            browser = None
        else:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                storage_state=str(SESSION_FILE),
                viewport={'width': 1920, 'height': 1080},
            )

        page = await context.new_page()

        try:
            log.info(f'Navigating to Lux portal for {customer_name}')
            await page.goto(LUX_URL, wait_until='networkidle', timeout=60000)
            await page.wait_for_timeout(3000)

            if not await _lux_ensure_on_portal(page, context):
                return False

            # Save refreshed session
            try:
                await context.storage_state(path=str(SESSION_FILE))
            except Exception:
                pass

            # Wait for search input (confirms list page is loaded)
            search_input = page.locator('input[placeholder*="Search" i]').first
            try:
                await search_input.wait_for(state='visible', timeout=15000)
            except Exception:
                log.error('Search input never appeared on Lux portal')
                return False

            # Search for customer by last name
            last_name = customer_name.split()[-1]
            log.info(f'Searching Lux portal for: {last_name}')
            await search_input.fill(last_name)
            await page.wait_for_timeout(2000)

            # Click the HRID (first .font-bold.cursor-pointer = the job ID link)
            hrid = page.locator('.font-bold.cursor-pointer').first
            try:
                await hrid.wait_for(state='visible', timeout=8000)
                hrid_text = await hrid.inner_text()
                log.info(f'Clicking job HRID: {hrid_text}')
                await hrid.click()
                await page.wait_for_url('**/installer/customer/**', timeout=15000)
                await page.wait_for_timeout(2000)
                log.info(f'Job page URL: {page.url}')
            except Exception as e:
                log.error(f'Could not navigate to job page for {customer_name}: {e}')
                return False

            # Click DOCUMENTS tab button
            docs_btn = page.locator('button:has-text("DOCUMENTS")').first
            await docs_btn.scroll_into_view_if_needed()
            await docs_btn.click()
            await page.wait_for_timeout(3000)
            log.info('Opened DOCUMENTS panel')

            # Write files to temp dir for upload
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_paths = []
                section_names = []
                for entry in files:
                    if len(entry) == 3:
                        filename, data, section = entry
                    else:
                        filename, data = entry
                        section = 'Bill of Materials'
                    path = Path(tmpdir) / filename
                    path.write_bytes(data)
                    tmp_paths.append(str(path))
                    section_names.append(section)
                    log.info(f'  {filename} → [{section}] ({len(data)} bytes)')

                # Find the upload label and click it to open the modal
                upload_label = page.locator('label[for="file-uploader"]').first
                await upload_label.scroll_into_view_if_needed()
                bbox = await upload_label.bounding_box()
                if not bbox:
                    log.error('Upload label has no bounding box')
                    return False
                cx = bbox['x'] + bbox['width'] / 2
                cy = bbox['y'] + bbox['height'] / 2
                await page.mouse.click(cx, cy)
                await page.wait_for_timeout(2000)

                # Wait for modal to open
                modal_box = page.locator('.modal-box').last
                try:
                    await modal_box.wait_for(state='visible', timeout=8000)
                except Exception:
                    log.error('Upload modal did not open')
                    return False
                log.info('Upload modal is open')

                # Set all files on the file input at once
                file_input = page.locator('input[type="file"]#files').first
                await file_input.set_input_files(tmp_paths)
                await page.wait_for_timeout(2000)

                # Wait for file rows to appear (one li per file)
                file_rows = modal_box.locator('li')
                try:
                    await file_rows.nth(len(tmp_paths) - 1).wait_for(state='visible', timeout=8000)
                except Exception:
                    log.warning('File rows may not have all appeared — proceeding anyway')

                # For each file, select the correct section via its dropdown
                selects = modal_box.locator('select')
                select_count = await selects.count()
                log.info(f'Section selects in modal: {select_count} (expected {len(section_names)})')
                for i, section in enumerate(section_names):
                    if i < select_count:
                        try:
                            await selects.nth(i).select_option(label=section)
                            log.info(f'  [{i}] {Path(tmp_paths[i]).name} → {section}')
                        except Exception as e:
                            log.warning(f'  [{i}] Could not select section "{section}": {e}')

                # Wait for UPLOAD button to be enabled, then click it
                upload_btn = modal_box.locator('button[type="submit"]').first
                for _ in range(10):
                    await asyncio.sleep(0.5)
                    disabled = await upload_btn.get_attribute('disabled')
                    if disabled is None:
                        break

                await upload_btn.click()
                log.info('Clicked UPLOAD button')

                # Wait for modal to close (upload complete)
                for _ in range(30):
                    await asyncio.sleep(2)
                    if not await modal_box.is_visible():
                        log.info('Upload modal closed — upload complete')
                        break
                else:
                    log.warning('Modal did not close after 60s — upload may have completed anyway')

            log.info(f'Lux portal upload done for {customer_name}: {len(files)} file(s)')
            return True

        except Exception as e:
            log.exception(f'upload_to_lux_portal failed for {customer_name}: {e}')
            return False
        finally:
            await context.close()
            if browser:
                await browser.close()


if __name__ == '__main__':
    import sys
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

    project_id = sys.argv[1] if len(sys.argv) > 1 else '100288461'
    pdf = asyncio.run(export_cc_checklist_pdf(project_id))
    if pdf:
        out = f'/tmp/test_checklist_{project_id}.pdf'
        Path(out).write_bytes(pdf)
        print(f'PDF saved to {out}: {len(pdf)} bytes')
    else:
        print('PDF export failed')
        sys.exit(1)
