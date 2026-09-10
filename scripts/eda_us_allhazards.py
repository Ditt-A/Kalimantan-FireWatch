"""Stream a reproducible EDA of the Figshare US ICS-209-PLUS archives.

The large CSV files stay inside their ZIP archives; this script reads them in
chunks and writes small tables, figures, a JSON summary, and a Markdown report.
"""

from __future__ import annotations

import json
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = PROJECT_ROOT / "data" / "figshare_all_hazards"
OUTPUT_DIR = PROJECT_ROOT / "reports" / "us_allhazards_eda"
FIGURE_DIR = OUTPUT_DIR / "figures"

ALL_HAZARDS_ZIP = INPUT_DIR / "ics209plus-allhazards.zip"
ALL_HAZARDS_MEMBER = (
    "ics209plus-allhazards/ics209-plus_sitreps_1999to2020.csv"
)
WILDFIRE_ZIP = INPUT_DIR / "ics209plus-wildfire.zip"
WILDFIRE_INCIDENT_MEMBER = (
    "ics209plus-wildfire/ics209-plus-wf_incidents_1999to2020.csv"
)

CHUNK_SIZE = 100_000
CAUSE_LABELS = {
    "H": "Human",
    "L": "Lightning/Natural",
    "O": "Other",
    "U": "Unknown",
}


def read_chunks(zip_path: Path, member: str, columns: list[str]) -> Iterable[pd.DataFrame]:
    """Yield selected columns from a CSV member without extracting the archive."""
    with zipfile.ZipFile(zip_path) as archive:
        with archive.open(member) as stream:
            yield from pd.read_csv(
                stream,
                usecols=columns,
                dtype=str,
                chunksize=CHUNK_SIZE,
                low_memory=False,
            )


def values(series: pd.Series) -> list[str]:
    """Return nonblank textual values, treating literal nan as missing."""
    cleaned = series.fillna("").astype(str).str.strip()
    return [value for value in cleaned if value and value.lower() != "nan"]


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    head = "| " + " | ".join(headers) + " |"
    divider = "| " + " | ".join("---" for _ in headers) + " |"
    body = [
        "| " + " | ".join(str(value).replace("|", "\\|") for value in row) + " |"
        for row in rows
    ]
    return "\n".join([head, divider, *body])


def percentage(part: int | float, whole: int | float) -> float | None:
    return round(100 * part / whole, 2) if whole else None


def analyze_all_hazards() -> dict:
    columns = [
        "CY",
        "INCIDENT_ID",
        "EVENT_ID",
        "INCTYP_ABBREVIATION",
        "INCTYP_DESC",
        "POO_STATE",
        "DISCOVERY_DATE",
        "REPORT_TO_DATE",
        "POO_LATITUDE",
        "POO_LONGITUDE",
        "ACRES",
        "NEW_ACRES",
        "TOTAL_PERSONNEL",
        "EST_IM_COST_TO_DATE",
        "FATALITIES",
        "INJURIES",
    ]
    row_count = 0
    annual_sitreps: Counter[str] = Counter()
    incident_ids: set[str] = set()
    event_ids: set[str] = set()
    incident_types: Counter[str] = Counter()
    states: Counter[str] = Counter()
    missing = Counter()
    coordinate_pairs = 0
    valid_coordinate_pairs = 0

    for chunk in read_chunks(ALL_HAZARDS_ZIP, ALL_HAZARDS_MEMBER, columns):
        row_count += len(chunk)
        for column in columns:
            missing[column] += len(chunk) - len(values(chunk[column]))
        annual_sitreps.update(values(chunk["CY"]))
        incident_ids.update(values(chunk["INCIDENT_ID"]))
        event_ids.update(values(chunk["EVENT_ID"]))
        incident_types.update(values(chunk["INCTYP_DESC"]))
        states.update(values(chunk["POO_STATE"]))
        latitude = pd.to_numeric(chunk["POO_LATITUDE"], errors="coerce")
        longitude = pd.to_numeric(chunk["POO_LONGITUDE"], errors="coerce")
        has_pair = latitude.notna() & longitude.notna()
        coordinate_pairs += int(has_pair.sum())
        valid_coordinate_pairs += int(
            (has_pair & latitude.between(-90, 90) & longitude.between(-180, 180)).sum()
        )

    annual_rows = [
        {"year": int(year), "sitrep_count": count}
        for year, count in sorted(annual_sitreps.items(), key=lambda item: int(item[0]))
    ]
    type_rows = [
        {"incident_type": name, "sitrep_count": count}
        for name, count in incident_types.most_common()
    ]
    state_rows = [
        {"state": state, "sitrep_count": count}
        for state, count in states.most_common()
    ]
    return {
        "sitrep_count": row_count,
        "unique_incident_ids": len(incident_ids),
        "unique_event_ids": len(event_ids),
        "year_range": [min(row["year"] for row in annual_rows), max(row["year"] for row in annual_rows)],
        "annual_sitreps": annual_rows,
        "incident_types": type_rows,
        "states_by_sitrep": state_rows,
        "missing_count": dict(missing),
        "coordinate_pair_count": coordinate_pairs,
        "valid_coordinate_pair_count": valid_coordinate_pairs,
    }


