"""Append model-input tables to the Feature Engineering source document.

The original document is never overwritten.  The script writes a revision next
to it so the model contract can be read without inferring features from prose.
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_BREAK
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt


SOURCE = Path("/mnt/c/Users/USER/Downloads/Feature_Engineering_Sumber_Data (per fitur).docx")
OUTPUT = Path("/mnt/c/Users/USER/Downloads/Feature_Engineering_Sumber_Data (per fitur) - tabel model.docx")


def add_table(document: Document, headers: list[str], rows: list[tuple[str, str, str]]) -> None:
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Normal Table"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = OxmlElement(f"w:{edge}")
        border.set(qn("w:val"), "single")
        border.set(qn("w:sz"), "4")
        border.set(qn("w:space"), "0")
        border.set(qn("w:color"), "808080")
        borders.append(border)
    table._tbl.tblPr.append(borders)
    header_cells = table.rows[0].cells
    for index, header in enumerate(headers):
        header_cells[index].text = header
        for run in header_cells[index].paragraphs[0].runs:
            run.bold = True
            run.font.size = Pt(8.5)
    for row in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row):
            cells[index].text = value
            cells[index].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            for paragraph in cells[index].paragraphs:
                paragraph.paragraph_format.space_after = Pt(0)
                for run in paragraph.runs:
                    run.font.size = Pt(8.0)
    widths = [Inches(1.3), Inches(3.2), Inches(2.55)]
    for row in table.rows:
        for cell, width in zip(row.cells, widths, strict=True):
            cell.width = width


def main() -> None:
    if not SOURCE.exists():
        raise FileNotFoundError(SOURCE)

    document = Document(SOURCE)
    document.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    document.add_heading("Tabel Input Model yang Digunakan", level=1)
    intro = document.add_paragraph(
        "Tabel ini adalah kontrak input model: hanya kolom yang benar-benar "
        "dikirim ke model saat inferensi dicantumkan. Sumber historis dan "
        "sumber operasional dibedakan bila tidak sama."
    )
    for run in intro.runs:
        run.font.size = Pt(9.5)

    document.add_heading("A. Forecasting Hotspot Mingguan", level=2)
    document.add_paragraph(
        "Dua model menggunakan set input yang sama: classifier untuk probabilitas "
        "kebakaran dan count regressor untuk perkiraan jumlah hotspot tujuh hari berikutnya."
    )
    forecast_rows = [
        (
            "Target model",
            "predicted_fire_probability; predicted_hotspot_count_next_7d",
            "Label dibentuk dari NASA FIRMS VIIRS NOAA-20 pada grid spasial dan jendela prediksi 7 hari.",
        ),
        (
            "Riwayat hotspot grid",
            "hotspot_prev_7d, hotspot_prev_14d, hotspot_prev_30d, hotspot_density_prev_30d, avg_frp_prev_30d, days_since_last_hotspot, days_since_last_hotspot_never",
            "NASA FIRMS VIIRS NOAA-20. Diagregasikan dari histori sebelum minggu yang diprediksi.",
        ),
        (
            "Riwayat hotspot sekitar",
            "neighbour_hotspot_prev_7d, neighbour_hotspot_prev_30d, district_hotspot_prev_7d, district_hotspot_prev_30d, province_hotspot_prev_7d, province_hotspot_prev_30d",
            "NASA FIRMS + geoBoundaries. Batas administrasi hanya dipakai untuk memetakan dan mengagregasi hotspot.",
        ),
        (
            "Pola musiman",
            "seasonal_grid_hotspot_mean_prior, month_sin, month_cos, week_sin, week_cos",
            "Histori NASA FIRMS sebelum periode prediksi; kalender tanggal prediksi. Tidak memakai hotspot masa depan.",
        ),
        (
            "Cuaca dan kekeringan",
            "rainfall_7d, rainfall_14d, rainfall_30d, rainfall_60d, rainfall_30d_anomaly, rainfall_7_to_30_ratio, temperature_mean_7d, temperature_mean_30d, temperature_max_7d, soil_moisture_7d, soil_moisture_30d, dry_days_30d, dry_days_60d",
            "ERA5-Land historis. Diubah menjadi agregat/jeda waktu sebelum minggu target.",
        ),
        (
            "Gambut",
            "peat_pct, peat_dryness_index",
            "Peta Fungsi Ekosistem Gambut BIG; peat_dryness_index = peat_pct dikombinasikan dengan kelembapan tanah ERA5-Land. WRI/GFW hanya data pendukung, bukan kolom input langsung.",
        ),
        (
            "Tutupan lahan dan area terbakar",
            "mapbiomas_lc_natural_share_pct_prev_year, mapbiomas_lc_anthropic_share_pct_prev_year, mapbiomas_lc_peat_swamp_share_pct_prev_year, mapbiomas_lc_oil_palm_share_pct_prev_year, mapbiomas_lc_pulpwood_share_pct_prev_year, mapbiomas_lc_agriculture_share_pct_prev_year, mapbiomas_lc_water_share_pct_prev_year, mapbiomas_burned_share_pct_prev_year, mapbiomas_prev_year_available, landcover_code_2022",
            "MapBiomas Indonesia dan MapBiomas Fire. Selalu memakai tahun sebelumnya yang tersedia agar tidak terjadi temporal leakage.",
        ),
    ]
    add_table(document, ["Kelompok", "Fitur yang masuk model", "Sumber dan proses"], forecast_rows)

    document.add_paragraph()
    document.add_heading("B. Regresi Kebutuhan Personel", level=2)
    document.add_paragraph(
        "Model menghasilkan planned_personnel sebagai referensi perencanaan. "
        "Mobil damkar dihitung setelah prediksi dengan kebijakan kapasitas ceil(personel/6); "
        "ia bukan model regresi terpisah."
    )
    personnel_rows = [
        (
            "Target model",
            "planned_personnel",
            "Label utama: personnel_total dari 50 kasus lokal Kalteng dengan pemetaan sumber daya berkeyakinan tinggi. Data sintetis terpisah dipakai saat training untuk memperluas rentang kondisi, bukan sebagai kolom input.",
        ),
        (
            "Skala insiden",
            "area_ha, log_area_ha",
            "Luas dari laporan insiden lokal pada training; pada dashboard dari marker hasil forecasting yang dipilih/di-merge atau input operator. log_area_ha = log(1 + area_ha).",
        ),
        (
            "Jenis tanah",
            "soil_class",
            "Training: kata kunci gambut/mineral dalam laporan lokal. Runtime: peta gambut BIG (ambang cakupan gambut 10%) dan referensi SoilGrids bila tidak ada konteks BIG; dapat dikonfirmasi operator.",
        ),
        (
            "Tipe api",
            "fire_type_class",
            "Training: tipe api pada laporan lokal bila disebut. Runtime: form observasi operator atau nilai unknown; tidak diinferensikan sebagai fakta lapangan.",
        ),
        (
            "Cuaca kebakaran",
            "fwi_class",
            "Training historis dikalibrasi dari konteks ERA5-Land. Runtime dihitung secara lokal dari prakiraan Open-Meteo menggunakan Canadian FWI calculator; ini konteks cuaca, bukan observasi lapangan.",
        ),
        (
            "Akses air",
            "water_access_index_relative_0_100, nearest_permanent_surface_water_km, water_access_class",
            "JRC Global Surface Water. Indeks relatif dan jarak air permukaan permanen; harus diverifikasi di lapangan, bukan jaminan sumber air dapat dipompa.",
        ),
        (
            "Benchmark skala area",
            "us_area_band, us_peak_personnel_median, us_peak_personnel_p90",
            "Ringkasan ICS-209-PLUS AS berdasarkan band luas. Dipakai sebagai referensi pola/skala dan dikalibrasi terhadap kasus Kalteng; bukan standar pengerahan Indonesia.",
        ),
        (
            "Kompleksitas operasi",
            "operational_complexity_level",
            "Aturan transparan dari kombinasi soil_class, fire_type_class, fwi_class, dan water_access_class: initial_attack, elevated, extended, high, very_high.",
        ),
        (
            "Bukan input/keluaran regresi aktif",
            "OSM fire-station/Manggala Agni; tanker; pickup",
            "OSM hanya menampilkan pos terdekat dan rute pada peta. Jumlah tanker/pickup dicatat sebagai bukti historis, namun belum menjadi output otomatis model. Operator tetap memverifikasi ketersediaan armada.",
        ),
    ]
    add_table(document, ["Kelompok", "Fitur yang masuk model", "Sumber dan proses"], personnel_rows)

    note = document.add_paragraph(
        "Catatan batasan: seluruh keluaran Feature 2 adalah planning reference, bukan perintah pengerahan otomatis. "
        "Kondisi lokasi, air, akses jalan, roster, dan armada wajib dikonfirmasi operator."
    )
    for run in note.runs:
        run.italic = True
        run.font.size = Pt(9)

    document.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    main()
