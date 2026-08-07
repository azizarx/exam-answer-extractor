import cv2
import numpy as np
import json
from backend.services.pdf_to_images import get_pdf_converter

# ==========  Load reference grid ==========
with open("page1_grid.json") as f:
    ref = json.load(f)
ref_cols = ref["columns"]
med_w    = ref["median_w"]
med_h    = ref["median_h"]
bubble_h = ref["bubble_h"]

# Top anchor
TOP_X, TOP_Y, TOP_W, TOP_H = 134, 989, 695, 74

# Offset of first row label top (from page 1 calibration)
OFFSET_FIRST_ROW = ref["rows"][0] - TOP_Y   # e.g., 1121 - 989 = 132

# Row pitch from GIMP measurements (bubble centres)
ROW_PITCH = (2777 - 1170) / 19   # ≈ 84.5789

# Manual vertical tweak – shift everything down by 18 px (bubble height)
Y_ADJUST = 18

# ==========  PDF to images ==========
pdf_path = "backend/examples/UZ1-35.pdf"
converter = get_pdf_converter(dpi=300)
image_paths = converter.convert_from_file(pdf_path)

# Crop template from page 1
page1 = cv2.imread(image_paths[0])
top_tpl = page1[TOP_Y:TOP_Y+TOP_H, TOP_X:TOP_X+TOP_W].copy()

all_answers = {}

for page_idx, img_path in enumerate(image_paths):
    print(f"Processing page {page_idx+1}...")
    img = cv2.imread(img_path)
    h, w = img.shape[:2]

    # --- Match top anchor ---
    res = cv2.matchTemplate(img, top_tpl, cv2.TM_CCOEFF_NORMED)
    _, top_val, _, top_loc = cv2.minMaxLoc(res)
    if top_val < 0.5:
        print(f"  Top match too low ({top_val:.2f}) – skip")
        continue
    dx = top_loc[0] - TOP_X
    top_y = top_loc[1]

    # --- Row positions (label tops) with manual vertical adjustment ---
    first_row_y = top_y + OFFSET_FIRST_ROW + Y_ADJUST
    rows = [int(first_row_y + i * ROW_PITCH) for i in range(20)]

    # --- Shift columns horizontally ---
    shifted_cols = [c + dx for c in ref_cols]

    # --- Ink‑density extraction ---
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, bin_inv = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)

    page_ans = {}
    labels = ["A", "B", "C", "D", "E"]
    for row_idx, y in enumerate(rows):
        scores = {}
        for col_idx, x in enumerate(shifted_cols):
            x1 = int(x - med_w/2)
            y1 = y + med_h
            x2 = int(x + med_w/2)
            y2 = y + med_h + bubble_h
            x1 = max(0, x1); y1 = max(0, y1)
            x2 = min(w, x2); y2 = min(h, y2)
            if x2 <= x1 or y2 <= y1:
                scores[labels[col_idx]] = 0
                continue
            roi = bin_inv[y1:y2, x1:x2]
            scores[labels[col_idx]] = cv2.countNonZero(roi)
        best = max(scores, key=scores.get)
        page_ans[str(row_idx+1)] = best

    all_answers[f"page_{page_idx+1}"] = page_ans

    # ---- Debug image ----
    debug = img.copy()
    for row_idx, y in enumerate(rows):
        for col_idx, x in enumerate(shifted_cols):
            x1 = int(x - med_w/2)
            y1 = y + med_h
            x2 = int(x + med_w/2)
            y2 = y + med_h + bubble_h
            cv2.rectangle(debug, (x1, y1), (x2, y2), (0,255,0), 1)
            cv2.putText(debug, f"{row_idx+1}{labels[col_idx]}",
                        (x1, y1-5), cv2.FONT_HERSHEY_SIMPLEX, 0.3, (0,255,0), 1)
    cv2.imwrite(f"debug_geo_{page_idx+1}.png", debug)

with open("extracted_answers.json", "w") as f:
    json.dump(all_answers, f, indent=4)
print("✔ Saved to extracted_answers.json")