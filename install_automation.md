# Install Automation

Monitors Coperniq for completed solar installs and runs the full post-install workflow.

## Trigger

- Polls every **30 minutes**
- Looks for projects in Coperniq with `install_scheduled_date` = today
- For each project, checks Company Cam for a completed VERO SOLAR INSTALLER CHECKLIST
- Skips jobs already processed (tracked in `processed_installs.json`)
- Retries incomplete checklists on the next poll

## What It Does

1. Finds today's Solar Installation projects in Coperniq
2. Checks Company Cam for a completed VERO SOLAR INSTALLER CHECKLIST
3. Completes the Solar Installation work order, form, and field visit in Coperniq
4. Sends Slack message to #vero with all install photos, tagging setter and closer
5. Sends customer follow-up SMS via Coperniq's built-in Communication API (no Twilio needed)
6. Downloads BOM from Gmail (`[Customer] Solar Materials` email) and CAD/planset from Coperniq
7. Exports Company Cam install photos as a multi-page PDF (via CC API + Pillow — no browser needed)
8. Takes a Tesla PowerHub commissioning screenshot via Tesla GridLogic API + HTML renderer (no browser login needed)
9. Uploads 4 documents to the Lux Financial portal via Playwright:
   - Installation Photos (CC checklist PDF)
   - CAD/Plan Set (from Coperniq files)
   - Bill of Materials (from Gmail attachment)
   - Commissioning Screen Shot (Tesla PNG)
10. Emails kathy.treanor@luxfinancial.io that M2 was submitted
11. Creates M2 work order and form in Coperniq, sets Finance Status → M2 Submitted, WO → WAITING, leaves a note

## Slack Format

```
Customer: [Name]
Setter: @[tag]
Closer: @[tag]
[X]kW+battery 🔋
Area: [City]
```

Plus all install photos from Company Cam uploaded in a single grouped message.

## How It Runs

- Polls every **30 minutes** via a `while True` loop
- Runs as a launchd daemon on the Mac mini (auto-restarts on failure/reboot)
- Processed project IDs saved to `processed_installs.json` to prevent reprocessing

## One-Time Setup

### Create Lux portal browser session

Before running for the first time, create a persistent Chrome profile for the Lux portal:

```bash
cd /Users/samlesueur/vero-power
python create_lux_session.py
```

This opens a browser, logs in with sam@veropwr.com via Google OAuth + Gmail 2FA, and saves the session to `lux_browser_profile/`. The session is reused on every run — no re-login needed.

## Restart

```bash
pkill -f install_automation.py
# launchd auto-restarts it
```

## Logs

```bash
tail -f install_automation.log
```

## Environment Variables Required

| Variable | Description |
|----------|-------------|
| `GMAIL_ADDRESS` | Gmail address to monitor |
| `GMAIL_APP_PASSWORD` | Gmail app password |
| `COPERNIQ_API_KEY` | Coperniq API key |
| `SLACK_BOT_TOKEN` | Slack bot token |
| `COMPANY_CAM_API_KEY` | Company Cam API key |
| `LUX_GOOGLE_PASSWORD` | Google password for Lux portal login |
| `TESLA_CLIENT_ID` | Tesla GridLogic API client ID |
| `TESLA_CLIENT_SECRET` | Tesla GridLogic API client secret |
| `TESLA_GROUP_ID` | Tesla GridLogic group ID (Vero group) |
| `KATHY_EMAIL` | Kathy's email at Lux Financial |

## Files

| File | Description |
|------|-------------|
| `install_automation.py` | Main script |
| `install_browser.py` | Playwright browser tasks (CC PDF, Tesla screenshot, Lux upload) |
| `create_lux_session.py` | One-time setup: persistent Chrome profile for Lux portal |
| `create_tesla_session.py` | Not needed — Tesla uses API credentials, no browser login |
| `test_jondrea.py` | Test runner: runs full install pipeline on Jondrea Freeman |
| `install_automation.log` | Log output |
| `processed_installs.json` | Tracks processed project IDs — do not delete |
| `lux_browser_profile/` | Persistent Chrome profile for Lux portal — do not commit |
