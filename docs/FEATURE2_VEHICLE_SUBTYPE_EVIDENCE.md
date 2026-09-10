# Feature 2: evidensi kendaraan per insiden

`data/processed/feature2_vehicle_evidence/feature2_vehicle_subtype_evidence.parquet`
adalah register tambahan untuk Response Planning & Resource Calculator. Isinya
15 insiden karhutla di Kalimantan yang memiliki hitungan positif eksplisit
untuk minimal satu jenis kendaraan. Pada versi awal ini terdapat 13 sumber
primer/resmi dan dua laporan berita yang dipisahkan secara eksplisit.

Setiap baris membawa `source_url`, `source_kind`, `source_publisher`, SHA-256
artefak HTML mentah, lokasi artefak lokal, serta
`source_field_provenance_json`. JSON tersebut menunjukkan kutipan/locator dan
sumber untuk setiap nilai area, personel, mobil damkar, tanker, atau pickup.

## Aturan label

| Kolom | Arti |
| --- | --- |
| `fire_engine_count_explicit_positive` | Jumlah mobil damkar yang sumbernya sebut secara eksplisit. |
| `water_tanker_count_explicit_positive` | Jumlah mobil tangki / water tank yang disebut eksplisit. |
| `pickup_count_explicit_positive` | Jumlah pickup/pikap; Hilux double-cab dicatat sebagai pickup dengan bukti teksnya. |
| `documented_vehicle_lower_bound` | Minimal kendaraan yang benar-benar dihitung sumber; bukan total armada sebenarnya. |
| `personnel_total` | Hanya ada bila semua kelompok personel dalam operasi dihitung sumber. |
| `personnel_known_lower_bound` | Jumlah minimum bila ada dukungan tambahan yang tidak diberi hitungan. |

`null` pada suatu subtype berarti **tidak dilaporkan**, bukan nol kendaraan.
Label ini tidak boleh diubah menjadi data negatif untuk melatih model.

## Hasil validasi saat dibuat

- 15 insiden: Kaltim 12, Kalsel 2, Kalbar 1.
- 7 insiden dengan total personel yang eksplisit.
- 10 observasi mobil damkar, 9 tanker, 3 pickup yang positif eksplisit.
- Seluruh artefak raw dan hash diverifikasi oleh
  `scripts/validate_feature2_vehicle_subtype_evidence.py`.

## Pemakaian Feature 2

Register ini cocok untuk *case library*, tampilan contoh kejadian, dan
validasi eksternal terhadap rekomendasi rule-based. Ia **belum** cukup untuk
melatih model regresi mobil damkar/tanker/pickup secara mandiri: observasi
masih sedikit dan hanya mengandung label positif yang dilaporkan.

Dataset ini dipisahkan dari cohort PDF Kalteng 2018–2019. Jangan menggabungkan
baris berita atau baris lintas provinsi itu ke cohort pelatihan tanpa proses
deduplikasi dan penilaian sumber baru.
