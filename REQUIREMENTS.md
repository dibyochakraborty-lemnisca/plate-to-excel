# Lab workflow implementation plan

Source: user-provided Image Sample Reading to Output.pdf, one page; clarified live Google Sheet per experiment and permission to move beyond Streamlit.

| Requirement | Implementation | Validation |
| --- | --- | --- |
| Fast phone capture, multiple photos | Camera-first file capture with immediate IndexedDB save | Browser capture/upload queue |
| Slow/offline internet | Cached app shell, device-local IndexedDB queue, explicit retry and reconnect upload | Offline reload, reconnect, duplicate upload tests |
| Saved photos; no accounts (user override) | Shared workspace, persistent SQLite photos and records | Restart persistence tests |
| Experiment records | Unique per-user experiment ID, start time, linked sheet | API and export tests |
| Multiple reusable templates | Named flask subset and replicate/traversal definitions per experiment | Protocol tests and template preview |
| Sequence-based assignment | Bottom/top row order, left/right replicate blocks, starting slot, sample index, plate rollover | Nonstandard grids and rollover tests |
| Editable output | Editable assignments, readings, capture time and sample index; original extraction retained | Validation, revision conflict tests |
| Direct vision LLM | Existing Gemini structured image extraction | Mocked failures plus bounded live sample if available |
| Time vs OD per flask | Reviewed replicate means, SD, real elapsed time, missing groups not averaged silently | Plot data and workbook chart tests |
| Live Google Sheet per experiment | Service account, idempotent managed tabs, automatic sync after writes, retry status | Mocked Sheets contract; live verification requires credentials and shared sheet |
| Excel | Experiment workbook with readings, trajectories/chart, photo provenance and raw grids | openpyxl read-back |

## Delivery choices

Vanilla HTML/CSS/JavaScript PWA, FastAPI, SQLite (WAL) on one persistent disk, one background worker. No frontend build tool. Existing Streamlit app remains runnable but does not provide the new workflows. Use a separate deployment with HTTPS and persistent disk. One worker process/replica, no app accounts; restrict the hosting endpoint to the lab if records must not be public. Google Sheets uses an existing spreadsheet shared with a service account; the key stays on the server.

## Limitations to make visible

Initial app load and experiment/template setup require internet. Browser storage can be evicted or cleared; queued photos remain local until upload is acknowledged. Offline capture time is device time and can be corrected. Automatic upload happens while the app is open; no claim of background sync on closed iPhones. Concurrent offline devices may assign samples in upload order, so sample index is editable. A photo uses one template; overlapping flask subsets are allowed explicitly. Photos and readings remain on disk across restarts; back up the SQLite database. No public deployment or live Sheets verification is claimed without evidence.
