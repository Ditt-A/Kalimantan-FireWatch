"""Build a page-verified expansion of the Indonesian Karhutla deployment corpus.

The original workbook-derived corpus is preserved unchanged.  This script adds
manually reviewed, incident-level entries from official PPID Kemendagri report
copies.  Each entry is intentionally conservative: a field
is null when a source did not state it unambiguously, and a resource figure is
not copied from a Poslap/day aggregate onto an individual incident.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / "data/processed/kalteng_historical_deployments/kalteng_historical_resource_deployment.parquet"
MANIFEST_PATHS = (
    ROOT / "data/raw/kalteng_historical_deployments/acquisition_manifest.json",
    ROOT / "data/raw/kalteng_historical_deployments/expansion_acquisition_manifest.json",
    ROOT / "data/raw/kalteng_historical_deployments/source_documents_manifest.json",
)
OUT_DIR = ROOT / "data/processed/kalteng_historical_deployments"


def incident(
    incident_id: str,
    document_id: str,
    source_page: str,
    report_year: int,
    report_start: str,
    report_end: str,
    district: str,
    location: str,
    excerpt: str,
    *,
    area_ha: float | None = None,
    soil_type: str | None = None,
    fire_type: str | None = None,
    vegetation: str | None = None,
    water_source: str | None = None,
    personnel_total: int | None = None,
    pickup: int = 0,
    tanker: int = 0,
    fire_engine: int = 0,
    patrol_car: int = 0,
    r4: int = 0,
    r2: int = 0,
    tosa: int = 0,
    trail: int = 0,
    other_vehicle: int = 0,
    other_vehicle_desc: str | None = None,
    pump_shibaura: int = 0,
    pump_robin: int = 0,
    pump_mark3: int = 0,
    pump_ninja: int = 0,
    other_pump_count: int = 0,
    other_pump_desc: str | None = None,
    hose_rolls: int | None = None,
    nozzle_count: int | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    confidence: str = "high",
) -> dict[str, Any]:
    """Create a reviewed record without turning an omitted subtype into zero.

    A zero in an optional subtype column means only that the subtype was not
    listed in this source block; it never proves that the resource was absent.
    `vehicle_total` is the sum of the documented vehicle units, and the
    Feature-2 cohort converts unreported subtype zeros to null labels.
    `other_vehicle_count` keeps AWC/R6, boats, and similar units visible
    instead of relabelling them as fire engines.
    """
    vehicle_total = pickup + tanker + fire_engine + patrol_car + r4 + r2 + tosa + trail + other_vehicle
    pump_total = pump_shibaura + pump_robin + pump_mark3 + pump_ninja + other_pump_count
    has_personnel = personnel_total is not None
    has_vehicle = vehicle_total > 0
    provenance = {
        "review_method": "manual page-level extraction from verified official PPID PDF",
        "source_document_id": document_id,
        "source_page": source_page,
        "incident_context": "report narrative/resource block explicitly labelled for this incident",
        "personnel": "explicit numeric source list" if has_personnel else "not explicitly numeric; retained as null",
        "vehicles": "explicit numeric source list" if has_vehicle else "no numeric vehicle count retained",
        "excerpt": excerpt,
    }
    return {
        "incident_id": incident_id,
        "source_report_year": report_year,
        "province": "Kalimantan Tengah",
        "district": district,
        "location": location,
        "area_ha": area_ha,
        "soil_type": soil_type,
        "fire_type": fire_type,
        "vegetation": vegetation,
        "water_source": water_source,
        "personnel_total": personnel_total,
        "personnel_count_quality": "exact" if has_personnel else "not_reported",
        "pump_shibaura": pump_shibaura,
        "pump_robin": pump_robin,
        "pump_mark3": pump_mark3,
        "pump_ninja": pump_ninja,
        "pump_mugen": 0,
        "other_pump_count": other_pump_count,
        "other_pump_desc": other_pump_desc,
        "pickup_count": pickup,
        "water_tanker_count": tanker,
        "fire_engine_count": fire_engine,
        "patrol_car_count": patrol_car,
        "vehicle_r4_other_count": r4,
        "vehicle_r2_motor_count": r2,
        "tosa_count": tosa,
        "trail_count": trail,
        "other_vehicle_count": other_vehicle,
        "other_vehicle_desc": other_vehicle_desc,
        "pump_total": pump_total,
        "hose_rolls": hose_rolls,
        "nozzle_count": nozzle_count,
        "deployment_scope": "incident",
        "resource_mapping_confidence": confidence,
        "source_document_url_original": None,
        "source_excerpt": excerpt,
        "vehicle_total": vehicle_total,
        "source_document_id": document_id,
        "source_document_url_retrieved": None,
        "source_document_sha256": None,
        "source_document_status": "verified_pdf",
        "report_period_start": report_start,
        "report_period_end": report_end,
        # Report monitoring date is not silently substituted for incident date.
        "incident_date": None,
        "source_page": source_page,
        "latitude": latitude,
        "longitude": longitude,
        "coordinate_source_note": "explicit source coordinate" if latitude is not None else None,
        "source_page_status": "manually_reviewed_page_level",
        "source_workbook_file": None,
        "source_workbook_sha256": None,
        "pump_total_reported": pump_total if pump_total else None,
        "vehicle_total_reported": vehicle_total if vehicle_total else None,
        "source_field_provenance_json": json.dumps(provenance, ensure_ascii=False, sort_keys=True),
        "training_eligible_personnel": bool(has_personnel and confidence == "high"),
        "training_eligible_vehicle": bool(has_vehicle and confidence == "high"),
        "dataset_row_source": "official_ppid_expansion_manual_review",
    }


# Every row below is an actual suppression/pendinginan incident, not a patrol,
# awareness, readiness, or asset-inventory record.  Page ranges cover an
# incident narrative and its associated resource list when PDF columns split
# them across adjacent pages.
CURATED_ROWS = [
    # 31 July–1 August 2019 report (GsCCvcll)
    incident("KLT-2019-GS-003", "ppid_kemendagri_GsCCvcll", "13", 2019, "2019-07-31", "2019-08-01", "Kota Palangka Raya", "Jl. Manduhara, Kereng Bangkirai", "5 ha; Poslap 4 8 + Dandru MPA 1 + TNI 4 + Polri 1 + BPBD 1 + masyarakat 2; Ninja, 1 trail dan 1 TOSA.", area_ha=5.0, soil_type="gambut/mineral (source ambiguous)", personnel_total=17, tosa=1, trail=1, pump_ninja=1, hose_rolls=13, nozzle_count=1),
    incident("KLT-2019-GS-004", "ppid_kemendagri_GsCCvcll", "13-14", 2019, "2019-07-31", "2019-08-01", "Kota Palangka Raya", "Jl. G. Obos XX F, Menteng", "Poslap 5: 5 anggota + 3 TNI + 2 Polri; TOSA dan kendaraan dinas R2 dipakai untuk pemadaman.", soil_type=None, personnel_total=10, r2=3, tosa=1, other_pump_count=1, other_pump_desc="mesin pompa air portable", hose_rolls=5, nozzle_count=2),
    incident("KLT-2019-GS-005", "ppid_kemendagri_GsCCvcll", "14", 2019, "2019-07-31", "2019-08-01", "Kota Palangka Raya", "Jl. Tjilik Riwut Km. 9.5, Bukit Tunggal", "Pemadaman lanjutan sekitar 1 ha oleh 12 anggota BPBD Kota Palangka Raya, dibantu Satpol PP; api belum padam sepenuhnya.", area_ha=1.0, personnel_total=12),
    incident("KLT-2019-GS-006", "ppid_kemendagri_GsCCvcll", "15", 2019, "2019-07-31", "2019-08-01", "Kota Palangka Raya", "Jl. Tjilik Riwut Km. 19 / Jl. Liden Arang", "10 ha gambut; kebakaran permukaan dan bawah; tim gabungan melaporkan 1 kendaraan R4.", area_ha=10.0, soil_type="gambut", fire_type="permukaan dan bawah", vegetation="tanaman keras, pakis, semak belukar", water_source="parit", r4=1, other_pump_count=1, other_pump_desc="set mesin pemadam lengkap", hose_rolls=15, latitude=-2.11302, longitude=113.772996),
    incident("KLT-2019-GS-007", "ppid_kemendagri_GsCCvcll", "15-16", 2019, "2019-07-31", "2019-08-01", "Kota Palangka Raya", "Jl. G. Obos X, Menteng", "4 ha gambut; kebakaran permukaan dan bawah; tim gabungan melaporkan 1 kendaraan R4.", area_ha=4.0, soil_type="gambut", fire_type="permukaan dan bawah", vegetation="tanaman keras dan semak belukar", water_source="parit", r4=1, other_pump_count=1, other_pump_desc="set mesin pemadam lengkap", hose_rolls=15, latitude=-2.242192, longitude=113.897903),
    incident("KLT-2019-GS-008", "ppid_kemendagri_GsCCvcll", "16", 2019, "2019-07-31", "2019-08-01", "Kota Palangka Raya", "Jl. Panenga Induk 2, Kereng Bangkirai", "1 ha mineral, kebakaran permukaan; tim gabungan melaporkan 1 kendaraan R4.", area_ha=1.0, soil_type="mineral", fire_type="permukaan", vegetation="tanaman keras dan pakis", water_source="parit", r4=1, other_pump_count=1, other_pump_desc="set mesin pemadam lengkap", hose_rolls=15, latitude=-2.279178, longitude=113.913377),
    incident("KLT-2019-GS-009", "ppid_kemendagri_GsCCvcll", "17", 2019, "2019-07-31", "2019-08-01", "Kabupaten Kotawaringin Timur", "Jalan Lingkar Selatan / Jalan Kecamatan", "Sekitar 6 ha terbakar; 11 personel Polres Kotim dan 7 kendaraan R2 dilaporkan dalam operasi pemadaman.", area_ha=6.0, personnel_total=11, r2=7),
    incident("KLT-2019-GS-010", "ppid_kemendagri_GsCCvcll", "17-18", 2019, "2019-07-31", "2019-08-01", "Kabupaten Kotawaringin Timur", "Jalan Kecamatan tembus Amin Kelaru, Mentawa Baru Ketapang", "Sekitar 4 ha gambut; daftar eksplisit 21 personel lintas instansi dan 2 R4, dengan tambahan back-up Polres dicatat terpisah.", area_ha=4.0, soil_type="gambut", personnel_total=21, r4=2, other_pump_count=2, other_pump_desc="mesin pompa alkon", hose_rolls=30, nozzle_count=4, confidence="medium"),
    incident("KLT-2019-GS-011", "ppid_kemendagri_GsCCvcll", "22", 2019, "2019-07-31", "2019-08-01", "Kabupaten Barito Selatan", "Hulu Kelurahan Rantau Kujang, Jenamas", "3 ha terbakar; Polri 3 dan masyarakat 2 melakukan pemadaman setelah menerima hotspot.", area_ha=3.0, personnel_total=5, other_pump_count=1, other_pump_desc="pompa portabel"),
    # 19–20 August 2019 report (hnS3t7HI)
    incident("KLT-2019-HN-001", "ppid_kemendagri_hnS3t7HI", "14", 2019, "2019-08-19", "2019-08-20", "Kota Palangka Raya", "Jl. Ir. Soekarno dan Jl. Merdeka, Menteng", "Poslap 1 regu I 7 + regu II 6 + Danru 2 + TNI 4 + AMCF 1; 2 pickup dan 2 motor untuk pemadaman lanjutan.", soil_type="gambut", fire_type="atas dan bawah", vegetation="semak belukar dan ilalang kering", water_source="parit", personnel_total=20, pickup=2, r2=2, pump_shibaura=1, hose_rolls=27, nozzle_count=2),
    incident("KLT-2019-HN-002", "ppid_kemendagri_hnS3t7HI", "14-15", 2019, "2019-08-19", "2019-08-20", "Kota Palangka Raya", "Jl. Kalibata, Menteng", "Pemadaman lanjutan sekitar 0.5 ha; 8 Poslap 1 + 2 damkar DPRD + 1 Satpol PP + 1 Danru + 2 TNI; pickup dan tangki damkar DPRD.", area_ha=0.5, soil_type="gambut", fire_type="atas dan bawah", vegetation="semak belukar dan ilalang kering", water_source="parit", personnel_total=14, pickup=1, tanker=1, pump_robin=1, hose_rolls=10, nozzle_count=1),
    incident("KLT-2019-HN-003", "ppid_kemendagri_hnS3t7HI", "15", 2019, "2019-08-19", "2019-08-20", "Kota Palangka Raya", "Jl. Tjilik Riwut Km. 13.5, Bukit Tunggal", "1 ha gambut; 8 Poslap 2 + 1 Danru + 4 TNI + 2 Satpol PP; 1 pickup dan 5 R2.", area_ha=1.0, soil_type="gambut", vegetation="semak belukar dan ilalang kering", water_source="sumur bor", personnel_total=15, pickup=1, r2=5, hose_rolls=10, nozzle_count=1),
    incident("KLT-2019-HN-004", "ppid_kemendagri_hnS3t7HI", "15-16", 2019, "2019-08-19", "2019-08-20", "Kota Palangka Raya", "Jl. Nagasari Ujung, Bukit Tunggal", "2 ha gambut; 8 Poslap 2 + 1 Danru + 1 Polri; 1 pickup dan 1 R2.", area_ha=2.0, soil_type="gambut", vegetation="semak belukar dan ilalang kering", water_source="sumur bor", personnel_total=10, pickup=1, r2=1, pump_robin=1, hose_rolls=8, nozzle_count=1),
    incident("KLT-2019-HN-005", "ppid_kemendagri_hnS3t7HI", "17-18", 2019, "2019-08-19", "2019-08-20", "Kota Palangka Raya", "Jl. Yusuf Armatea / Tjilik Riwut Km. 12", "1.5 ha gambut, kebakaran permukaan dan bawah; tim gabungan melaporkan 1 R4.", area_ha=1.5, soil_type="gambut", fire_type="permukaan dan bawah", vegetation="tanaman keras dan pakis", water_source="sumur bor", r4=1, other_pump_count=2, other_pump_desc="mesin pemadam lengkap", hose_rolls=23),
    incident("KLT-2019-HN-006", "ppid_kemendagri_hnS3t7HI", "17-18", 2019, "2019-08-19", "2019-08-20", "Kota Palangka Raya", "Jl. Saluang Belum, Tjilik Riwut Km. 23", "7.5 ha gambut sedang, kebakaran permukaan dan bawah; 1 R4 dan 1 R2.", area_ha=7.5, soil_type="gambut sedang", fire_type="permukaan dan bawah", vegetation="semak belukar, tanaman keras dan pakis", water_source="kolam", r4=1, r2=1, other_pump_count=1, other_pump_desc="mesin pemadam lengkap", hose_rolls=25),
    incident("KLT-2019-HN-007", "ppid_kemendagri_hnS3t7HI", "18-19", 2019, "2019-08-19", "2019-08-20", "Kota Palangka Raya", "Jl. Yos Sudarso, Menteng", "Pemadaman lanjutan di tanah mineral; 1 R4 dan 2 R2 digunakan oleh tim gabungan.", soil_type="mineral", fire_type="permukaan", vegetation="tanaman keras dan pakis", water_source="sumur bor", r4=1, r2=2, other_pump_count=2, other_pump_desc="mesin pemadam lengkap", hose_rolls=18),
    incident("KLT-2019-HN-008", "ppid_kemendagri_hnS3t7HI", "18-19", 2019, "2019-08-19", "2019-08-20", "Kota Palangka Raya", "Jl. Mahir Mahar Km. 14.5 / Trans Kalimantan", "1.5 ha gambut; kebakaran permukaan dan bawah; 1 R4 digunakan tim gabungan.", area_ha=1.5, soil_type="gambut", fire_type="permukaan dan bawah", vegetation="semak belukar, tanaman keras dan pakis", water_source="sumur bor", r4=1, other_pump_count=1, other_pump_desc="mesin pemadam lengkap", hose_rolls=8),
    incident("KLT-2019-HN-009", "ppid_kemendagri_hnS3t7HI", "19", 2019, "2019-08-19", "2019-08-20", "Kota Palangka Raya", "Jl. Mahir Mahar, Kereng Bangkirai", "1.5 ha gambut sedang; kebakaran permukaan dan bawah; dukungan truk tangki dan 1 R4 tercatat.", area_ha=1.5, soil_type="gambut sedang", fire_type="permukaan dan bawah", vegetation="semak belukar, tanaman keras dan pakis", water_source="dukungan truk tangki", r4=1, other_pump_count=1, other_pump_desc="mesin pemadam lengkap", hose_rolls=10),
    incident("KLT-2019-HN-010", "ppid_kemendagri_hnS3t7HI", "20", 2019, "2019-08-19", "2019-08-20", "Kota Palangka Raya", "Jalan Mahir Mahar Lingkar Luar", "Pendinginan sekitar 5 ha; Polres 12; 1 R4 dan 1 R6/AWC dicatat terpisah.", area_ha=5.0, water_source="AWC", personnel_total=12, r4=1, other_vehicle=1, other_vehicle_desc="R6/AWC", other_pump_count=1, other_pump_desc="mesin pompa air", hose_rolls=2),
    incident("KLT-2019-HN-011", "ppid_kemendagri_hnS3t7HI", "20-21", 2019, "2019-08-19", "2019-08-20", "Kota Palangka Raya", "Jl. Simpei Karuhei III, Menteng", "2 ha mineral; 31 Polri + 10 BNPB + 15 Tagana + 7 Damkar; 2 AWC, mobil karhutla, barrier, dan truk.", area_ha=2.0, soil_type="mineral", fire_type="atas", vegetation="semak belukar dan ilalang kering", water_source="AWC Brimob, BNPB, Tagana", personnel_total=63, other_vehicle=5, other_vehicle_desc="2 AWC, mobil karhutla, mobil barrier, truk", other_pump_count=3, other_pump_desc="1 alkon sedang dan 2 alkon kecil", hose_rolls=16, nozzle_count=3),
    incident("KLT-2019-HN-012", "ppid_kemendagri_hnS3t7HI", "21", 2019, "2019-08-19", "2019-08-20", "Kota Palangka Raya", "Jl. D. A. Tawa, Bukit Tunggal", "8 ha mineral; 31 Polri; 2 AWC, mobil karhutla, barrier, truk, dan mobil jabatan dicatat.", area_ha=8.0, soil_type="mineral", fire_type="atas", vegetation="semak belukar dan ilalang kering", water_source="AWC Brimob dan kolam", personnel_total=31, other_vehicle=6, other_vehicle_desc="2 AWC, mobil karhutla, mobil barrier, truk, mobil jabatan", other_pump_count=3, other_pump_desc="1 alkon sedang dan 2 alkon kecil", hose_rolls=16, nozzle_count=3),
    incident("KLT-2019-HN-013", "ppid_kemendagri_hnS3t7HI", "22-23", 2019, "2019-08-19", "2019-08-20", "Kabupaten Pulang Pisau", "Handel Simin, Desa Buntoi, Kahayan Hilir", "Lahan gambut/perkebunan; Polri 4 + TNI 1 + masyarakat 5 + Distan 6; 1 R4 dan 13 R2.", soil_type="gambut", fire_type="atas dan bawah", vegetation="perkebunan dan lahan kosong", water_source="Sungai Handel Simin", personnel_total=16, r4=1, r2=13, pump_robin=5, other_pump_count=3, other_pump_desc="2 alkon dan 1 maxtre"),
    incident("KLT-2019-HN-014", "ppid_kemendagri_hnS3t7HI", "22-24", 2019, "2019-08-19", "2019-08-20", "Kabupaten Pulang Pisau", "Sekunder Timur, Desa Mintin, Kahayan Hilir", "Gambut; Polri 21 + TNI 2 + MPA 5 + BPBD 4 + Satgas BPBD 10 + masyarakat 15; 6 R2 dan 6 mesin pompa.", soil_type="gambut", vegetation="pakis dan semak belukar", water_source="irigasi/kanal", personnel_total=57, r2=6, other_pump_count=6, other_pump_desc="mesin pompa air"),
    incident("KLT-2019-HN-015", "ppid_kemendagri_hnS3t7HI", "22-25", 2019, "2019-08-19", "2019-08-20", "Kabupaten Pulang Pisau", "Desa Sebangau Mulya, Sebangau Kuala", "Gambut tipis; TNI 1 + Polri 12 + MPA 18 + masyarakat 15; 10 R2 dan 1 TOSA saat pemadaman/pendinginan.", soil_type="gambut tipis", vegetation="sawit, karet, pakis, galam, akasia, semak", water_source="parit irigasi (terbatas)", personnel_total=46, r2=10, tosa=1, pump_robin=4),
    # 24–25 August 2018 report (7WWeUbX0)
    incident("KLT-2018-7W-001", "ppid_kemendagri_7WWeUbX0", "6", 2018, "2018-08-24", "2018-08-25", "Kota Palangka Raya", "Jl. Raflesia, Petuk Katimpun", "3.5 ha; TNI 1 + Polri 8 + BPK Borneo Fire Rescue 7; Robin dan mesin apung Honda digunakan.", area_ha=3.5, personnel_total=16, pump_robin=1, other_pump_count=1, other_pump_desc="mesin apung Honda", hose_rolls=8, nozzle_count=2),
    incident("KLT-2018-7W-002", "ppid_kemendagri_7WWeUbX0", "6-7", 2018, "2018-08-24", "2018-08-25", "Kota Palangka Raya", "Jl. Tjilik Riwut Km. 17, Marang", "3 ha; Babinsa 1 + Bhabinkamtibmas 1 + TSAK Bukit Tunggal 8; Shibaura dan 1 R4.", area_ha=3.0, personnel_total=10, r4=1, pump_shibaura=1, hose_rolls=16, nozzle_count=3),
    incident("KLT-2018-7W-003", "ppid_kemendagri_7WWeUbX0", "6-7", 2018, "2018-08-24", "2018-08-25", "Kota Palangka Raya", "Jl. Mahir Mahar – Jl. Yos Sudarso Ujung", "0.24 ha; Polri 7 + Poslap 3 regu 2 sebanyak 7; 1 mobil dan 1 Shibaura.", area_ha=0.24, personnel_total=14, r4=1, pump_shibaura=1, hose_rolls=6, nozzle_count=1),
    incident("KLT-2018-7W-004", "ppid_kemendagri_7WWeUbX0", "9-10", 2018, "2018-08-24", "2018-08-25", "Kabupaten Katingan", "Jl. D. I. Panjaitan Km. 1.5, Kasongan Lama", "1.5 ha; TNI 4 + Polri 6 + BPBD 2 + Tagana 1; 2 R4, 1 R2, Shibaura dan pompa apung.", area_ha=1.5, personnel_total=13, r4=2, r2=1, pump_shibaura=1, other_pump_count=1, other_pump_desc="pompa apung", hose_rolls=10, nozzle_count=2),
    incident("KLT-2018-7W-005", "ppid_kemendagri_7WWeUbX0", "10", 2018, "2018-08-24", "2018-08-25", "Kabupaten Katingan", "Desa Tumbang Atei, Sanaman Mantikei", "2 ha; TNI 3 + Polri 4 + PBS 4; 1 R4, 4 R2 dan pompa karhutla.", area_ha=2.0, personnel_total=11, r4=1, r2=4, other_pump_count=1, other_pump_desc="pompa karhutla"),
    incident("KLT-2018-7W-006", "ppid_kemendagri_7WWeUbX0", "10", 2018, "2018-08-24", "2018-08-25", "Kabupaten Kapuas", "Desa Palangkau Lama, Kapuas Murung", "4 ha; TNI 2 + Polri 1 + Manggala Agni 8 + masyarakat 7; Mark-3 dan 10 R2.", area_ha=4.0, personnel_total=18, r2=10, pump_mark3=1),
    incident("KLT-2018-7W-007", "ppid_kemendagri_7WWeUbX0", "11", 2018, "2018-08-24", "2018-08-25", "Kabupaten Lamandau", "Alun-Alun arah Desa Bumi Agung, Bulik", "0.25 ha; TNI 1 + Polri 1 + BPBD/TRC 5 + DLHK 2 + Satpol PP Damkar 3; 1 mobil patroli dan 2 mobil damkar.", area_ha=0.25, personnel_total=12, fire_engine=2, patrol_car=1, pump_robin=1),
    incident("KLT-2018-7W-008", "ppid_kemendagri_7WWeUbX0", "12", 2018, "2018-08-24", "2018-08-25", "Kabupaten Kotawaringin Timur", "Desa Babaung dan Pentaguan, Pulau Hanaut", "Pemadaman karhutla: Posramil 1 + Polsek 4 + kecamatan 1; 4 R2.", personnel_total=6, r2=4),
    incident("KLT-2018-7W-009", "ppid_kemendagri_7WWeUbX0", "12-13", 2018, "2018-08-24", "2018-08-25", "Kabupaten Kotawaringin Timur", "Jalan Padat Karya, Baamang Hulu", "0.4 ha; Kapolsek + 2 Polsek, Danramil + 2, Polres 1, Damkar 2, Basarnas 1, BPBD 1, Pol PP 1, PMI 1; 2 mobil tangki BPBD.", area_ha=0.4, personnel_total=13, tanker=2),
    incident("KLT-2018-7W-010", "ppid_kemendagri_7WWeUbX0", "13-14", 2018, "2018-08-24", "2018-08-25", "Kabupaten Kotawaringin Timur", "Desa Jaya Karet, Gang Bersama, Mentaya Hilir Selatan", "10 ha; dua daftar personel eksplisit total 42 pada operasi, dengan 2 R4, 4 R2 dan 3 mesin alkon. Semua tim yang disebut dikelompokkan sebagai satu operasi lokasi.", area_ha=10.0, personnel_total=42, r4=2, r2=4, other_pump_count=3, other_pump_desc="mesin alkon", confidence="medium"),
    incident("KLT-2018-7W-011", "ppid_kemendagri_7WWeUbX0", "12-14", 2018, "2018-08-24", "2018-08-25", "Kabupaten Kotawaringin Timur", "Jl. H. M. Arsyad Km. 8, Desa Eka Bahurui", "7 ha; lima personel bernama dari tim patroli/pemadaman; 3 mobil damkar dan 3 alkon warga/MPA.", area_ha=7.0, personnel_total=5, fire_engine=3, other_pump_count=3, other_pump_desc="alkon warga/MPA"),
    # 2–3 August 2019 report (5c5Bttsh)
    incident("KLT-2019-5C-001", "kemendagri_ppid_5c5Bttsh", "19", 2019, "2019-08-02", "2019-08-03", "Kota Palangka Raya", "Jl. Temanggung Tawa, Bukit Tunggal", "2 ha gambut dalam (±1 ha dipadamkan); Poslap 2 9 + Satpol PP 1 + Babinsa 1 + regu tangki air 2; 1 pickup.", area_ha=2.0, soil_type="gambut dalam", personnel_total=13, pickup=1, pump_robin=1, other_pump_count=1, other_pump_desc="mesin Honda lengkap", hose_rolls=4, nozzle_count=1),
    incident("KLT-2019-5C-002", "kemendagri_ppid_5c5Bttsh", "19", 2019, "2019-08-02", "2019-08-03", "Kota Palangka Raya", "Jl. Tjilik Riwut Km. 8.5 / Jl. Ir. Soeyatno, Bukit Tunggal", "1.5 ha gambut dalam (±1 ha dipadamkan); Poslap 2 8 + TNI 2 + Polri 1 + Satpol PP 1; 1 pickup dan 3 R2.", area_ha=1.5, soil_type="gambut dalam", personnel_total=12, pickup=1, r2=3, pump_robin=2, hose_rolls=6, nozzle_count=2),
    incident("KLT-2019-5C-003", "kemendagri_ppid_5c5Bttsh", "19-20", 2019, "2019-08-02", "2019-08-03", "Kota Palangka Raya", "Jl. Merdeka, Perum Nyi Balau, Bukit Tunggal", "Sekitar 1 ha gambut dalam; Poslap 2 Regu 1 8 + Polri 1 + BPBPK 2 + Satpol PP 1 + Dandru 1; 1 pickup dan 1 R2.", area_ha=1.0, soil_type="gambut dalam", fire_type="atas dan bawah", vegetation="semak belukar kering dan akasia", water_source="truk tangki BPBPK", personnel_total=13, pickup=1, r2=1, pump_shibaura=1, hose_rolls=17, nozzle_count=2),
    incident("KLT-2019-5C-004", "kemendagri_ppid_5c5Bttsh", "21", 2019, "2019-08-02", "2019-08-03", "Kota Palangka Raya", "Jl. Lingkar Luar antara Jl. Yos Sudarso dan Jl. G. Obos, Menteng", "±0.01 ha dipadamkan; Poslap 5 5 + TNI 3 + Polri 1 + Brimob 1; 1 TOSA dan 3 kendaraan dinas beroda tak dirinci.", area_ha=0.01, personnel_total=10, tosa=1, other_vehicle=3, other_vehicle_desc="ranmor dinas Babinsa, Brimob, dan Bhabinkamtibmas", other_pump_count=1, other_pump_desc="pompa air Bravo", hose_rolls=5, nozzle_count=1),
    incident("KLT-2019-5C-005", "kemendagri_ppid_5c5Bttsh", "24", 2019, "2019-08-02", "2019-08-03", "Kabupaten Pulang Pisau", "Kahayan Hilir (lokasi kebun masyarakat / Buntoi)", "±4 ha gambut; Polri 12 + TNI 1 + MPA 9 + Distana 6 + masyarakat 20; 15 R2 dan 10 mesin Robin, dengan dukungan water bombing.", area_ha=4.0, soil_type="gambut", fire_type="atas dan bawah", vegetation="perkebunan dan lahan kosong", water_source="Sungai Ruhat", personnel_total=48, r2=15, pump_robin=10),
    incident("KLT-2019-5C-006", "kemendagri_ppid_5c5Bttsh", "26", 2019, "2019-08-02", "2019-08-03", "Kabupaten Pulang Pisau", "Desa Bukit Rawi, Kahayan Tengah", "Pemadaman kebakaran permukaan di tanah mineral; TNI 9 + Polri 2 + MPA 6 + kecamatan 2 + masyarakat 4; 2 R4 dan 6 R2.", soil_type="mineral", fire_type="permukaan", vegetation="pakis, kayu, dan semak belukar", water_source="parit/irigasi (terbatas dan sebagian kering)", personnel_total=23, r4=2, r2=6, other_pump_count=3, other_pump_desc="mesin portable"),
    incident("KLT-2019-5C-007", "kemendagri_ppid_5c5Bttsh", "27-28", 2019, "2019-08-02", "2019-08-03", "Kabupaten Pulang Pisau", "Desa Bawan, Banama Tingang", "Pemadaman kebakaran permukaan di tanah mineral; TNI 1 + Polri 6 + kecamatan 2 + masyarakat 8; 1 R4 dan 8 R2.", soil_type="mineral", fire_type="permukaan", vegetation="pakis, kayu tanah, garonggang, dan semak belukar", water_source="parit/galian", personnel_total=17, r4=1, r2=8, other_pump_count=1, other_pump_desc="mesin portable"),
    # 21–22 July 2018 report (QAT3WnjC)
    incident("KLT-2018-QAT-001", "kemendagri_ppid_QAT3WnjC", "11", 2018, "2018-07-21", "2018-07-22", "Kota Palangka Raya", "Jl. Ir. Soekarno, Gg. Parawei, Menteng", "±0.06 ha; TNI 1 + Polri 2 + BPK Berkat Usaha 6; 1 unit damkar BPBD dan 1 unit damkar BPK Berkat Usaha.", area_ha=0.06, personnel_total=9, fire_engine=2),
    incident("KLT-2018-QAT-002", "kemendagri_ppid_QAT3WnjC", "14", 2018, "2018-07-21", "2018-07-22", "Kabupaten Kapuas", "Desa Pulau Telo Baru, Selat", "Laporan sementara ±2 ha; BPBD 7; 1 R3 Tossa dan 2 R2.", area_ha=2.0, personnel_total=7, tosa=1, r2=2, other_pump_count=1, other_pump_desc="mesin pemadam portable"),
    incident("KLT-2018-QAT-003", "kemendagri_ppid_QAT3WnjC", "14", 2018, "2018-07-21", "2018-07-22", "Kabupaten Kapuas", "Desa Harapan Jaya, Mantangai", "±0.3 ha; Damkar Desa 5; 1 kendaraan R3/Tossa dan 1 mesin pompa.", area_ha=0.3, personnel_total=5, tosa=1, other_pump_count=1, other_pump_desc="mesin pompa"),
    incident("KLT-2018-QAT-004", "kemendagri_ppid_QAT3WnjC", "15", 2018, "2018-07-21", "2018-07-22", "Kabupaten Kapuas", "UPT Dadahub A6, Kapuas Murung", "Lahan terbakar ditemukan saat groundcheck dan dipadamkan; TNI 2 + Polri 1 + Manggala Agni 3; 6 R2.", personnel_total=6, r2=6, other_pump_count=1, other_pump_desc="mesin portable"),
    incident("KLT-2018-QAT-005", "kemendagri_ppid_QAT3WnjC", "19", 2018, "2018-07-21", "2018-07-22", "Kabupaten Sukamara", "Desa Sei Tabuk, Pantai Lunci", "±1.5 ha; Polri 2 + TNI 2 + TSA 3; 5 R2 dan 2 semprot handpom.", area_ha=1.5, personnel_total=7, r2=5, other_pump_count=2, other_pump_desc="semprot handpom"),
    # 25–26 July 2018 report (bZ3oGAy7)
    incident("KLT-2018-BZ-001", "kemendagri_ppid_bZ3oGAy7", "10", 2018, "2018-07-25", "2018-07-26", "Kabupaten Kotawaringin Timur", "Desa Sebabi, Telawang", "Kebakaran ditemukan dan langsung dipadamkan; TNI 2 + Polri 2 + perusahaan 14; 4 R4 dan 3 R2.", personnel_total=18, r4=4, r2=3, latitude=-2.4072, longitude=112.5580),
    incident("KLT-2018-BZ-002", "kemendagri_ppid_bZ3oGAy7", "13", 2018, "2018-07-25", "2018-07-26", "Kabupaten Pulang Pisau", "Desa Sei Rungun, Sebangau Kuala", "±2 ha dan masih dilakukan pemadaman; TNI 2 + Polri 2 + Manggala Agni 4 + MPA 2 + PT KLS I 20; 7 R2 dan 2 klotok.", area_ha=2.0, personnel_total=30, r2=7, other_vehicle=2, other_vehicle_desc="klotok", latitude=-3.21881175041, longitude=113.928260803),
    # 27–28 August 2018 report (qOBVbpMG)
    incident("KLT-2018-QOB-001", "kemendagri_ppid_qOBVbpMG", "7", 2018, "2018-08-27", "2018-08-28", "Kota Palangka Raya", "Jl. Tingang / Jl. Tenggiri, Jekan Raya", "±1 ha; Damkar Dishut 6; sumber menyebut 1 unit \"mobil damkar/pickup\" tanpa membedakan tipe kendaraan.", area_ha=1.0, personnel_total=6, other_vehicle=1, other_vehicle_desc="mobil damkar/pickup (tipe sumber ambigu)", other_pump_count=2, other_pump_desc="mesin jinjing", hose_rolls=9),
    incident("KLT-2018-QOB-002", "kemendagri_ppid_qOBVbpMG", "8", 2018, "2018-08-27", "2018-08-28", "Kabupaten Pulang Pisau", "Desa Bahaur Tengah, Kahayan Kuala", "Kebakaran masih dalam proses pemadaman; Manggala Agni 6 + TNI 3 + Polri 3 + MPA 15; 1 mobil SlipOn dan 5 R2.", personnel_total=27, r2=5, other_vehicle=1, other_vehicle_desc="mobil SlipOn", pump_mark3=1, pump_robin=5, hose_rolls=30),
    # 9–10 August 2018 report (2IIueVEW)
    incident("KLT-2018-2II-001", "kemendagri_ppid_2IIueVEW", "9", 2018, "2018-08-09", "2018-08-10", "Kota Palangka Raya", "Merdeka, Perum Pemda RT 07/14, Bukit Tunggal", "40×60 m di tanah mineral, kebakaran permukaan; Manggala Agni 3 + TSAK Bukit Tunggal 5; 1 pickup dan 1 Shibaura.", area_ha=0.24, soil_type="mineral", fire_type="permukaan", vegetation="semak belukar dan akasia", personnel_total=8, pickup=1, pump_shibaura=1, hose_rolls=6, nozzle_count=2, latitude=-2.16492, longitude=113.84098),
    incident("KLT-2018-2II-002", "kemendagri_ppid_2IIueVEW", "12-13", 2018, "2018-08-09", "2018-08-10", "Kabupaten Kotawaringin Timur", "Desa Basirih Hilir, Mentaya Hilir Selatan", "Pemadaman lanjutan; Manggala Agni 5 + Damkar 4 + Polri 1 + TNI 1 + BPBD 2; 2 mobil dan 3 motor.", personnel_total=13, r4=2, r2=3, other_pump_count=2, other_pump_desc="pompa apung dan mesin mestri", hose_rolls=12, nozzle_count=1),
    incident("KLT-2018-2II-003", "kemendagri_ppid_2IIueVEW", "12-13", 2018, "2018-08-09", "2018-08-10", "Kabupaten Kotawaringin Timur", "Jl. Jenderal Sudirman, Perum Pandawa", "Pemadaman lanjutan; Damkar 2 + TNI 1 + Satpol PP 1 + BPBD 1 + DLH 1 + Basarnas 1 + Pertanian 1; 1 mobil tangki dan 1 motor.", personnel_total=8, tanker=1, r2=1, other_pump_count=1, other_pump_desc="alkon", hose_rolls=5, nozzle_count=1),
    # 13–14 August 2018 report (CEeYYckC)
    incident("KLT-2018-CE-001", "kemendagri_ppid_CEeYYckC", "12", 2018, "2018-08-13", "2018-08-14", "Kabupaten Kapuas", "Desa Bina Jaya (A1), Dadahup", "20 ha; TNI 4 + Polri 2 + BPBD 1 + Manggala Agni 8 + Damkar/PP 2 + Brigade Serbu Api 1 + Balakar 1 + Damkar Mandiri 1 + Emergency 508 1 + masyarakat 1; 2 R4 dan 6 R2.", area_ha=20.0, personnel_total=22, r4=2, r2=6, other_pump_count=2, other_pump_desc="mesin pemadam portable"),
    incident("KLT-2018-CE-002", "kemendagri_ppid_CEeYYckC", "12-13", 2018, "2018-08-13", "2018-08-14", "Kabupaten Kapuas", "Desa Tarung Manuah Rey 10/11/12 RT 04, Basarang", "Pemadaman berlangsung dan api belum padam; TNI 2 + Polri 3 + BPBD 15 + kecamatan 4 + Emergency 508 5 + masyarakat 10; 3 R3 dan 4 R2.", personnel_total=39, r2=4, other_vehicle=3, other_vehicle_desc="kendaraan R3", other_pump_count=1, other_pump_desc="mesin pemadam portable"),
    incident("KLT-2018-CE-003", "kemendagri_ppid_CEeYYckC", "13", 2018, "2018-08-13", "2018-08-14", "Kabupaten Kapuas", "Desa Timpah, Timpah", "0.6 ha; TNI 1 + Polri 2 + Manggala Agni 6 + masyarakat 6; 1 mobil personel dan 3 R2.", area_ha=0.6, personnel_total=15, other_vehicle=1, other_vehicle_desc="mobil personel", r2=3, other_pump_count=1, other_pump_desc="mesin pemadam portable"),
    # 1–2 August 2019 report (Wi5wQA8ONbMlvTAd), retained base source
    incident("KLT-2019-WI-001", "ppid_kalteng_Wi5wQA8ONbMlvTAd", "22", 2019, "2019-08-01", "2019-08-02", "Kabupaten Barito Utara", "Desa Trahean, Teweh Selatan", "±1.5 ha gambut/mineral; BPBD Barito Utara 9 + MPA Trahean 8; 1 water supply, 1 rescue, dan 5 motor trail.", area_ha=1.5, soil_type="gambut/mineral", vegetation="ilalang, pakis, dan semak belukar", personnel_total=17, trail=5, other_vehicle=2, other_vehicle_desc="water supply dan rescue", other_pump_count=2, other_pump_desc="mesin portable"),
    incident("KLT-2019-WI-002", "ppid_kalteng_Wi5wQA8ONbMlvTAd", "23-24", 2019, "2019-08-01", "2019-08-02", "Kabupaten Barito Selatan", "Desa Jutuh / Simpang Tiga Jalan Pendang, Dusun Selatan", "±2.37 ha; BPBD 16 + Kepolisian 4 + Damkar 1 + DLH 1 + Tagana 1 + Dinas Sosial 1 + Kodim 1 + KPHP 1 + Manggala Agni 5; 4 R4 dan 7 R2.", area_ha=2.37, personnel_total=31, r4=4, r2=7, other_pump_count=1, other_pump_desc="mesin pemadam ringan", hose_rolls=None),
]


def load_document_metadata() -> dict[str, dict[str, Any]]:
    metadata: dict[str, dict[str, Any]] = {}
    for manifest_path in MANIFEST_PATHS:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for entry in manifest["documents"]:
            if entry.get("status") != "verified_pdf":
                continue
            # The original 15-row corpus predates the expansion manifest and
            # uses source_document_id/local_pdf_path names.  Normalize both
            # manifest formats before enforcing hashes below.
            normalized = dict(entry)
            document_id = str(normalized.get("document_id") or normalized["source_document_id"])
            normalized["document_id"] = document_id
            normalized["source_document_url"] = (
                normalized.get("source_document_url")
                or normalized.get("source_document_url_retrieved")
                or normalized.get("source_document_url_original")
            )
            normalized["local_path"] = normalized.get("local_path") or normalized.get("local_pdf_path")
            if not normalized["source_document_url"] or not normalized["local_path"]:
                raise ValueError(f"Incomplete provenance manifest entry for {document_id}")
            previous = metadata.get(document_id)
            if previous and previous["sha256"] != normalized["sha256"]:
                raise ValueError(f"Conflicting SHA-256 values for {document_id}")
            metadata[document_id] = normalized
    required = {row["source_document_id"] for row in CURATED_ROWS}
    missing = required - set(metadata)
    if missing:
        raise ValueError(f"Curated rows reference unavailable verified PDFs: {sorted(missing)}")
    for entry in metadata.values():
        local_path = ROOT / str(entry["local_path"])
        if not local_path.exists():
            raise FileNotFoundError(local_path)
        actual = hashlib.sha256(local_path.read_bytes()).hexdigest()
        if actual != entry["sha256"]:
            raise ValueError(f"SHA-256 mismatch for {entry['document_id']}")
    return metadata


def main() -> None:
    if not BASE_PATH.exists():
        raise FileNotFoundError(f"Build the base reviewed dataset first: {BASE_PATH}")
    metadata = load_document_metadata()
    base = pd.read_parquet(BASE_PATH).copy()
    expansion = pd.DataFrame(CURATED_ROWS)
    for index, row in expansion.iterrows():
        source = metadata[row["source_document_id"]]
        expansion.at[index, "source_document_url_original"] = source["source_document_url"]
        expansion.at[index, "source_document_url_retrieved"] = source["source_document_url"]
        expansion.at[index, "source_document_sha256"] = source["sha256"]

    # Keep all source fields from the original 15 rows and make the expanded
    # schema explicit about vehicle types that did not exist in that workbook.
    for column in expansion.columns:
        if column not in base.columns:
            base[column] = pd.NA
    for column in base.columns:
        if column not in expansion.columns:
            expansion[column] = pd.NA
    combined = pd.concat([base[expansion.columns], expansion], ignore_index=True)
    if combined["incident_id"].duplicated().any():
        duplicates = combined.loc[combined["incident_id"].duplicated(), "incident_id"].tolist()
        raise ValueError(f"Duplicate incident IDs: {duplicates}")
    if len(combined) < 50:
        raise ValueError("Expansion failed to reach the minimum 50 reviewed records")
    if not combined["source_document_url_retrieved"].notna().all():
        raise ValueError("Every output row requires an official source-document URL")
    if not combined["source_document_sha256"].notna().all():
        raise ValueError("Every output row requires a source-document SHA-256")
    if not combined["source_page"].notna().all():
        raise ValueError("Every output row requires page-level provenance")
    if not combined["province"].eq("Kalimantan Tengah").all():
        raise ValueError("This corpus must remain Central Kalimantan only")

    numeric_vehicle = pd.to_numeric(combined["vehicle_total"], errors="coerce").fillna(0).gt(0)
    strict_full_resource = (
        combined["personnel_total"].notna()
        & numeric_vehicle
        & combined["resource_mapping_confidence"].eq("high")
    )
    if int(strict_full_resource.sum()) < 50:
        raise ValueError(
            "Expansion must retain at least 50 HIGH incident records with both numeric personnel and vehicles"
        )

    # The original Excel-derived output carries a few count columns as mixed
    # object values.  Make serving/training count fields genuinely numeric
    # before writing Arrow/Parquet; invalid source text becomes null rather
    # than an invented zero.
    integer_columns = [
        "personnel_total", "pump_shibaura", "pump_robin", "pump_mark3", "pump_ninja",
        "pump_mugen", "other_pump_count", "pickup_count", "water_tanker_count",
        "fire_engine_count", "patrol_car_count", "vehicle_r4_other_count",
        "vehicle_r2_motor_count", "tosa_count", "trail_count", "other_vehicle_count",
        "pump_total", "hose_rolls", "nozzle_count", "vehicle_total",
        "pump_total_reported", "vehicle_total_reported",
    ]
    for column in integer_columns:
        if column in combined:
            combined[column] = pd.to_numeric(combined[column], errors="coerce").round().astype("Int64")
    for column in ("area_ha", "latitude", "longitude"):
        if column in combined:
            combined[column] = pd.to_numeric(combined[column], errors="coerce")

    key_columns = [
        "incident_id", "source_document_sha256", "source_page", "area_ha", "soil_type",
        "personnel_total", "vehicle_total", "source_field_provenance_json",
    ]
    record_material = combined[key_columns].astype("string").fillna("")
    combined["record_sha256"] = record_material.agg("|".join, axis=1).map(
        lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    parquet_path = OUT_DIR / "kalteng_historical_resource_deployment_expanded.parquet"
    csv_path = OUT_DIR / "kalteng_historical_resource_deployment_expanded.csv"
    report_path = OUT_DIR / "expanded_quality_report.json"
    source_register_path = OUT_DIR / "expanded_source_register.csv"
    combined.to_parquet(parquet_path, index=False)
    combined.to_csv(csv_path, index=False)
    source_rows = combined.groupby("source_document_id", dropna=False)["incident_id"].agg(list)
    source_register = pd.DataFrame(
        [
            {
                "source_document_id": document_id,
                "source_document_url": metadata[document_id]["source_document_url"],
                "source_document_sha256": metadata[document_id]["sha256"],
                "local_pdf_path": metadata[document_id]["local_path"],
                "page_count": metadata[document_id]["page_count"],
                "publisher": metadata[document_id].get("publisher"),
                "source_row_count": len(incident_ids),
                "incident_ids": json.dumps(incident_ids, ensure_ascii=False),
            }
            for document_id, incident_ids in source_rows.items()
        ]
    ).sort_values("source_document_id")
    source_register.to_csv(source_register_path, index=False)
    report = {
        "status": "passed",
        "dataset": "Central Kalimantan historical wildfire resource deployments — reviewed expansion",
        "rows": int(len(combined)),
        "base_workbook_reviewed_rows": int(len(base)),
        "new_official_ppid_reviewed_rows": int(len(expansion)),
        "unique_source_document_hash_count": int(combined["source_document_sha256"].nunique()),
        "official_ppid_pdf_copies_in_expansion": int(expansion["source_document_id"].nunique()),
        "new_unique_source_hash_count": int(
            len(set(expansion["source_document_sha256"]) - set(base["source_document_sha256"]))
        ),
        "personnel_numeric_rows": int(combined["personnel_total"].notna().sum()),
        "vehicle_numeric_rows": int(numeric_vehicle.sum()),
        "numeric_personnel_and_vehicle_rows": int((combined["personnel_total"].notna() & numeric_vehicle).sum()),
        "high_confidence_personnel_and_vehicle_rows": int(strict_full_resource.sum()),
        "training_eligible_personnel_rows": int(combined["training_eligible_personnel"].fillna(False).astype(bool).sum()),
        "training_eligible_vehicle_rows": int(combined["training_eligible_vehicle"].fillna(False).astype(bool).sum()),
        "resource_mapping_confidence": combined["resource_mapping_confidence"].value_counts(dropna=False).to_dict(),
        "source_policy": "official PPID PDF, retained SHA-256, page-level manual extraction and field-level provenance",
        "source_register": str(source_register_path.relative_to(ROOT)),
        "duplicate_mirror_note": "One expansion PPID copy has the same SHA-256 as an already retained base-source PDF; duplicate incident rows from that copy were excluded.",
        "modeling_boundary": "At least 50 reviewed incident records now exist, but a local resource ML model still requires held-out validation and should not be presented as a dispatch standard.",
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
