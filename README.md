# Plate to Excel — lab workspace

A camera-first web app for collecting plate-reader photos, mapping wells to flask replicates, reviewing OD readings, plotting trajectories, and syncing an experiment to Google Sheets. No accounts or login.

## Run the lab app

Python 3.12 recommended.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 8080 --workers 1
```

Create `.env` next to `server.py`:

```dotenv
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini-3.8-flash
DATA_DIR=./data
```

Open http://localhost:8080. Phone camera capture, installation and offline page loading require HTTPS when accessing from another device. Keep one server process/replica: SQLite and the durable processing worker are deliberately single-instance.

## Lab workflow

1. Create an experiment ID and start time; link its Google Sheet URL.
2. Add a reading template: first flask number, flask count, replicates and traversal order. Preview the sequence with an example grid. Multiple templates support subsets such as FL-01–08 and FL-09–16. Duplicate a template to change future captures without rewriting past protocols.
3. **Take photo** or upload multiple images. Each photo saves to this device before upload. Continue capturing without waiting for vision. A successful server acknowledgment removes its local copy; the server retains the photo. Uploads retry when the open app reconnects, or via **Upload saved photos**.
4. **Extract pending photos** starts durable queued Gemini jobs. A failed image can be retried without duplicating the photo or earlier successful work.
5. Review well assignments and readings beside the original photo, correct the capture time, or remap the template/sample number. Save the reviewed readings. Original extraction and prior saved edits remain in the database.
6. **Trajectories** displays a simple line plot per flask, time in hours vs mean OD. All expected replicates must be present for a mean; missing groups create gaps. The table includes replicate count and sample SD.
7. Linked Google Sheets update after uploads, extraction and review. Sync failure never discards readings. Use **Retry sync** after fixing access. **Download Excel** exports readings, provenance, raw grids and line plots.

Default sequence: bottom-to-top rows, adjacent columns for replicates, then the next block to the right. Flasks consume slots in sequence; subsequent samples continue where the preceding sample stopped. When a complete sample no longer fits, start a new plate. Adjust direction and initial skipped slots per template. Review the preview against the lab's protocol.

Photos are assigned sample numbers in upload order per template. Within a device, queued captures upload oldest first. With multiple offline devices, check and adjust sample numbers during review. A photo uses one template. Uploaded images are timestamped when added; the app does not infer the original experiment time from file metadata.

## Google Sheets authentication

The deployment needs a Google service account with the target spreadsheet shared to its email as **Editor**. Enable the Sheets API in its project. The app edits only its own `Plate <experiment UUID prefix> Readings`, `Trajectories`, and `Photos` tabs. Sheet cell updates are atomic and repeatable; retries replace these managed tabs' data instead of appending duplicates. Other tabs are untouched. Make measurement edits in the app because managed tabs are regenerated. Each flask receives a line-connected scatter chart using real elapsed hours.

Supported credential modes, in order:

- `GOOGLE_SERVICE_ACCOUNT_JSON`: secret JSON supplied by the host.
- `GOOGLE_SERVICE_ACCOUNT_FILE`: path to a private JSON file outside Git.
- Local keyless access: `SHEETS_IMPERSONATE_ACCOUNT` and `SHEETS_GCLOUD_ACCOUNT`, using an authenticated `gcloud` installation. The user must have `roles/iam.serviceAccountTokenCreator` on that one service account.
- Google Cloud hosting: application default credentials from the attached service account, scoped for Sheets. No downloaded key required.

The Lemnisca project blocks downloadable keys. Use keyless credentials for this setup; don't change the organization policy. The existing local `.env` is configured for keyless access and is excluded from Git. API keys, private credentials, photos and the database must never be committed.

## Deploy

This lab app replaces the Streamlit-only runtime for the new workflows. It requires **HTTPS plus a persistent local disk**. A single Google Compute Engine VM running the included Docker image, with the Sheets service account attached and a persistent disk mounted at `/data`, supports keyless authentication. The instance OAuth scopes must include `https://www.googleapis.com/auth/spreadsheets`; allow Gemini outbound HTTPS. Put an HTTPS reverse proxy in front of port 8080. Keep the disk and database backups across deployments.

```sh
docker build -t plate-lab .
docker run --restart unless-stopped -p 8080:8080 --env-file .env -v plate-lab-data:/data -e DATA_DIR=/data plate-lab
```

For container hosting, configure native Google Cloud credentials or supported credential secrets; the image does not include the local `gcloud` CLI. Do not place SQLite on an ephemeral filesystem or object-storage mount. Run only one replica. The workspace has no application login as requested: anyone who can reach the endpoint can see/edit its experiments, photos and use its Gemini key. Restrict network/hosting access if it should only be available to the lab.

The old Streamlit entrypoint remains available via `streamlit run app.py` for the original photo-to-workbook utility. It does not implement persistent experiments, offline capture, templates or Sheets sync. Pushing this code to a Streamlit deployment will not switch it to the new app.

## Verification

```sh
python -m unittest test_lab.py test_extraction.py
pip install -r requirements-dev.txt
python -m playwright install chromium
LAB_TEST_URL=http://127.0.0.1:8080 python test_browser.py
```

Use a disposable database for the browser check; it creates a test experiment and two small test photos, without invoking Gemini. The browser check covers offline capture/reload, reconnect, idempotent upload, multiple templates and mobile/desktop layout. Core tests cover mapping, rollover, timestamp preservation, correction history, malformed inputs, retries, missing replicates, XLSX and Sheets request structure.

## Operational limits

The first app load, experiment/template setup and saved-photo review need connectivity. Previously opened experiments remain available for offline capture. Local browser storage can be evicted, especially in private mode; don't clear browser data before photos upload. Upload resumes while the app is open, not after iOS closes it. Captures survive server restarts; interrupted extraction requires a deliberate retry to avoid unknowingly repeating billed calls. Browser-native JPEG/PNG/WebP uploads up to 20 MB and 25 megapixels are supported; convert HEIC if your camera does not return JPEG.

Back up `data/lab.sqlite3` using SQLite's backup API while running. The database includes photos and edit history. Capture times use the device clock, stored in UTC; the review UI uses local time. An incorrect device clock must be corrected during review. AI readings and sequence mapping always need review before experimental use.
