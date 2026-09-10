"""Acquire additional official PPID mirrors of Kalteng Posko Karhutla reports.

This expansion corpus is intentionally kept separate from the original workbook
sources.  Each download is verified as a readable PDF, hashed, and extracted
page-by-page before it can enter the incident-review queue.  Duplicate files
are recorded, never silently treated as independent source documents.
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
OUT = ROOT / "data/raw/kalteng_historical_deployments/expansion_documents"
TEXT_OUT = ROOT / "data/interim/kalteng_historical_deployments/expansion_text"
MANIFEST = ROOT / "data/raw/kalteng_historical_deployments/expansion_acquisition_manifest.json"

# URLs were discovered through official government PPID portals.  The document
# content identifies BPBPK/Pusdalops Kalteng as the originating operational
# reporting context; this is checked again during manual incident review.
SOURCES = (
    {
        "document_id": "ppid_kemendagri_GsCCvcll",
        "url": "https://ppid.kemendagri.go.id/storage/dokumen/GsCCvcllFQctovfn6qZH4hc9eFKnx6c5ckPHzYQ6.pdf",
        "publisher": "PPID Kementerian Dalam Negeri",
        "year_hint": 2019,
    },
    {
        "document_id": "ppid_kemendagri_7WWeUbX0",
        "url": "https://ppid.kemendagri.go.id/storage/dokumen/7WWeUbX0tiwDaCVC08WC4OxnwVQ5GigtPZ7amZ5a.pdf",
        "publisher": "PPID Kementerian Dalam Negeri",
        "year_hint": 2018,
    },
    {
        "document_id": "ppid_kemendagri_hnS3t7HI",
        "url": "https://ppid.kemendagri.go.id/storage/dokumen/hnS3t7HI52ZYSfDkV21YciXIcDPD4otT53udcDKR.pdf",
        "publisher": "PPID Kementerian Dalam Negeri",
        "year_hint": 2019,
    },
    {
        "document_id": "ppid_babelprov_WyvnKyCo",
        "url": "https://ppid.babelprov.go.id/storage/dokumen/WyvnKyCou2Gn10auTCwO5P09uI16N2tRbQ9fMYvX.pdf",
        "publisher": "PPID Provinsi Kepulauan Bangka Belitung",
        "year_hint": 2019,
    },
    {
        "document_id": "ppid_lamandau_l13gOGur",
        "url": "https://ppid.lamandaukab.go.id/storage/dokumen/l13gOGur2KA6DD9y4VzH1L2vC9WV9FWauEhxeV02.pdf",
        "publisher": "PPID Kabupaten Lamandau",
        "year_hint": 2019,
    },
    {
        "document_id": "ppid_gorontalo_wCzKfyk1",
        "url": "https://e-ppid.gorontaloprov.go.id/storage/dokumen/wCzKfyk1huOFW8SY9pQu58mqaoxB6V1ghArl6dTE.pdf",
        "publisher": "PPID Provinsi Gorontalo",
        "year_hint": 2019,
    },
)


def validate_and_extract(payload: bytes, destination: Path) -> tuple[int, int]:
    if not payload.startswith(b"%PDF-"):
        raise ValueError("response is not a PDF")
    destination.write_bytes(payload)
    try:
        reader = PdfReader(destination)
        page_text = [page.extract_text() or "" for page in reader.pages]
        if not any(text.strip() for text in page_text):
            raise ValueError("PDF has no extractable text; requires separate OCR review")
        TEXT_OUT.mkdir(parents=True, exist_ok=True)
        (TEXT_OUT / f"{destination.stem}.txt").write_text(
            "\n\n".join(
                f"=== PAGE {number} ===\n{text}" for number, text in enumerate(page_text, start=1)
            ),
            encoding="utf-8",
        )
        return len(reader.pages), sum(bool(text.strip()) for text in page_text)
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "Kalimantan-FireWatch-research/1.0"
    known_hashes: dict[str, str] = {}
    records: list[dict[str, object]] = []
    for source in SOURCES:
        target = OUT / f"{source['document_id']}.pdf"
        record: dict[str, object] = {
            "document_id": source["document_id"],
            "source_document_url": source["url"],
            "publisher": source["publisher"],
            "originating_agency_expected": "BPBPK/Pusdalops Provinsi Kalimantan Tengah, subject to document review",
            "document_year_hint": source["year_hint"],
            "local_path": str(target.relative_to(ROOT)),
        }
        try:
            payload = target.read_bytes() if target.exists() and not args.force else session.get(
                str(source["url"]), timeout=120
            ).content
            digest = hashlib.sha256(payload).hexdigest()
            pages, text_pages = validate_and_extract(payload, target)
            record.update(
                {
                    "status": "verified_pdf",
                    "sha256": digest,
                    "bytes": len(payload),
                    "page_count": pages,
                    "text_bearing_page_count": text_pages,
                }
            )
            if digest in known_hashes:
                record["duplicate_of_document_id"] = known_hashes[digest]
            else:
                known_hashes[digest] = str(source["document_id"])
        except Exception as error:
            record.update({"status": "failed", "error": str(error)})
        records.append(record)
        print(source["document_id"], record["status"])
    MANIFEST.write_text(
        json.dumps(
            {
                "dataset": "Kalteng historical karhutla deployment expansion source corpus",
                "acquired_at_utc": datetime.now(UTC).isoformat(),
                "source_policy": "official government PPID mirrors only; readable PDF, hash and page text retained",
                "admission_policy": "only page-verified incident-level resource mappings may become model labels",
                "documents": records,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(MANIFEST.relative_to(ROOT))


if __name__ == "__main__":
    main()
