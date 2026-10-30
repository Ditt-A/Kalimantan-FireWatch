# Menjalankan pipeline dan deployment

Status implementasi: kode dan kandidat model tersedia; pembaruan provider dan deployment publik belum diaktifkan otomatis. Snapshot dashboard lama tetap tersedia sampai sebuah release baru dipublish secara eksplisit.

## 1. Perubahan yang sudah dibangun

- API mem-pin satu release untuk seluruh request. Cache data/model mengikuti release; publish dan rollback memakai pointer atomik dan checksum.
- Selection peta membawa release_id ke planner. Selection dari versi lama ditolak dengan 409 agar luas/konteks tidak tercampur.
- Scorer terpisah dari trainer dan tidak membutuhkan label masa depan.
- Model operasional memakai window cuaca yang tertinggal tujuh hari pada grid mingguan, untuk mengakomodasi latensi ERA5.
- Hujan ERA5-Land dikoreksi: tp pukul 00 UTC menjadi total hari sebelumnya. Nilai kumulatif tiap jam tidak dijumlahkan. Parser juga memeriksa 24 observasi suhu/soil moisture per hari. Rujukan: https://confluence.ecmwf.int/pages/viewpage.action?pageId=315578204
- FWI memilih noon terakhir yang sudah lewat; state historis dipersist dan periode fetched dihitung ulang untuk revisi.
- Luas incident memiliki sumber dan konfirmasi tersendiri. Backend menghitung luas geodesik polygon GeoJSON; footprint FIRMS tetap berstatus belum terkonfirmasi.
- Observasi tipe api terikat incident_id, observed_at, masa berlaku 24 jam, dan actor. Observasi lama yang hanya mempunyai grid tidak digunakan otomatis untuk insiden baru.
- SQLite tetap dapat digunakan lokal. PostgreSQL menjadi penyimpanan audit/observasi pada profil Compose.
- Job ingestion, weather, forecast dan backup mempunyai lock, status dan audit. Kegagalan job mempertahankan release sebelumnya.
- Login operator memakai session cookie bertanda tangan. Profil production mewajibkan secret; monitoring viewer dapat dibaca tanpa login. Ini akses per role dengan shared access code, belum manajemen akun personal/SSO.

## 2. Setup lokal

Dari root project:

```bash
python -m pip install -r requirements-dev.txt
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 python -m unittest discover -s tests -v
cd web
npm ci
npm run build
```

Gunakan Python 3.14 dan dependency versi requirements-production.txt untuk artefak yang tersedia. Mengganti versi sklearn secara bebas dapat membuat joblib tidak kompatibel.

Jalankan API dengan uvicorn dan dashboard seperti README. FIREWATCH_AUTH_REQUIRED=0 mempertahankan mode lokal; production memakai FIREWATCH_AUTH_REQUIRED=1.

## 3. Bootstrap dan validasi model

```bash
# Membuat daily weather corrected terpisah dari artefak lama.
python scripts/rebuild_operational_weather.py

# Melatih candidate forecast dari tahun 2023–2025, test temporal 2026.
# Path candidate harus kosong; pilih --model-dir baru untuk eksperimen berikutnya.
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 python scripts/forecast_pipeline.py train

# Replay feature builder + scorer dengan data historis, tanpa publikasi.
python scripts/check_operational_forecast.py --week 2026-07-20

# Audit real-only tanpa mengubah model serving.
python scripts/evaluate_personnel_grouped.py

# Nested real-data validation: config jitter dipilih hanya pada inner folds.
python scripts/train_personnel_jitter_candidate.py
```

Hasil forecast baru berada di models/candidates/forecast_lag7_corrected/. Hasil personel berada di models/candidates/personnel_jitter/. Provenance origin disimpan sebagai sidecar; tidak dimasukkan ke fitur training.

Jangan mengganti label deployment asli dengan jitter. Generator hanya menggunakan real train fold. Inner validation dan outer test memakai kasus asli tanpa jitter; pengelompokan menggabungkan source_document_id dan incident_or_evidence_id yang berhubungan.