def numeric_summary(numbers: list[float]) -> dict[str, float | int | None]:
    if not numbers:
        return {"count": 0, "sum": None, "median": None, "p90": None, "p99": None, "max": None}
    array = np.array(numbers, dtype=float)
    return {
        "count": int(array.size),
        "sum": float(array.sum()),
        "median": float(np.quantile(array, 0.5)),
        "p90": float(np.quantile(array, 0.9)),
        "p99": float(np.quantile(array, 0.99)),
        "max": float(array.max()),
    }


def analyze_wildfires() -> tuple[dict, pd.DataFrame]:
    columns = [
        "INCIDENT_ID",
        "INCIDENT_NAME",
        "FINAL_ACRES",
        "CAUSE",
        "DISCOVERY_DATE",
        "START_YEAR",
        "POO_STATE",
        "POO_LATITUDE",
        "POO_LONGITUDE",
        "INC_MGMT_NUM_SITREPS",
        "FATALITIES",
        "INJURIES_TOTAL",
        "STR_DESTROYED_TOTAL",
        "PROJECTED_FINAL_IM_COST",
        "WF_PEAK_PERSONNEL",
        "WF_GROWTH_DURATION",
    ]
    row_count = 0
    incident_ids: set[str] = set()
    annual_count: Counter[str] = Counter()
    annual_acres: defaultdict[str, float] = defaultdict(float)
    state_count: Counter[str] = Counter()
    state_acres: defaultdict[str, float] = defaultdict(float)
    causes: Counter[str] = Counter()
    missing = Counter()
    final_acres: list[float] = []
    top_candidates: list[pd.DataFrame] = []

    for chunk in read_chunks(WILDFIRE_ZIP, WILDFIRE_INCIDENT_MEMBER, columns):
        row_count += len(chunk)
        for column in columns:
            missing[column] += len(chunk) - len(values(chunk[column]))
        incident_ids.update(values(chunk["INCIDENT_ID"]))
        years = values(chunk["START_YEAR"])
        annual_count.update(years)
        causes.update(values(chunk["CAUSE"]))
        state_values = chunk["POO_STATE"].fillna("").astype(str).str.strip()
        state_values = state_values.mask(state_values.str.lower().eq("nan"), "")
        state_count.update(value for value in state_values if value)
        acres = pd.to_numeric(chunk["FINAL_ACRES"], errors="coerce")
        valid_acres = acres[acres.notna() & (acres >= 0)]
        final_acres.extend(valid_acres.astype(float).tolist())
        prepared = chunk.assign(_acres=acres, _state=state_values)
        for _, row in prepared.loc[prepared["_acres"].notna() & (prepared["_acres"] >= 0)].iterrows():
            state = row["_state"]
            year = str(row["START_YEAR"]).strip()
            area = float(row["_acres"])
            if state:
                state_acres[state] += area
            if year and year.lower() != "nan":
                annual_acres[year] += area
        top_candidates.append(
            prepared.loc[
                prepared["_acres"].notna() & (prepared["_acres"] >= 0),
                ["INCIDENT_ID", "INCIDENT_NAME", "START_YEAR", "POO_STATE", "CAUSE", "FINAL_ACRES", "_acres"],
            ].nlargest(25, "_acres")
        )

    annual_rows = [
        {
            "year": int(float(year)),
            "incident_count": annual_count[year],
            "final_acres_sum": annual_acres.get(year, 0.0),
        }
        for year in sorted(annual_count, key=lambda value: float(value))
    ]
    state_rows = [
        {
            "state": state,
            "incident_count": state_count[state],
            "final_acres_sum": state_acres.get(state, 0.0),
        }
        for state in state_count
    ]
    state_rows.sort(key=lambda row: row["final_acres_sum"], reverse=True)
    candidates = pd.concat(top_candidates, ignore_index=True)
    largest = candidates.nlargest(20, "_acres").drop(columns="_acres")
    acreage = numeric_summary(final_acres)
    sorted_acres = np.sort(np.array(final_acres, dtype=float))[::-1]
    top_one_percent = max(1, int(np.ceil(len(sorted_acres) * 0.01)))
    acreage["top_1_percent_share"] = float(sorted_acres[:top_one_percent].sum() / sorted_acres.sum()) if sorted_acres.sum() else None
    return (
        {
            "incident_row_count": row_count,
            "unique_incident_ids": len(incident_ids),
            "duplicate_incident_id_rows": row_count - len(incident_ids),
            "year_range": [min(row["year"] for row in annual_rows), max(row["year"] for row in annual_rows)],
            "annual": annual_rows,
            "states": state_rows,
            "causes": [
                {"code": code, "label": CAUSE_LABELS.get(code, code), "incident_count": count}
                for code, count in causes.most_common()
            ],
            "missing_count": dict(missing),
            "final_acres": acreage,
        },
        largest,
    )


