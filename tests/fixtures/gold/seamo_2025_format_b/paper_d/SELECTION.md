# Paper D gold page selection

Source PDF: `backend/examples/seamo-2025-format-b-split/seamo-2025-format-b-paper-d.pdf` (56 pages).
Rendered with `backend.services.pdf_to_images.PDFConverter` at **200 DPI**.
Quality scored via Laplacian variance (sharpness), gray std (contrast), Hough/`minAreaRect` tilt (skew).

`page_map.txt` maps D split pages → full PDF `source_page`. Historical LLM timeout / empty-header
band in the full PDF was **source pages 127–138** → D split **0-based indices 19–30** (1-based D20–D31).

## Selected pages (exactly 5)

| Fixture | Split idx0 | D 1-based | Full source_page | Role | Sharp | Contrast | Tilt° | Why |
|---------|------------|-----------|------------------|------|-------|----------|-------|-----|
| `page_001` | 13 | 14 | 121 | clean | 434.6 | 56.1 | 0.26 | Clean baseline: high sharpness (434.6) and contrast (56.1), low tilt, crisp header/MCQ. Source page 121 (outside timeout band). |
| `page_002` | 14 | 15 | 122 | bad_washed | 240.9 | 36.6 | 0.08 | Worst quality in split: washed background (mean~241), lowest contrast (36.6), reddish grain/noise. Visibly shit scan for stress-testing header OCR and bubble fill detection. |
| `page_003` | 11 | 12 | 119 | bad_skew_soft | 329.3 | 52.7 | 1.87 | Visibly soft/blurry with ~1.9° content tilt and compression fuzz. Second-worst composite score; exercises deskew + faint bubble edges. |
| `page_004` | 19 | 20 | 127 | timeout_band_skew | 428.9 | 56.7 | 2.49 | Maps to full-PDF source_page=127 (start of historical LLM timeout / empty-header band 127–138). Also among highest measured tilts (~2.5°). |
| `page_005` | 28 | 29 | 136 | timeout_band_soft | 277.3 | 51.7 | 0.27 | Maps to full-PDF source_page=136 (inside timeout band). Soft/noisy scan; worst relative quality among band pages 127–138 — keep for regression on historically flaky LLM pages. |

## Mix rationale

- **≥2 visibly bad:** `page_002` (washed/low-contrast grain) and `page_003` (soft + skewed).
- **1–2 clean:** `page_001` is the clean baseline.
- **Timeout-band coverage:** `page_004` (src 127) and `page_005` (src 136) sit inside the historical 127–138 band.

## Stub labels

JSON stubs use `template_id=seamo_2025_d_fb`, `page_id=paper_d/page_00N`, and `source.page_index`
as the **0-based** index into the D split PDF. `answers` / `header` are null placeholders for hand-labeling.

## Files

```
tests/fixtures/gold/seamo_2025_format_b/paper_d/
  page_001.png … page_005.png
  page_001.json … page_005.json
  manifest.json
  SELECTION.md
```
