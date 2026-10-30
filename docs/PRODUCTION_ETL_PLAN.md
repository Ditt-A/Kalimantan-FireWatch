# Rancangan ETL Production — Kalimantan FireWatch

Tanggal audit: 30 Oktober 2026. Status: rancangan implementasi; scheduler, database production, dan deployment yang dijelaskan di sini belum dibangun oleh dokumen ini.

## 1. Keputusan dan batas penggunaan

Pipeline dapat dikembangkan menjadi layanan monitoring hotspot dan dukungan perencanaan dengan pembaruan terjadwal. Repository saat ini menjalankan dashboard berbasis snapshot, bukan pipeline production otomatis. Model personel dapat dipakai dalam pilot dengan pemeriksaan operator; kelayakan pengerahan operasional memerlukan validasi tambahan pada insiden lokal nyata.

ETL menyediakan data yang konsisten dan dapat diaudit. ETL sendiri tidak mengatasi keterlambatan satelit, tidak mengubah footprint piksel menjadi luas terbakar, dan tidak membuktikan akurasi model di lapangan.

Scope yang dipertahankan:

- Feature 1: prediksi keberadaan hotspot dan jumlah deteksi pada grid 0,1 derajat untuk tujuh hari.
- Feature 2: estimasi personel, lalu proxy kapasitas mobil damkar berdasarkan ceil(personel / 6).
- Lokasi pos, air, gambut, dan cuaca sebagai konteks keputusan.
- Empat bobot 35/25/25/15 belum termasuk pipeline aktif. Pembangunannya membutuhkan kontrak scoring dan layer exposure tersendiri.

## 2. Kondisi repository yang diaudit

| Komponen | Implementasi sekarang | Gap menuju production |
|---|---|---|
| FIRMS | scripts/scrape_firms.py: download per blok, retry, manifest dan cache file; default VIIRS_NOAA20_SP | Polling NRT terjadwal, rekonsiliasi revisi/SP, identitas deteksi stabil, watermark |
| Grid/feature | src/firewatch/data_pipeline.py: build batch dan fact_grid_week | Incremental processing, cutoff saat keputusan, feature history yang berversi |
| Forecasting | src/firewatch/modeling.py: temporal training, seleksi model, ekspor prediksi untuk test 2026 | Scoring artefak existing pada minggu yang belum memiliki label; scheduler terpisah |
| Dashboard | api/main.py membaca prediction_grid_week.parquet dan firms_kalimantan_clean.parquet | Publish atomik satu release; invalidasi cache dan freshness |
| Personel | HistGradientBoostingRegressor + aturan kapasitas mobil | Validasi lokal independen, interval/penanda ketidakpastian dan monitoring |
| Weather/FWI | Open-Meteo saat request, cache memori per jam; Canadian FWI dari histori dan initial state standar | Akuisisi terjadwal, persistent state FWI, pemisahan observed/model/forecast, degradasi saat gagal |
| Field observation | SQLite lokal; pencarian terbaru berdasarkan grid | PostgreSQL, incident_id, event time, masa berlaku, otorisasi dan audit |
| Routing | Default OSRM publik, pos OSM | Server/paket layanan routing sendiri; freshness dan status armada belum ada |
| Deployment | Next.js development server, FastAPI lokal, ngrok | Build production, HTTPS, service restart, health checks, backup, akses operator |

Bukti kode: src/firewatch/modeling.py, src/firewatch/data_pipeline.py, src/firewatch/fire_weather.py, src/firewatch/field_observations.py, api/main.py, models/dashboard_model_registry.json, reports/model_evaluation.json, reports/feature2_modeling/feature2_mixed_holdout_origin_audit.json.

## 3. Arsitektur yang diusulkan

Gunakan satu scheduler batch terlebih dahulu, object storage untuk arsip/Parquet/raster, PostgreSQL + PostGIS untuk data operasional, serta FastAPI dan Next.js untuk serving. Semua job harus mempunyai run_id, checkpoint, dan satu lock per dataset/partition. Tidak perlu streaming broker untuk cadence awal di bawah ini.

Alur dependensi:

1. Scheduler memeriksa ketersediaan sumber dan watermark terakhir.
2. Extract menulis payload asli dan manifest ke raw.
3. Validate raw memisahkan payload valid, schema berubah, respons error, dan respons kosong yang sah.
4. Transform membuat tabel deteksi, cuaca, dan konteks spasial yang bersih.
5. Build features membuat input sesuai schema/model dan cutoff keputusan.
6. Score memuat model approved; tidak melatih ulang model.
7. Validate release menguji kualitas data, kontrak API, dan kesesuaian artefak.
8. Publish mengaktifkan satu release lengkap; semua worker membaca release yang sama.
9. Monitor mengukur freshness, kelengkapan, drift, dan kegagalan.

Dua jalur berjalan independen:

- Alert NRT diperbarui saat deteksi satelit baru tersedia.
- Forecast mingguan hanya diperbarui jika semua fitur yang dipersyaratkan tersedia dan horizon prediksi masih berlaku.

Alert yang lebih baru tidak boleh memaksa forecast historis diberi tanggal hari ini.

## 4. Sumber, jadwal, dan kredensial

Cadence di tabel adalah rancangan awal, bukan SLA sumber. Ukur latensi dan biaya sebelum menetapkannya sebagai SLA.

| Sumber | Cadence awal | Extract dan kredensial | Catatan |
|---|---|---|---|
| FIRMS NOAA-20 NRT | Tiap 30 menit, lookback 3 hari | Area API + FIRMS_MAP_KEY; cek data availability dan kuota | Batasi ke lima provinsi Indonesia setelah bbox query; tidak mencakup seluruh Borneo |
| FIRMS Standard Processing | Pemeriksaan mingguan/bulanan | MAP_KEY yang sama, arsip bila diperlukan | SP dan NRT untuk observasi yang sama direkonsiliasi, bukan dijumlahkan |
| ERA5-Land | Tiap hari, hanya tanggal yang tersedia | CDS API, kredensial dan persetujuan terms dataset | Update sekitar lima hari tertinggal; versi preliminary dapat direvisi |
| Open-Meteo | Tiap 3 jam untuk lokasi cuaca unik | Endpoint sesuai paket; free untuk penggunaan nonkomersial, paket commercial bila sesuai | Batch/cache lokasi meteorologis yang sama; bukan satu request per klik atau per marker |
| BIG gambut, SoilGrids, boundaries | Cek versi bulanan; refresh saat ada rilis | Endpoint resmi/unduhan terversi | Simpan vintage, CRS, lisensi, hash dan cakupan |
| MapBiomas | Cek rilis bulanan, ingest ketika tahun/versi baru tersedia | Unduhan resmi | Tahun produk tidak sama dengan tanggal produk tersedia |
| JRC surface water | Saat ada versi baru | Unduhan raster resmi | Air historis tidak memastikan kedalaman, izin atau kemampuan dipompa |
| OSM pos dan jaringan jalan | Cek mingguan, pembaruan terjadwal | Extract OSM; routing dari graph lokal yang versinya diketahui | Pos terpetakan tidak membuktikan adanya kru, mobil atau helikopter |
| Laporan lokal personel | Bulanan + intake laporan baru | Sumber resmi/berita bersumber jelas; review manusia | PDF/text/OCR tidak langsung menjadi label trusted |
| Observasi lapangan | Per event melalui API terotorisasi | Identitas operator, incident_id dan audit | Terpisah dari proses scrape |

Kunci disimpan di environment/secret manager server. Jangan gunakan argumen CLI berisi key, URL FIRMS mentah dalam log, atau file credential sebagai artefak job. Manifest menyimpan URL yang token/path credential-nya telah disensor.

## 5. Storage dan lineage

Konvensi object storage yang diusulkan:

- raw/{source}/{product}/event_date=YYYY-MM-DD/run_id=.../payload
- curated/firms/event_date=.../part-....parquet
- curated/weather/provider=.../date=.../part-....parquet
- features/schema_version=.../as_of=.../part-....parquet
- releases/{release_id}/data/processed/...
- releases/{release_id}/models/...
- releases/{release_id}/manifest.json