def write_figures(all_hazards: dict, wildfires: dict) -> None:
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    annual = pd.DataFrame(all_hazards["annual_sitreps"])
    fig, axis = plt.subplots(figsize=(11, 4.5))
    axis.plot(annual["year"], annual["sitrep_count"], marker="o", color="#a83803")
    axis.set(title="US ICS-209-PLUS: laporan situasi per tahun", xlabel="Tahun", ylabel="Jumlah sitrep")
    axis.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "annual_sitrep_count.png", dpi=180)
    plt.close(fig)

    types = pd.DataFrame(all_hazards["incident_types"]).head(12).sort_values("sitrep_count")
    fig, axis = plt.subplots(figsize=(10, 5.5))
    axis.barh(types["incident_type"], types["sitrep_count"], color="#d96825")
    axis.set(title="Tipe insiden dengan sitrep terbanyak", xlabel="Jumlah sitrep", ylabel="")
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "sitrep_by_incident_type.png", dpi=180)
    plt.close(fig)

    states = pd.DataFrame(wildfires["states"]).head(12).sort_values("final_acres_sum")
    fig, axis = plt.subplots(figsize=(10, 5.5))
    axis.barh(states["state"], states["final_acres_sum"] / 1_000_000, color="#8c2d04")
    axis.set(title="Luas akhir kebakaran hutan menurut state", xlabel="Juta acre", ylabel="State")
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "wildfire_acres_by_state.png", dpi=180)
    plt.close(fig)


