# Book Discovery — Automated Polish Non-Fiction Alerts

Automatically scans [lubimyczytac.pl](https://lubimyczytac.pl) every two weeks
for highly-rated Polish non-fiction books and emails you a curated digest of new finds.

**What it does:**
- Scrapes 5 non-fiction categories (literatura faktu, biografie, historia, psychologia, literatura popularnonaukowa)
- Filters books with rating ≥ 7.0 / 10 and ≥ 20 ratings
- Stores all discovered books in a Google Sheet (your human-readable database)
- Emails only *new* books — previously emailed books are never repeated
- Highlights the "Book of the Fortnight" (highest composite score)
- Groups books by category in the email
- Generates a Polish "why read this" blurb via Gemini AI (free tier)
- Includes Empik.com search links for every book
- Runs automatically via GitHub Actions — no server needed

**Cost: entirely free.**

---

## One-Time Setup (~45 minutes)

### Step 1 — Google Cloud (Sheets API)

1. Go to [console.cloud.google.com](https://console.cloud.google.com) → **New Project** → name it `book-discovery`
2. **APIs & Services → Enable APIs** → search and enable:
   - **Google Sheets API**
   - **Google Drive API**
3. **APIs & Services → Credentials → Create Credentials → Service Account**
   - Name: `book-bot` → Create → Done
4. Click the created service account → **Keys** tab → **Add Key → Create new key → JSON**
   - Download the JSON file. **Do not commit it to git.**
5. Copy the `client_email` value from the JSON (looks like `book-bot@project-id.iam.gserviceaccount.com`)

### Step 2 — Google Sheet

1. Go to [sheets.google.com](https://sheets.google.com) → create a new spreadsheet
2. Name it: **Book Discovery Database**
3. **Share** the sheet with the service account email (from Step 1) → **Editor** access
4. Copy the spreadsheet ID from the URL:
   ```
   https://docs.google.com/spreadsheets/d/THIS_IS_THE_ID/edit
   ```

### Step 3 — Gmail App Password

1. Go to [myaccount.google.com/security](https://myaccount.google.com/security)
2. Ensure **2-Step Verification** is ON
3. Search for **App Passwords** → Create → name it `Book Bot` → copy the 16-character password

### Step 4 — Gemini API Key (free)

1. Go to [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
2. Sign in → **Create API key** → copy it

### Step 5 — Make the Repository Public

GitHub Actions is free with unlimited minutes on **public** repositories.

Go to **Settings → General → Danger Zone → Change visibility → Public**

### Step 6 — Add GitHub Secrets

Go to your repo → **Settings → Secrets and variables → Actions → New repository secret**

Add these 6 secrets:

| Secret name | Value |
|---|---|
| `GOOGLE_SHEETS_CREDENTIALS_JSON` | Full contents of the service account JSON file |
| `GOOGLE_SHEET_ID` | The spreadsheet ID from Step 2 |
| `GMAIL_USER` | Your Gmail address |
| `GMAIL_APP_PASSWORD` | The 16-character App Password from Step 3 |
| `RECIPIENT_EMAIL` | Email address to receive digests (can be same as above) |
| `GEMINI_API_KEY` | API key from Step 4 |

### Step 7 — Initialize the Database

Run the setup script once **locally** (or skip to Step 8 and let the first GitHub Actions run do it):

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables (or create a .env file — see .env.example)
export GOOGLE_SHEETS_CREDENTIALS_JSON='{ ... full JSON ... }'
export GOOGLE_SHEET_ID='your_sheet_id'
# ... etc.

# Create the sheet tabs and headers
python scripts/setup_sheet.py
```

### Step 8 — Test Run

1. Go to your repo → **Actions** tab → **Book Discovery** workflow
2. Click **Run workflow** → **Run workflow**
3. Watch the logs — the run takes ~5–10 minutes
4. Check your email and Google Sheet for results

---

## Automatic Schedule

The workflow runs automatically at **08:00 UTC** on the **1st and 15th** of each month.

You can also trigger it manually at any time from the Actions tab.

---

## Customising via Google Sheet

Open the **preferences** tab in your Google Sheet to adjust settings without touching code:

| Key | Default | Description |
|---|---|---|
| `min_rating` | `7.0` | Minimum average rating (0–10) |
| `min_ratings_count` | `20` | Minimum number of ratings |
| `recipient_email` | *(from secret)* | Override the recipient email |

---

## Marking Books as Read

In the **books** tab, find any book and set the `already_read` column to `TRUE`.
That book will never appear in future email digests.

---

## Project Structure

```
book_discovery/
├── main.py              # Orchestrator
├── scraper.py           # lubimyczytac.pl scraper (curl_cffi + BeautifulSoup)
├── sheets_client.py     # Google Sheets read/write
├── email_sender.py      # Gmail SMTP digest
├── ai_descriptions.py   # Gemini AI blurbs
├── models.py            # Book dataclass
├── config.py            # Constants and Config loader
└── templates/
    ├── email.html       # HTML email template
    └── email.txt        # Plain-text email template

scripts/
└── setup_sheet.py       # One-time sheet initializer

.github/workflows/
└── book_discovery.yml   # GitHub Actions cron workflow
```

---

## Troubleshooting

**Run failed — check the log:**
Go to Actions → the failed run → download the `run-log-N` artifact.

**403 error from lubimyczytac.pl:**
The scraper uses `curl_cffi` to impersonate Chrome. If errors persist, update the `impersonate` version in `scraper.py`.

**Email not received:**
Check Gmail spam folder. Verify the App Password is correct and 2FA is enabled.

**Sheet not updating:**
Confirm the service account email has Editor access to the spreadsheet.
