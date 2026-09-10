# EDA dataset ICS-209-PLUS Amerika Serikat (1999-2020)

## Unit analisis yang tepat

- **All-hazards sitrep** berisi laporan situasi berkala. Satu insiden dapat muncul di banyak baris; jangan menjumlahkan `ACRES`, personel, atau biaya antarbaris karena nilainya berupa snapshot/akumulasi per laporan.
- **Wildfire incidents** lebih tepat untuk analisis kebakaran hutan per kejadian. Satu `INCIDENT_ID` pada tabel ini muncul sekali; tabel ini dapat dipakai sebagai unit insiden.

## Cakupan dan ukuran

- Sitrep all-hazards: **187,115** baris, 35,170 `INCIDENT_ID`, dan 35,773 `EVENT_ID`; tahun 1999-2020.
- Tabel wildfire incidents: **34,622** baris dan 34,622 `INCIDENT_ID`; tahun 1999-2020.
- Koordinat sitrep yang terisi: 184,296 (98.49%). Dari nilai tersebut, 184,296 berada dalam rentang latitude/longitude yang valid secara numerik.

## Insiden menurut tipe laporan

| Tipe insiden | Jumlah sitrep |
| --- | --- |
| Wildfire | 174,207 |
| Wildland Fire Used for Resource Benefit | 6,120 |
| Hurricane | 2,460 |
| Complex | 2,028 |
| Other | 1,107 |
| Prescribed Fire | 490 |
| Flood | 235 |
| Tornado | 165 |
| Hazardous Materials | 118 |
| Search and Rescue / Recovery | 67 |
| Hurricane/Typhoon | 38 |
| Biological or Toxic Conditions | 34 |

Jumlah di atas adalah **jumlah laporan**, bukan jumlah kejadian unik. Tipe yang banyak melapor bisa terlihat dominan walau jumlah kejadiannya tidak sebesar itu.

## Kebakaran hutan: ukuran dan konsentrasi

- `FINAL_ACRES` tersedia untuk 34,622 insiden; total 132,308,832 acre, median 309.0 acre, persentil-90 4,980.0 acre, persentil-99 68,953.4 acre, dan maksimum 1,305,592.0 acre.
- 44.4% dari total `FINAL_ACRES` berasal dari 1% insiden terbesar. Artinya distribusinya sangat condong: rata-rata luas kebakaran akan menyesatkan; gunakan median dan persentil.

### State dengan total `FINAL_ACRES` terbesar

| State | Insiden | Total acre |
| --- | --- | --- |
| AK | 1,241 | 21,636,356 |
| CA | 3,233 | 18,540,494 |
| ID | 2,076 | 11,798,353 |
| NV | 1,051 | 9,639,865 |
| OR | 1,051 | 9,546,627 |
| TX | 2,149 | 7,897,263 |
| MT | 1,448 | 7,186,726 |
| AZ | 1,283 | 6,487,196 |
| WA | 887 | 5,811,147 |
| OK | 3,396 | 5,423,224 |

### Penyebab kebakaran hutan

| Kode | Arti | Insiden |
| --- | --- | --- |
| H | Human | 16,436 |
| L | Lightning/Natural | 10,835 |
| U | Unknown | 6,134 |
| O | Other | 1,011 |

## Kualitas data dan kehati-hatian

- Kolom tanpa nama pada CSV adalah indeks ekspor dan sebaiknya dibuang saat pemodelan.
- Missingness sitrep: `POO_STATE` 4.23% kosong, koordinat latitude 1.51% kosong, `ACRES` 0.0% kosong, dan `NEW_ACRES` 0.0% kosong.
- Missingness wildfire incident: `FINAL_ACRES` 0.0% kosong, `POO_STATE` 3.26% kosong, dan `PROJECTED_FINAL_IM_COST` 35.2% kosong.
- `PROJECTED_FINAL_IM_COST` adalah biaya **proyeksi**, bukan realisasi; jangan dipakai sebagai kerugian aktual tanpa validasi tambahan.
- Dataset mencakup periode historis dan sistem pelaporan yang berubah; analisis tren antar-tahun harus disertai pengecekan perubahan definisi/kelengkapan pelaporan.

## Berkas keluaran

- `annual_sitreps.csv`, `incident_types.csv`, `wildfire_annual.csv`, dan `wildfire_state_summary.csv`: tabel siap EDA lanjutan.
- `largest_wildfires.csv`: 20 insiden berdasarkan `FINAL_ACRES` terbesar.
- `figures/*.png`: tiga visual ringkas.
