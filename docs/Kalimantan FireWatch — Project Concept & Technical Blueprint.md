# Kalimantan FireWatch
## Spatio-Temporal Analysis and Prediction of Fire Hotspot Risk in Kalimantan

---

## 1. Project Overview

### 1.1 Project Title

**Kalimantan FireWatch: Spatio-Temporal Analysis and Prediction of Fire Hotspot Risk**

### 1.2 Main Idea

Kalimantan FireWatch merupakan proyek analisis data geospasial yang bertujuan untuk memahami pola historis hotspot kebakaran di Kalimantan, mengidentifikasi karakteristik lingkungan yang berkaitan dengan kemunculan hotspot, serta memprediksi wilayah yang berpotensi mengalami aktivitas hotspot pada periode berikutnya.

NASA FIRMS digunakan sebagai sumber utama data deteksi hotspot. Data tersebut kemudian diperkaya menggunakan data:

- cuaca dan kondisi permukaan;
- lahan gambut;
- tutupan lahan;
- batas administratif;
- serta burned area jika diperlukan untuk validasi.

Fokus utama dashboard adalah **satu geomap interaktif Kalimantan**. Pengguna dapat mengganti informasi yang ditampilkan pada peta melalui beberapa mode sesuai research/business question.

---

# 2. Main Research / Business Objective

Pertanyaan utama proyek:

> **Where and when are fire hotspots concentrated in Kalimantan, what environmental characteristics are associated with their occurrence, and which areas are most likely to experience hotspot activity in the next seven days?**

Secara sederhana, proyek harus mampu menjawab:

1. **Apa yang telah terjadi?**
2. **Di mana hotspot paling banyak ditemukan?**
3. **Kapan periode hotspot meningkat?**
4. **Kondisi lingkungan seperti apa yang banyak ditemukan pada wilayah fire-prone?**
5. **Di mana hotspot kemungkinan muncul selanjutnya?**
6. **Berapa banyak hotspot yang diperkirakan muncul?**
7. **Wilayah mana yang perlu diprioritaskan untuk monitoring?**

---

# 3. Core Analytical Framework

Alur utama proyek:

**Historical Fire Activity**

↓

**Spatial Distribution**

↓

**Environmental Characteristics**

↓

**Predictive Fire Risk**

↓

**Expected Hotspot Activity**

↓

**Monitoring Priority**

Dengan struktur tersebut, dashboard tidak hanya bersifat deskriptif tetapi berkembang menjadi:

**Descriptive → Diagnostic/Associative → Predictive → Decision Support**

---

# 4. Data Sources

## 4.1 Primary Dataset — NASA FIRMS

### Dataset

**NASA FIRMS Active Fire / VIIRS**

### Fungsi

Menjadi dataset utama untuk mengidentifikasi aktivitas hotspot di Kalimantan.

### Variabel utama

- `latitude`
- `longitude`
- `acq_date`
- `acq_time`
- `confidence`
- `frp`
- `brightness`
- `satellite`
- `daynight`

### Periode utama

**2024–2026**

### Informasi yang digunakan

NASA FIRMS digunakan untuk mengetahui:

- lokasi deteksi hotspot;
- waktu deteksi;
- jumlah hotspot;
- tingkat confidence;
- Fire Radiative Power atau FRP;
- pola temporal;
- pola spasial.

### Catatan

Satu record FIRMS sebaiknya disebut:

**active fire / hotspot detection**

dan bukan langsung dianggap sebagai satu kejadian kebakaran independen.

---

# 5. Supporting Datasets

## 5.1 Indonesian Province Maps with Population

### Status

**Supporting dataset dari daftar dataset wajib tugas.**

### Fungsi utama

Memberikan:

- polygon provinsi;
- nama provinsi;
- batas administratif Kalimantan.

Data populasi dapat digunakan jika diperlukan, tetapi bukan fokus utama proyek.

### Join dengan NASA FIRMS

Tidak memerlukan common ID.

Digabung menggunakan:

**Spatial Join / Point-in-Polygon**

Contoh:

`FIRMS latitude-longitude`

↓

`Province polygon`

↓

`Province = Kalimantan Tengah`

---