## 4. Update data dan scoring operasional

Server memerlukan FIRMS_MAP_KEY serta CDSAPI_URL/CDSAPI_KEY untuk refresh sumber. Provider credentials dibaca dari environment server. Open-Meteo API key/endpoint dapat dikonfigurasi sesuai paket dan terms penggunaan.

```bash
# Ingest melakukan lookback dan rekonsiliasi observasi, kemudian publish alert.
python scripts/etl_worker.py --job firms

# Refresh raw ERA5 lalu rebuild daily corrected; gunakan latest available cutoff.
python scripts/etl_worker.py --job era5

# Mengisi persistent weather cache untuk lokasi yang dikonfigurasi.
python scripts/etl_worker.py --job weather

# Menjalankan builder, scorer, lalu publish apabila semua input siap.
python scripts/etl_worker.py --job forecast

# Menjalankan jadwal berulang dengan retry terkontrol.
python scripts/etl_worker.py --loop
```

FIRMS ingestion memeriksa availability provider. Jika interval kosong karena arsip NRT tidak menjangkau gap, lakukan backfill eksplisit dari sumber yang sesuai; job tidak mengisi gap tersebut sebagai nol.

Weather locations berada pada deploy/weather_locations.json. Dua lokasi contoh bukan cakupan seluruh Kalimantan. Tambahkan lokasi/cell sesuai area operasi dan anggaran API. API planner juga dapat mengambil cuaca saat dibutuhkan dan menggunakan cache persisten.

Fitur forecast membutuhkan successful FIRMS watermark dan 60 hari cuaca lengkap untuk grid yang eligible. Hari tanpa hotspot dapat valid; keberhasilan ingest dibuktikan oleh watermark, bukan ada/tidaknya deteksi.

ERA5 mask lokal menyediakan 4.553 grid dengan seluruh variabel lengkap dari 4.672 grid spasial. Grid tanpa cuaca lengkap tidak diberi prediksi Low. Eligible-mask hash disimpan di kontrak model; perubahan cakupan harus direview. Forecast yang baru dapat dipublish bila coverage eligible memenuhi kontrak. Tidak ada interpolasi soil moisture laut sebagai nilai lapangan.

Perintah manual:

```bash
python scripts/forecast_pipeline.py features --week YYYY-MM-DD
python scripts/forecast_pipeline.py score
python scripts/forecast_pipeline.py publish --release-id forecast-YYYYMMDD
python scripts/forecast_pipeline.py rollback --release-id RELEASE_SEBELUMNYA
```

--week harus Senin. Forecast adalah window mingguan Senin–Senin; jangan menyebutnya tujuh hari penuh setelah klik apabila diterbitkan di tengah minggu. Window yang sudah berakhir ditolak secara default. --allow-historical hanya untuk replay/evaluasi eksplisit.

## 5. Production dengan Docker Compose

1. Aktifkan integrasi distro Ubuntu pada Docker Desktop, atau gunakan Docker Engine di server Linux.
2. Buat deploy/.env dari contoh. Gunakan secret berbeda yang acak: session minimal 32 karakter, access code operator minimal 24 karakter. Gunakan password database yang URL-safe, misalnya hex.
3. Isi domain dan DNS. Default localhost/bind 127.0.0.1 untuk akses mesin lokal. Untuk server publik, set domain nyata dan bind address sesuai jaringan yang disiapkan.
4. Siapkan Git LFS data/model serta kandidat forecast corrected pada volume host.
5. Jalankan:

```bash
docker compose --env-file deploy/.env -f deploy/compose.yaml config --quiet
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --build
docker compose --env-file deploy/.env -f deploy/compose.yaml ps
```

Caddy menyediakan HTTPS; domain publik perlu DNS dan port yang sesuai. Sertifikat localhost memakai trust lokal yang harus dikonfigurasi di browser. Cookie operator production bersifat Secure dan HttpOnly.