def write_outputs(all_hazards: dict, wildfires: dict, largest: pd.DataFrame) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(all_hazards["annual_sitreps"]).to_csv(OUTPUT_DIR / "annual_sitreps.csv", index=False)
    pd.DataFrame(all_hazards["incident_types"]).to_csv(OUTPUT_DIR / "incident_types.csv", index=False)
    pd.DataFrame(wildfires["annual"]).to_csv(OUTPUT_DIR / "wildfire_annual.csv", index=False)
    pd.DataFrame(wildfires["states"]).to_csv(OUTPUT_DIR / "wildfire_state_summary.csv", index=False)
    largest.to_csv(OUTPUT_DIR / "largest_wildfires.csv", index=False)
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "all_hazards_sitrep": f"{ALL_HAZARDS_ZIP.name}::{ALL_HAZARDS_MEMBER}",
            "wildfire_incidents": f"{WILDFIRE_ZIP.name}::{WILDFIRE_INCIDENT_MEMBER}",
        },
        "all_hazards_sitreps": all_hazards,
        "wildfire_incidents": wildfires,
    }
    (OUTPUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    acreage = wildfires["final_acres"]
    all_missing = all_hazards["missing_count"]
    wf_missing = wildfires["missing_count"]
    wildfire_unit_note = (
        "Satu `INCIDENT_ID` pada tabel ini muncul sekali; tabel ini dapat dipakai "
        "sebagai unit insiden."
        if wildfires["duplicate_incident_id_rows"] == 0
        else (
            f"Ada {wildfires['duplicate_incident_id_rows']:,} baris dengan `INCIDENT_ID` "
            "berulang; lakukan deduplikasi bila unit analisis harus insiden unik."
        )
    )
    report = f"""# EDA dataset ICS-209-PLUS Amerika Serikat (1999-2020)

## Unit analisis yang tepat

- **All-hazards sitrep** berisi laporan situasi berkala. Satu insiden dapat muncul di banyak baris; jangan menjumlahkan `ACRES`, personel, atau biaya antarbaris karena nilainya berupa snapshot/akumulasi per laporan.
- **Wildfire incidents** lebih tepat untuk analisis kebakaran hutan per kejadian. {wildfire_unit_note}

## Cakupan dan ukuran

- Sitrep all-hazards: **{all_hazards['sitrep_count']:,}** baris, {all_hazards['unique_incident_ids']:,} `INCIDENT_ID`, dan {all_hazards['unique_event_ids']:,} `EVENT_ID`; tahun {all_hazards['year_range'][0]}-{all_hazards['year_range'][1]}.
- Tabel wildfire incidents: **{wildfires['incident_row_count']:,}** baris dan {wildfires['unique_incident_ids']:,} `INCIDENT_ID`; tahun {wildfires['year_range'][0]}-{wildfires['year_range'][1]}.
- Koordinat sitrep yang terisi: {all_hazards['coordinate_pair_count']:,} ({percentage(all_hazards['coordinate_pair_count'], all_hazards['sitrep_count'])}%). Dari nilai tersebut, {all_hazards['valid_coordinate_pair_count']:,} berada dalam rentang latitude/longitude yang valid secara numerik.

## Insiden menurut tipe laporan

{markdown_table(['Tipe insiden', 'Jumlah sitrep'], [[row['incident_type'], f"{row['sitrep_count']:,}"] for row in all_hazards['incident_types'][:12]])}

Jumlah di atas adalah **jumlah laporan**, bukan jumlah kejadian unik. Tipe yang banyak melapor bisa terlihat dominan walau jumlah kejadiannya tidak sebesar itu.

## Kebakaran hutan: ukuran dan konsentrasi

- `FINAL_ACRES` tersedia untuk {acreage['count']:,} insiden; total {acreage['sum']:,.0f} acre, median {acreage['median']:,.1f} acre, persentil-90 {acreage['p90']:,.1f} acre, persentil-99 {acreage['p99']:,.1f} acre, dan maksimum {acreage['max']:,.1f} acre.
- {wildfires['final_acres']['top_1_percent_share']:.1%} dari total `FINAL_ACRES` berasal dari 1% insiden terbesar. Artinya distribusinya sangat condong: rata-rata luas kebakaran akan menyesatkan; gunakan median dan persentil.

### State dengan total `FINAL_ACRES` terbesar

{markdown_table(['State', 'Insiden', 'Total acre'], [[row['state'], f"{row['incident_count']:,}", f"{row['final_acres_sum']:,.0f}"] for row in wildfires['states'][:10]])}

### Penyebab kebakaran hutan

{markdown_table(['Kode', 'Arti', 'Insiden'], [[row['code'], row['label'], f"{row['incident_count']:,}"] for row in wildfires['causes']])}

## Kualitas data dan kehati-hatian

- Kolom tanpa nama pada CSV adalah indeks ekspor dan sebaiknya dibuang saat pemodelan.
- Missingness sitrep: `POO_STATE` {percentage(all_missing['POO_STATE'], all_hazards['sitrep_count'])}% kosong, koordinat latitude {percentage(all_missing['POO_LATITUDE'], all_hazards['sitrep_count'])}% kosong, `ACRES` {percentage(all_missing['ACRES'], all_hazards['sitrep_count'])}% kosong, dan `NEW_ACRES` {percentage(all_missing['NEW_ACRES'], all_hazards['sitrep_count'])}% kosong.
- Missingness wildfire incident: `FINAL_ACRES` {percentage(wf_missing['FINAL_ACRES'], wildfires['incident_row_count'])}% kosong, `POO_STATE` {percentage(wf_missing['POO_STATE'], wildfires['incident_row_count'])}% kosong, dan `PROJECTED_FINAL_IM_COST` {percentage(wf_missing['PROJECTED_FINAL_IM_COST'], wildfires['incident_row_count'])}% kosong.
- `PROJECTED_FINAL_IM_COST` adalah biaya **proyeksi**, bukan realisasi; jangan dipakai sebagai kerugian aktual tanpa validasi tambahan.
- Dataset mencakup periode historis dan sistem pelaporan yang berubah; analisis tren antar-tahun harus disertai pengecekan perubahan definisi/kelengkapan pelaporan.

## Berkas keluaran

- `annual_sitreps.csv`, `incident_types.csv`, `wildfire_annual.csv`, dan `wildfire_state_summary.csv`: tabel siap EDA lanjutan.
- `largest_wildfires.csv`: 20 insiden berdasarkan `FINAL_ACRES` terbesar.
- `figures/*.png`: tiga visual ringkas.
"""
    (OUTPUT_DIR / "README.md").write_text(report, encoding="utf-8")


def main() -> None:
    for zip_path in (ALL_HAZARDS_ZIP, WILDFIRE_ZIP):
        if not zip_path.is_file():
            raise FileNotFoundError(f"Missing input archive: {zip_path}")
        with zipfile.ZipFile(zip_path) as archive:
            bad_member = archive.testzip()
            if bad_member:
                raise RuntimeError(f"Corrupt ZIP member: {bad_member}")
    all_hazards = analyze_all_hazards()
    wildfires, largest = analyze_wildfires()
    write_figures(all_hazards, wildfires)
    write_outputs(all_hazards, wildfires, largest)
    print(f"EDA complete: {OUTPUT_DIR}")
    print(f"All-hazards sitreps: {all_hazards['sitrep_count']:,}")
    print(f"Wildfire incident rows: {wildfires['incident_row_count']:,}")


if __name__ == "__main__":
    main()