## 5.2 Administrative Boundary — Kabupaten/Kota

### Source

**geoBoundaries Indonesia ADM2** atau sumber boundary administratif Indonesia yang kompatibel.

### Fungsi

Digunakan untuk menentukan:

- provinsi;
- kabupaten/kota;
- luas wilayah.

### Join

**Point-in-Polygon**

FIRMS point → polygon kabupaten.

### Output

Setiap hotspot akhirnya memiliki:

- `province`
- `district`
- `district_area`

Ini memungkinkan analisis:

\[
HotspotDensity =
\frac{Jumlah\ Hotspot}{Luas\ Wilayah}
\]

---

# 6. Environmental Dataset

## 6.1 ERA5-Land

### Source

**Copernicus Climate Data Store / ECMWF ERA5-Land**

### Fungsi

Memberikan kondisi lingkungan pada lokasi dan periode tertentu.

### Variabel yang direkomendasikan

- precipitation;
- 2-meter temperature;
- maximum temperature;
- soil moisture;
- optional wind variables.

### Feature engineering

Jangan hanya menggunakan cuaca pada hari hotspot.

Buat variabel historis seperti:

- `rainfall_7d`
- `rainfall_14d`
- `rainfall_30d`
- `temperature_mean_7d`
- `temperature_max_7d`
- `soil_moisture`
- `dry_days`

Contoh:

\[
Rain30D_t=
\sum_{i=t-30}^{t}Rain_i
\]

### Join

ERA5-Land dan FIRMS tidak membutuhkan ID yang sama.

Digabung menggunakan:

**Spatial + Temporal Join**

FIRMS:

`latitude + longitude + date`

↓

nearest ERA5 grid

↓

tanggal/periode yang sesuai.

---

# 7. Peatland Dataset

## 7.1 Indonesia Peat Lands

### Source

**Global Forest Watch / WRI Indonesia Peat Lands**

### Fungsi

Mengidentifikasi apakah suatu wilayah berada pada:

- peatland;
- non-peatland.

Untuk model berbasis grid, dapat dihitung:

`peat_percentage`

### Join

Untuk hotspot individual:

**Point-in-Polygon**

Untuk model:

**Polygon × Spatial Grid Intersection**

Contoh:

\[
PeatPercentage =
\frac{PeatAreaWithinGrid}
{TotalGridArea}
\times100
\]

---

# 8. Land-Cover Dataset

## 8.1 MapBiomas Indonesia

### Fungsi

Mengetahui komposisi tutupan lahan, misalnya:

- forest;
- plantation;
- cropland;
- grassland;
- wetland;
- peat swamp forest;
- non-vegetated area;
- dan kelas lainnya.

### Join

MapBiomas merupakan raster.

Untuk analisis hotspot:

`FIRMS lat-long`

↓

sample raster pixel

↓

`land_cover_class`

Untuk model grid:

`MapBiomas pixels`

↓

intersect spatial grid

↓

hitung:

- `forest_pct`
- `plantation_pct`
- `cropland_pct`
- dll.

### Catatan periode

Jika data land-cover yang tersedia hanya sampai **2024**, analisis yang membutuhkan kondisi land cover aktual harus dibatasi pada periode yang tersedia.

Jangan mengklaim land cover 2024 sebagai kondisi pasti tahun 2025–2026.

---

# 9. Optional Validation Dataset

## MapBiomas Fire / Burned Area

Dataset ini bersifat opsional tetapi dapat meningkatkan kualitas analisis.

### Tujuan

Membedakan:

**NASA FIRMS**

= active thermal detection

dengan:

**Burned Area**

= wilayah yang terpetakan sebagai area terbakar.

### Business Question

> Seberapa banyak hotspot FIRMS yang berimpit dengan wilayah yang kemudian teridentifikasi sebagai burned area?

### Join

Spatial intersection:

`FIRMS point`

×

`burned-area raster/polygon`

↓

`burned_area_match = Yes / No`

---

# 10. Common Spatial Unit for Modeling

Untuk modeling, jangan menjadikan setiap FIRMS point sebagai satu observasi.

Gunakan:

## Spatial Grid × Time

Rekomendasi awal:

**Grid sekitar 0.1° × 0.1°**

atau disesuaikan dengan resolusi data lingkungan.

Setiap grid diberi:

`grid_id`

Contoh:

- G0001
- G0002
- G0003

Kemudian seluruh Kalimantan direpresentasikan sebagai kumpulan grid.

---

# 11. Temporal Unit

Rekomendasi:

## Weekly Observation

Satu record berarti:

> kondisi satu spatial grid dalam satu minggu.

Key utama:

`grid_id + week`

Contoh:

| grid_id | week |
|---|---|
| G001 | 2025-W30 |
| G001 | 2025-W31 |
| G002 | 2025-W30 |

Keuntungan:

- lebih stabil daripada daily prediction;
- jumlah observasi cukup banyak;
- mudah dipetakan;
- cocok untuk prediksi 7 hari.

---

# 12. Final Master Dataset

Setelah preprocessing, seluruh sumber data digabung menjadi satu tabel utama.

Contoh struktur:

| Variable | Description |
|---|---|
| `grid_id` | Spatial grid identifier |
| `week` | Observation week |
| `latitude` | Grid centroid latitude |
| `longitude` | Grid centroid longitude |
| `province` | Province |
| `district` | District |
| `rainfall_7d` | Rainfall previous 7 days |
| `rainfall_30d` | Rainfall previous 30 days |
| `temperature_mean_7d` | Previous 7-day temperature |
| `soil_moisture` | Soil moisture |
| `peat_pct` | Peatland percentage |
| `forest_pct` | Forest proportion |
| `plantation_pct` | Plantation proportion |
| `cropland_pct` | Cropland proportion |
| `hotspot_prev_7d` | Previous 7-day hotspot count |
| `hotspot_prev_30d` | Previous 30-day hotspot count |
| `avg_frp_prev_30d` | Previous average FRP |
| `month` | Month |
| `week_of_year` | Week number |
| `hotspot_next_7d` | Future hotspot count |
| `fire_next_7d` | Future fire occurrence target |

---

# 13. Dashboard Main Concept

Dashboard menggunakan:

# ONE MAIN GEOMAP

Peta tidak berubah lokasi.

Informasi yang ditampilkan pada peta berubah melalui navigation button.

Main modes:

**Historical**

**Environment**

**Fire Risk**

**Expected Count**

**Priority**

---

# 14. Dashboard General Layout

Struktur yang disarankan:

```text
┌───────────────────────────────────────────────────────────────┐
│                    KALIMANTAN FIREWATCH                       │
│ Year ▼ Month ▼ Province ▼ District ▼ Confidence ▼             │
├───────────────────────────────────────────────────────────────┤
│ [Historical] [Environment] [Fire Risk] [Expected] [Priority] │
├──────────────────────────────────────┬────────────────────────┤
│                                      │ KPI                    │
│                                      │                        │
│                                      │ Context information    │
│             MAIN GEOMAP              │                        │
│                                      │ Selected Area Detail   │
│                                      │                        │
├──────────────────────────────────────┴────────────────────────┤
│                 DYNAMIC SUPPORTING VISUALIZATION              │
└───────────────────────────────────────────────────────────────┘
```

---

# 15. Global Filters

Minimal filters:

- Year
- Month
- Province
- District
- Confidence
- Satellite

Untuk prediction:

- Prediction period
- Risk category

Optional:

- Peat only
- Land-cover type

---

# 16. Business Question 1 — Historical Fire

## Question

> **Where and when has fire activity historically concentrated in Kalimantan?**

## Main Geomap

Default mode:

**Hotspot Density Map**

Spatial unit:

`grid`

Metric:

`hotspot_count`

Alternative toggle:

- Density
- Point
- FRP

### Density

Warna grid menunjukkan jumlah hotspot.

### Point

Menampilkan individual FIRMS detections.

### FRP

Menampilkan intensitas berdasarkan FRP.

---

# 17. Historical Supporting Visualizations

## 17.1 Monthly Hotspot Trend

### Type

**Line Chart**

### X-axis

Month / date.

### Y-axis

Hotspot count.

### Tujuan

Mengetahui:

