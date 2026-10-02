# Plate to Excel

<!-- impeccable:product-schema 1 -->

## Platform
web

## Users
Lab staff taking plate-reader photos on phones at sample points, including slow or unavailable internet.

## Product Purpose
Capture once, preserve the photo and timestamp, assign measured wells to flasks and replicates using reusable sequence templates, review readings, and maintain an experiment-specific Google Sheet and OD trajectories.

## Capabilities and Constraints
User approved replacing the Streamlit-only UI with a small installable web app and persistent Python backend. Reuse Gemini gemini-3.8-flash vision extraction. User explicitly removed login/account requirements. One shared lab workspace retrieves experiments, photographs, templates and edits. Local capture must not wait for vision or Google Sheets. Multiple templates per experiment; adjustable flasks, replicate counts and traversal. Google Sheet per experiment is confirmed; Excel remains an export.

## Operating Context
Capture is the primary action. Timestamps reflect capture time, not upload/processing time. The proposed lab default is bottom-to-top rows, adjacent replicate columns, then blocks to the right; user's reply was "ok". Preserve configurable direction and preview rather than assuming all experiments use that order. Template changes apply to future captures, with explicit remapping of existing photos.

## Evidence on Hand
User-provided one-page PDF: /Users/dibyochakraborty/Downloads/Image Sample Reading to Output.pdf. Earlier sample image shows an 8-by-12 grid but the software must support arbitrary labeled grids. Existing application and tests are in this repository. No live Google service-account credentials or target sheet have been provided.

## Product Principles
- Save photos before processing; make pending and synced states unambiguous.
- Never replace unreadable measurements with guesses or zeroes.
- Preview well correspondence and preserve original readings when edited.
- Keep phone capture fast and defer configuration to experiment setup.
- Use reviewed measurements for trajectories; disclose missing replicates.
