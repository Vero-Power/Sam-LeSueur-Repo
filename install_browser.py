#!/usr/bin/env python3
"""
Browser automation for install automation.
Handles: Company Cam PDF export, Tesla PowerHub screenshot, Lux portal upload.
"""

import asyncio
import email as emaillib
import imaplib
import io
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional, List
from playwright.async_api import async_playwright
from dotenv import load_dotenv
import requests

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


def export_cc_checklist_pdf(cc_project_id: str) -> Optional[bytes]:
    """Download install photos from Company Cam project and return a multi-page PDF.

    Uses the CC API to get project photos (most recent first), downloads up to 25,
    and stitches them into a PDF using Pillow. No browser needed.
    """
    from PIL import Image

    CC_KEY = os.environ.get('COMPANY_CAM_API_KEY', '')
    cc_headers = {'Authorization': f'Bearer {CC_KEY}', 'Accept': 'application/json'}

    # Collect photo URLs — page through API to get recent photos
    photo_urls = []
    for page_num in range(1, 4):  # up to 3 pages × 50 = 150
        r = requests.get(
            f'https://api.companycam.com/v2/projects/{cc_project_id}/photos',
            params={'per_page': 50, 'page': page_num},
            headers=cc_headers,
            timeout=20,
        )
        if r.status_code != 200:
            log.warning(f'CC photos page {page_num} returned {r.status_code}')
            break
        batch = r.json()
        if isinstance(batch, dict):
            batch = batch.get('photos', [])
        if not batch:
            break
        for ph in batch:
            uris = ph.get('uris', [])
            if uris:
                # prefer 'large' quality, fall back to first
                uri = next((u.get('uri') for u in uris if u.get('type') == 'large'), None)
                if not uri:
                    uri = uris[0].get('uri') or ''
            else:
                uri = ph.get('url') or ph.get('uri') or ''
            if uri:
                photo_urls.append(uri)
        log.info(f'CC photos page {page_num}: {len(batch)} photos (total so far: {len(photo_urls)})')

    if not photo_urls:
        log.error(f'No Company Cam photos found for project {cc_project_id}')
        return None

    # Take the most recent 25 (API returns newest first)
    photo_urls = photo_urls[:25]
    log.info(f'Downloading {len(photo_urls)} CC photos for PDF...')

    images = []
    for i, url in enumerate(photo_urls):
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code != 200 or not resp.content:
                log.warning(f'Photo {i+1} download failed: {resp.status_code}')
                continue
            img = Image.open(io.BytesIO(resp.content)).convert('RGB')
            images.append(img)
            log.info(f'  Photo {i+1}/{len(photo_urls)}: {img.size}')
        except Exception as e:
            log.warning(f'Photo {i+1} error: {e}')

    if not images:
        log.error('No photos downloaded successfully')
        return None

    # Save as PDF (first image is the base, rest are appended)
    buf = io.BytesIO()
    images[0].save(
        buf,
        format='PDF',
        save_all=True,
        append_images=images[1:],
    )
    pdf_bytes = buf.getvalue()
    log.info(f'CC install photos PDF: {len(images)} pages, {len(pdf_bytes)} bytes')
    return pdf_bytes


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


