# install_browser.py

Browser and API helper module used by `install_automation.py` and `m3_automation.py`. Handles Company Cam PDF export, Tesla commissioning screenshot, and Lux portal file uploads.

## Functions

### `export_cc_checklist_pdf(cc_project_id)`

Downloads install photos from Company Cam and combines them into a multi-page PDF.

- Fetches the 25 most recent photos from the Company Cam project via CC API
- Downloads images directly from `img.companycam.com` CDN (no auth headers needed)
- Combines all images into a single PDF using Pillow
- Returns PDF as `bytes`, or `None` on failure
- **SYNC function — do NOT await it** (uses `requests`, not async)

### `screenshot_tesla_commissioning(customer_address, customer_name, battery_model, battery_kwh)`

Generates a Tesla PowerHub commissioning screenshot using the Tesla GridLogic API.

- Authenticates via `client_credentials` flow using `TESLA_CLIENT_ID` / `TESLA_CLIENT_SECRET`
- Searches Vero's Tesla group for the site matching the battery part number
- Renders an HTML commissioning report (customer name, address, Non-Export Mode, battery specs) to PNG using Playwright headless
- **No browser login or CAPTCHA needed** — pure API + headless HTML render
- Returns PNG as `bytes`, or `None` on failure

Tesla group ID: `17e18012-15c4-441e-9f4f-674d0e76c048`

### `upload_to_lux_portal(customer_name, files)`

Uploads files to the Lux Financial portal using a persistent Chrome profile.

- Uses `lux_browser_profile/` (created by `create_lux_session.py`) — no re-login needed
- Searches for the customer by name in the portal
- Clicks HRID → DOCUMENTS → selects section per file → uploads
- `files` is a list of `(filename, bytes, section_label)` tuples
- Section labels used in install automation: `'Installation Photos'`, `'CAD/Plan Set'`, `'Bill of Materials'`, `'Commissioning Screen Shot'`
- Section labels used in M3 automation: `'Proof of Commissionsing'` (Lux typo — double 's'), `'PTO Letter'`
- Returns `True` on success, `False` on failure

## Usage

```python
from install_browser import export_cc_checklist_pdf, screenshot_tesla_commissioning, upload_to_lux_portal

# Sync — no await
pdf_bytes = export_cc_checklist_pdf(cc_project_id)

# Async — must await
screenshot = await screenshot_tesla_commissioning(
    customer_address='123 Main St, Houston, TX 77001',
    customer_name='Jane Smith',
    battery_model='Powerwall 3 (Tesla) 1707000-21-Y',
    battery_kwh=13.5,
)

success = await upload_to_lux_portal('Jane Smith', [
    ('jane_smith_commissioning.png', screenshot, 'Commissioning Screen Shot'),
])
```

## Environment Variables Required

| Variable | Description |
|----------|-------------|
| `GMAIL_ADDRESS` | Gmail address (for Lux 2FA fallback) |
| `GMAIL_APP_PASSWORD` | Gmail app password |
| `LUX_GOOGLE_PASSWORD` | Google password for Lux portal |
| `TESLA_CLIENT_ID` | Tesla GridLogic API client ID |
| `TESLA_CLIENT_SECRET` | Tesla GridLogic API client secret |
| `TESLA_GROUP_ID` | Tesla GridLogic Vero group ID |
| `COMPANY_CAM_API_KEY` | Company Cam API key |

## Dependencies

- `playwright` (Chromium) — for Tesla HTML render and Lux portal upload
- `Pillow` — for combining CC photos into PDF
- `requests` — for all API calls

## Notes

- Lux portal uses a persistent Chrome profile at `lux_browser_profile/` — run `create_lux_session.py` once to create it
- Tesla GridLogic API uses OAuth2 client credentials — no browser login ever needed
- CC CDN URLs (`img.companycam.com`) work without auth headers