Health: /health menguji proses. /ready memeriksa konfigurasi auth, akses database, dataset dan personel model. /api/v1/metadata/status menampilkan release, freshness tiap layer dan status job.

Credential file .env di-ignore. Image build mengecualikan data, models, log, dan credential; artefak runtime dimount melalui volume. Worker tidak melatih model atau mempromosikan kandidat personel secara otomatis.

## 6. Backup dan pemulihan

```bash
python scripts/etl_worker.py --job backup
```

SQLite memakai online backup dan integrity check. PostgreSQL memakai pg_dump format custom dan checksum manifest. File pg_dump tidak menyediakan PITR; jika diperlukan, tambah base backup dan WAL archiving sebagai pekerjaan operasional terpisah.

Salin backup serta release/model yang dirujuk ke storage independen. Volume backups dalam Compose masih berada pada host yang sama; volume itu sendiri belum menjadi disaster recovery.

Uji restore ke database terpisah sebelum menetapkan RPO/RTO. Jangan restore langsung ke database produksi untuk pengujian. Pertahankan release yang sedang dipakai dan satu atau lebih release rollback. Retensi storage perlu ditetapkan; pipeline tidak otomatis menghapus arsip.

## 7. Hasil evaluasi dan batasan tersisa

Forecast corrected + lag tujuh hari, pada subset test temporal 2026 yang eligible:

- ROC-AUC: 0,8613; PR-AUC: 0,3261; precision: 0,3702; recall: 0,3426; F1: 0,3559.
- Count MAE: 0,1802; RMSE: 1,2521.
- Bukan perbandingan head-to-head dengan angka lama karena unit eligible dan data cuaca sudah dikoreksi.
- Arsip preliminary dan first-availability historis belum lengkap; validasi shadow prospektif masih diperlukan.

Personel, 43 kasus nyata dan 18 kelompok sumber/insiden:

- Grouped OOF awal: Huber MAE 7,765; hybrid boosting 8,794.
- Nested validation eksperimen jitter: selected candidate MAE 8,754; reference tanpa jitter 8,880; Huber 7,765.
- Jitter ringan terpilih untuk final candidate, tetapi peningkatan kecil dan belum mengalahkan baseline sederhana.
- Missingness cuaca/air pada kasus nyata, cakupan wilayah, dan label kebutuhan optimum belum terselesaikan.
- Kandidat personel belum mengganti bobot serving. Nilai kecil pada test sintetis tidak dipakai untuk menyatakan akurasi lapangan.

Arsip cuaca corrected yang tersedia lokal berakhir 30 Agustus 2026. Snapshot dashboard tetap bertanggal aslinya sampai sumber diperbarui dan sebuah release diaktifkan. Menjalankan scorer tidak membuat data Oktober muncul secara otomatis.

Polygon dari operator adalah estimasi bersumber yang dapat dikonfirmasi, belum survei perimeter independen. Estimasi otomatis dari citra dan label optimal kebutuhan kru masih membutuhkan data/validasi tambahan.

Pemeriksaan stack Docker lokal sudah lulus: HTTPS, health/readiness, halaman Next.js, proxy API, forecast grid, login operator, model personel, penyimpanan observasi PostgreSQL, penolakan selection stale, serta backup dan restore ke database terpisah. Stack dan volume fixture dihapus setelah pemeriksaan. Laporan ada di reports/production_checks/docker_stack.json. Tidak ada job akuisisi provider yang diaktifkan oleh tes ini.

Unit/regression suite berisi 14 tes dan lulus. Replay feature builder/scorer memakai data nyata menghasilkan 4.553 grid tanpa memerlukan label target, dengan release serving tetap sama. Build image backend dan frontend berhasil; image frontend standalone sekitar 354 MB.

Implementasi ini menutup gap engineering dasar. Konfigurasi domain/credential, refresh data provider hingga periode operasi, backup di luar host, restore drill di lingkungan tujuan, biaya/terms provider, dan validasi lapangan tetap diperlukan sebelum SLA operasional ditetapkan.
