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


async def screenshot_tesla_commissioning(customer_address: str) -> Optional[bytes]:
    """Log into Tesla PowerHub, find job by address, screenshot commissioning page."""
    pass  # Task 10


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
