# Arsip data besar lokal — 30 Oktober 2026

Indeks ini mencatat 80 file proyek berukuran minimal 10 MiB, total 4.858.003.070 byte. Semua salinan Google Drive dicocokkan dengan ukuran file dan MD5 lokal. SHA-256 juga disimpan untuk pemeriksaan setelah pemulihan. Data tetap mengikuti folder sumbernya di Drive; `file_index.csv` memetakan path lokal ke path Drive dan ID file.

Folder data Drive: https://drive.google.com/drive/u/0/folders/1OsHEAWEAl57AKlB6iIkKYiDlof6-hV0E

Indeks dan petunjuk ini juga disimpan di:

```text
metadata/project_archives/compfest_academy_2_2026-10-30/
```

Subfolder `recovered_from_commit_a3b57bb/` menyimpan 107 file hasil olahan terbaru yang dipulihkan dari Git LFS: cuaca ERA5 terkoreksi, akuisisi FIRMS September–Oktober, dan snapshot FIRMS terkurasi. Ukuran, MD5, dan SHA-256 ada di `recovered_manifest.json`. Paket tambahan ini terpisah dari 80 file snapshot lokal pada `local_manifest.json`.

## Memulihkan file besar ke project

Gunakan Python 3.9+ dan rclone yang sudah terhubung ke Drive. Jalankan dari root project:

```bash
# Lihat daftar tanpa mengunduh.
python3 docs/data_archive_20261030/restore_large_data.py --project "$PWD" --list

# Pulihkan seluruh file besar, sesuai path asli.
python3 docs/data_archive_20261030/restore_large_data.py --project "$PWD" --remote mygdrive:

# Atau hanya file data processed.
python3 docs/data_archive_20261030/restore_large_data.py --project "$PWD" --match 'data/processed/*'
```

File yang sudah cocok akan dilewati. Unduhan ditulis ke file sementara dan baru menggantikan tujuan sesudah ukuran, MD5, serta SHA-256 lolos pemeriksaan. API key dan konfigurasi akun rclone tidak disertakan dalam arsip ini.

Untuk mengambil paket hasil olahan terbaru secara terpisah:

```bash
rclone copy mygdrive:metadata/project_archives/compfest_academy_2_2026-10-30/recovered_from_commit_a3b57bb ./recovered_from_commit_a3b57bb \
  --drive-root-folder-id 1OsHEAWEAl57AKlB6iIkKYiDlof6-hV0E \
  --checksum --transfers 1 --checkers 2 --buffer-size 8M --multi-thread-streams 0
```

Sesudah pembersihan lokal, pulihkan dataset yang dibutuhkan sebelum menjalankan dashboard, training, atau pipeline.
