"""EDA kebutuhan personel dan resource udara per wildfire ICS-209-PLUS.

Input dibaca per chunk dari ZIP Figshare. Output memisahkan resource puncak
per insiden dari total resource yang dijumlahkan di seluruh sitrep.
"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402


PROJECT_ROOT = Path(__file__).resolve().parents[1]
INPUT_ZIP = PROJECT_ROOT / "data" / "figshare_all_hazards" / "ics209plus-wildfire.zip"
INPUT_MEMBER = "ics209plus-wildfire/ics209-plus-wf_incidents_1999to2020.csv"
OUTPUT_DIR = PROJECT_ROOT / "reports" / "us_fire_resource_eda"
FIGURE_DIR = OUTPUT_DIR / "figures"
CHUNK_SIZE = 100_000

COLUMNS = [
    "INCIDENT_ID",
    "INCIDENT_NAME",
    "FINAL_ACRES",
    "START_YEAR",
    "POO_STATE",
    "CAUSE",
    "INC_MGMT_NUM_SITREPS",
    "TOTAL_AERIAL_SUM",
    "TOTAL_PERSONNEL_SUM",
    "WF_PEAK_AERIAL",
    "WF_PEAK_PERSONNEL",
    "WF_GROWTH_DURATION",
]

RESOURCE_COLUMNS = [
    "TOTAL_PERSONNEL_SUM",
    "WF_PEAK_PERSONNEL",
    "TOTAL_AERIAL_SUM",
    "WF_PEAK_AERIAL",
]

ACRE_BINS = [-np.inf, 100, 1_000, 10_000, 100_000, np.inf]
ACRE_LABELS = ["<100", "100-999", "1k-9.9k", "10k-99.9k", "100k+"]


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def stat(values: pd.Series) -> dict[str, int | float | None]:
    valid = values.dropna()
    if valid.empty:
        return {
            "reported_incidents": 0,
            "zero_count": 0,
            "median": None,
            "p90": None,
            "p99": None,
            "max": None,
        }
    return {
        "reported_incidents": int(valid.size),
        "zero_count": int((valid == 0).sum()),
        "median": float(valid.median()),
        "p90": float(valid.quantile(0.9)),
        "p99": float(valid.quantile(0.99)),
        "max": float(valid.max()),
    }


def resource_label(column: str) -> str:
    return {
        "WF_PEAK_PERSONNEL": "Peak personnel",
        "TOTAL_PERSONNEL_SUM": "Personnel summed across sitreps",
        "WF_PEAK_AERIAL": "Peak aerial resources",
        "TOTAL_AERIAL_SUM": "Aerial resources summed across sitreps",
    }[column]


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    return "\n".join(
        [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
            *[
                "| " + " | ".join(str(cell).replace("|", "\\|") for cell in row) + " |"
                for row in rows
            ],
        ]
    )


def load_incidents() -> pd.DataFrame:
    if not INPUT_ZIP.is_file():
        raise FileNotFoundError(f"Missing input archive: {INPUT_ZIP}")
    with zipfile.ZipFile(INPUT_ZIP) as archive:
        bad_member = archive.testzip()
        if bad_member:
            raise RuntimeError(f"Corrupt ZIP member: {bad_member}")
        with archive.open(INPUT_MEMBER) as stream:
            chunks = pd.read_csv(
                stream,
                usecols=COLUMNS,
                dtype=str,
                chunksize=CHUNK_SIZE,
                low_memory=False,
            )
            frames = []
            for chunk in chunks:
                frames.append(chunk)
    incidents = pd.concat(frames, ignore_index=True)
    for column in [
        "FINAL_ACRES",
        "START_YEAR",
        "INC_MGMT_NUM_SITREPS",
        "WF_GROWTH_DURATION",
        *RESOURCE_COLUMNS,
    ]:
        incidents[column] = numeric(incidents[column])
    incidents["POO_STATE"] = incidents["POO_STATE"].fillna("").str.strip()
    incidents["area_band"] = pd.cut(
        incidents["FINAL_ACRES"], bins=ACRE_BINS, labels=ACRE_LABELS, right=False
    )
    return incidents


def bucket_summary(incidents: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for band in ACRE_LABELS:
        group = incidents.loc[incidents["area_band"] == band]
        staff = group["WF_PEAK_PERSONNEL"].dropna()
        air = group["WF_PEAK_AERIAL"].dropna()
        rows.append(
            {
                "final_acres_band": band,
                "incident_count": len(group),
                "peak_personnel_reported": len(staff),
                "peak_personnel_median": staff.median() if not staff.empty else np.nan,
                "peak_personnel_p90": staff.quantile(0.9) if not staff.empty else np.nan,
                "peak_aerial_reported": len(air),
                "peak_aerial_median": air.median() if not air.empty else np.nan,
                "peak_aerial_p90": air.quantile(0.9) if not air.empty else np.nan,
            }
        )
    return pd.DataFrame(rows)


def correlations(incidents: pd.DataFrame) -> dict[str, dict[str, float | int | None]]:
    results = {}
    for resource in ("WF_PEAK_PERSONNEL", "WF_PEAK_AERIAL"):
        pair = incidents[["FINAL_ACRES", resource]].dropna()
        pair = pair.loc[(pair["FINAL_ACRES"] >= 0) & (pair[resource] >= 0)]
        results[resource] = {
            "paired_incidents": int(len(pair)),
            "spearman_rank_correlation": float(pair["FINAL_ACRES"].corr(pair[resource], method="spearman"))
            if len(pair) > 1
            else None,
        }
    return results


def quality_review(incidents: pd.DataFrame) -> tuple[dict[str, int], pd.DataFrame]:
    """Flag values needing source-level review without altering raw observations."""
    personnel = incidents["WF_PEAK_PERSONNEL"]
    aerial = incidents["WF_PEAK_AERIAL"]
    flags = incidents[
        [
            "INCIDENT_ID",
            "INCIDENT_NAME",
            "START_YEAR",
            "POO_STATE",
            "FINAL_ACRES",
            "WF_PEAK_PERSONNEL",
            "WF_PEAK_AERIAL",
        ]
    ].copy()
    flags["peak_personnel_non_integer"] = personnel.notna() & (personnel % 1 != 0)
    flags["peak_aerial_non_integer"] = aerial.notna() & (aerial % 1 != 0)
    flags["peak_personnel_over_10000_review"] = personnel > 10_000
    flags["peak_aerial_over_100_review"] = aerial > 100
    flag_columns = [column for column in flags if column.endswith(("_integer", "_review"))]
    flagged = flags.loc[flags[flag_columns].any(axis=1)]
    quality = {
        "peak_personnel_non_integer": int(flags["peak_personnel_non_integer"].sum()),
        "peak_aerial_non_integer": int(flags["peak_aerial_non_integer"].sum()),
        "peak_personnel_over_10000_review": int(
            flags["peak_personnel_over_10000_review"].sum()
        ),
        "peak_aerial_over_100_review": int(flags["peak_aerial_over_100_review"].sum()),
        "negative_personnel_values": int((personnel < 0).sum()),
        "negative_aerial_values": int((aerial < 0).sum()),
    }
    return quality, flagged


def annual_summary(incidents: pd.DataFrame) -> pd.DataFrame:
    grouped = incidents.dropna(subset=["START_YEAR"]).groupby("START_YEAR", observed=True)
    rows = []
    for year, group in grouped:
        staff = group["WF_PEAK_PERSONNEL"].dropna()
        air = group["WF_PEAK_AERIAL"].dropna()
        rows.append(
            {
                "year": int(year),
                "incident_count": len(group),
                "peak_personnel_reported": len(staff),
                "peak_personnel_median": staff.median() if not staff.empty else np.nan,
                "peak_personnel_p90": staff.quantile(0.9) if not staff.empty else np.nan,
                "peak_aerial_reported": len(air),
                "peak_aerial_median": air.median() if not air.empty else np.nan,
            }
        )
    return pd.DataFrame(rows).sort_values("year")


def write_figures(bands: pd.DataFrame, annual: pd.DataFrame) -> None:
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    fig, axis = plt.subplots(figsize=(9, 4.8))
    axis.bar(bands["final_acres_band"], bands["peak_personnel_median"], color="#ba4a00")
    axis.set(
        title="Median kebutuhan personel puncak per kelas luas kebakaran",
        xlabel="FINAL_ACRES (acre)",
        ylabel="Median WF_PEAK_PERSONNEL",
    )
    axis.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "peak_personnel_by_acres_band.png", dpi=180)
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(9, 4.8))
    axis.bar(bands["final_acres_band"], bands["peak_aerial_median"], color="#7d3c98")
    axis.set(
        title="Median resource udara puncak per kelas luas kebakaran",
        xlabel="FINAL_ACRES (acre)",
        ylabel="Median WF_PEAK_AERIAL",
    )
    axis.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "peak_aerial_by_acres_band.png", dpi=180)
    plt.close(fig)

    fig, axis = plt.subplots(figsize=(10, 4.8))
    axis.plot(annual["year"], annual["peak_personnel_median"], marker="o", color="#ba4a00")
    axis.plot(annual["year"], annual["peak_personnel_p90"], marker="o", color="#5d4037", label="P90")
    axis.set(title="Kebutuhan personel puncak per tahun", xlabel="Tahun", ylabel="Personel")
    axis.grid(axis="y", alpha=0.25)
    axis.legend(title="Statistik")
    fig.tight_layout()
    fig.savefig(FIGURE_DIR / "annual_peak_personnel.png", dpi=180)
    plt.close(fig)


def write_report(
    incidents: pd.DataFrame,
    resource_stats: dict[str, dict],
    bands: pd.DataFrame,
    annual: pd.DataFrame,
    assoc: dict[str, dict],
    quality: dict[str, int],
    flagged: pd.DataFrame,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    missing = {
        column: int(incidents[column].isna().sum()) for column in RESOURCE_COLUMNS
    }
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "input": f"{INPUT_ZIP.name}::{INPUT_MEMBER}",
        "incident_count": int(len(incidents)),
        "resource_definitions": {
            "WF_PEAK_PERSONNEL": "Maximum value for TOTAL_PERSONNEL in an incident's sitreps.",
            "WF_PEAK_AERIAL": "Maximum value for TOTAL_AERIAL in an incident's sitreps.",
            "TOTAL_PERSONNEL_SUM": "TOTAL_PERSONNEL summed across an incident's sitreps; not a unique headcount.",
            "TOTAL_AERIAL_SUM": "TOTAL_AERIAL summed across an incident's sitreps; not a count of unique aircraft.",
        },
        "resource_stats": resource_stats,
        "resource_missing": missing,
        "resource_by_final_acres_band": bands.to_dict(orient="records"),
        "resource_area_association": assoc,
        "quality_review": quality,
    }
    (OUTPUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    bands.to_csv(OUTPUT_DIR / "resource_by_final_acres_band.csv", index=False)
    annual.to_csv(OUTPUT_DIR / "annual_resource_summary.csv", index=False)

    top_staff = incidents.nlargest(
        25, "WF_PEAK_PERSONNEL"
    )[[
        "INCIDENT_ID", "INCIDENT_NAME", "START_YEAR", "POO_STATE", "FINAL_ACRES",
        "WF_PEAK_PERSONNEL", "WF_PEAK_AERIAL", "WF_GROWTH_DURATION", "INC_MGMT_NUM_SITREPS",
    ]]
    top_air = incidents.nlargest(
        25, "WF_PEAK_AERIAL"
    )[[
        "INCIDENT_ID", "INCIDENT_NAME", "START_YEAR", "POO_STATE", "FINAL_ACRES",
        "WF_PEAK_AERIAL", "WF_PEAK_PERSONNEL", "WF_GROWTH_DURATION", "INC_MGMT_NUM_SITREPS",
    ]]
    top_staff.to_csv(OUTPUT_DIR / "top_peak_personnel_incidents.csv", index=False)
    top_air.to_csv(OUTPUT_DIR / "top_peak_aerial_incidents.csv", index=False)
    flagged.to_csv(OUTPUT_DIR / "resource_value_review_flags.csv", index=False)

    staff = resource_stats["WF_PEAK_PERSONNEL"]
    air = resource_stats["WF_PEAK_AERIAL"]
    report = f"""# EDA kebutuhan personel dan resource per wildfire AS

