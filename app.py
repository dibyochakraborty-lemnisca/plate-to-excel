import hashlib
import os
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from google.genai.errors import APIError
from httpx import HTTPError

from extraction import export_workbook, extract_plate, normalize_image


st.set_page_config(page_title="Plate to Excel", layout="centered")
st.title("Plate to Excel")
st.write("Capture or upload your plate readings. Keep every value in its original row and column.")

load_dotenv(Path(__file__).with_name(".env"))
api_key = os.getenv("GEMINI_API_KEY", "")
model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
if not api_key:
    try:
        api_key = st.secrets.get("GEMINI_API_KEY", "")
        model = st.secrets.get("GEMINI_MODEL", model)
    except FileNotFoundError:
        pass
if not api_key:
    st.info("Add GEMINI_API_KEY to .env locally or the hosting app's Secrets settings, then restart the app.")

source = st.radio("Add photos", ["Take photo", "Upload photos"], horizontal=True)
if source == "Upload photos":
    photos = st.file_uploader("Choose plate photos", type=["jpg", "jpeg", "png", "webp"], accept_multiple_files=True)
else:
    st.caption("Allow camera access. On a phone, open the app over HTTPS to use the camera.")
    st.session_state.setdefault("captures", [])
    st.session_state.setdefault("capture_number", 1)
    photo = st.camera_input("Capture the plate grid", key=f"camera-{st.session_state.capture_number}")
    if st.button("Add photo", disabled=photo is None):
        try:
            normalize_image(photo.getvalue())
        except ValueError as exc:
            st.error(str(exc))
        else:
            saved = BytesIO(photo.getvalue())
            saved.name = f"Capture {st.session_state.capture_number}.jpg"
            st.session_state.captures.append(saved)
            st.session_state.capture_number += 1
            st.rerun()
    photos = st.session_state.captures
    st.caption(f"{len(photos)} photos added. Take a photo and press Add photo for each plate, then Extract values.")
    if photos and st.button("Clear captured photos"):
        st.session_state.captures = []
        st.session_state.capture_number += 1
        st.rerun()

if not photos:
    st.session_state.pop("results", None)
    st.caption("Include all row and column labels. Avoid glare and keep the camera square to the screen.")
    st.stop()

photo_ids = [(photo.name, hashlib.sha256(photo.getvalue()).hexdigest()) for photo in photos]
if st.session_state.get("photo_ids") != photo_ids:
    st.session_state.pop("results", None)
    st.session_state.photo_ids = photo_ids

images = []
for photo in photos:
    try:
        images.append(normalize_image(photo.getvalue()))
    except ValueError as exc:
        st.error(f"{photo.name}: {exc}")
        st.stop()

st.caption("Extract values sends each photo to Gemini. Each photo becomes a separate worksheet in one Excel file.")
if st.button("Extract values", type="primary", disabled=not api_key):
    st.session_state.results = {}
    st.session_state.revision = st.session_state.get("revision", 0) + 1
    for index, (photo, image) in enumerate(zip(photos, images)):
        try:
            with st.spinner(f"Reading photo {index + 1} of {len(photos)}: {photo.name}…"):
                st.session_state.results[index] = extract_plate(image, api_key, model)
        except (APIError, HTTPError):
            st.session_state.results[index] = "Gemini could not complete the request. Check your key, model access, connection, and credits, then retry."
        except ValueError as exc:
            st.session_state.results[index] = str(exc)

sheets = []
for index, (photo, image) in enumerate(zip(photos, images)):
    with st.expander(photo.name, expanded=len(photos) == 1):
        st.image(image, caption="Source photo", width="stretch")
        if source == "Take photo" and st.button("Remove photo", key=f"remove-{photo.name}"):
            st.session_state.captures.pop(index)
            st.rerun()
        plate = st.session_state.get("results", {}).get(index)
        if isinstance(plate, str):
            st.error(plate)
            continue
        if plate is None:
            continue
        readings = {(well.row, well.column): well.value for well in plate.wells}
        unclear = {(well.row, well.column) for well in plate.wells if well.status == "unclear"}
        st.caption("Check the detected labels and numbers against the photo. Edit any value before downloading.")
        if unclear:
            st.warning("Unclear readings left blank: " + ", ".join(f"{row}/{column}" for row, column in sorted(unclear)))
        frame = pd.DataFrame(
            [[readings[row, column] for column in plate.columns] for row in plate.rows],
            index=plate.rows, columns=plate.columns, dtype=float,
        )
        edited = st.data_editor(
            frame, key=f"grid-{index}-{photo_ids[index][1]}-{st.session_state.revision}",
            column_config={column: st.column_config.NumberColumn(column, format="%.3f") for column in plate.columns},
            width="stretch",
        )
        values = [[None if pd.isna(value) else float(value) for value in row] for row in edited.to_numpy()]
        sheets.append((Path(photo.name).stem, values, unclear, plate.rows, plate.columns))

if sheets:
    if len(sheets) != len(photos):
        st.warning(f"Only {len(sheets)} of {len(photos)} photos were extracted. Failed photos are excluded from the workbook.")
    st.download_button(
        "Download Excel", data=export_workbook(sheets), file_name="plate-readings.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary",
    )