- tren;
- seasonal pattern;
- periode puncak hotspot;
- perbandingan antar tahun.

---

## 17.2 Year × Month Heatmap

### Type

**Calendar / Matrix Heatmap**

Rows:

Year.

Columns:

Jan–Dec.

Color intensity:

Hotspot count.

### Tujuan

Memperlihatkan seasonality dengan cepat.

---

# 18. Historical KPIs

Saat mode Historical aktif:

- Total Hotspots
- High Confidence %
- Average FRP
- Maximum FRP
- Peak Month
- Highest Hotspot Density District

---

# 19. Business Question 2 — Environmental Characteristics

## Question

> **How do environmental conditions differ across areas with different levels of fire activity?**

Sub-mode:

- Rainfall
- Soil Moisture
- Peatland
- Land Cover

---

# 20. Environment — Rainfall

## Geomap

Warna grid:

`rainfall_30d`

Hotspot dapat tetap menjadi overlay.

## Supporting Chart

**Scatter Plot**

X:

`rainfall_30d`

Y:

`weekly hotspot_count`

### Tujuan

Mengetahui apakah wilayah/periode dengan kondisi lebih kering cenderung memiliki hotspot lebih tinggi.

Kesimpulan harus menggunakan istilah:

**associated with**

dan bukan:

**causes**

karena analisis bersifat observational.

---

# 21. Environment — Soil Moisture

## Geomap

Warna grid:

`soil_moisture`

## Supporting Chart

**Scatter Plot**

X:

soil moisture.

Y:

hotspot count.

---

# 22. Environment — Peatland

## Geomap

Warna grid berdasarkan:

`peat_percentage`

atau peat / non-peat overlay.

## Supporting Chart

**Grouped Bar Chart**

Bandingkan:

- hotspot density;
- average FRP;
- high-confidence rate.

Antara:

**Peat**

vs

**Non-Peat**

---

# 23. Environment — Land Cover

## Geomap

Menampilkan dominant land-cover class atau proporsi kelas tertentu.

## Supporting Chart

**Horizontal Bar Chart**

Metric utama:

\[
HotspotDensity =
\frac{Hotspots}
{LandCoverArea}
\]

Contoh unit:

**hotspots / 1,000 km²**

Bukan hanya raw hotspot count.

---

# 24. Environmental KPIs

Contoh KPI:

- Average 30-Day Rainfall
- Average Soil Moisture
- Hotspots on Peatland %
- Highest Peat Hotspot Density
- Dominant Fire-Prone Land Cover

---

# 25. Predictive Modeling

Modeling memiliki dua task utama.

---

# 26. MODEL 1 — Fire Occurrence Classification

## Main Predictive Task

### Question

> **Which areas of Kalimantan are most likely to experience hotspot activity during the next seven days?**

### Target

`fire_next_7d`

Definisi:

\[
Y=
\begin{cases}
1,&\text{hotspot ditemukan pada 7 hari berikutnya}\\
0,&\text{tidak ditemukan hotspot}
\end{cases}
\]

### Output

\[
P(Fire_{next7d})
\]

Contoh:

| Grid | Predicted probability |
|---|---:|
| G001 | 0.91 |
| G002 | 0.16 |
| G003 | 0.73 |

---

# 27. Classification Models

Minimal compare:

### Baseline

**Logistic Regression**

Tujuan:

memberikan baseline sederhana dan interpretable.

### Nonlinear Baseline

**Random Forest Classifier**

### Main Candidates

**XGBoost Classifier**

dan/atau

**LightGBM Classifier**

Model terbaik dipilih berdasarkan evaluation set, bukan karena algoritmanya paling kompleks.

---

# 28. Classification Features

### Historical Fire Features

- `hotspot_prev_7d`
- `hotspot_prev_14d`
- `hotspot_prev_30d`
- `avg_frp_prev_30d`
- `days_since_last_hotspot`

### Environmental Features

- `rainfall_7d`
- `rainfall_30d`
- `temperature_mean_7d`
- `temperature_max_7d`
- `soil_moisture`

### Spatial Features

- `peat_pct`
- `forest_pct`
- `plantation_pct`
- `cropland_pct`

### Temporal Features

- month
- week of year
- dry-season indicator

