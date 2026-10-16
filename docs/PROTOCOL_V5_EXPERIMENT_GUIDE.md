# Cẩm Nang Thực Nghiệm Thực Tế (Streamlined Experiment Runbook)

Tài liệu này hướng dẫn quy trình thực thi thực nghiệm tinh gọn cho đề tài **Intent- and Context-Aware Profile Recommendation for JupyterHub (Intent-Spawner)**.

Toàn bộ các rào cản hành chính giả định (yêu cầu chữ ký reviewer, quy trình đóng băng bất biến SHA-256, kiểm định cô lập split nhân tạo, và ma trận giả thuyết phức tạp) đã được tháo ngòi để phục vụ trực tiếp cho mục tiêu kỹ thuật thực tế của Đồ án Tốt nghiệp (ĐATN).

---

## ⚡ Bảng Lệnh Nhanh (Cheat Sheet)

| Tác vụ | Lệnh thực thi nhanh | Kết quả đầu ra |
| :--- | :--- | :--- |
| **Menu thực nghiệm tương tác (E1-E5)** | `make experiments` hoặc `python scripts/run_experiments.py` | Hộp thoại chọn E1..E5, chọn chế độ `local`/`llm`, tự hỏi API key nếu thiếu |
| **Kiểm thử logic lõi** | `make test` | 80/80 unit tests pass (0.6s) |
| **E1 Chế độ Local (0-cost, regex)** | `make eval-offline-local` | Lưu tại `results_v5/.../E1/run-e1-local` |
| **E1 Chế độ LLM (Semantic extractor)** | `make eval-offline-llm` | Tự hỏi API key; lưu tại `results_v5/.../E1/run-e1-llm-<model-slug>` |
| **Chạy với Dataset tùy chọn** | `make eval-dataset DATASET=path/to/dataset.yaml` | Kết quả đánh giá trên tập benchmark chỉ định |
| **Tổng quan độ bền ngôn ngữ (E2)** | `make eval-robustness` | Báo cáo phân tích 68 biến thể và độ bền tiếng Việt |
| **Smoke test khảo sát người dùng (E3)** | `make v5-user-study-smoke` | Kiểm thử luồng phân bổ kịch bản ngẫu nhiên cho người dùng |
| **Kiểm tra trạng thái cluster Kubernetes (E4)** | `make v5-e4-preflight` | Xác nhận cụm K8s sẵn sàng (OrbStack/Kind/K3s/Minikube) |
| **Đo lường lưu trữ Image E5 (Storage)** | `PYTHONPATH=. .venv/bin/python -m evaluation_v5.image_storage --experiment storage --mode docker --stage development` | Báo cáo chia sẻ layer và dung lượng tiết kiệm |
| **Kiểm thử chức năng Image E5 (Functional)** | `PYTHONPATH=. .venv/bin/python -m evaluation_v5.image_storage --experiment functional --recommendation-run results_v5/protocol-v5.0.0/E1/run-e1-local --mode docker --pull-policy missing` | Kết quả khởi chạy container và probe kiểm thử thư viện |

---

## 🔬 Chi Tiết Các Thực Nghiệm & Lựa Chọn Chế Độ (Local vs LLM)

Hệ thống hỗ trợ 2 chế độ trích xuất ý định (Intent Extractor) cho P2:
* **`local` (Mặc định):** Parser xác định dựa trên regex và rules. Chạy ngoại tuyến, không tốn chi phí token, độ trễ cực thấp (~1ms). Lưu tại `run-*-local`.
* **`llm`:** Gọi mô hình ngôn ngữ lớn (OpenAI / Ollama / vLLM / LiteLLM) để trích xuất ngữ nghĩa tự nhiên. Nếu thiếu API key trong môi trường, script sẽ **tự động hỏi người dùng paste API key trực tiếp trên terminal (bằng tiếng Anh)**.
* **Phân vùng lưu trữ tách biệt theo Model (`run-*-llm-<model-slug>`):** Kết quả của từng model LLM khác nhau (ví dụ: `gpt-4o-mini`, `gpt-4o`, `llama3.1`, `qwen2.5-coder`) được lưu vào các thư mục riêng biệt (như `run-e1-llm-gpt-4o-mini`, `run-e1-llm-llama3.1`) để không bao giờ bị ghi đè lẫn nhau và tuyệt đối không xung đột mã băm provenance.
* **Thanh tiến độ thời gian thực (Progress Bar & Heartbeat):** Mọi thí nghiệm đều hiển thị thanh tiến độ trực quan với thời gian đã chạy (Elapsed), dự kiến hoàn thành (ETA), và gửi nhịp tim (Heartbeat) định kỳ 30-60 giây để đảm bảo terminal không bao giờ bị đơ/đứng hình khi chờ phản hồi mạng.

### 1. Thực Nghiệm E1 — Đo Chất Lượng Gợi Ý (P1 vs P2 Recommendation Quality)
Đánh giá khả năng dịch intent của người dùng thành cấu hình phần cứng (Small, Medium, Large) và Image môi trường phù hợp.

