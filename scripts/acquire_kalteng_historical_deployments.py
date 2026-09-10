"""Acquire a reproducible seed corpus of official Kalteng deployment reports.

These are source documents, not a ready-made incident table.  The downloader
keeps original PDFs and their hashes so every later extraction can be audited.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import requests
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/raw/kalteng_historical_deployments/documents"
TEXT_OUT = ROOT / "data/interim/kalteng_historical_deployments/extracted_text"
MANIFEST = ROOT / "data/raw/kalteng_historical_deployments/acquisition_manifest.json"

# Official PPID Kemendagri copies. The report text attributes the operational
# content to BPBPK/Pusdalops Kalimantan Tengah. This seed is explicitly not
# claimed to be the complete archive.
SOURCES = (
    ("kemendagri_ppid_5c5Bttsh", "https://ppid.kemendagri.go.id/storage/dokumen/5c5BttshgnnrdIMbARNAimlFm8TepbUXPRBjaADG.pdf", 2019),
    ("kemendagri_ppid_2IIueVEW", "https://ppid.kemendagri.go.id/storage/dokumen/2IIueVEWXsbCypfVFPwuPGc1ijV6gv63Uamdtv5J.pdf", None),
    ("kemendagri_ppid_CEeYYckC", "https://ppid.kemendagri.go.id/storage/dokumen/CEeYYckCBcfaxp8e3yuO51xUQE0RfbL2BAeS1IfX.pdf", None),
    ("kemendagri_ppid_bZ3oGAy7", "https://ppid.kemendagri.go.id/storage/dokumen/bZ3oGAy7dIGwENxVW06aC0aMRKVb5LPBp9HnwCyZ.pdf", 2018),
    ("kemendagri_ppid_Bzeu4jwV", "https://ppid.kemendagri.go.id/storage/dokumen/Bzeu4jwVnVHTXhRvq5hNeqs2AdvTOCBNCtAmkaTy.pdf", None),
    ("kemendagri_ppid_QAT3WnjC", "https://ppid.kemendagri.go.id/storage/dokumen/QAT3WnjCOV6myLrqt6TaO6n2bNKVkEpUUvcWvMRH.pdf", 2018),
    ("kemendagri_ppid_6PZPkibk", "https://ppid.kemendagri.go.id/storage/dokumen/6PZPkibkUaisH24IFliV7FVSiIairLw6zTtSpGjg.pdf", 2018),
    ("kemendagri_ppid_qOBVbpMG", "https://ppid.kemendagri.go.id/storage/dokumen/qOBVbpMGK1VhXnbkjdfYgAFDNveyeVrFeg1Omkgy.pdf", 2018),
)


def inspect_pdf(payload: bytes, path: Path) -> tuple[int, int]:
    if not payload.startswith(b"%PDF-"):
        raise ValueError("response is not a PDF")
    path.write_bytes(payload)
    try:
        pdf = PdfReader(path)
        pages = len(pdf.pages)
        TEXT_OUT.mkdir(parents=True, exist_ok=True)
        text_path = TEXT_OUT / f"{path.stem}.txt"
        if text_path.exists() and text_path.stat().st_size:
            text_pages = sum(line.startswith("=== PAGE ") for line in text_path.read_text(encoding="utf-8", errors="replace").splitlines())
        else:
            page_text = [page.extract_text() or "" for page in pdf.pages]
            text_pages = sum(bool(text.strip()) for text in page_text)
            text_path.write_text(
                "\n\n".join(f"=== PAGE {number} ===\n{text}" for number, text in enumerate(page_text, start=1)),
                encoding="utf-8",
            )
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return pages, text_pages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    http = requests.Session()
    http.headers["User-Agent"] = "Kalimantan-FireWatch-research/1.0"
    documents = []
    for document_id, url, year in SOURCES:
        target = OUT / f"{document_id}.pdf"
        entry = {
            "document_id": document_id,
            "source_document_url": url,
            "publisher": "PPID Kementerian Dalam Negeri",
            "originating_agency": "BPBPK/Pusdalops Provinsi Kalimantan Tengah (as stated in report)",
            "document_year_hint": year,
            "local_path": str(target.relative_to(ROOT)),
        }
        try:
            payload = target.read_bytes() if target.exists() and not args.force else http.get(url, timeout=120).content
            pages, text_pages = inspect_pdf(payload, target)
            entry.update({
                "status": "verified_pdf",
                "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload),
                "page_count": pages,
                "text_bearing_page_count": text_pages,
            })
        except Exception as error:
            entry.update({"status": "failed", "error": str(error)})
        documents.append(entry)
        print(document_id, entry["status"])
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps({
        "dataset": "Kalteng historical karhutla deployment source corpus",
        "acquired_at_utc": datetime.now(UTC).isoformat(),
        "source_policy": "official PPID documents only; original PDF and SHA-256 retained",
        "known_limitations": [
            "A document can describe multiple incidents or aggregate a Poslap's resources.",
            "Only explicit incident-resource mappings are training eligible.",
            "This seed corpus is not the complete provincial archive.",
        ],
        "documents": documents,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(MANIFEST.relative_to(ROOT))


if __name__ == "__main__":
    main()