---

# 29. Classification Evaluation

Gunakan:

- ROC-AUC
- PR-AUC
- Precision
- Recall
- F1-score
- Confusion Matrix
- Calibration jika memungkinkan

Karena kemungkinan jumlah grid tanpa hotspot jauh lebih besar daripada grid dengan hotspot, **accuracy tidak boleh menjadi metric utama**.

Recall dan PR-AUC penting jika tujuan utamanya adalah mengidentifikasi area berisiko.

---

# 30. Validation Strategy

Jangan melakukan random split tanpa memperhatikan waktu.

Gunakan:

## Temporal Split

Contoh konsep:

**Train**

periode awal.

**Validation**

periode setelah training.

**Test**

periode paling akhir.

Contoh jika data mencukupi:

2024 → training

2025 → training/validation

2026 → final test

Pembagian aktual harus disesuaikan dengan jumlah data.

Tujuannya mencegah:

**future data leakage**.

---

# 31. Business Question 3 — Fire Risk

## Question

> **Which areas are most likely to experience hotspot activity during the next seven days?**

## Main Geomap

**Predicted Fire Risk Map**

Setiap grid diberi warna berdasarkan:

`predicted_fire_probability`

Kategori dapat menjadi:

- Low
- Moderate
- High
- Very High

Threshold ditentukan berdasarkan:

- validation result;
- operational requirement;
- atau quantile.

Bukan threshold arbitrary.

---

# 32. Fire Risk Supporting Visualization

## Feature Importance / SHAP

### Type

Horizontal bar / SHAP visualization.

Menampilkan fitur utama seperti:

- previous hotspot activity;
- rainfall;
- soil moisture;
- peat percentage;
- temperature;
- plantation percentage.

### Tujuan

Menjawab:

> **Why does the model classify an area as high-risk?**

Jika grid dipilih pada geomap, tampilkan:

**local explanation** untuk grid tersebut jika memungkinkan.

---

# 33. Fire Risk KPIs

- High-Risk Grids
- Mean Predicted Risk
- Highest-Risk District
- ROC-AUC / PR-AUC
- Recall
- Number of High-Risk Peat Grids

---

# 34. MODEL 2 — Hotspot Count Prediction

## Question

> **How many hotspot detections are expected in each grid during the next seven days?**

### Target

`hotspot_count_next_7d`

Contoh:

| Grid | Future hotspot count |
|---|---:|
| G001 | 17 |
| G002 | 0 |
| G003 | 5 |

---

# 35. Count Prediction Models

### Statistical Baseline

**Poisson Regression**

### Alternative

**Negative Binomial Regression**

Digunakan apabila target menunjukkan overdispersion:

\[
Variance > Mean
\]

### Machine Learning Candidate

**XGBoost / LightGBM Regressor**

dengan objective yang sesuai untuk count data jika tersedia.

---

# 36. Count Model Evaluation

Gunakan:

- MAE
- RMSE
- Poisson Deviance
- actual vs predicted distribution

RMSE tidak perlu menjadi satu-satunya metric karena hotspot count dapat sangat skewed.

---

# 37. Business Question 4 — Expected Hotspot Count

## Question

> **Which areas are expected to experience the highest hotspot activity during the next seven days?**

## Main Geomap

Warna grid berdasarkan:

`predicted_hotspot_count_next_7d`

Contoh kategori:

- 0
- Low
- Medium
- High

Batas kategori harus mengikuti distribusi prediksi.

---

# 38. Expected Count Supporting Chart

## Actual vs Predicted

### Type

Line Chart pada validation/test period.

Actual:

jumlah hotspot aktual.

Predicted:

jumlah hotspot yang diprediksi.

### Tujuan

Memperlihatkan kemampuan model mengikuti perubahan aktivitas hotspot sepanjang waktu.

Optional:

**Actual vs Predicted Scatter Plot**

---

# 39. Expected Count KPIs

- Total Expected Hotspots
- Highest Expected Grid
- Highest Expected District
- MAE
- RMSE / Poisson Deviance

---

# 40. Optional Model — Fire Intensity

Jika waktu mencukupi, dapat ditambahkan model ketiga.

