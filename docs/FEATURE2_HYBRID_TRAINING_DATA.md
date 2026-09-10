# Feature 2 hybrid training data

Folder `data/processed/feature2_hybrid_training/` membuat data Feature 2
siap dilatih tanpa menyamarkan asal bukti.

> Status: this is the upstream hybrid scenario package. The direct MVP model
> package is now `data/processed/feature2_model_ready/`, documented in
> `docs/FEATURE2_MODEL_READY_DATA.md`. It narrows the calculator to personnel
> and a personnel-derived fire-engine planning capacity; tanker/pickup files in
> this upstream package are retained as evidence, not served as MVP targets.

## Tiga folder dengan schema yang sama

| Folder | Isi | Dipakai model? |
| --- | --- | --- |
| `real/` | Hanya observasi berlabel dari laporan Kalteng dan register kendaraan lintas Kalimantan. | Ya, untuk audit dan evaluasi terbatas. |
| `synthetic/` | Skenario rekomendasi yang dihasilkan deterministik dengan seed terdokumentasi. | Ya, hanya sebagai augmentation. |
| `training/` | Gabungan `real` dan `synthetic` yang diacak deterministik. | Ya, ini file yang langsung dimuat untuk training. |

Setiap folder memiliki empat file: `personnel`, `fire_engine`,
`water_tanker`, dan `pickup` dalam format Parquet dan CSV. Kolom target selalu
bernama `target_value`; nama file menentukan targetnya. Tidak ada kolom
`is_synthetic`, `simulation_only`, `data_origin`, URL, atau hash sumber di
file `training/`, sehingga provenance tidak menjadi fitur dan tidak dapat
mendongkrak metrik model secara palsu.

Provenance tetap tersedia di:

- data sumber asli Feature 2;
- `synthetic_generation_manifest.json`;
- file `real/` dan `synthetic/` yang terpisah;
- `hybrid_training_validation.json`.

## Acuan pembuatan skenario

1. **Personel** memakai `WF_PEAK_PERSONNEL` ICS-209-PLUS AS per band luas
   sebagai bentuk/kuantil prior, bukan angka staffing Indonesia. Nilainya
   dipetakan kembali ke median/P90 dari 43 observasi personel lokal dan trend
   Huber terhadap `log1p(area_ha)`.
2. **Kompleksitas** memilih kuantil lokal `.35`, `.50`, `.65`, `.80`, atau
   `.90` berdasarkan kombinasinya: gambut, tipe api bawah/campuran, FWI tinggi,
   serta akses air relatif rendah. NWCG menyatakan kompleksitas memang dinilai
   dari kombinasi indikator yang dapat berubah, bukan luas saja.
3. **Mobil damkar, tanker, dan pickup** menggunakan aturan diskret dengan
   batas hasil observasi kendaraan eksplisit lokal (0–4 untuk tanker/pickup,
   1–4 untuk mobil damkar). Ini adalah skenario rekomendasi, bukan klaim bahwa
   setiap insiden historis memakai angka tersebut.
4. **Gambut dan air** hanya mengeskalasi kelas kompleksitas/logistik. Dasarnya
   adalah pedoman dan bukti bahwa api gambut lebih sulit dipadamkan, dapat
   membara bawah permukaan, serta membutuhkan air dan akses yang andal.

Referensi aturan disimpan dengan URL di manifest; rujukan utama adalah
[NWCG Risk and Complexity Assessment](https://www.nwcg.gov/announcement/publications/updated-nwcg-wildland-fire-risk-and-complexity-assessment-pms-236),
[State of Indonesia's Forests 2020](https://kemlu.go.id/files-service/storage/repositori/72942/State-Forest-of-Indonesia-2020.pdf),
dan [manual Wetlands International](https://indonesia.wetlands.org/publication/manual-for-the-control-of-fire-in-peatlands-and-peatland-forest/).

## Batas penggunaan

- Jangan menamai target sintetis sebagai data pengerahan historis.
- Jangan memakai metrik cross-validation acak pada file gabungan sebagai bukti
  akurasi operasional; lakukan evaluasi utama pada holdout data riil.
- Model hasilnya adalah **planning recommendation**, bukan dispatch otomatis.
- Area skenario harus berasal dari operator/perimeter terverifikasi, tidak
  diestimasi langsung dari jumlah titik FIRMS.
