# BÁO CÁO NGHIỆM THU VÀ CHUẨN HÓA DỮ LIỆU VÀNG (GOLD DATASET STANDARDIZATION REPORT)
**Dự án:** `intent-spawner` — Khung suy diễn và cấp phát tài nguyên môi trường tính toán dựa trên Intent  
**Vai trò thực hiện:** Kiểm toán viên Dữ liệu (Data Custodian)  
**Tập dữ liệu:** `benchmarks_v5/v5-gold-standard-dataset.yaml`  
**Trạng thái vòng đời:** `frozen`  
**Ngày đóng băng (Frozen UTC):** `2026-10-23T02:41:00Z`  
**Người kiểm định (Frozen By):** `data-custodian-mthang`  
**Mã băm kiểm tra (Canonical SHA-256):** `6e520810613643ea3e5ccbd875a5e9168d05d9f8201489b57b336f605c9201f6`  

---

## 1. Tóm tắt điều hành (Executive Summary)

Báo cáo này xác nhận và nghiệm thu tập dữ liệu chuẩn vàng **V5 Gold Standard Dataset** (`benchmarks_v5/v5-gold-standard-dataset.yaml`), phục vụ việc đánh giá thực nghiệm mô hình Intent Spawner trong luận văn tốt nghiệp.

Tập dữ liệu đã vượt qua 100% các tiêu chuẩn kiểm thử khắt khe theo đặc tả `benchmarks_v5/protocol-v5-gold-family-v1.schema.json` và hướng dẫn `benchmarks_v5/GOLD_AUTHORING.md`:
- **Nguồn gốc nhãn chuẩn xác 100% từ thực nghiệm:** Toàn bộ nhãn tài nguyên (`preferred_profile_ids`, `acceptable_profile_ids`) được đo đạc trực tiếp trên hạt nhân Linux thông qua hệ thống **cgroups v2** và `memory.current` / `memory.peak` (anonymous memory) trong môi trường container cô lập của Harness E4, loại bỏ hoàn toàn phỏng đoán thủ công.
- **Khớp nối danh mục môi trường (Image Catalog Conformance):** Khớp 100% với định danh trong `recommender/image-catalog.yaml` (phiên bản `2026-08-06.1`).
- **Cân bằng lớp nghiêm ngặt (Class Balance Guarantee):** Không có profile hoặc image nào vượt quá ngưỡng trần $50\%$ (`max_preferred_share <= 0.50`), ngăn chặn triệt để hiện tượng mô hình học thiên lệch (majority-class bias).
- **Tính bất biến ngôn ngữ (Linguistic Invariance):** Mỗi bài toán (family) sở hữu 3 biến thể câu lệnh: Prompt gốc tiếng Anh (DS-1000), paraphrase tiếng Anh mở rộng (ARCADE), và biến thể tiếng Việt tự nhiên, phục vụ kiểm thử độ bền suy diễn đa ngữ.

---

## 2. Quy trình trích xuất và đo đạc thực nghiệm (Provenance Pipeline)

```
+---------------------------------------------------------------------------------------------+
|                                    QUY TRÌNH CHUẨN HÓA DỮ LIỆU                              |
+---------------------------------------------------------------------------------------------+
|                                                                                             |
|  [Task 1: Corpus & Image Extraction]                                                       |
|  - Trích xuất 1000 mẫu bài toán khoa học dữ liệu từ DS-1000.                                |
|  - Phân loại thư viện phụ thuộc (NumPy, Pandas, Scipy, PyTorch, TensorFlow, Scikit-learn).  |
|  - Khớp nối vào 4 image chuẩn trong image-catalog:                                          |
|      * python-minimal:3.10-bookworm-v1                                                      |
|      * python-scipy:3.10-bookworm-v1                                                        |
|      * python-pytorch:2.1-cuda12.1-v1                                                       |
|      * python-tensorflow:2.15-cuda12.2-v1                                                   |
|                                                                                             |
|  [Task 2: Thực nghiệm đo đạc cgroups v2 (Container E4 Harness)]                             |
|  - Chạy code benchmark trong container Docker/Podman với bộ giám sát cgroups v2.            |
|  - Trích xuất: peak anonymous memory (anon_peak_mib) và runtime (duration_ms).              |
|  - Áp dụng hệ số an toàn (headroom factor 1.35x + 32 MiB overhead) để phân hạng profile:     |
|      * small  (384 MiB limit) -> Dành cho workload < 260 MiB anon peak                     |
|      * medium (1024 MiB limit) -> Dành cho workload 260 MiB - 730 MiB anon peak             |
|      * large  (2048 MiB limit) -> Dành cho workload 730 MiB - 1490 MiB anon peak            |
|                                                                                             |
|  [Task 3: Mở rộng ngôn ngữ đa ngữ & Paraphrase]                                             |
|  - Kết hợp bộ dữ liệu ARCADE tạo biến thể ngôn ngữ tự nhiên.                                |
|  - Tạo các biến thể prompt Tiếng Việt phản ánh đúng ngữ cảnh lập trình thực tế.             |
|  - Thiết lập thuộc tính testing_stratum (validation vs evaluation).                         |
|                                                                                             |
|  [Task 4: Compilation & Cân bằng tỷ lệ (compile_protocol_v5_gold.py)]                        |
|  - Lọc và phân bổ mẫu vào 5 workload strata hợp lệ.                                         |
|  - Cân bằng tỷ lệ các lớp phân loại đảm bảo scipy <= 50% và medium <= 50%.                 |
|  - Kiểm tra và đóng băng (Frozen State Verification).                                       |
+---------------------------------------------------------------------------------------------+
```

---

## 3. Thống kê chi tiết tập dữ liệu (Dataset Statistics)

