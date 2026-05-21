#!/usr/bin/env python3
"""
Browser automation for install automation.
Handles: Company Cam PDF export, Tesla PowerHub screenshot, Lux portal upload.
"""

import asyncio
import logging
import os
import tempfile
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


async def upload_to_lux_portal(customer_name: str, files: List) -> bool:
    """Log into Lux portal using saved session, find job, upload all files.

    files: list of (filename, bytes) tuples
    """
    if not SESSION_FILE.exists():
        log.error('lux_session.json not found — run create_lux_session.py first')
        return False

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            storage_state=str(SESSION_FILE),
            viewport={'width': 1600, 'height': 900},
            accept_downloads=True,
        )
        page = await context.new_page()

        try:
            log.info(f'Navigating to Lux portal for {customer_name}')
            await page.goto(LUX_URL, wait_until='domcontentloaded', timeout=60000)
            await page.wait_for_timeout(3000)

            current_url = page.url
            log.info(f'Lux portal URL after load: {current_url}')

            # If session expired and redirected to Google login, re-authenticate
            if 'accounts.google.com' in current_url:
                log.info('Session expired — attempting Google re-authentication')
                email_input = page.locator('input[type="email"]').first
                if await email_input.is_visible(timeout=5000):
                    await email_input.fill(GMAIL_ADDRESS)
                    await page.locator('#identifierNext, button:has-text("Next")').first.click()
                    await page.wait_for_timeout(2000)
                    await page.locator('input[type="password"]').first.fill(LUX_GOOGLE_PASSWORD)
                    await page.locator('#passwordNext, button:has-text("Next")').first.click()
                    await page.wait_for_load_state('domcontentloaded')
                    await page.wait_for_timeout(3000)

                # Save refreshed session
                try:
                    await context.storage_state(path=str(SESSION_FILE))
                    log.info('Refreshed Lux session saved')
                except Exception as e:
                    log.warning(f'Could not save refreshed session: {e}')

            # Verify we're on the Lux portal
            if 'luxfinancial.io' not in page.url:
                log.error(f'Not on Lux portal after auth — URL: {page.url}')
                return False

            # Search for the customer
            last_name = customer_name.split()[-1]
            log.info(f'Searching Lux portal for: {last_name}')

            # Try various search input patterns
            search_selectors = [
                'input[placeholder*="search" i]',
                'input[type="search"]',
                'input[placeholder*="customer" i]',
                'input[placeholder*="name" i]',
            ]
            search_input = None
            for selector in search_selectors:
                loc = page.locator(selector).first
                if await loc.is_visible(timeout=2000):
                    search_input = loc
                    log.info(f'Found search input: {selector}')
                    break

            if search_input:
                await search_input.fill(last_name)
                await page.keyboard.press('Enter')
                await page.wait_for_timeout(2000)
            else:
                log.warning('No search input found — trying to find job in listing directly')

            # Click into the job row
            job_clicked = False
            for name_part in [customer_name, last_name, customer_name.split()[0]]:
                try:
                    job_row = page.locator(f'text={name_part}').first
                    if await job_row.is_visible(timeout=3000):
                        await job_row.click()
                        await page.wait_for_load_state('domcontentloaded')
                        await page.wait_for_timeout(2000)
                        job_clicked = True
                        log.info(f'Clicked job row for: {name_part}')
                        break
                except Exception:
                    continue

            if not job_clicked:
                log.warning(f'Could not find job row for {customer_name} in Lux portal — attempting upload on current page anyway')

            log.info(f'Current Lux URL: {page.url}')

            # Write files to temp dir
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_paths = []
                for filename, data in files:
                    path = Path(tmpdir) / filename
                    path.write_bytes(data)
                    tmp_paths.append(str(path))
                    log.info(f'Temp file: {path} ({len(data)} bytes)')

                # Find all file upload inputs
                upload_inputs = page.locator('input[type="file"]')
                input_count = await upload_inputs.count()
                log.info(f'Found {input_count} file input(s) on Lux portal job page')

                if input_count > 0:
                    # Try to upload all files to available inputs
                    # Multiple inputs may map to different doc types (checklist, CAD, BOM, Tesla)
                    for i, tmp_path in enumerate(tmp_paths):
                        input_idx = min(i, input_count - 1)
                        try:
                            await upload_inputs.nth(input_idx).set_input_files(tmp_path)
                            await page.wait_for_timeout(1500)
                            log.info(f'Uploaded {Path(tmp_path).name} to input[{input_idx}]')
                        except Exception as e:
                            log.warning(f'Could not upload {Path(tmp_path).name}: {e}')
                else:
                    # Try drag-and-drop zones or other upload mechanisms
                    log.warning('No file inputs found — Lux portal may use drag-and-drop upload zone')
                    drop_zones = page.locator('[class*="upload" i], [class*="dropzone" i], [class*="drop" i]')
                    dz_count = await drop_zones.count()
                    log.info(f'Found {dz_count} potential drop zones')

                # Look for Save/Submit/Upload/Confirm buttons
                for btn_text in ['Save', 'Submit', 'Upload', 'Confirm', 'Update']:
                    btn = page.locator(f'button:has-text("{btn_text}")').first
                    try:
                        if await btn.is_visible(timeout=2000):
                            await btn.click()
                            await page.wait_for_load_state('domcontentloaded')
                            await page.wait_for_timeout(2000)
                            log.info(f'Clicked "{btn_text}" button on Lux portal')
                            break
                    except Exception:
                        continue

            log.info(f'Lux portal upload complete for {customer_name}: {len(files)} files')
            return True

        except Exception as e:
            log.exception(f'upload_to_lux_portal failed for {customer_name}: {e}')
            return False
        finally:
            await context.close()
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
