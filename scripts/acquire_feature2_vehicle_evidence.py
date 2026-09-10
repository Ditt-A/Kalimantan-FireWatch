"""Acquire official incident evidence that explicitly names response vehicles.

The source list deliberately prefers official PPID PDFs and official police/TNI
publications. News-style web publications are retained as a separate evidence
stream; they are never silently merged into the page-verified Kalteng cohort.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import requests
from pypdf import PdfReader


ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data/raw/feature2_vehicle_evidence"
PDF_DIR = RAW_DIR / "pdf"
HTML_DIR = RAW_DIR / "web"
TEXT_DIR = ROOT / "data/interim/feature2_vehicle_evidence"
MANIFEST_PATH = RAW_DIR / "acquisition_manifest.json"
TIMEOUT_SECONDS = 120

SOURCES = [
    {
        "source_id": "ppid_lampung_I8plypfm",
        "kind": "official_ppid_pdf_mirror",
        "publisher": "PPID Pemerintah Kabupaten Lampung Selatan",
        "originating_agency_expected": "BPBPK/Pusdalops Provinsi Kalimantan Tengah (subject to PDF review)",
        "url": "https://ppid.lampungselatankab.go.id/storage/dokumen/I8plypfmfsVO1rr9hvRERYRVXV3FcUSb2CYfkEfv.pdf",
    },
    {
        "source_id": "ppid_singkawang_kzDeln90",
        "kind": "official_ppid_pdf_mirror",
        "publisher": "PPID Pemerintah Kota Singkawang",
        "originating_agency_expected": "BPBPK/Pusdalops Provinsi Kalimantan Tengah (subject to PDF review)",
        "url": "https://ppid.singkawangkota.go.id/storage/dokumen/kzDeln90azFeka88noJ4T6YmpWkY2KydFgZh5XfL.pdf",
    },
    {
        "source_id": "polreskukar_muara_muntai_20260831",
        "kind": "official_police_web_article",
        "publisher": "Polres Kutai Kartanegara",
        "url": "https://polreskukar.id/2026/08/31/bukan-sekadar-padam-satgas-karhutla-muara-muntai-perkuat-pendinginan-di-lahan-gambut/",
    },
    {
        "source_id": "polreskukar_teluk_dalam_20260903",
        "kind": "official_police_web_article",
        "publisher": "Polres Kutai Kartanegara",
        "url": "https://polreskukar.id/2026/09/03/tim-gabungan-bergerak-cepat-padamkan-karhutla-di-teluk-dalam-api-berhasil-dikendalikan/",
    },
    {
        "source_id": "tribrata_polri_tabang_20260905",
        "kind": "official_police_web_article",
        "publisher": "Tribrata News Polri",
        "url": "https://tribratanews.polri.go.id/blog/nasional-3/tengah-malam-polisi-dan-tim-gabungan-berjibaku-padamkan-karhutla-di-tabang-105463",
    },
    {
        "source_id": "polres_sintang_kelam_permai_20260831",
        "kind": "official_police_web_article",
        "publisher": "Tribrata News Polres Sintang",
        "url": "https://tribratanews.ressintang.kalbar.polri.go.id/kapolres-sintang-pimpin-pemadaman-karhutla-di-kelam-permai/",
    },
    {
        "source_id": "ppid_tniad_gunung_seteleng_20260904",
        "kind": "official_tni_web_article",
        "publisher": "PPID TNI Angkatan Darat",
        "url": "https://ppid.tniad.mil.id/i/39661/babinsa-dan-tim-gabungan-bergerak-cepat-padamkan-karhutla-di-gunung-seteleng",
    },
    {
        "source_id": "polreskukar_muara_muntai_20260828",
        "kind": "official_police_web_article",
        "publisher": "Polres Kutai Kartanegara",
        "url": "https://polreskukar.id/2026/08/28/libatkan-puluhan-personel-polsek-muara-muntai-bersama-tim-gabungan-padamkan-karhutla-25-hektare/",
    },
    {
        "source_id": "polreskukar_sebulu_modern_20260903",
        "kind": "official_police_web_article",
        "publisher": "Polres Kutai Kartanegara",
        "url": "https://polreskukar.id/2026/09/03/karhutla-meluas-2-hektare-di-sebulu-modern-polisi-turun-langsung-tangani-api/",
    },
    {
        "source_id": "polreskukar_loa_janan_20260829",
        "kind": "official_police_web_article",
        "publisher": "Polres Kutai Kartanegara",
        "url": "https://polreskukar.id/2026/08/29/diduga-berawal-dari-bakar-sampah-karhutla-di-loa-janan-meluas-hingga-tebing/",
    },
    {
        "source_id": "haloterkini_sebulu_modern_20260904",
        "kind": "news_web_article",
        "publisher": "Halo Terkini",
        "url": "https://www.haloterkini.com/2026/09/04/tim-gabungan-bergerak-cepat-karhutla-2-hektare-di-sebulu-berhasil-dipadamkan/",
    },
    {
        "source_id": "haloterkini_muara_aloh_20260903",
        "kind": "news_web_article",
        "publisher": "Halo Terkini",
        "url": "https://www.haloterkini.com/2026/09/03/karhutla-di-muara-aloh-membesar-tim-gabungan-berjibaku-selamatkan-permukiman-warga/",
    },
    {
        "source_id": "mc_kalsel_banjarbaru_20260710",
        "kind": "official_government_web_article",
        "publisher": "Media Center Provinsi Kalimantan Selatan",
        "url": "https://diskominfomc.kalselprov.go.id/2026/07/10/trc-bpbd-kalsel-tangani-tiga-titik-karhutla-di-banjarbaru-seluruh-api-berhasil-dikendalikan/",
    },
    {
        "source_id": "polreskukar_tamapole_20260902",
        "kind": "official_police_web_article",
        "publisher": "Polres Kutai Kartanegara",
        "url": "https://polreskukar.id/2026/09/02/karhutla-di-tamapole-ditangani-cepat-petugas-padamkan-api-di-lahan-semak-seluas-2-hektare/",
    },
    {
        "source_id": "polreskukar_teluk_dalam_20260828",
        "kind": "official_police_web_article",
        "publisher": "Polres Kutai Kartanegara",
        "url": "https://polreskukar.id/2026/08/28/gerak-cepat-polsek-muara-jawa-bersama-tim-gabungan-jinakkan-karhutla-seluas-15-hektare/",
    },
    {
        "source_id": "polreskukar_tol_km50_20260902",
        "kind": "official_police_web_article",
        "publisher": "Polres Kutai Kartanegara",
        "url": "https://polreskukar.id/2026/09/02/api-mengancam-hutan-lindung-polisi-dan-tim-gabungan-bergerak-cepat-padamkan-karhutla-di-tol-km-50/",
    },
    {
        "source_id": "polreskukar_wonotirto_20260823",
        "kind": "official_police_web_article",
        "publisher": "Polres Kutai Kartanegara",
        "url": "https://polreskukar.id/2026/08/23/polsek-samboja-bersama-tim-gabungan-padamkan-karhutla-seluas-7-hektare-di-wonotirto/",
    },
]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class TextExtractor(HTMLParser):
    """Small stdlib extractor; provenance retains the untouched HTML separately."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._suppressed_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self._suppressed_depth += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._suppressed_depth:
            self._suppressed_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._suppressed_depth:
            return
        clean = " ".join(unescape(data).split())
        if clean:
            self.parts.append(clean)
            if self._in_title:
                self.title_parts.append(clean)