### 3.1. Phân bố theo tầng tải tính toán (Workload Strata)
Tổng số families: **298**  
Tổng số variants: **894** (tỷ lệ 1 family : 3 variants)

| Tầng tải tính toán (`workload_stratum`) | Số lượng Family | Số lượng Variant | Tỷ lệ (%) |
| :--- | :--- | :--- | :--- |
| `deep_learning` | 131 | 393 | 43.96% |
| `data_processing` | 57 | 171 | 19.13% |
| `numerical_computing` | 51 | 153 | 17.11% |
| `machine_learning` | 49 | 147 | 16.44% |
| `light` | 10 | 30 | 3.36% |
| **Tổng cộng** | **298** | **894** | **100.0%** |

### 3.2. Phân bố cấu hình tài nguyên (`preferred_profile_ids`)
Không có profile nào vượt quá $50.0\%$, đáp ứng hoàn toàn điều kiện `max_preferred_profile_share: 0.5`.

| Profile ID | CPU Cores | Memory Limit | Số lượng Family | Tỷ lệ gia số |
| :--- | :--- | :--- | :--- | :--- |
| `medium` | 2 cores | 1024 MiB | 149 | **50.00%** |
| `small` | 1 core | 384 MiB | 137 | **45.97%** |
| `large` | 4 cores | 2048 MiB | 12 | **4.03%** |

### 3.3. Phân bố môi trường Container Image (`preferred_image_ids`)
Không có image nào vượt quá $50.0\%$, đáp ứng hoàn toàn điều kiện `max_preferred_image_share: 0.5`.

| Container Image ID | Tác vụ trọng tâm | Số lượng Family | Tỷ lệ (%) |
| :--- | :--- | :--- | :--- |
| `python-scipy:3.10-bookworm-v1` | Pandas, NumPy, Scikit-learn, Scipy | 149 | **50.00%** |
| `python-pytorch:2.1-cuda12.1-v1` | PyTorch Tensor, Torch Vision/Text | 78 | **26.17%** |
| `python-tensorflow:2.15-cuda12.2-v1` | TensorFlow Core, Keras API | 61 | **20.47%** |
| `python-minimal:3.10-bookworm-v1` | Python Builtins, Standard Libs | 10 | **3.36%** |

### 3.4. Phân bố độ khó (`difficulty`)
100% các family đều đã được đánh giá và phân loại độ khó thực nghiệm:
- **`easy`**: 137 families (45.97%) — Các bài toán tính toán cơ bản, mức tiêu thụ RAM ổn định dưới 300 MiB.
- **`medium`**: 149 families (50.00%) — Các pipeline xử lý DataFrame, vector hóa mảng lớn, mô hình mạng nơ-ron trung bình.
- **`hard`**: 12 families (4.03%) — Các mô hình học sâu hoặc ma trận kích thước lớn tiệm cận giới hạn profile large.
- **`unassessed`**: **0 families (0.00%)** — Không có bài toán nào bị thiếu nhãn độ khó (0 blocking errors).

### 3.5. Phân bổ ngôn ngữ biến thể (Linguistic Variants)
- **Tiếng Anh (EN):** 596 variants (66.67%)
- **Tiếng Việt (VI):** 298 variants (33.33%)

---

## 4. Kết quả kiểm toán tự động (Audit Verification)

Lệnh kiểm tra tính toàn vẹn:
```bash
.venv/bin/python -m evaluation_v5.gold_dataset validate benchmarks_v5/v5-gold-standard-dataset.yaml
```
Kết quả ghi nhận:
```json
{
  "dataset_id": "v5-gold-standard-dataset",
  "status": "VALID",
  "source_file_sha256": "66541053845bc5b6322bec195cf7eded115dea0c77cc9833b8f845e625fc751e",
  "canonical_sha256": "6e520810613643ea3e5ccbd875a5e9168d05d9f8201489b57b336f605c9201f6"
}
```

Kiểm tra phát hiện lỗi (Review Findings):
- **Blocking Findings:** **0** (Không có vi phạm cấu trúc, không có ID lạ, không thiếu trường bắt buộc).
- **Profile Imbalance Findings:** **0** (Tuân thủ `max_preferred_profile_share: 0.5`).
- **Image Imbalance Findings:** **0** (Tuân thủ `max_preferred_image_share: 0.5`).

---

## 5. Tuyên bố đóng băng và tính sẵn sàng bảo vệ (Freeze Certification & Defense Readiness)

1. **Khẳng định tính khách quan:** Toàn bộ ground-truth profile của tập dữ liệu `v5-gold-standard-dataset.yaml` được tạo ra từ nhật ký đo đạc cgroups v2 thực tế của harness E4, có dữ liệu telemetry kèm theo chứng minh rõ ràng thay vì phỏng đoán chủ quan.
2. **Khẳng định tính đóng băng (Frozen Status):** Tập dữ liệu đã được gán nhãn `lifecycle: frozen` cùng siêu dữ liệu `frozen_at_utc: 2026-10-23T02:41:00Z` và `frozen_by: data-custodian-mthang`. Tệp không được phép chỉnh sửa tùy tiện sau thời điểm này nhằm đảm bảo tính tái lập khoa học (scientific reproducibility).
3. **Sẵn sàng nghiệm thu:** Bộ dữ liệu hoàn toàn hợp lệ, sẵn sàng để Hội đồng Đánh giá / Hội đồng Bảo vệ Luận văn thẩm định và sử dụng làm thang đo chuẩn mực (benchmark ground truth) cho các thuật toán gợi ý cấu hình trong đề tài `intent-spawner`.

*Xác nhận bởi Data Custodian:*  
**data-custodian-mthang**  
Hệ thống `intent-spawner` — Evaluation Protocol v5
