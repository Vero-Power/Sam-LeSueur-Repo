# Install Automation — Design Spec
Date: 2026-05-20

## Overview
A Python script that polls every 30 minutes for completed solar installs and automates the entire post-install workflow: completing Coperniq records, notifying the team on Slack, texting the customer, submitting M2 to Lux, and kicking off the M2 phase in Coperniq.

## Trigger
- Runs as a launchd daemon on the Mac mini
- Polls every 30 minutes
- Queries Coperniq for today's Solar Installation work orders
- For each job, checks Company Cam for a completed VERO SOLAR INSTALLER CHECKLIST (green checkmark, all items done — count varies 9/9, 10/10, 11/11 etc.)
- Skips jobs already processed (tracked in `processed_installs.json`)
- Skips jobs whose checklist isn't done yet — retries next poll

## Step-by-Step Flow

### 1. Get Today's Installs (Coperniq API)
- Query Coperniq scheduler for today's Solar Installation WOs
- Extract: customer name, project ID, project details (setter, closer, system size, area/city)

### 2. Check Company Cam
- Search Company Cam project by customer name
- Check if VERO SOLAR INSTALLER CHECKLIST has green checkmark (all items completed)
- If not complete: skip, retry next poll

### 3. Complete Coperniq Install WO + Form
- Find install work order on the project
- Check off all WO checklist items
- Mark WO completed
- Find install form, fill in install date fields
- Mark form completed
- Check off the visit

### 4. Grab Install Photos (Company Cam API)
- Pull photos from checklist items whose names contain "installed panels" or "battery"
- Download photo files locally

### 5. Send Slack to #vero
Format:
```
Customer: [Name]
Setter: @[tag]
Closer: @[tag]
[X]kW+battery 🔋
Area: [City]
```
Plus panel and battery photos attached

### 6. Send Customer SMS (Coperniq API)
Template:
> "Hey [First Name]! This is Sam with Vero, just checking in to make sure the install went well and to thank you for being great to work with. If you ever have any neighbors or friends who are interested in the program, let us know so we can send ya a $500 referral bonus!"

### 7. Download All Documents
- **Company Cam checklist PDF** — Playwright: log into Company Cam, navigate to project → Checklists → VERO SOLAR INSTALLER CHECKLIST → "..." → Export to PDF → download
- **CAD/Planset** — Coperniq API: download most recent CAD/planset file from project docs
- **Bill of Materials** — Gmail IMAP: search for `[Customer Name] Solar Materials`, download all PDF attachments from most recent email
- **Tesla commissioning screenshot** — Playwright: log into powerhub.energy.tesla.com, navigate to job, screenshot commissioning page

### 8. Upload to Lux Portal (Playwright)
- Log in via Google OAuth (sam@veropwr.com)
- Find the job by customer name
- Upload all 4 document types: checklist PDF, CAD/planset, BOM attachments, Tesla screenshot

### 9. Email Kathy (SMTP)
- Send email to Lux underwriting saying M2 was submitted for the customer

### 10. Start M2 in Coperniq
- Find or create M2 work order
- Find or create M2 form
- Set Finance Status → M2 Submitted
- Fill M2 Submitted Date and M2 Completed Date → today
- Set WO to WAITING
- Leave note: "M2 submitted"

## Tech Stack
- **API:** `requests` (Coperniq, Company Cam, Slack)
- **Browser automation:** `playwright` (Python) for Company Cam PDF export, Tesla screenshot, Lux portal upload
- **Email:** IMAP (Gmail attachment download), SMTP (Kathy notification)
- **Runtime:** launchd daemon on Mac mini, same pattern as ntp/m2/stip automations

## Credentials (all in .env)
- `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` — existing
- `COPERNIQ_API_KEY` — existing
- `SLACK_BOT_TOKEN` — existing
- `COMPANY_CAM_API_KEY` — to be added
- `LUX_GOOGLE_PASSWORD` — to be added (Google OAuth: sam@veropwr.com)
- `TESLA_PASSWORD` — to be added (sam@veropwr.com)

## Files
- `install_automation.py` — main script
- `processed_installs.json` — tracks processed job IDs
- `install_automation.log` — log output

## Open Items
- Confirm Coperniq API endpoint for scheduler/calendar WOs by date
- Confirm Company Cam API endpoints for checklist status + photo download
- Confirm Coperniq API for sending SMS to customer
- Confirm Coperniq API for CAD/planset file download
- Confirm how Tesla PowerHub matches jobs (by address or customer name)
- Confirm Lux portal job search/upload field structure (discovered via Playwright during build)
- Kathy's email address for M2 submission notification