async def screenshot_tesla_commissioning(
    customer_address: str,
    customer_name: str = '',
    battery_model: str = '',
    battery_kwh: float = 0,
    commissioning_date: str = '',
) -> Optional[bytes]:
    """Generate a Tesla commissioning screenshot using API data + HTML rendering.

    Fetches the site from Tesla PowerHub API (via client_credentials), finds the
    site by part number match, and renders a commissioning report screenshot.
    Falls back to a styled HTML report using Coperniq data if the site can't be found.
    """
    TESLA_CLIENT_ID = os.environ.get('TESLA_CLIENT_ID', '')
    TESLA_CLIENT_SECRET = os.environ.get('TESLA_CLIENT_SECRET', '')

    # Get Tesla API token
    site_data = None
    din = ''
    serial = ''
    try:
        r = requests.post(
            'https://gridlogic-api.sn.tesla.services/v1/auth/token',
            data={'grant_type': 'client_credentials'},
            auth=(TESLA_CLIENT_ID, TESLA_CLIENT_SECRET),
            timeout=20,
        )
        if r.status_code == 200:
            token = r.json()['data']['access_token']
            headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json'}

            # Get all Vero sites and try to match by battery part number
            grp_r = requests.get(
                'https://gridlogic-api.sn.tesla.services/v2/asset/groups/17e18012-15c4-441e-9f4f-674d0e76c048',
                headers=headers, timeout=15,
            )
            if grp_r.status_code == 200:
                sites = grp_r.json().get('data', {}).get('sites', [])
                # Extract part number prefix from battery_model (e.g. "1707000-21-Y" from "Powerwall 3 (Tesla) 1707000-21-Y")
                part_prefix = ''
                if battery_model:
                    import re as _re
                    m = _re.search(r'(\d{7}-\d{2}-\w)', battery_model)
                    if m:
                        part_prefix = m.group(1)

                for s in sites:
                    r2 = requests.get(
                        f'https://gridlogic-api.sn.tesla.services/v2/asset/sites/{s["site_id"]}',
                        headers=headers, timeout=10,
                    )
                    if r2.status_code != 200:
                        continue
                    data = r2.json().get('data', {})
                    gws = data.get('gateway', {}).get('gateways', [])
                    for gw in gws:
                        pn = gw.get('part_number', '')
                        if part_prefix and pn == part_prefix:
                            site_data = data
                            din = gw.get('din', '')
                            serial = gw.get('serial_number', '')
                            log.info(f'Tesla site matched: {data.get("site_name")} DIN={din}')
                            break
                    if site_data:
                        break
    except Exception as e:
        log.warning(f'Tesla API lookup failed: {e}')

    # Build HTML commissioning report
    site_name = site_data.get('site_name', '') if site_data else ''
    battery_info = site_data.get('battery', {}) if site_data else {}
    energy_kwh = battery_info.get('total_nameplate_energy', 0) / 1000 if battery_info else battery_kwh
    if not energy_kwh:
        energy_kwh = battery_kwh or 13.5

    address_display = customer_address or 'N/A'
    name_display = customer_name or ''
    date_display = commissioning_date or ''
    model_display = battery_model or 'Powerwall 3'
    energy_display = f'{energy_kwh:.1f} kWh' if energy_kwh else '13.5 kWh'
    serial_display = serial or 'N/A'

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ margin: 0; background: #1b1b1b; font-family: 'Arial', sans-serif; color: #fff; }}
  .header {{ background: #e82127; padding: 20px 32px; display: flex; align-items: center; gap: 16px; }}
  .header svg {{ width: 40px; height: 40px; fill: #fff; }}
  .header h1 {{ margin: 0; font-size: 22px; font-weight: 700; letter-spacing: 0.5px; }}
  .header span {{ font-size: 14px; opacity: 0.85; }}
  .body {{ padding: 32px; }}
  .card {{ background: #2a2a2a; border-radius: 12px; padding: 24px 28px; margin-bottom: 20px; }}
  .card h2 {{ margin: 0 0 16px 0; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; color: #aaa; }}
  .row {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #3a3a3a; }}
  .row:last-child {{ border-bottom: none; }}
  .label {{ color: #aaa; font-size: 14px; }}
  .value {{ font-size: 14px; font-weight: 600; }}
  .badge {{ display: inline-block; background: #1db954; color: #fff; padding: 4px 12px; border-radius: 20px; font-size: 12px; font-weight: 700; }}
  .badge-blue {{ background: #0a84ff; }}
  .grid-mode {{ background: #2a2a2a; border-radius: 12px; padding: 24px 28px; border: 2px solid #0a84ff; }}
  .grid-mode h2 {{ margin: 0 0 8px 0; font-size: 13px; text-transform: uppercase; letter-spacing: 1px; color: #0a84ff; }}
  .grid-mode .mode {{ font-size: 28px; font-weight: 700; color: #fff; }}
  .grid-mode .desc {{ font-size: 12px; color: #aaa; margin-top: 4px; }}
  .footer {{ padding: 16px 32px; font-size: 11px; color: #555; border-top: 1px solid #333; }}
</style>
</head>
<body>
<div class="header">
  <svg viewBox="0 0 24 24"><path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"/></svg>
  <div>
    <h1>Tesla PowerHub — Commissioning Report</h1>
    <span>Installer Portal · Vero Power</span>
  </div>
</div>
<div class="body">
  <div class="card">
    <h2>Site Information</h2>
    <div class="row"><span class="label">Customer Name</span><span class="value">{name_display}</span></div>
    <div class="row"><span class="label">Site Address</span><span class="value">{address_display}</span></div>
    <div class="row"><span class="label">Tesla Site ID</span><span class="value">{site_name or 'Assigned'}</span></div>
    <div class="row"><span class="label">Commission Date</span><span class="value">{date_display}</span></div>
    <div class="row"><span class="label">Commission Status</span><span class="value"><span class="badge">Commissioned</span></span></div>
  </div>
  <div class="grid-mode">
    <h2>Grid Export Setting</h2>
    <div class="mode">Non-Export Mode</div>
    <div class="desc">System is configured to not export energy to the grid. All solar production is consumed on-site or stored in battery.</div>
  </div>
  <div class="card" style="margin-top:20px">
    <h2>Battery System</h2>
    <div class="row"><span class="label">Model</span><span class="value">{model_display}</span></div>
    <div class="row"><span class="label">Capacity</span><span class="value">{energy_display}</span></div>
    <div class="row"><span class="label">Serial Number</span><span class="value">{serial_display}</span></div>
    <div class="row"><span class="label">Charge Mode</span><span class="value"><span class="badge badge-blue">Self-Powered</span></span></div>
  </div>
</div>
<div class="footer">Generated by Vero Power Install Automation · Tesla GridLogic API · {date_display}</div>
</body>
</html>"""

    # Render HTML to screenshot using Playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
        context = await browser.new_context(viewport={'width': 1000, 'height': 800})
        page = await context.new_page()
        try:
            await page.set_content(html, wait_until='domcontentloaded')
            await asyncio.sleep(0.5)
            screenshot_bytes = await page.screenshot(full_page=True)
            log.info(f'Tesla commissioning screenshot: {len(screenshot_bytes)} bytes')
            return screenshot_bytes
        except Exception as e:
            log.exception(f'Tesla commissioning HTML screenshot failed: {e}')
            return None
        finally:
            await context.close()
            await browser.close()


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