### Question

> **If hotspot activity occurs, how intense could the detected fire activity be?**

### Target

Salah satu:

- `max_frp_next_7d`
- `avg_frp_next_7d`

### Models

- Random Forest Regressor
- XGBoost Regressor
- LightGBM Regressor

### Catatan

Model intensity sebaiknya bersifat conditional:

hanya dievaluasi pada area/periode yang memiliki hotspot.

Model ini **optional**, bukan prioritas utama.

---

# 41. Business Question 5 — Monitoring Priority

## Question

> **Which areas should receive the highest monitoring priority?**

Priority tidak perlu menggunakan arbitrary weighted risk index.

Gunakan dua output model utama:

### Dimension 1

\[
P(Fire)
\]

### Dimension 2

\[
ExpectedHotspotCount
\]

Area dengan:

**High Probability + High Expected Count**

menjadi prioritas utama.

---

# 42. Monitoring Priority Geomap

Geomap menampilkan kategori seperti:

- Low
- Watch
- High
- Critical

Kategorisasi harus berasal dari kombinasi:

- predicted probability;
- expected hotspot count.

---

# 43. Monitoring Priority Supporting Visualization

## Quadrant Scatter Plot

### X-axis

Predicted Fire Probability.

### Y-axis

Expected Hotspot Count.

### Point

Grid atau district.

### Interpretation

**Top-right quadrant**

=

high probability + high expected activity

=

highest monitoring priority.

---

# 44. Priority Supporting Table

Tampilkan:

## Top 10 Priority Areas

Kolom:

- Rank
- District
- Fire Probability
- Expected Hotspots
- Peat %
- Rainfall 30D
- Previous Hotspots
- Priority Category

---

# 45. Priority KPIs

- Critical Grids
- Critical Districts
- High-Risk Peat Grids
- Highest Priority Province
- Total Expected Hotspots in Critical Areas

---

# 46. Dynamic Dashboard Behavior

Dashboard tidak menggunakan lima map terpisah.

Gunakan:

## ONE GEOMAP

Dengan navigation:

`Historical`

`Environment`

`Fire Risk`

`Expected Count`

`Priority`

Setiap button mengubah:

1. map layer;
2. KPI;
3. tooltip;
4. supporting visualization;
5. contextual information.

---

# 47. Cross-Filtering

Dashboard sebaiknya memiliki interaksi dua arah.

Contoh:

User memilih:

**Kalimantan Tengah**

pada map.

↓

Monthly trend hanya menampilkan Kalimantan Tengah.

Kemudian user memilih:

**August 2025**

pada line chart.

↓

Geomap hanya menampilkan kondisi Agustus 2025.

Kemudian user memilih satu grid.

↓

Panel detail menampilkan:

- district;
- hotspot count;
- average FRP;
- rainfall;
- soil moisture;
- peat percentage;
- land cover;
- predicted risk;
- expected count.

---

# 48. Map Tooltip

Historical example:

**Grid G0211**

District: XXX  
Province: Central Kalimantan  
Hotspots: 31  
Avg FRP: 22.3 MW  
High Confidence: 61%

Environment mode:

Rainfall 30D: XX mm  
Soil Moisture: XX  
Peatland: XX%  
Dominant Land Cover: XXX

Prediction mode:

Predicted Fire Probability: XX%  
Expected Hotspots: XX  
Risk Category: High

---

# 49. Model Explainability

Untuk model boosting, tambahkan:

## Global Explanation

Feature importance / SHAP summary.

Menjawab:

> Fitur apa yang paling banyak mempengaruhi prediction secara keseluruhan?

## Local Explanation

Ketika user klik grid:

> Faktor apa yang meningkatkan atau menurunkan prediction pada grid tersebut?

Contoh:

**Risk increasing**

- low rainfall;
- low soil moisture;
- recent hotspot activity;
- high peatland share.

**Risk decreasing**

- high rainfall;
- no recent hotspot.

---

# 50. Important Methodological Rules

## 50.1 Avoid Data Leakage

Jika target:

`fire_next_7d`

maka feature hanya boleh menggunakan informasi yang tersedia **sebelum prediction date**.

Tidak boleh menggunakan:

- rainfall masa depan;
- future hotspot count;
- future FRP;
- informasi setelah prediction period.

---

## 50.2 Correlation ≠ Causation

Dashboard boleh menyatakan:

> low rainfall is associated with higher hotspot activity.

Tetapi jangan langsung menyatakan:

> low rainfall causes fires.

---

## 50.3 Normalize Spatial Comparisons

Jangan hanya membandingkan jumlah hotspot antar wilayah.

Gunakan juga:

\[
HotspotDensity =
\frac{Hotspots}{Area}
\]

Misalnya:

**hotspots / 1,000 km²**

---

## 50.4 Do Not Treat FIRMS Point as Independent Fire Event

Gunakan istilah:

- hotspot detection;
- active-fire detection;
- fire activity.

Bukan otomatis:

- individual wildfire event.

---

# 51. Recommended Dashboard Modes Summary

| Mode | Main Map Metric | Main Supporting Visualization |
|---|---|---|
| **Historical** | Hotspot density / FRP | Monthly trend + seasonality heatmap |
| **Environment** | Rainfall / soil moisture / peat / land cover | Scatter / comparison bar |
| **Fire Risk** | Predicted probability | SHAP / feature importance |
| **Expected Count** | Predicted hotspot count | Actual vs predicted |
| **Priority** | Monitoring priority category | Probability × expected count quadrant |

---

# 52. Models Summary

| Task | Prediction | Models |
|---|---|---|
| **Classification** | Probability of hotspot occurrence during next 7 days | Logistic Regression, Random Forest, XGBoost/LightGBM |
| **Count Prediction** | Number of hotspot detections during next 7 days | Poisson, Negative Binomial, XGBoost/LightGBM |
| **Optional Intensity Regression** | Expected FRP when hotspot occurs | Random Forest, XGBoost/LightGBM |

---

# 53. Recommended Project Scope

## Must Have

### Data

- NASA FIRMS
- Province/administrative boundary
- ERA5-Land
- Peatland

### Analysis

- historical hotspot analysis;
- hotspot density;
- seasonal analysis;
- rainfall / soil-moisture relationship;
- peatland comparison.

### Dashboard

- one main geomap;
- dynamic mode buttons;
- historical line chart;
- environmental supporting charts;
- predictive fire-risk layer;
- expected-count layer;
- monitoring-priority layer.

### Modeling

- Fire Occurrence Classification
- Hotspot Count Prediction

---

# 54. Nice to Have

Jika waktu mencukupi:

- MapBiomas land cover;
- MapBiomas Fire validation;
- SHAP local explanation;
- FRP intensity prediction;
- animation/time slider;
- district-level ranking;
- burned-area validation.

---

# 55. Optional / Low Priority

Tidak perlu dipaksakan jika waktu terbatas:

- LSTM;
- ConvLSTM;
- deep-learning fire prediction;
- complicated arbitrary risk index;
- terlalu banyak external datasets;
- population exposure analysis;
- school exposure analysis.

Lebih baik memiliki dua model yang tervalidasi dengan baik daripada banyak model tanpa metodologi yang kuat.

---

# 56. Recommended Technical Pipeline

```text
NASA FIRMS
     │
     │ latitude + longitude + date
     ▼
SPATIAL GRID
     │
     ├────────── ERA5-Land
     │            weather
     │
     ├────────── Peatland
     │
     ├────────── MapBiomas
     │            land cover
     │
     └────────── Administrative Boundary
                  province/district
     │
     ▼
GRID × WEEK MASTER TABLE
     │
     ├──────── Historical Analysis
     │
     ├──────── Classification
     │           P(Fire next 7d)
     │
     └──────── Count Prediction
                 Hotspot Count next 7d
     │
     ▼
MODEL OUTPUT TABLE
     │
     ▼
INTERACTIVE DASHBOARD
     │
     ├── Historical
     ├── Environment
     ├── Fire Risk
     ├── Expected Count
     └── Monitoring Priority
```

---

# 57. Preprocessing Workflow

## Step 1

Filter NASA FIRMS hanya untuk Kalimantan.

## Step 2

Clean:

- duplicate;
- invalid coordinate;
- missing values;
- date/time format.

