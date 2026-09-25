# Cẩm Nang Thực Nghiệm Thực Tế (Streamlined Experiment Runbook)

Tài liệu này hướng dẫn quy trình thực thi thực nghiệm tinh gọn cho đề tài **Intent- and Context-Aware Profile Recommendation for JupyterHub (Intent-Spawner)**.

Toàn bộ các rào cản hành chính giả định (yêu cầu chữ ký reviewer, quy trình đóng băng bất biến SHA-256, kiểm định cô lập split nhân tạo, và ma trận giả thuyết phức tạp) đã được tháo ngòi để phục vụ trực tiếp cho mục tiêu kỹ thuật thực tế của Đồ án Tốt nghiệp (ĐATN).

---

## ⚡ Bảng Lệnh Nhanh (Cheat Sheet)

| Tác vụ | Lệnh thực thi nhanh | Kết quả đầu ra |
| :--- | :--- | :--- |
| **Kiểm thử logic lõi** | `make test` | 80/80 unit tests pass (0.6s) |
| **Chạy thực nghiệm E1 (Chất lượng gợi ý)** | `make eval-offline` | Bảng Markdown so sánh Top-1, Image Match, Latency của P1 vs P2 |
| **Chạy với Dataset tùy chọn** | `make eval-dataset DATASET=path/to/dataset.yaml` | Kết quả đánh giá trên tập benchmark chỉ định |
| **Tổng quan độ bền ngôn ngữ (E2)** | `make eval-robustness` | Báo cáo phân tích số lượng biến thể và độ bền tiếng Việt |
| **Kiểm tra trạng thái cluster Kubernetes** | `make v5-e4-preflight` | Xác nhận cụm K8s sẵn sàng (OrbStack/Kind/K3s/Minikube) |

---

## 🔬 Chi Tiết Các Thực Nghiệm

### 1. Thực Nghiệm E1 — Đo Chất Lượng Gợi Ý (P1 vs P2 Recommendation Quality)
Đánh giá khả năng dịch intent của người dùng thành cấu hình phần cứng (Small, Medium, Large) và Image môi trường phù hợp.

```bash
# Lệnh chạy nhanh:
make eval-offline

# Hoặc chạy lệnh trực tiếp với các tùy chọn:
PYTHONPATH=. .venv/bin/python -m evaluation_v5.offline.runner \
  --systems P1,P2 \
  --repeats 1 \
  --result-dir results_v5/protocol-v5.0.0/E1/run-e1-offline
```

* **Chỉ số đo lường:**
  * **Top-1 Profile Match:** Tỉ lệ gợi ý đúng kích cỡ phần cứng.
  * **Image Match:** Tỉ lệ gợi ý đúng Image notebook (Data Science, PyTorch, SciPy...).
  * **Khớp đồng thời (Joint):** Tỉ lệ đúng cả Profile và Image.
  * **Thời gian xử lý (Median Latency):** Độ trễ phản hồi (ms).
  * **Phân tích theo ngôn ngữ (English vs Vietnamese):** Đo độ chính xác trên câu prompt tiếng Anh và tiếng Việt.
* **Đầu ra:** Bảng Markdown in trực tiếp ra màn hình và lưu tại `results_v5/protocol-v5.0.0/E1/run-e1-offline/SUMMARY.md` cùng `summary_metrics.json`.

---

### 2. Thực Nghiệm E2 — Độ Bền Vững Ngôn Ngữ Tự Nhiên (NL Robustness)
Đo lường độ ổn định của Intent Extractor trước các câu paraphrase, cách hành văn khác nhau và tiếng Việt.

* **Cách 1 (Tích hợp sẵn trong E1):** Runner E1 tự động bóc tách và xuất bảng `Phân Tích Độ Bền Ngôn Ngữ (Language Robustness: English vs Vietnamese)` khi tập dữ liệu có các câu hỏi đa ngôn ngữ.
* **Cách 2 (Xem tổng quan bộ dữ liệu độ bền):**
  ```bash
  make eval-robustness
  ```

---

### 3. Thực Nghiệm E4 — Đánh Giá Tài Nguyên Thực Tế Trên Kubernetes
Đo đạc việc phân bổ tài nguyên thực tế của KubeSpawner trên cụm Kubernetes cục bộ (OrbStack, Kind, Docker Desktop, Minikube, K3s):

1. **Kiểm tra kết nối và tính sẵn sàng của cụm:**
   ```bash
   make v5-e4-preflight
   ```
2. **Kiểm tra mô phỏng (Dry-run):**
   ```bash
   make v5-resource-dry-run
   # hoặc kiểm tra phân bổ hiệu quả:
   make v5-resource-efficiency-dry-run
   ```
3. **Thực thi đo đạc Pod thực tế trên K8s:**
   Khi cụm K8s đang chạy (`status: READY`), thực thi đo đạc tải thực tế trên các workload tính toán để chứng minh:
   * **Baseline Small:** Bị OOM (Out Of Memory) với workload dữ liệu lớn.
   * **Intent-Spawner (P2):** Tự động nhận diện intent -> cấp phát Medium/Large -> Pod chạy thành công mà không lãng phí tài nguyên quá mức.
   ```bash
   PYTHONPATH=. .venv/bin/python -m evaluation_v5.resource execute \
     --result-dir results_v5/protocol-v5.0.0/E4/run-e4-observed \
     --run-id run-e4-observed \
     --image intent-spawner-resource-v5:latest \
     --readiness-attestation benchmarks_v5/protocol-v5-e4-readiness-attestation-orbstack.json
   ```

---

## 📌 Lưu Ý Về Phạm Vi Các Thí Nghiệm (E1 – E5)

* **E1 (Chất lượng gợi ý) & E2 (Độ bền ngôn ngữ):** Đóng vai trò là các thực nghiệm cốt lõi kiểm chứng thuật toán P2, được thực thi và trích xuất số liệu tự động qua `make eval-offline`.
* **E4 (Đo lường tài nguyên thực tế trên K8s):** Đóng vai trò kiểm chứng hạ tầng thực tế trên cụm Kubernetes (OrbStack/Kind), đo đạc việc cấp phát phần cứng tránh OOM.
* **E3 (Khảo sát người dùng UI) & E5 (Image storage footprint):** Là các nội dung mở rộng/phụ trợ; trọng tâm kỹ thuật của ĐATN tập trung chính vào E1, E2 và E4.

---

## 📁 Cấu Trúc Thư Mục Kết Quả

* `results_v5/protocol-v5.0.0/E1/`: Chứa file `SUMMARY.md` (bảng số liệu hoàn chỉnh cho luận văn), `summary_metrics.json` (dữ liệu máy đọc), và thư mục `raw/` (chứa toàn bộ log từng trường hợp thử nghiệm).
* `results_v5/protocol-v5.0.0/E4/`: Chứa báo cáo tài nguyên, cgroup metrics đo đạc được từ các pod thực tế trên Kubernetes.
