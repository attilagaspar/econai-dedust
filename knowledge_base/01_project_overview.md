# Project overview

## What it is

**EconAI (econai-dedust)** is a browser-based, human-in-the-loop pipeline for digitizing historical economic documents — mostly Hungarian statistical yearbooks, censuses, land registers, company registers and exhibition catalogs from ~1890–1949. It turns scanned pages into structured, ID-linked datasets.

It is a *research tool built by a researcher* (Attila Gáspár, CEU), operated by the author and research assistants. It is not a product; its measure of success is **hours of human time per thousand clean data rows**.

## Why it exists

The datasets feed identified empirical-economics projects:
- **Techxtremism / "Machines Against Men"** — agricultural mechanization → strikes → far-right vote, Hungary 1895–1939. Needs the 1935 land/machine census (`foldbirtok1935`, `machines1935`), labor-market tables (`munkaeropiac`), 1949 census (`eletkor1949`).
- **Ethnic occupational specialization** in Austria-Hungary — firm/industry data (`firms_by_settlement*`, `firms_by_county_industry_1890`).
- **1900 Paris Exhibition firm catalog** (`product_catalog*`) — structured firm records with HS-coded products.
- **Gazetteer work** (`helysegnevtar_1933`) — future 1933/1935 place-authority layer.

A core design idea: resolve strings to **canonical entity IDs at annotation time** (places, industries; later firms) so heterogeneous sources join on stable IDs instead of ad-hoc fuzzy matching in Stata afterwards.

## The two document types

- **Type A — tables**: layout model detects cells; a "lattice" (superstructure) groups them into a printed grid; each cell may carry an *internal row structure* (one line per settlement etc.) with per-row OCR/LLM/Human readings.
- **Type B — structured text**: free annotations (e.g. one firm record) go through LLM extraction into schema-conforming JSON objects.

## Pipeline in one line

Import PDF → annotate sample → train Detectron2 layout model on a remote GPU (Docker over SSH) → infer on all pages → human corrects boxes → lattice detection → OCR (Tesseract/EasyOCR) + PDF text layer → LLM cleaning (OpenAI/Azure/local) → rule validation & LLM-assisted fixing → authority resolution → export (Excel with layout/Resolved/Structured sheets, or JSON records).

## Content layers

Every reading exists in up to four layers with fixed priority **Human > LLM > OCR > PDF**. The Human layer means "a person verified this" and is never written silently by automation — LLM fixes go to the LLM layer, human edits (including edits of LLM proposals in the rule-fix dialog) go to Human.

## People / environment

- Runs locally on the author's Windows laptop (`python econai.py serve`, FastAPI + static HTML).
- Project data lives inside the repo folder under `projects/<name>/` but is gitignored; the repo folder itself is in Dropbox (so data syncs via Dropbox, code via git — RAs also use Dropbox copies).
- GPU work happens on remote servers (`gpu.koren.work`, a "TK GPU" server) via SSH + Docker; an assistant (Mátyás) also trains/infers.
- Authorities (`authorities/*.authority.json`) are git-tracked shared reference data.