## Step 3

Spatial join:

FIRMS → province/district.

## Step 4

Generate spatial grid.

## Step 5

Assign FIRMS to `grid_id`.

## Step 6

Aggregate FIRMS:

`grid × week`.

## Step 7

Join ERA5 weather.

## Step 8

Calculate rainfall and temperature rolling features.

## Step 9

Intersect peatland polygons.

## Step 10

Optional:

aggregate MapBiomas land-cover composition.

## Step 11

Generate lag features.

## Step 12

Generate prediction targets.

## Step 13

Chronological train-validation-test split.

## Step 14

Train models.

## Step 15

Evaluate models.

## Step 16

Generate predictions for each grid.

## Step 17

Export dashboard-ready tables.

---

# 58. Suggested Dashboard-Ready Tables

## `fact_grid_week`

Contains historical/environmental information.

Primary key:

`grid_id + week`

## `prediction_grid_week`

Contains:

- grid_id;
- prediction date;
- fire probability;
- expected hotspot count;
- risk category;
- priority category.

## `dim_grid`

Contains:

- geometry;
- centroid;
- province;
- district;
- area.

## `dim_date`

Contains:

- date;
- week;
- month;
- year;
- season.

---

# 59. Final Dashboard Narrative

Dashboard sebaiknya mengarahkan pengguna melalui cerita berikut:

## 01 — Historical

**What happened?**

Di mana dan kapan hotspot terjadi?

↓

## 02 — Environment

**Under what conditions?**

Bagaimana kondisi rainfall, soil moisture, peatland, dan land cover pada area tersebut?

↓

## 03 — Fire Risk

**What may happen next?**

Area mana yang memiliki probabilitas hotspot tertinggi dalam tujuh hari berikutnya?

↓

## 04 — Expected Count

**How much activity may occur?**

Berapa hotspot yang diperkirakan muncul?

↓

## 05 — Monitoring Priority

**Where should attention be focused?**

Wilayah mana yang paling perlu diprioritaskan untuk monitoring?

---

# 60. Expected Final Deliverables

Project final setidaknya harus menghasilkan:

### 1. Cleaned Dataset

NASA FIRMS Kalimantan.

### 2. Enriched Dataset

FIRMS + geography + environment + peatland + optional land cover.

### 3. Modeling Dataset

Grid × Week.

### 4. Exploratory Analysis

Historical and environmental analysis.

### 5. Predictive Models

Fire occurrence classification.

Hotspot count prediction.

### 6. Model Evaluation

Metrics + validation visualizations.

### 7. Prediction Outputs

Predicted fire probability and expected hotspot count per grid.

### 8. Interactive Dashboard

One central geomap with dynamic information modes.

### 9. Business Insights

Interpretation of historical patterns, environmental associations, predicted risk, and monitoring priorities.

---

# 61. Project Success Criteria

Proyek dianggap berhasil jika dashboard mampu menjawab secara jelas:

**WHERE**

Di mana hotspot terjadi dan diprediksi terjadi?

**WHEN**

Kapan aktivitas hotspot meningkat?

**WHAT CONDITIONS**

Kondisi lingkungan seperti apa yang banyak ditemukan pada wilayah hotspot?

**HOW LIKELY**

Seberapa besar kemungkinan hotspot terjadi dalam tujuh hari berikutnya?

**HOW MUCH**

Berapa banyak hotspot yang diperkirakan?

**WHAT NEXT**

Wilayah mana yang perlu diprioritaskan untuk monitoring?

---

# 62. Final Concept

Kalimantan FireWatch bukan hanya dashboard:

> **“Jumlah hotspot di Kalimantan.”**

Melainkan sebuah:

> **Spatio-Temporal Fire Monitoring and Predictive Decision-Support Dashboard**

yang menggabungkan:

**Satellite Active-Fire Detection**

+

**Environmental Conditions**

+

**Peatland and Land Characteristics**

+

**Machine Learning Prediction**

+

**Interactive Geospatial Visualization**

untuk memberikan satu alur analisis yang konsisten:

> **Understand the past → Explain spatial patterns → Predict future hotspot risk → Prioritize monitoring.**