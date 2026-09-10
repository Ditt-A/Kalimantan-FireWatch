# EDA kebutuhan personel dan resource per wildfire AS

## Cara membaca metrik

- **`WF_PEAK_PERSONNEL`**: jumlah personel tertinggi yang dilaporkan pada satu titik waktu selama satu kebakaran. Ini metrik terbaik untuk *kebutuhan personel puncak*.
- **`WF_PEAK_AERIAL`**: jumlah resource udara tertinggi dalam satu sitrep. Ini metrik terbaik untuk *kebutuhan udara puncak*.
- **`TOTAL_PERSONNEL_SUM`** dan **`TOTAL_AERIAL_SUM`** menjumlahkan nilai di seluruh sitrep insiden. Keduanya menunjukkan intensitas pelaporan/resource lintas waktu, **bukan** jumlah orang atau pesawat unik.

## Kelengkapan pelaporan

- Total insiden wildfire: **34,622**.
- `WF_PEAK_PERSONNEL` tersedia pada 27,252 insiden (78.7%); 0 bernilai nol.
- `WF_PEAK_AERIAL` tersedia pada 11,954 insiden (34.5%); 0 bernilai nol.

Karena resource udara hanya terlapor pada sebagian insiden, angka aerial harus dianalisis pada subset yang memiliki data dan tidak boleh dianggap nol untuk baris yang kosong.

## Distribusi kebutuhan puncak

| Metrik | Median | P90 | P99 |
| --- | ---: | ---: | ---: |
| Peak personel | 27 | 277 | 1,284 |
| Peak resource udara | 2 | 9 | 21 |

Median menggambarkan kebakaran tipikal yang memiliki pelaporan resource; P90/P99 menunjukkan kapasitas yang perlu dipertimbangkan untuk kejadian besar.

## Review kualitas nilai resource

- Tidak ada nilai resource negatif.
- Metadata sumber mendeskripsikan kedua kolom peak sebagai integer, tetapi 3,447 nilai peak personel dan 228 nilai peak aerial berupa pecahan. Jangan membulatkan otomatis tanpa menelusuri sitrep asalnya.
- Ada 1 nilai peak personel di atas 10.000 dan 2 nilai peak aerial di atas 100. Ini adalah **flag review**, bukan penghapusan data. Nilai ekstrem tersebut tidak dipakai sebagai rekomendasi kapasitas; gunakan median/P90/P99 sambil memeriksa `resource_value_review_flags.csv`.

## Kebutuhan resource menurut luas akhir kebakaran

| FINAL_ACRES | Insiden | Peak personel median | Peak personel P90 | Peak udara median | Peak udara P90 |
| --- | --- | --- | --- | --- | --- |
| <100 | 7,632 | 13 | 138 | 2 | 7 |
| 100-999 | 17,595 | 20 | 153 | 2 | 6 |
| 1k-9.9k | 7,266 | 59 | 427 | 2 | 9 |
| 10k-99.9k | 1,923 | 205 | 1,077 | 5 | 13 |
| 100k+ | 206 | 472 | 1,828 | 8 | 21 |

Asosiasi rank Spearman antara luas akhir dan peak personel adalah **0.426** (27,252 insiden berpasangan); dengan peak aerial **0.267** (11,954 insiden). Ini adalah asosiasi, bukan hubungan sebab-akibat: kebakaran kecil dekat area terbangun dapat memerlukan banyak personel, sementara kebakaran sangat luas di area terpencil bisa memiliki pola berbeda.

## Cara pakai untuk model/operasi

1. Gunakan `WF_PEAK_PERSONNEL` sebagai target bila pertanyaannya: “berapa personel maksimal yang perlu disiapkan?”
2. Gunakan kelas luas, durasi, state/region, cause, dan musim sebagai fitur awal; jangan menjadikan `TOTAL_PERSONNEL_SUM` target headcount.
3. Tangani missing aerial dengan indikator `aerial_reported`; jangan imputasi kosong menjadi nol.
4. Untuk Kalimantan, gunakan pola ini sebagai benchmark metodologis saja. Struktur organisasi, unit resource, dan praktik pelaporan AS berbeda dari Indonesia.