def html_to_text(content: bytes) -> tuple[str, str | None]:
    parser = TextExtractor()
    parser.feed(content.decode("utf-8", errors="replace"))
    parser.close()
    title = " ".join(parser.title_parts) or None
    return "\n".join(parser.parts), title


def acquire(source: dict[str, str]) -> dict[str, Any]:
    response = requests.get(
        source["url"],
        timeout=TIMEOUT_SECONDS,
        headers={"User-Agent": "compfest-academy-research/1.0 (+non-commercial provenance archive)"},
    )
    response.raise_for_status()
    content = response.content
    common = {
        **source,
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "retrieved_url": str(response.url),
        "http_content_type": response.headers.get("content-type"),
        "sha256": sha256_bytes(content),
        "bytes": len(content),
        "status": "verified_content",
    }
    if source["kind"] == "official_ppid_pdf_mirror":
        if not content.startswith(b"%PDF-"):
            raise RuntimeError("response is not a PDF")
        PDF_DIR.mkdir(parents=True, exist_ok=True)
        local_path = PDF_DIR / f"{source['source_id']}.pdf"
        local_path.write_bytes(content)
        reader = PdfReader(str(local_path))
        pages = []
        for index, page in enumerate(reader.pages, start=1):
            pages.append(f"=== PAGE {index} ===\n{page.extract_text() or ''}")
        TEXT_DIR.mkdir(parents=True, exist_ok=True)
        text_path = TEXT_DIR / f"{source['source_id']}.txt"
        text_path.write_text("\n\n".join(pages), encoding="utf-8")
        return {
            **common,
            "local_path": str(local_path.relative_to(ROOT)),
            "local_text_path": str(text_path.relative_to(ROOT)),
            "page_count": len(reader.pages),
            "title": None,
        }
    HTML_DIR.mkdir(parents=True, exist_ok=True)
    local_path = HTML_DIR / f"{source['source_id']}.html"
    local_path.write_bytes(content)
    text, title = html_to_text(content)
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    text_path = TEXT_DIR / f"{source['source_id']}.txt"
    text_path.write_text(text, encoding="utf-8")
    return {
        **common,
        "local_path": str(local_path.relative_to(ROOT)),
        "local_text_path": str(text_path.relative_to(ROOT)),
        "page_count": None,
        "title": title,
    }