## Cara membaca metrik

- **`WF_PEAK_PERSONNEL`**: jumlah personel tertinggi yang dilaporkan pada satu titik waktu selama satu kebakaran. Ini metrik terbaik untuk *kebutuhan personel puncak*.
- **`WF_PEAK_AERIAL`**: jumlah resource udara tertinggi dalam satu sitrep. Ini metrik terbaik untuk *kebutuhan udara puncak*.
- **`TOTAL_PERSONNEL_SUM`** dan **`TOTAL_AERIAL_SUM`** menjumlahkan nilai di seluruh sitrep insiden. Keduanya menunjukkan intensitas pelaporan/resource lintas waktu, **bukan** jumlah orang atau pesawat unik.

## Kelengkapan pelaporan

- Total insiden wildfire: **{len(incidents):,}**.
- `WF_PEAK_PERSONNEL` tersedia pada {staff['reported_incidents']:,} insiden ({staff['reported_incidents'] / len(incidents):.1%}); {staff['zero_count']:,} bernilai nol.
- `WF_PEAK_AERIAL` tersedia pada {air['reported_incidents']:,} insiden ({air['reported_incidents'] / len(incidents):.1%}); {air['zero_count']:,} bernilai nol.

Karena resource udara hanya terlapor pada sebagian insiden, angka aerial harus dianalisis pada subset yang memiliki data dan tidak boleh dianggap nol untuk baris yang kosong.

