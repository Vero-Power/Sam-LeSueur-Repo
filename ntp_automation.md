# NTP Approval Automation

Monitors Gmail for Lux Financial NTP approval emails and automatically updates Coperniq.

## Trigger

Watches for emails with subject: `Vero LLC NTP Approval: [Customer Name]`

- Ignores reply emails (subject starts with `Re:`)
- Skips emails already processed

## What It Does

1. Parses the customer name from the email subject
2. Searches Coperniq for the project by customer name
3. Finds or creates the **Notice to Proceed work order** (template ID: 1907087)
4. Checks off all checklist items on the work order
5. Finds or creates the **Notice to Proceed form** (template ID: 1191546)
6. Skips if Finance Status is already `M2 Approved` or `M2 Submitted`
7. Sets form fields:
   - Finance Status → `NTP Approved`
   - NTP Submitted Date → today
   - NTP Completed Date → today
   - Stipulations → `NA`
   - Finance Provider → `Lux Financial`
8. Marks the form `COMPLETED`
9. Marks the work order `COMPLETED`
10. Leaves a comment: *"NTP Approved - automated via LUX Financial approval email."*

## How It Runs

- Polls Gmail every **2 minutes** via IMAP
- IMAP connections retry up to 3 times (5s between attempts) before skipping the poll — transient Gmail connection drops don't cause errors
- Runs as a launchd daemon on the Mac mini (auto-restarts on failure/reboot)
- Processed email IDs saved to `processed_emails.json` to prevent double-processing

## Restart

```bash
pkill -f ntp_automation.py
# launchd auto-restarts it
```

## Logs

```bash
tail -f ntp_automation.log
```

## Environment Variables Required

| Variable | Description |
|----------|-------------|
| `GMAIL_ADDRESS` | Gmail address to monitor |
| `GMAIL_APP_PASSWORD` | Gmail app password (not your login password) |
| `COPERNIQ_API_KEY` | Coperniq API key |

## Files

| File | Description |
|------|-------------|
| `ntp_automation.py` | Main script |
| `ntp_automation.log` | Log output |
| `processed_emails.json` | Tracks processed email IDs — do not delete |
