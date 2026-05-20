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
    """Log into Tesla PowerHub, find job by address, screenshot commissioning page.

    Flow:
      1. Launch Chromium with a persistent user-data directory so Tesla session
         cookies survive across runs.  If the session is still valid the login
         step is skipped automatically.
      2. If not logged in, pre-fill email + password and wait up to 3 minutes
         for the user to complete any CAPTCHA / MFA in the visible window.
      3. Search for the customer by address using the site's search bar.
      4. Open the matching job and navigate to the Commissioning tab.
      5. Return a full-page PNG screenshot as bytes.
    """
    import os as _os
    _os.makedirs(TESLA_USER_DATA_DIR, exist_ok=True)

    async with async_playwright() as p:
        # Use a persistent context so browser cookies survive between runs
        context = await p.chromium.launch_persistent_context(
            TESLA_USER_DATA_DIR,
            headless=False,   # must be visible for manual CAPTCHA if needed
            slow_mo=200,
            viewport={'width': 1600, 'height': 900},
            args=['--no-sandbox', '--disable-blink-features=AutomationControlled'],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        try:
            # Step 1 — Ensure we are logged in
            logged_in = await _tesla_ensure_login(page, context)
            if not logged_in:
                log.error("screenshot_tesla_commissioning: could not log in — aborting")
                return None

            # Let the dashboard fully render
            await asyncio.sleep(3)
            log.info(f"Searching PowerHub for address: {customer_address!r}")

            # Step 2 — Try the search bar first
            # PowerHub typically has a global search input
            search_selectors = [
                'input[placeholder*="search" i]',
                'input[placeholder*="address" i]',
                'input[placeholder*="customer" i]',
                'input[aria-label*="search" i]',
                'input[type="search"]',
                '[data-testid*="search"] input',
            ]
            search_input = None
            for sel in search_selectors:
                candidate = page.locator(sel).first
                if await candidate.count() and await candidate.is_visible():
                    search_input = candidate
                    log.info(f"Found search input: {sel}")
                    break

            job_url = None

            if search_input:
                # Use address short form for better match (street + city)
                short_address = customer_address.split(',')[0].strip()
                await search_input.click()
                await search_input.fill(short_address)
                await asyncio.sleep(2)

                # Pick first dropdown result
                result_selectors = [
                    '[role="option"]',
                    '[role="listbox"] li',
                    '.search-result',
                    '.autocomplete-item',
                    'ul li a',
                ]
                for r_sel in result_selectors:
                    results = page.locator(r_sel)
                    if await results.count() > 0:
                        log.info(f"Search dropdown hit — clicking first result ({r_sel})")
                        await results.first.click()
                        await page.wait_for_load_state('domcontentloaded')
                        await asyncio.sleep(2)
                        job_url = page.url
                        break

            # Step 3 — If search didn't navigate us, try browsing the jobs list
            if not job_url or job_url == TESLA_BASE_URL:
                log.info("Search navigation did not work — browsing jobs list")
                for list_path in ['/jobs', '/projects', '/installations', '/']:
                    await page.goto(TESLA_BASE_URL + list_path, wait_until='domcontentloaded', timeout=20000)
                    await asyncio.sleep(2)

                    # Look for a row/card containing the address
                    addr_short = customer_address.split(',')[0].strip()
                    addr_locator = page.locator(
                        f'text="{addr_short}", a:has-text("{addr_short}"), [data-address*="{addr_short}"]'
                    ).first
                    if await addr_locator.count() and await addr_locator.is_visible():
                        await addr_locator.click()
                        await page.wait_for_load_state('domcontentloaded')
                        await asyncio.sleep(2)
                        job_url = page.url
                        log.info(f"Found job via list browsing: {job_url}")
                        break

                    # Also try a search field on this page
                    for sel in search_selectors:
                        si = page.locator(sel).first
                        if await si.count() and await si.is_visible():
                            await si.fill(customer_address.split(',')[0].strip())
                            await asyncio.sleep(2)
                            for r_sel in ['[role="option"]', '[role="listbox"] li', 'li']:
                                rs = page.locator(r_sel)
                                if await rs.count() > 0:
                                    await rs.first.click()
                                    await page.wait_for_load_state('domcontentloaded')
                                    await asyncio.sleep(2)
                                    job_url = page.url
                                    break
                            if job_url:
                                break
                    if job_url:
                        break

            if not job_url or job_url == TESLA_BASE_URL:
                log.warning("Could not navigate to a specific job — taking screenshot of current page")
            else:
                log.info(f"On job page: {job_url}")

            # Step 4 — Navigate to the Commissioning tab if present
            commissioning_selectors = [
                'a:has-text("Commissioning")',
                'button:has-text("Commissioning")',
                '[role="tab"]:has-text("Commissioning")',
                'a:has-text("Commission")',
                '[data-testid*="commission" i]',
            ]
            for sel in commissioning_selectors:
                tab = page.locator(sel).first
                if await tab.count() and await tab.is_visible():
                    log.info(f"Clicking commissioning tab: {sel}")
                    await tab.click()
                    await page.wait_for_load_state('domcontentloaded')
                    await asyncio.sleep(2)
                    break
            else:
                log.info("No 'Commissioning' tab found — screenshotting the job page as-is")

            # Step 5 — Full-page screenshot
            log.info(f"Taking full-page screenshot of: {page.url}")
            screenshot_bytes = await page.screenshot(full_page=True)
            log.info(f"Screenshot captured: {len(screenshot_bytes)} bytes")
            return screenshot_bytes

        except Exception as e:
            log.exception(f"screenshot_tesla_commissioning failed: {e}")
            return None
        finally:
            await context.close()


async def upload_to_lux_portal(customer_name: str, files: List) -> bool:
    """Log into Lux portal using saved session, find job, upload all files."""
    pass  # Task 11


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
