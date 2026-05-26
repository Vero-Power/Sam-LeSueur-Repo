# M2 Approval Automation

Monitors Gmail for Lux Financial M2 approval emails and automatically updates Coperniq.

## Trigger

Watches for emails with subject: `Vero LLC M2 Approval: [Customer Name]`

- Ignores emails not matching this subject exactly
- Skips emails already processed

## What It Does

1. Parses the customer name from the email subject
2. Parses these fields from the email body:
   - System Size
   - Product Type
   - Monthly Payment
   - EPC Install Payout
   - EPC PTO Payout
   - EPC Total Payout
3. Searches Coperniq for the project by customer last name
4. Finds the **M2 (Milestone 2) work order** on the project
5. Finds the **M2 form** on the project
6. Updates form fields:
   - Finance Status → `M2 Approved`
   - M2 Submitted Date → today
   - M2 Completed Date → today
   - Finance Provider → `Lux Financial`
   - Finance Product Type → from email
   - Financing Monthly Payment ($) → from email
7. Marks the form `COMPLETED`
8. Checks off all work order checklist items
9. Marks the work order `COMPLETED`
10. Leaves a comment: *"M2 Approved — automated via LUX Financial approval email."*
11. Replies to Lux Financial: *"Thank you!"*

## How It Runs

- Polls Gmail every **2 minutes** via IMAP
- IMAP connections retry up to 3 times (5s between attempts) before skipping the poll — transient Gmail connection drops don't cause errors
- Runs as a launchd daemon on the Mac mini (auto-restarts on failure/reboot)
- Processed email IDs saved to `processed_m2_emails.json` to prevent double-processing
- Retries automatically on Coperniq rate limits (up to 5 retries, 15s spacing)

## Restart

```bash
pkill -f m2_automation.py
# launchd auto-restarts it
```

## Logs

```bash
tail -f m2_automation.log
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
| `m2_automation.py` | Main script |
| `m2_automation.log` | Log output |
| `processed_m2_emails.json` | Tracks processed email IDs — do not delete |