def cached_web_artifact(source: dict[str, str], retrieval_error: Exception) -> dict[str, Any] | None:
    """Retain a previously hash-verified raw article if a transient fetch fails.

    This is intentionally limited to an already archived HTML file.  It does
    not bypass TLS, fabricate a response, or turn a failed first acquisition
    into a successful one.
    """
    if source["kind"] == "official_ppid_pdf_mirror":
        return None
    local_path = HTML_DIR / f"{source['source_id']}.html"
    if not local_path.exists():
        return None
    content = local_path.read_bytes()
    text, title = html_to_text(content)
    TEXT_DIR.mkdir(parents=True, exist_ok=True)
    text_path = TEXT_DIR / f"{source['source_id']}.txt"
    text_path.write_text(text, encoding="utf-8")
    return {
        **source,
        "retrieved_at_utc": None,
        "cache_verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "verified_cached_content",
        "retrieval_error": f"{type(retrieval_error).__name__}: {retrieval_error}",
        "sha256": sha256_bytes(content),
        "bytes": len(content),
        "local_path": str(local_path.relative_to(ROOT)),
        "local_text_path": str(text_path.relative_to(ROOT)),
        "page_count": None,
        "title": title,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-id",
        action="append",
        dest="source_ids",
        help="Acquire only this source ID; repeat the flag for multiple sources.",
    )
    args = parser.parse_args()
    selected = SOURCES
    if args.source_ids:
        requested = set(args.source_ids)
        known = {source["source_id"] for source in SOURCES}
        unknown = sorted(requested - known)
        if unknown:
            raise RuntimeError(f"Unknown source ID(s): {unknown}")
        selected = [source for source in SOURCES if source["source_id"] in requested]

    previous_documents: dict[str, dict[str, Any]] = {}
    if MANIFEST_PATH.exists():
        previous = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        previous_documents = {
            row["source_id"]: row
            for row in previous.get("documents", [])
            if row.get("source_id")
        }
    documents: list[dict[str, Any]] = []
    for source in selected:
        try:
            documents.append(acquire(source))
        except Exception as exc:  # Keep failed sources visible; never bypass TLS or invent content.
            cached = cached_web_artifact(source, exc)
            documents.append(cached or {
                **source,
                "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            })
    if args.source_ids:
        for source_id, previous_row in previous_documents.items():
            if source_id not in requested:
                documents.append(previous_row)
        documents.sort(key=lambda row: [source["source_id"] for source in SOURCES].index(row["source_id"]))
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "dataset": "Feature-2 vehicle subtype evidence — official incident sources",
        "acquired_at_utc": datetime.now(timezone.utc).isoformat(),
        "admission_policy": "Official PPID PDFs, official police/TNI publications, and attributable news reports are acquired. Web reports remain separate evidence until manually curated; every retained incident must state an actual incident and an explicit positive vehicle count. News is never used to overwrite a primary official source.",
        "documents": documents,
    }
    MANIFEST_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