```bash
# Cách 1: Dùng script điều phối chung có menu tương tác (Menu bằng tiếng Anh)
python scripts/run_experiments.py --experiment e1

# Cách 2: Chạy trực tiếp chế độ Local
make eval-offline-local
# (Kết quả lưu tại: results_v5/protocol-v5.0.0/E1/run-e1-local)

# Cách 3: Chạy trực tiếp chế độ LLM (sẽ hỏi API key và cấu hình model nếu chưa export)
make eval-offline-llm
# (Kết quả lưu tại: results_v5/protocol-v5.0.0/E1/run-e1-llm-<model-slug>)
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
* **Quy trình kiểm thử & Thực thi (Tích hợp trong `make experiments`):**
  Chạy `make experiments`, chọn `3. [E3] Human Usability Study`, menu sẽ hỗ trợ 4 chế độ:
  1. **`smoke` (Kiểm thử mô phỏng):** Xác nhận tính toàn vẹn của harness và luồng phân bổ đối ngẫu.
  2. **`generate-plan` (Tạo lịch phân bổ người dùng):** Tự động sinh danh sách phân bổ người dùng với số lượng $N$ tùy chọn (6, 12, hoặc 36).
  3. **`trial-session` (Chạy phiên thử nghiệm người dùng thật):** Người dùng ngồi làm bài trực tiếp trên terminal với 8 tasks (2 warm-up, 6 đo lường), trải nghiệm cả B0 và P2, đo thời gian ra quyết định chính xác tới miligiây, và trả lời bảng hỏi độ hài lòng (SEQ, SUS 10 câu, Final preference).
  4. **`finalize` (Đóng băng & Phân tích thống kê):** Tự động chạy mô hình thống kê GEE, tính điểm SUS, vẽ biểu đồ và xuất báo cáo `results_v5/protocol-v5.0.0/E3/run-e3-observed/`.

* **Lệnh chạy thủ công qua CLI:**
  ```bash
  # Tạo lịch phân bổ 12 người tham gia:
  python -m evaluation_v5.user_study.runner generate-assignments benchmarks_v5/user-study-draft-v1.yaml --output-dir benchmarks_v5/protocol-v5-e3-assignment-target-12 --study-id e3-user-study --participant-count 12 --consent-version consent-v1 --seed 20260827

  # Đóng băng kết quả sau khi thu thập staging:
  PROTOCOL_V5_ALLOW_DIRTY=1 python -m evaluation_v5.user_study.runner finalize --run-id run-e3-observed --task-set benchmarks_v5/user-study-draft-v1.yaml --assignments benchmarks_v5/protocol-v5-e3-assignment-target-12/assignment-manifest.json --events results_v5/protocol-v5.0.0/E3/staging/events.jsonl --sessions results_v5/protocol-v5.0.0/E3/staging/sessions.jsonl --exclusions results_v5/protocol-v5.0.0/E3/staging/exclusions.jsonl --questionnaires results_v5/protocol-v5.0.0/E3/staging/questionnaires.jsonl --execution-status OBSERVED
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
* **Kết nối Runtime (Docker / OrbStack):**
  Runner tự động nhận diện và kết nối trực tiếp với Docker daemon hoặc cụm Kubernetes của **OrbStack** trên macOS.

* **Quy trình thực thi chi tiết:**

  1. **Đo lường hiệu quả lưu trữ & chia sẻ layer (Storage Deduplication):**
     * *Chế độ mô phỏng (Dry-run, không cần Docker):*
       ```bash
       PYTHONPATH=. .venv/bin/python -m evaluation_v5.image_storage \
         --experiment storage \
         --mode dry-run \
         --stage development \
         --output-dir results_v5/protocol-v5.0.0/E5/run-e5-storage-dry-run
       ```
     * *Chế độ thực tế với Docker (OrbStack):*
       ```bash
       PYTHONPATH=. .venv/bin/python -m evaluation_v5.image_storage \
         --experiment storage \
         --mode docker \
         --stage development \
         --arch arm64 \
         --output-dir results_v5/protocol-v5.0.0/E5/run-e5-storage-orbstack
       ```

  2. **Kiểm thử chức năng Image trong Container (Functional Validation):**
     *(Yêu cầu đã chạy E1 trước đó để lấy file kết quả khuyến nghị làm đầu vào)*
     * *Chế độ mô phỏng (Dry-run):*
       ```bash
       PYTHONPATH=. .venv/bin/python -m evaluation_v5.image_storage \
         --experiment functional \
         --recommendation-run results_v5/protocol-v5.0.0/E1/run-e1-offline \
         --mode dry-run \
         --output-dir results_v5/protocol-v5.0.0/E5/run-e5-functional-dry-run
       ```
     * *Chế độ thực tế với Docker (OrbStack - tự động khởi chạy container & probe kiểm thử):*
       ```bash
       PYTHONPATH=. .venv/bin/python -m evaluation_v5.image_storage \
         --experiment functional \
         --recommendation-run results_v5/protocol-v5.0.0/E1/run-e1-offline \
         --mode docker \
         --pull-policy missing \
         --output-dir results_v5/protocol-v5.0.0/E5/run-e5-functional-orbstack
       ```
       *(Cờ `--pull-policy missing` đảm bảo OrbStack tự động tải image về nếu local store chưa có).*

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