Raw bersifat immutable. Revisi sumber menambah versi baru dengan hubungan supersedes. Setiap tabel turunan dapat dilacak ke input checksum, versi transformasi, dan run_id.

Tabel kontrol di PostgreSQL yang diusulkan:

| Tabel | Isi utama |
|---|---|
| etl_runs | job, run_id, parameter/cutoff, mulai/selesai, status, source manifest, code SHA |
| source_watermarks | source/product, waktu terakhir diminta, complete-through, retry/revision window |
| dataset_versions | dataset_id, schema_version, object URI, checksum, row count, event range, coverage |
| dataset_releases | release_id, semua dataset/model version, QC result, status approved/current |
| weather_samples | weather cell/provider/model run, valid_time, issued_at, available_at, units |
| fwi_states | weather cell, local date/noon, FFMC/DMC/DC, initialization, algorithm version |
| incident_observations | incident_id, geometry, observed_at, recorded_at, fire type, area, verification, actor |
| response_plan_audit | request/incident, input resolved, release/model/policy version, output, operator override |

Manifest release minimal: release_id, created_at_utc, code_sha, schema_version, grid_version, input source/version/hash, complete_through per sumber, forecast_issue_time, feature_as_of, forecast_valid_from/to, model_sha256, policy_version, quality_status, dan sample evidence. Tanggal pengambilan data tidak boleh menggantikan tanggal observasi atau periode prediksi.

## 6. Transformasi penting

### 6.1 Deteksi FIRMS

- Baca response dengan schema ketat; HTML/error berstatus 200 tidak boleh dianggap CSV sukses.
- Bedakan no observations dari request gagal. Nol deteksi valid dapat diterima jika request, schema, dan cakupannya lengkap.
- Simpan event time UTC, ingestion time UTC, product, satellite, sensor dan versi sumber.
- Clip polygon kelima provinsi; bbox saja masih mencakup wilayah luar Indonesia.
- Buat identitas observasi stabil dari identitas sumber/pixel bila tersedia, atau kombinasi satellite/instrument/time/coordinate yang divalidasi. FRP dan confidence adalah atribut yang dapat direvisi, bukan identitas tetap.
- Retain NRT/SP sebagai provenance terpisah lalu terapkan precedence untuk satu observasi canonical. Jangan menggabungkan satelit tambahan ke model lama tanpa evaluasi pergeseran distribusi.
- Replay lookback wajib membaca ulang raw window. Downloader sekarang melewati chunk yang sudah ada; diperlukan revision-aware adapter agar lookback benar-benar mengoreksi data lama.

### 6.2 Cuaca dan FWI

- Validasi K ke Celsius, meter hujan ke mm, akumulasi hujan per hari, RH persen, angin km/jam, serta data hilang pada jam yang wajib.
- Gunakan timezone lokasi untuk FWI local noon: Kalimantan meliputi WIB dan WITA. Agregasi forecasting harus mempertahankan definisi UTC/tanggal yang dipakai model, sampai versi baru dilatih.
- FWI state disimpan harian per lokasi meteorologis. Revisi hari sebelumnya memicu perhitungan ulang state downstream.
- Pisahkan FWI hari observasi/model terakhir dan FWI forecast. Simpan forecast issue time dan valid time; jangan mengambil hari forecast terakhir sebagai seolah-olah kondisi hari ini.
- Initial state standar dan warm-up 30 hari perlu diuji, terutama DC pada periode kering; FWI bukan pengukuran kedalaman gambut atau tipe api.
- Kegagalan provider menghasilkan status unavailable atau stale dengan umur data yang jelas, bukan default cuaca low.

### 6.3 Fitur forecasting

Gunakan satu implementasi feature transform yang sama untuk training dan serving, dengan schema registry dari artefak approved. Gunakan spatial joins yang mempertahankan grid_id dan versi batas wilayah.

Catatan kompatibilitas pada kode saat ini:

- Nama hotspot_prev_30d dan avg_frp_prev_30d saat ini dihitung dari empat minggu sebelumnya, sekitar 28 hari. Jangan diam-diam mengubahnya menjadi 30 hari saat serving; perbaikan definisi harus menjadi schema baru disertai retraining.
- dry_days_30d/60d menghitung jumlah hari hujan di bawah 1 mm pada window, bukan panjang dry spell berturut-turut.
- Beberapa fitur MapBiomas merupakan agregat provinsi, sedangkan landcover_code_2022 adalah layer statis grid. Jangan mengklaim seluruhnya komposisi lokal detail.
- Pemilihan MapBiomas saat ini dibatasi pada produk hingga 2024 di kode. Ganti dengan lookup vintage berbasis available_at; model baru harus dievaluasi bila coverage/definisi produk berubah.
- Untuk backtest, event_time sebelum cutoff saja belum cukup: available_at sumber juga harus sebelum issue_time. Peta annual tahun sebelumnya belum tentu sudah dirilis saat prediksi dibuat.
- Inference row tidak memerlukan fire_next_7d atau hotspot_next_7d. Label hanya dihitung setelah target window lengkap dan sumber telah tiba.
- Riwayat minimal 60 hari diperlukan untuk fitur cuaca; pola musiman dan days_since_last_hotspot membutuhkan state historis lebih panjang. Incremental refresh tidak boleh memotong semua histori menjadi 60 hari saja.

### 6.4 Konteks response planning

- Peat minimal 10% dipertahankan sebagai policy konteks grid; simpan share sebenarnya dan metode point/overlap. Ini belum bukti bahwa seluruh perimeter insiden adalah gambut.
- Water Access Index, jarak air, tanah, dan area harus mengacu pada lokasi/geometry dan release yang sama.
- Display grouping FIRMS menghasilkan footprint piksel, bukan perimeter terbakar. Autofill mempertahankan nilai ini sebagai starting proxy dengan sumber yang jelas. Untuk perencanaan lapangan gunakan luas teramati/polygon yang dapat dikonfirmasi operator.
- Area grid forecasting bukan luas satu insiden; default area lokal tidak boleh diperlakukan sebagai hasil estimasi perimeter.
- Observasi tipe api ditautkan ke incident_id, waktu, dan masa berlaku. Implementasi latest-by-grid saat ini dapat membawa observasi lama ke insiden baru dalam grid yang sama.
- Label personel yang tidak disebut tetap unknown, bukan nol. Shared resource beberapa insiden tidak disalin penuh ke setiap insiden.
- Provenance tetap ada di tabel audit terpisah meskipun bukan fitur model.

## 7. Scoring baru dan keterlambatan ERA5

Komponen baru yang perlu dibangun: build_current_feature_snapshot dan score_current_forecast, terpisah dari train_and_predict.

Langkah scoring: pilih approved model dan feature contract; tetapkan issue_time serta target tujuh hari; ambil hanya input yang tersedia saat itu; bangun seluruh grid eligible; jalankan classifier/count regressor; terapkan threshold versi model; validasi; publish.

Model lama menggunakan histori ERA5 tanpa menguji skenario keterlambatan operasional secara penuh. Mengisi lima hari ERA5 yang belum tersedia dengan Open-Meteo secara langsung dapat mengubah distribusi input.

Dua jalur yang sah:

1. Tetap gunakan ERA5 dengan cutoff tertinggal. Tampilkan feature_as_of dan valid_from/to sebenarnya. Jika horizon sudah lewat, hasil hanya historis; jangan menggeser tanggal atau mengklaim forecast tujuh hari dari hari ini.
2. Bangun model operasional baru dengan fitur cuaca yang benar-benar tersedia saat issue time: delayed-ERA5 atau historical forecast/model weather yang diarsipkan. Replay histori menggunakan lag/provider yang sama, latih ulang, uji temporal, dan kalibrasi sebelum promosi.

Jalur kedua adalah target untuk forecast yang berlaku dari hari penerbitan. Memilih NRT untuk hotspot tidak menghilangkan latensi input cuaca.

## 8. Orkestrasi, publish, dan recovery

Job usulan:

| Job baru | Pemicu | Output/promosi |
|---|---|---|
| ingest_firms_nrt | 30 menit | Raw revision -> canonical detections -> alert release |
| reconcile_firms_sp | Mingguan | Revisi canonical -> recompute partition terdampak |
| ingest_era5 | Harian setelah availability check | Daily weather + completeness watermark |
| ingest_model_weather_and_fwi | 3 jam | Weather cells, current/forecast FWI, status |
| refresh_static_context | Cek bulanan/per versi | Grid context baru; QC delta cakupan |
| build_current_feature_snapshot | Mingguan setelah input ready | Feature snapshot point-in-time |
| score_current_forecast | Setelah feature snapshot lolos QC | Prediksi model approved, tanpa retraining |
| publish_release | Setelah quality gate | Satu release valid untuk data dan model |
| evaluate_and_train_candidate | Bulanan/manual, label telah matang | Candidate + laporan; tidak auto-promote |
| import_reviewed_local_incidents | Setelah review | Label lokal dan provenance baru |

Retry transient error dengan backoff/jitter dan batas percobaan; 401/403 ditangani sebagai konfigurasi/otorisasi, bukan retry tanpa batas. Eksekusi ulang run dengan input hash sama harus menghasilkan output semantik yang sama tanpa duplikasi. Run yang gagal tidak memajukan complete watermark.

Aturan publish:

1. Tulis seluruh artefak ke release staging baru.
2. Validasi schema, hash, join, timestamp dan sample response.
3. Transaksi database mengaktifkan release_id setelah semua file siap.
4. API memperoleh release_id baru dan mengganti cache secara konsisten pada seluruh worker.
5. Pertahankan release sebelumnya untuk rollback; snapshot gagal tidak mengganti current.

Migrasi minimal dapat mempertahankan format Parquet dan API sekarang: mount folder release yang immutable, pasang bundle baru, lalu rolling restart worker yang membaca release tersebut. Untuk multiworker, symlink swap tanpa cache invalidation tidak cukup karena lru_cache tetap menyimpan data lama. Tahap berikutnya pindahkan lookup operasional ke PostGIS dan cache berdasarkan release_id.

Frontend membawa release_id bersama selection/area/grouping parameters ke planner. Bila release berubah, backend mengembalikan status selection expired atau melakukan re-resolve dengan penjelasan sumber, agar area dari marker lama tidak dicampur dengan konteks baru.

## 9. Quality gates dan monitoring

Syarat promosi:

- Semua source requests yang dibutuhkan berhasil atau memiliki no-data status yang sah.
- CRS WGS84 untuk koordinat; luas/jarak dihitung pada sistem proyeksi/metode yang sesuai.
- Kunci grid/date unik, canonical detection tidak duplikat, spatial join dan rentang waktu lengkap.
- Weather window wajib lengkap; file parsial/daily hours tidak lengkap masuk quarantine.
- Tidak ada feature dengan available_at setelah issue_time.
- Kolom input, tipe, category encoder, model checksum, dan threshold cocok.
- Nilai prediksi finite dan dalam domain; area > training support ditandai untuk review.
- Bandingkan delta jumlah deteksi/priority antar release serta tiap provinsi. Outlier memicu review, bukan dipaksa menyerupai distribusi lama.
- Smoke check health, priority/grid, planner-context, response-plans, response landmarks dan frontend proxy terhadap staging release.
- Tanggal stale ditampilkan terpisah untuk hotspot, forecast, cuaca, dan peta statis. Metadata status tunggal not_live=false yang ada sekarang perlu diganti per-layer freshness.

Pantau duration/error/retry job, provider quota, complete-through, missing features, cache/release version, API latency dan data/model drift. Setelah label matang, pantau PR-AUC, recall/top-K per provinsi, count MAE/RMSE, calibration dan coverage. Threshold alert numerik ditetapkan setelah baseline monitoring, bukan klaim SLA saat ini.

Jika sumber baru gagal, tetap sajikan last-known-good dengan timestamp dan status stale. Jangan menampilkan pipeline error sebagai nol hotspot atau risiko Low.

## 10. Model governance dan batasan personel

Audit lokal menunjukkan MAE mixed-holdout personel 1,429, tetapi subset nyata hanya sembilan test rows dengan MAE 7,564 personel. Subset sintetis memiliki MAE 1,101. Angka agregat tidak mewakili akurasi operasi lapangan.

Audit juga menemukan:

- Statistik seluruh label lokal dipakai untuk kalibrasi sintetis sebelum random split; ini membuat evaluasi belum independen.
- Missingness FWI/water/complexity membedakan origin nyata dan sintetis.
- Fitur US/complexity sekaligus dipakai membentuk label sintetis; model bisa terutama meniru generator.
- Label mobil sintetis mengikuti rumus yang sama dengan policy pembanding.

Evaluasi production candidate harus memisahkan incident/document/time groups sebelum membuat sintetis; generator dan normalizer hanya fit pada train fold. Lakukan enrichment real secara point-in-time, holdout lokal nyata independen, evaluasi per ukuran/jenis lahan dan provinsi, serta review operator. Tetap laporkan provenance dan missingness pada audit. Jangan hanya menghapus provenance column lalu menganggap risiko leakage selesai.

Personel historis adalah deployment yang terjadi, belum tentu kebutuhan optimum. Data AS adalah benchmark skala, bukan standar Indonesia. Policy enam personel per mobil merupakan proxy kapasitas kru, tidak membuktikan akses kendaraan atau ketersediaan fleet.

## 11. Deployment production dan urutan pengerjaan

Tahap A — data freshness: bangun scheduler, object layout, watermark, ingest NRT, weather cache, quality gate dan release atomik. Hasil awal: monitoring yang diperbarui dan snapshot forecast bertanggal jelas.

Tahap B — current forecasting: pisahkan feature builder dari label builder dan scorer dari trainer; selesaikan latency/weather contract; backtest operasional dan promosi model baru. Hasil: forecast tujuh hari dari issue time yang dapat diuji.

Tahap C — response pilot: PostgreSQL untuk observasi/audit, incident_id, validasi area, review output dan enrichment kasus lokal. Tetap gunakan operator approval untuk personel/mobil.

Tahap D — deployment layanan: container/runtime terkunci sesuai artefak sklearn/joblib, build Next.js production, FastAPI di belakang HTTPS reverse proxy, scheduler terpisah, authentication/role untuk write endpoints, secret manager, structured logging, health/readiness, backup dan restore drill. Ngrok tetap untuk demo.

Tahap E — validasi operasional: holdout insiden nyata, review lintas provinsi, calibration dan aturan kapan operator wajib override. Baru tetapkan SLA, kapasitas layanan dan batas dukungan model.

Acceptance test sebelum release operasional:

1. Run/replay ulang tidak menduplikasi deteksi; revisi NRT/SP tidak dihitung dua kali.
2. Tidak ada data dari luar lima provinsi yang ikut akibat bbox.
3. Request sukses tanpa deteksi dibedakan dari provider failure.
4. Backfill hari hujan merevisi rolling features dan state FWI terkait.
5. Forecast masa depan dapat dibuat tanpa label target dan tanpa training ulang.
6. Setiap fitur lolos pengecekan available_at <= issue_time.
7. Request peta -> planner mengacu pada release, geometry, grouping, area dan sumber yang konsisten.
8. Pergantian release terbaca semua worker; rollback mengembalikan bundle lengkap.
9. Gangguan cuaca/routing tidak menghasilkan low-risk palsu; stale/unavailable terlihat.
10. Artefak dapat dimuat dengan dependency terkunci, secret tidak terpublikasi, backup operasional dapat dipulihkan.

## 12. Rujukan resmi untuk dependensi eksternal

- NASA FIRMS API: kebutuhan MAP_KEY, data availability dan beda NRT/SP: https://firms.modaps.eosdis.nasa.gov/content/academy/data_api/firms_api_use.html
- Copernicus Climate Reanalysis: ERA5/ERA5-Land update sekitar lima hari tertinggal dan produk preliminary: https://climate.copernicus.eu/climate-reanalysis
- Open-Meteo Terms: batas penggunaan API free/nonkomersial, paket commercial dan atribusi data: https://open-meteo.com/en/terms
- Open-Meteo official project: opsi self-host serta penggunaan API: https://github.com/open-meteo/open-meteo

Ketentuan eksternal diperiksa 30 Oktober 2026; availability aktual harus dibaca dari provider saat job berjalan.
