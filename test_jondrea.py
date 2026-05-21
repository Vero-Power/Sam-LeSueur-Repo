#!/usr/bin/env python3
"""
Test the full install upload pipeline on Jondrea Freeman.
Deletes leftover test_checklist.pdf first, then uploads all 4 real files.
"""
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

# Jondrea Freeman constants
CUSTOMER_NAME = 'Jondrea Freeman'
CUSTOMER_ADDRESS = '1526 Island Grove Dr, Iowa Colony, TX 77583'
COPERNIQ_PROJECT_ID = 793003
CC_PROJECT_ID = '99879909'


async def delete_test_file_from_lux():
    """Delete test_checklist.pdf from Jondrea's Installation Photos in Lux portal."""
    from playwright.async_api import async_playwright
    from install_browser import BROWSER_PROFILE_DIR, LUX_URL, _lux_ensure_on_portal

    if not BROWSER_PROFILE_DIR.exists():
        log.error('No lux_browser_profile — run create_lux_session.py first')
        return

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            str(BROWSER_PROFILE_DIR),
            headless=True,
            viewport={'width': 1920, 'height': 1080},
            args=['--no-sandbox'],
        )
        page = await context.new_page()
        try:
            await page.goto(LUX_URL, wait_until='networkidle', timeout=60000)
            await page.wait_for_timeout(3000)

            if not await _lux_ensure_on_portal(page, context):
                log.error('Could not reach Lux portal')
                return

            # Search for Jondrea
            search = page.locator('input[placeholder*="Search" i]').first
            await search.wait_for(state='visible', timeout=15000)
            await search.fill('Freeman')
            await page.wait_for_timeout(2000)

            hrid = page.locator('.font-bold.cursor-pointer').first
            await hrid.wait_for(state='visible', timeout=8000)
            await hrid.click()
            await page.wait_for_url('**/installer/customer/**', timeout=15000)
            await page.wait_for_timeout(2000)

            # Open DOCUMENTS panel
            docs_btn = page.locator('button:has-text("DOCUMENTS")').first
            await docs_btn.click()
            await page.wait_for_timeout(3000)

            # Expand all sections via JS so delete buttons are clickable
            await page.evaluate("""
                document.querySelectorAll('input[name="milestone-collapse"]').forEach(cb => { cb.checked = true; });
            """)
            await page.wait_for_timeout(1000)

            # Find test_checklist.pdf row and delete it
            deleted = 0
            rows = await page.locator('li').all()
            for row in rows:
                try:
                    text = await row.inner_text()
                    if 'test_checklist.pdf' in text or 'test_bom' in text or 'test_cad' in text:
                        delete_btn = row.locator('button[aria-label*="delete" i], button[title*="delete" i], svg[data-icon="trash"], button:has(svg)').last
                        bbox = await delete_btn.bounding_box()
                        if bbox:
                            await page.mouse.click(bbox['x'] + bbox['width'] / 2, bbox['y'] + bbox['height'] / 2, force=True)
                            await page.wait_for_timeout(1500)
                            # Confirm dialog if present
                            confirm = page.locator('button:has-text("Delete"), button:has-text("Confirm"), button:has-text("Yes")').first
                            try:
                                if await confirm.is_visible(timeout=2000):
                                    await confirm.click()
                                    await page.wait_for_timeout(1500)
                            except Exception:
                                pass
                            log.info(f'Deleted: {text.strip()[:60]}')
                            deleted += 1
                except Exception as e:
                    log.warning(f'Row delete error: {e}')
            log.info(f'Cleanup done: {deleted} test file(s) removed')
        except Exception as e:
            log.exception(f'Cleanup failed: {e}')
        finally:
            await context.close()


def fetch_bom():
    from install_automation import download_bom_from_gmail
    files = download_bom_from_gmail(CUSTOMER_NAME)
    log.info(f'BOM files: {[(n, len(d)) for n, d in files]}')
    return files


def fetch_cad():
    from install_automation import download_cad_from_coperniq
    result = download_cad_from_coperniq(COPERNIQ_PROJECT_ID)
    if result:
        log.info(f'CAD file: {result[0]} ({len(result[1])} bytes)')
    else:
        log.warning('No CAD file found')
    return result


async def run_full_test():
    log.info('=== Step 1: Delete leftover test files from Lux portal ===')
    await delete_test_file_from_lux()

    log.info('=== Step 2: Download BOM from Gmail ===')
    bom_files = fetch_bom()

    log.info('=== Step 3: Download CAD from Coperniq ===')
    cad_file = fetch_cad()

    log.info('=== Step 4: Run browser tasks (CC PDF + Tesla screenshot + Lux upload) ===')
    from install_browser import export_cc_checklist_pdf, upload_to_lux_portal, screenshot_tesla_commissioning

    log.info('Exporting CC install photos PDF...')
    pdf = export_cc_checklist_pdf(CC_PROJECT_ID)
    if pdf:
        Path('/tmp/jondrea_install_checklist.pdf').write_bytes(pdf)
        log.info(f'CC PDF: {len(pdf)} bytes -> /tmp/jondrea_install_checklist.pdf')
    else:
        log.warning('CC PDF export failed')

    log.info('Taking Tesla screenshot...')
    tesla_png = await screenshot_tesla_commissioning(
        customer_address=CUSTOMER_ADDRESS,
        customer_name=CUSTOMER_NAME,
        battery_model='Powerwall 3 (Tesla) 1707000-21-Y',
        battery_kwh=13.5,
        commissioning_date='2026-05-19',
    )
    if tesla_png:
        Path('/tmp/jondrea_tesla.png').write_bytes(tesla_png)
        log.info(f'Tesla screenshot: {len(tesla_png)} bytes -> /tmp/jondrea_tesla.png')
    else:
        log.warning('Tesla screenshot failed')

    lux_files = []
    if pdf:
        lux_files.append(('install_checklist.pdf', pdf, 'Installation Photos'))
    if cad_file:
        cad_name, cad_data = cad_file
        lux_files.append((cad_name, cad_data, 'CAD/Plan Set'))
    for name, data in bom_files:
        lux_files.append((name, data, 'Bill of Materials'))
    if tesla_png:
        lux_files.append(('tesla_commissioning.png', tesla_png, 'Commissioning Screen Shot'))

    log.info(f'Files to upload: {[(n, s, sec) for n, d, sec in lux_files for s in [len(d)]]}')

    if lux_files:
        log.info(f'=== Step 5: Uploading {len(lux_files)} file(s) to Lux portal ===')
        ok = await upload_to_lux_portal(CUSTOMER_NAME, lux_files)
        log.info(f'Lux upload result: {ok}')
    else:
        log.error('No files to upload!')

    log.info('=== Done ===')


asyncio.run(run_full_test())
