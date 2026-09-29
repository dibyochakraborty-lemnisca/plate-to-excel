# Plate to Excel

A small Streamlit UI: upload one or more photos or capture one with your camera, extract the circled readings with Gemini vision, review/edit the grid, and download an Excel workbook.

## Run

Requires Python 3.11 or newer and a Gemini API key with access to gemini-3.8-flash.

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Before starting, create `.env` next to `app.py` containing `GEMINI_API_KEY=your-api-key`. The app loads it automatically; an existing environment variable takes precedence. `.env` is ignored by Git.

Open http://localhost:8501. The key stays on the server. The default model is `gemini-3.8-flash`; override with `GEMINI_MODEL` using a model supporting image inputs and structured outputs. Each click of **Extract values** makes one API request per photo. The downloaded workbook contains one worksheet per successful photo, named after its file; failed photos are explicitly flagged and excluded. Changing the selected files resets the review results. Editing and downloading do not make API calls.

Camera capture requires browser permission and localhost or HTTPS. For phone access, host this app over HTTPS; a plain HTTP LAN address will not enable camera access. JPEG, PNG and WebP uploads are supported (20 MB / 25 megapixel maximum); convert HEIC first.

**Take photo** opens by default. Capture a plate, press **Add photo**, and repeat for additional plates. Then click **Extract values** to review all added photos and download one workbook with a worksheet per capture. You can remove individual captures or clear the queue. Captures remain in the current session when switching upload/camera modes; reloading or closing the session can discard them.

## Mapping

The row and column labels are detected from each image, with no fixed A–H or 1–12 layout. Their visible order is preserved, including rows beyond H, multi-letter labels, and columns beyond 12. Include readable headers in the photo. The workbook has row and column headers, so in the sample layout image coordinate A1 goes to Excel B2, A12 to M2, and D12 to M5. Empty positions stay blank. Unreadable positions stay blank and are highlighted with a comment in Excel unless corrected in the review table. Values are numeric, not strings. AI extraction can make mistakes: review the grid before using it.

Photos and results stay in app memory, with no application file storage; the photo is transmitted to Gemini for extraction. Image text is treated as data, never as instructions.

API implementation follows the official [image input](https://ai.google.dev/gemini-api/docs/image-understanding) and [structured output](https://ai.google.dev/gemini-api/docs/structured-output) documentation.

## Check

```sh
python -m unittest test_extraction.py
```

## Deploy on Streamlit Community Cloud

At https://share.streamlit.io, create an app from `Lemniscabio/plate-to-excel`, branch `main`, entrypoint `app.py`. In Advanced settings, select Python 3.12 and add the following in Secrets, replacing the placeholder with your key:

```toml
GEMINI_API_KEY = "your-key"
GEMINI_MODEL = "gemini-3.8-flash"
```

Keep the app private for team use. Community Cloud provides HTTPS for phone camera access. Do not upload `.env` to GitHub. Local `.env` and hosted Streamlit secrets are both supported.
