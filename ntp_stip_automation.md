# NTP Stipulation Automation

Monitors Gmail for Lux Financial NTP stipulation emails, updates Coperniq, notifies the closer on Slack, and replies to Lux.

## Trigger

Watches for emails with subject: `Vero NTP Stipulation: [Customer Name]`

- Ignores reply emails (subject starts with `Re:`)
- Skips emails already processed

## What It Does

1. Parses the customer name from the email subject
2. Parses the stipulation list from the email body
3. Matches stips to Coperniq dropdown values (see Stipulation Matching below)
4. Searches Coperniq for the project by customer name (prefers full name match over last name)
5. **Skips** if project status is `CANCELLED` or `ON_HOLD`
6. **Skips** if Finance Status is already `NTP Approved`, `M2 Approved`, or `M2 Submitted`
7. Finds or creates the **NTP work order** — if the NTP phase is `NOT_STARTED`, starts it first and waits up to 60s for `IN_PROGRESS` before creating the WO
8. Finds or creates the **NTP form**
9. Updates form fields:
   - Finance Status → `Pending Stipulation`
   - Stipulations → matched dropdown values
10. Sets the NTP work order to `WAITING`
11. Leaves a note on the project tagging Sam LeSueur with the stip list
12. Notifies the rep — Slack to their `-ops` channel if found, otherwise emails the rep directly at their Coperniq `closer_email`
13. Replies to Lux: *"Hi Kathy, Thank you for the heads up — we are on it!"*

## Stipulation Matching

Email text is matched to Coperniq dropdown values using keywords:

| Coperniq Value | Keywords That Trigger It |
|----------------|--------------------------|
| Bank Verification | bank verification, bank |
| Title Verification | title, property ownership, proof of ownership |
| Energy Community Error | energy community |
| Finance Contract Signature Needed | signature, contract |
| Pending Change Order | change order |
| Identity Verification | identity, id verification |
| Address Discrepancy | address, city, discrepancy, different address |
| Design Upload Needed | design |
| Utility Bill Needed | utility bill needed, provide a bill |
| FEOC for Inverter/battery and racking | feoc, inverter, racking |
| Behind on Utility Bill | past due, behind on utility, balance |
| Voided check | voided check, void check |
| Copy of ID | copy of id, photo id, id front, id back, provide an id |
| Social Security card | social security, ssn, ss #, ss# |

## Rep Notification

- Sends Slack to the closer's `-ops` channel (looked up by last name from channel list), tagging them by Slack user ID
- If no `-ops` channel found: emails the rep directly at their `closer_email` from Coperniq
- Only falls back to `#corporate-operations` if no channel AND no email is available

## How It Runs

- Polls Gmail every **2 minutes** via IMAP
- IMAP connections retry up to 3 times (5s between attempts) before skipping the poll — transient Gmail connection drops don't cause errors
- Runs as a launchd daemon on the Mac mini (auto-restarts on failure/reboot)
- Processed email IDs saved to `processed_stip_emails.json` to prevent double-processing
- Retries automatically on Coperniq rate limits (up to 5 retries, 15s spacing)

## Restart

```bash
pkill -f ntp_stip_automation.py
# launchd auto-restarts it
```

> **Important:** `processed_stip_emails.json` is loaded into memory at startup. If you manually edit it, restart the daemon for changes to take effect.

## Logs

```bash
tail -f ntp_stip_automation.log
```

## Environment Variables Required

| Variable | Description |
|----------|-------------|
| `GMAIL_ADDRESS` | Gmail address to monitor |
| `GMAIL_APP_PASSWORD` | Gmail app password (not your login password) |
| `COPERNIQ_API_KEY` | Coperniq API key |
| `SLACK_BOT_TOKEN` | Slack bot token |

## Files

| File | Description |
|------|-------------|
| `ntp_stip_automation.py` | Main script |
| `ntp_stip_automation.log` | Log output |
| `processed_stip_emails.json` | Tracks processed email IDs — do not delete |