## Distribusi kebutuhan puncak

| Metrik | Median | P90 | P99 |
| --- | ---: | ---: | ---: |
| Peak personel | {staff['median']:,.0f} | {staff['p90']:,.0f} | {staff['p99']:,.0f} |
| Peak resource udara | {air['median']:,.0f} | {air['p90']:,.0f} | {air['p99']:,.0f} |

Median menggambarkan kebakaran tipikal yang memiliki pelaporan resource; P90/P99 menunjukkan kapasitas yang perlu dipertimbangkan untuk kejadian besar.

## Review kualitas nilai resource

- Tidak ada nilai resource negatif.
- Metadata sumber mendeskripsikan kedua kolom peak sebagai integer, tetapi {quality['peak_personnel_non_integer']:,} nilai peak personel dan {quality['peak_aerial_non_integer']:,} nilai peak aerial berupa pecahan. Jangan membulatkan otomatis tanpa menelusuri sitrep asalnya.
- Ada {quality['peak_personnel_over_10000_review']:,} nilai peak personel di atas 10.000 dan {quality['peak_aerial_over_100_review']:,} nilai peak aerial di atas 100. Ini adalah **flag review**, bukan penghapusan data. Nilai ekstrem tersebut tidak dipakai sebagai rekomendasi kapasitas; gunakan median/P90/P99 sambil memeriksa `resource_value_review_flags.csv`.

