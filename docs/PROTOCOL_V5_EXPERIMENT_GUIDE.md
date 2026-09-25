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
| **Tổng quan độ bền ngôn ngữ (E2)** | `make eval-robustness` | Báo cáo phân tích 68 biến thể và độ bền tiếng Việt |
| **Smoke test khảo sát người dùng (E3)** | `make v5-user-study-smoke` | Kiểm thử luồng phân bổ kịch bản ngẫu nhiên cho người dùng |
| **Kiểm tra trạng thái cluster Kubernetes (E4)** | `make v5-e4-preflight` | Xác nhận cụm K8s sẵn sàng (OrbStack/Kind/K3s/Minikube) |
| **Kiểm tra khả năng lưu trữ & Image (E5)** | `PYTHONPATH=. python -m evaluation_v5.image_storage --help` | Công cụ đo đạc chia sẻ layer và kiểm thử khởi động container |

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

### 3. Thực Nghiệm E3 — Khảo Sát Trải Nghiệm Người Dùng (Human Usability Study: B0 vs P2)
Đánh giá tính hữu ích và tốc độ ra quyết định của người dùng thực tế khi chọn môi trường trên giao diện JupyterHub:
* **B0 (Manual Selection):** Người dùng tự chọn Profile và Image bằng tay qua dropdown mặc định.
* **P2 (Intent-Spawner):** Người dùng mô tả bài toán bằng ngôn ngữ tự nhiên, hệ thống tự động nhận diện và đề xuất cấu hình phù hợp.

* **Chỉ số đo lường:**
  * **Selection Correctness:** Tỉ lệ chọn đúng môi trường tối ưu đáp ứng yêu cầu bài toán.
  * **Decision Time:** Thời gian từ lúc nhận đề bài đến khi xác nhận khởi tạo môi trường (giây).
  * **Interaction Burden:** Số lần chỉnh sửa, số thao tác click chuột và tỉ lệ hoàn thành nhiệm vụ.
* **Quy trình kiểm thử & Thực thi:**
  1. **Kiểm tra luồng phân bổ ngẫu nhiên (Smoke test):**
     ```bash
     make v5-user-study-smoke
     ```
  2. **Kiểm tra tính toàn vẹn của kịch bản nhiệm vụ:**
     ```bash
     make v5-user-study-test
     ```
  3. **Tạo lịch trình phân bổ đối ngẫu (Counterbalanced assignments):**
     ```bash
     PYTHONPATH=. .venv/bin/python -m evaluation_v5.user_study.runner generate-assignment \
       --task-set benchmarks_v5/user-study-draft-v1.yaml \
       --output benchmarks_v5/protocol-v5-e3-assignment-target-36 \
       --seed 20260827
     ```

---

### 4. Thực Nghiệm E4 — Đánh Giá Tài Nguyên Thực Tế Trên Kubernetes
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

### 5. Thực Nghiệm E5 — Hiệu Quả Lưu Trữ Image & Kiểm Thử Khởi Động Container (Image Storage & Functionality)
Đo lường dung lượng lưu trữ thực tế trên node Kubernetes và tính tương thích chức năng của các Image notebook trong catalog:
* **Tối ưu hóa lưu trữ:** Đo lường hiệu quả chia sẻ layer (layer sharing/deduplication) giữa các image notebook (`scipy-data-science`, `pytorch-deep-learning`, `tensorflow-deep-learning`, `minimal-python`).
* **Tính tương thích chức năng:** Kiểm tra container khởi động thành công và đáp ứng đúng năng lực (Python, PyTorch, CUDA/GPU, TensorFlow).

* **Chỉ số đo lường:**
  * **Layer Deduplication Ratio:** Tỉ lệ dung lượng tiết kiệm được nhờ dùng chung base layers.
  * **Container Startup Time:** Thời gian pull và khởi chạy container.
  * **Probe Success Rate:** Tỉ lệ vượt qua các kiểm tra thư viện lõi bên trong container.
* **Lệnh thực thi kiểm tra:**
  ```bash
  # Kiểm tra các tùy chọn đo đạc lưu trữ và kiểm thử Image:
  PYTHONPATH=. .venv/bin/python -m evaluation_v5.image_storage --help
  ```

---

## 📌 Lưu Ý Về Phạm Vi Các Thí Nghiệm (E1 – E5)

* **E1 (Chất lượng gợi ý) & E2 (Độ bền ngôn ngữ):** Đóng vai trò là các thực nghiệm cốt lõi kiểm chứng thuật toán P2, được thực thi và trích xuất số liệu tự động qua `make eval-offline`.
* **E4 (Đo lường tài nguyên thực tế trên K8s):** Đóng vai trò kiểm chứng hạ tầng thực tế trên cụm Kubernetes (OrbStack/Kind), đo đạc việc cấp phát phần cứng tránh OOM.
* **E3 (Khảo sát người dùng UI) & E5 (Image storage footprint):** Đóng vai trò mở rộng khảo sát trải nghiệm thực tế và tối ưu hóa hạ tầng lưu trữ.

---

## 📁 Cấu Trúc Thư Mục Kết Quả

* `results_v5/protocol-v5.0.0/E1/`: Chứa file `SUMMARY.md` (bảng số liệu hoàn chỉnh cho luận văn), `summary_metrics.json` (dữ liệu máy đọc), và thư mục `raw/` (chứa toàn bộ log từng trường hợp thử nghiệm).
* `results_v5/protocol-v5.0.0/E3/`: Chứa nhật ký sự kiện tương tác của người tham gia khảo sát (decision time, selection correctness).
* `results_v5/protocol-v5.0.0/E4/`: Chứa báo cáo tài nguyên, cgroup metrics đo đạc được từ các pod thực tế trên Kubernetes.
* `results_v5/protocol-v5.0.0/E5/`: Chứa số liệu kích thước layer và kết quả kiểm thử chức năng môi trường container.
