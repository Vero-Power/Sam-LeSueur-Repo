# create_lux_session.py

One-time setup script that creates a persistent Chrome profile for the Lux Financial portal. Run this once on the Mac mini — `install_automation.py` and `m3_automation.py` reuse the saved session without re-authenticating.

## When to Run

- First-time setup on a new machine
- If the Lux session expires and uploads start failing (re-run to refresh)

## Usage

```bash
cd /Users/samlesueur/vero-power
python3 create_lux_session.py
```

A Chrome browser window will open. The script handles:
1. Navigating to the Lux portal
2. Clicking "Sign in with Google"
3. Filling Google credentials (email + password)
4. Waiting for iPhone push notification approval (approve it on your phone)
5. Auto-fetching Lux 2FA code from Gmail if prompted
6. Saving the session to `lux_browser_profile/`

After it completes, the browser profile is saved and future runs use it silently (headless).

## Files Created

| File | Description |
|------|-------------|
| `lux_browser_profile/` | Persistent Chrome profile — do not delete or commit |
| `lux_session.json` | Saved storage state — do not commit |

## Environment Variables Required

| Variable | Description |
|----------|-------------|
| `GMAIL_ADDRESS` | sam@veropwr.com |
| `GMAIL_APP_PASSWORD` | Gmail app password |
| `LUX_GOOGLE_PASSWORD` | Google account password for Lux portal login |