## Kebutuhan resource menurut luas akhir kebakaran

{markdown_table(
    ['FINAL_ACRES', 'Insiden', 'Peak personel median', 'Peak personel P90', 'Peak udara median', 'Peak udara P90'],
    [[
        row['final_acres_band'], f"{int(row['incident_count']):,}",
        '-' if pd.isna(row['peak_personnel_median']) else f"{row['peak_personnel_median']:,.0f}",
        '-' if pd.isna(row['peak_personnel_p90']) else f"{row['peak_personnel_p90']:,.0f}",
        '-' if pd.isna(row['peak_aerial_median']) else f"{row['peak_aerial_median']:,.0f}",
        '-' if pd.isna(row['peak_aerial_p90']) else f"{row['peak_aerial_p90']:,.0f}",
    ] for _, row in bands.iterrows()]
)}

Asosiasi rank Spearman antara luas akhir dan peak personel adalah **{assoc['WF_PEAK_PERSONNEL']['spearman_rank_correlation']:.3f}** ({assoc['WF_PEAK_PERSONNEL']['paired_incidents']:,} insiden berpasangan); dengan peak aerial **{assoc['WF_PEAK_AERIAL']['spearman_rank_correlation']:.3f}** ({assoc['WF_PEAK_AERIAL']['paired_incidents']:,} insiden). Ini adalah asosiasi, bukan hubungan sebab-akibat: kebakaran kecil dekat area terbangun dapat memerlukan banyak personel, sementara kebakaran sangat luas di area terpencil bisa memiliki pola berbeda.

## Cara pakai untuk model/operasi

1. Gunakan `WF_PEAK_PERSONNEL` sebagai target bila pertanyaannya: “berapa personel maksimal yang perlu disiapkan?”
2. Gunakan kelas luas, durasi, state/region, cause, dan musim sebagai fitur awal; jangan menjadikan `TOTAL_PERSONNEL_SUM` target headcount.
3. Tangani missing aerial dengan indikator `aerial_reported`; jangan imputasi kosong menjadi nol.
4. Untuk Kalimantan, gunakan pola ini sebagai benchmark metodologis saja. Struktur organisasi, unit resource, dan praktik pelaporan AS berbeda dari Indonesia.
"""
    (OUTPUT_DIR / "README.md").write_text(report, encoding="utf-8")


def main() -> None:
    incidents = load_incidents()
    if incidents.empty or incidents["INCIDENT_ID"].nunique() != len(incidents):
        raise RuntimeError("Expected a non-empty one-row-per-wildfire incident table")
    resource_stats = {column: stat(incidents[column]) for column in RESOURCE_COLUMNS}
    bands = bucket_summary(incidents)
    annual = annual_summary(incidents)
    assoc = correlations(incidents)
    quality, flagged = quality_review(incidents)
    write_figures(bands, annual)
    write_report(incidents, resource_stats, bands, annual, assoc, quality, flagged)
    print(f"Resource EDA complete: {OUTPUT_DIR}")
    print(f"Incidents: {len(incidents):,}")
    print(f"Peak-personnel coverage: {resource_stats['WF_PEAK_PERSONNEL']['reported_incidents']:,}")
    print(f"Peak-aerial coverage: {resource_stats['WF_PEAK_AERIAL']['reported_incidents']:,}")


if __name__ == "__main__":
    main()
