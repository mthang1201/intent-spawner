# Kế hoạch xây dựng dataset chuẩn: NL workload description → (profile, image)

> Mục tiêu (theo phản biện): đây là bài toán **recommend có ràng buộc**, nhãn phải dựa trên **đo đạc** (chạy reference solution trong harness E4, đo peak resource, gán profile nhỏ nhất đủ dùng), không dựa trên ý kiến chủ quan.

## 0. Trạng thái xác minh các nguồn dữ liệu (đã kiểm tra 2026-10-21)

| Nguồn | Trạng thái | Ghi chú |
|---|---|---|
| DS-1000 (arXiv 2211.11501) | Có sẵn, tải trực tiếp | 1.000 bài, nhãn 7 thư viện: pandas 291, NumPy 220, Matplotlib 155, sklearn 115, SciPy 106, PyTorch 68, TF 45 |
| ARCADE (arXiv 2212.09248) | **Đã release** tại `google-research/arcade-nl2code` | Advisor lo "will be released" — thực tế đã public. Split `new_tasks` tải thẳng (`dataset.json`); split `existing_tasks` phải chạy script reproduce + tải data artifacts qua **Kaggle API key**. 1.082 intent, thiên pandas |
| DistilKaggle (Zenodo 10317389, CC BY 4.0) | Có sẵn | 3 file: `code.csv` (~12M code cells), `markdown.csv` (~5M), `notebook_metrics.csv` (~517K notebooks). **Không có nhãn GPU/kích thước dữ liệu** — phải join với MetaKaggle metadata |
| Alibaba PAI GPU trace (alibaba/clusterdata) | Có sẵn | Chỉ dùng cho giả lập/scale, không có text intent |

## 1. Phụ thuộc tổng thể

```
T0.1 (tải dữ liệu) ──┐
T0.2 (đóng băng catalog) ──┤
                           ▼
              T1.x (xây corpus intent)          ← chạy song song với nhau
                           │
                           ▼
              T2.1 (harness đo peak resource)   ← cần T1.x + T0.1
                           │
                           ▼
              T2.2 (gán nhãn profile bằng rule) ← cần T2.1 + T0.2
                           │
                           ▼
              T3.1 (merge + split + schema)     ← cần T2.2 (+T1.x)
                           │
              T3.2 (baseline eval) ← cần T3.1   │ T3.3 (docs) ← cần T3.1
```

- **T0.1, T0.2 chạy song song**, độc lập hoàn toàn.
- **T1.1, T1.2, T1.3 chạy song song** (mỗi nguồn một task).
- T2.1 → T2.2 → T3.1 là chuỗi **tuần tự bắt buộc**.
- T2.3 (Alibaba, giả lập) chạy song song với T2, không chặn pipeline chính.

## 2. Các task và prompt

Quy ước effort: **low** (<30 phút), **medium** (30–90 phút), **high** (nhiều giờ / nhiều vòng lặp).
Quy ước model: những chỗ yêu cầu phán đoán thiết kế/nhãn → model reasoning mạnh; chỗ kỹ thuật lặp → model bình thường là đủ.

---

### T0.1 — Tải và kiểm chứng 4 nguồn dữ liệu
**Phụ thuộc:** không có (đầu pipeline). **Song song với:** T0.2.

```text
Bạn đang làm việc trong workspace /Users/mthang1201/Documents/datn/intent-spawner.

Nhiệm vụ: tải và kiểm chứng 4 nguồn dữ liệu cho benchmark intent→profile, lưu vào data/raw/:

1. DS-1000: tải từ repo HF/arXiv chính thức. Kiểm chứng đủ 1.000 mẫu, 7 thư viện, đúng phân phối nhãn (pandas 291, NumPy 220, Matplotlib 155, sklearn 115, SciPy 106, PyTorch 68, TF 45). Xuất ra data/raw/ds1000/ với file manifest.json ghi số mẫu + nguồn URL + checksum.
2. ARCADE: clone https://github.com/google-research/arcade-nl2code. Tải split new_tasks (dataset.json sẵn). Split existing_tasks chỉ ghi script reproduce, KHÔNG chạy (cần Kaggle key — đánh dấu cần ngưỡi dùng cung cấp ~/.kaggle/kaggle.json). Kiểm chứng new_tasks có bao nhiêu intent/notebook, thống kê độ dài intent.
3. DistilKaggle: tải 3 CSV từ Zenodo 10317389 vào data/raw/distilkaggle/. Kiểm chứng row count và schema từng file, ghi vào manifest.
4. Alibaba PAI GPU trace: tải file nhỏ nhất của GPU trace để xem schema (không tải full nếu >5GB), ghi schema vào manifest.

Acceptance criteria:
- data/raw/manifest.json tổng hợp: nguồn, URL, kích thước, rowcount, checksum, trạng thái OK/THIẾU.
- Không để file raw lớn vào git (ghi .gitignore).
- Báo cáo cuối: bảng trạng thái 4 nguồn + mọi bất thường.

Effort: medium (chủ yếu chờ tải). Model: bình thường, không cần reasoning mạnh.
Chạy ĐỘC LẬP, song song với T0.2. Output là input của T1.1–T1.3 và T2.x.
```

---

### T0.2 — Đóng băng catalog (profile, image) đã duyệt
**Phụ thuộc:** không có. **Song song với:** T0.1.

```text
Bạn đang làm việc trong workspace /Users/mthang1201/Documents/datn/intent-spawner.

Nhiệm vụ: đóng băng danh mục catalog đã được hội đồng/cố vấn duyệt thành file
data/catalog/catalog_v1.json. Nguồn: tìm trong workspace các định nghĩa profile
hiện có (recommender/, evaluation_v4/, evaluation_v5/, benchmarks_v5/, docs/).

Mỗi entry gồm: profile_id, image (đường dẫn image chuẩn), cpu (cores), memory_gb,
gpu (loại + count, null nếu không có), và flags: has_gpu, is_spark, is_ml.
Kèm catalog_v1.md mô tả ngắn từng profile và lý do tồn tại.

Acceptance criteria:
- catalog_v1.json parse được, đủ trường, không có profile trùng lặp.
- Ghi rõ version + ngày đóng băng. File này là BẮT BUỘC cho T2.2 (gán nhãn) — không sửa sau khi T2.2 bắt đầu; mọi thay đổi phải tạo catalog_v2.

Effort: low–medium. Model: reasoning mạnh (quyết định thiết kế nhãn, cần hiểu ràng buộc K8s/JupyterHub của project).
Chạy ĐỘC LẬP, song song với T0.1. Output bắt buộc cho T2.2.
```

---

### T1.1 — Corpus từ DS-1000
**Phụ thuộc:** T0.1. **Song song với:** T1.2, T1.3.

```text
Bạn đang làm việc trong workspace /Users/mthang1201/Documents/datn/intent-spawner.

Nhiệm vụ: chuyển DS-1000 (data/raw/ds1000/) thành data/interim/ds1000_intents.jsonl.
Mỗi dòng: {id, source: "ds1000", nl_intent (giữ nguyên câu hỏi gốc),
libraries (list nhãn thư viện), reference_solution_path, data_size_hint (nếu có)}.
Giữ nguyên văn bản gốc, KHÔNG paraphrase, KHÔNG tự gán nhãn profile/memory.

Acceptance criteria:
- Đúng 1.000 dòng, phân phối nhãn thư viện khớp manifest của T0.1.
- Script chuyển đổi lưu ở scripts/build_ds1000_intents.py, chạy lại được.

Effort: low. Model: bình thường. Chạy ĐỘC LẬP với T1.2/T1.3 sau khi T0.1 xong.
```

---

### T1.2 — Corpus từ ARCADE
**Phụ thuộc:** T0.1. **Song song với:** T1.1, T1.3.

```text
Bạn đang làm việc trong workspace /Users/mthang1201/Documents/datn/intent-spawner.

Nhiệm vụ: trích xuất intent từ split new_tasks của ARCADE thành data/interim/arcade_intents.jsonl.
Mỗi dòng: {id, source: "arcade", nl_intent (turn.intent.value, bản có output spec),
utterance_no_output_spec (metadata.utterance_without_output_spec),
libraries (suy ra từ code_context + reference code bằng import-scan, KHÔNG đoán),
reference_solution_path, notebook_context_length}.
Lọc bỏ intent có not_sure=true nếu tỉ lệ lớn >20% (báo cáo trước khi lọc).
Ghi nhận thiên lệch: ARCADE gần như toàn pandas — đánh cờ is_pandas_heavy=true để T3.1 cân bằng.

Acceptance criteria:
- Số dòng + thống kê intent length; import-scan có unit test trên 20 mẫu thủ công.
- Script ở scripts/build_arcade_intents.py.

Effort: low–medium. Model: bình thường. Chạy ĐỘC LẬP sau T0.1.
```

---

### T1.3 — Corpus quy mô lớn từ DistilKaggle
**Phụ thuộc:** T0.1. **Song song với:** T1.1, T1.2. **Lưu ý:** task nặng nhất về dữ liệu (12M+5M dòng) — phải streaming/chunking, không load toàn bộ vào memory.

```text
Bạn đang làm việc trong workspace /Users/mthang1201/Documents/datn/intent-spawner.

Nhiệm vụ: khai thác DistilKaggle (data/raw/distilkaggle/) để tạo data/interim/distilkaggle_intents.jsonl.
Phương pháp (chunked, dùng pandas read_csv(chunksize) hoặc DuckDB):
1. Với từng notebook: ghép markdown cell làm "intent thô", quét code.csv để suy ra tập import (thư viện).
2. Chỉ giữ notebook thỏa: có ≥1 markdown cell, ≥3 code cells, số import thuộc whitelist 7 thư viện + thư viện ML phổ biến.
3. Chọn 1 markdown cell đại diện (dài 20–500 ký tự, có động từ phân tích: train/plot/fit/analyze...) làm nl_intent.
4. KHÔNG tự gán nhãn GPU/memory (không có trong DistilKaggle). Nếu cần nhãn GPU tham khảo, join kernel metadata từ MetaKaggle — chỉ ghi ở cột gpu_hint, tách biệt khỏi nhãn chính.
Giới hạn: chọn ~20.000 notebook cân bằng theo tỉ lệ whitelist, ưu tiên đa dạng Performance Tier (từ notebook_metrics.csv).

Acceptance criteria:
- ~20.000 dòng, báo cáo phân phối thư viện + PT tier.
- Script ở scripts/build_distilkaggle_intents.py, có flag --limit để chạy thử.
- Xử lý được full 17M dòng CSV trong memory hạn chế (<8GB).

Effort: high. Model: bình thường cho code, nhưng review phương pháp lọc markdown bằng model reasoning mạnh trước khi chạy full.
Chạy ĐỘC LẬP sau T0.1. Kết quả là nguồn dữ liệu lớn nhất cho benchmark.
```

---

### T2.1 — Harness E4: chạy reference solution, đo peak resource
**Phụ thuộc:** T0.1 + T1.1 + T1.2 (T1.3 có thể thiếu — chạy được phần có). **Tuần tự trước:** T2.2.

```text
Bạn đang làm việc trong workspace /Users/mthang1201/Documents/datn/intent-spawner.

Bối cảnh: cố vấn yêu cầu nhãn phải dựa trên ĐO ĐẠC: chạy reference solution, đo peak
memory/CPU/GPU, rồi gán profile nhỏ nhất đủ dùng (task gán nhãn là T2.2, KHÔNG làm ở đây).

Nhiệm vụ: xây/dùng harness E4 (kiểm tra evaluation_v4/ và evaluation_v5/ trước, tái sử dụng
nếu có) để thực thi từng reference solution từ ds1000_intents.jsonl và arcade_intents.jsonl
trong container image tương ứng thư viện của mẫu. Đo: peak RSS (psutil trong container),
wall time, GPU peak memory + utilization (nvidia-smi/pynvml nếu GPU task), kích thước
dữ liệu đầu vào đã tải. Output: data/interim/resource_measurements.jsonl
{id, source, peak_rss_mb, wall_time_s, gpu_peak_mb, gpu_util, input_bytes, status: ok/oom/timeout/err}.

Yêu cầu kỹ thuật:
- Mỗi lần chạy 1 container riêng (docker), giới hạn --memory và --cpus ở mức MAX của catalog (T0.2),
  timeout mặc định 10 phút/sample.
- Resume được: skip sample đã có kết quả status=ok.
- DS-1000/ARCADE có data artifacts riêng — dùng đúng work_dir của từng bài.
- Ước tính trước tổng thởi gian (số sample × timeout) và báo cáo để xin phê duyệt trước khi chạy full.

Acceptance criteria:
- ≥90% sample chạy được status=ok; sample fail phân loại rõ oom/timeout/err.
- Báo cáo phân phối peak_rss_mb theo thư viện (bảng + histogram).

Effort: high (nhiều giờ chạy thực tế, cần vòng lặp debug container). Model: reasoning mạnh cho thiết kế harness + xử lý lỗi; phần chạy batch là script tự động.
CHẠY TUẦN TỰ sau T1.1/T1.2, BẮT BUỘC trước T2.2. Đây là điểm quyết định độ tin cậy của nhãn.
```

---

### T2.2 — Gán nhãn profile bằng rule từ measurement (KHÔNG đoán)
**Phụ thuộc:** T2.1 (bắt buộc) + T0.2 (bắt buộc). **Tuần tự sau:** T2.1.

```text
Bạn đang làm việc trong workspace /Users/mthang1201/Documents/datn/intent-spawner.

Nhiệm vụ: gán nhãn ground-truth cho từng mẫu bằng RULE DETERMINISTIC từ
data/interim/resource_measurements.jsonl + data/catalog/catalog_v1.json:
- gt_profile = profile nhỏ nhất trong catalog sao cho: cpu ≥ measured cpu demand,
  memory_gb ≥ peak_rss_mb (cộng hệ số an toàn 1.2×, làm tròn theo bậc catalog),
  gpu phù hợp nếu gpu_peak_mb > 0.
- gt_image = image của profile đó.
KHÔNG dùng model LLM để gán nhãn. Mọi rule phải viết trong scripts/label_profiles.py,
có unit test: 10 mẫu tính tay phải khớp output.

Output: data/labeled/intent_profile_v1.jsonl
{id, source, nl_intent, libraries, gt_profile, gt_image, peak_rss_mb, wall_time_s,
gpu_peak_mb, label_rule_version}.

Acceptance criteria:
- Không có mẫu nào gt_profile thỏa < measured demand.
- Báo cáo confusion giữa các profile (ma trận) + phân phối nhãn. Nếu một profile chiếm
  >80% nhãn → cảnh báo catalog quá thô, báo lại để quyết định nâng cấp catalog_v2.

Effort: medium. Model: reasoning mạnh (thiết kế rule + hệ số an toàn cần hiểu ràng buộc), code rule là deterministic.
CHẠY TUẦN TỰ, BẮT BUỘC sau T2.1 và T0.2. Đây là phần "nhãn đáng tin" cố vấn yêu cầu.
```

---

### T2.3 — Giả lập quy mô từ Alibaba PAI GPU trace
**Phụ thuộc:** T0.1. **Song song với toàn bộ T2.1→T3.3**, không chặn pipeline chính.

```text
Bạn đang làm việc trong workspace /Users/mthang1201/Documents/datn/intent-spawner.

Nhiệm vụ: dùng Alibaba GPU trace (data/raw/alibaba/, schema đã ghi ở T0.1) để tạo
bộ workload giả lập cho đánh giá quy mô lớn: sinh các workload tổng hợp {resource_shape,
duration, arrival_time} theo phân phối thực của trace, GHÉP NGẪU NHIÊN với nl_intent
từ data/interim/*_intents.jsonl để tạo request stream mô phỏng cho hệ thống recommender.

Acceptance criteria:
- data/sim/workload_stream.jsonl: ≥10.000 request, thống kê arrival/duration khớp trace gốc
  (so sánh histogram, ghi vào báo cáo).
- Script ở scripts/build_sim_stream.py. Ghi rõ đây là dữ liệu GIẢ LẬP, không dùng làm ground truth nhãn.

Effort: medium. Model: bình thường. ĐỘC LẬP, không ảnh hưởng pipeline chính.
```

---

### T3.1 — Merge, cân bằng, split, đóng schema benchmark
**Phụ thuộc:** T2.2 + T1.3 (T1.3 nếu chưa xong có thể chạy T3.1 với 2 nguồn đầu, rồi cập nhật). **Tuần tự sau:** T2.2.

```text
Bạn đang làm việc trong workspace /Users/mthang1201/Documents/datn/intent-spawner.

Nhiệm vụ: hợp nhất data/labeled/intent_profile_v1.jsonl (và phần đã gán nhãn của
distilkaggle nếu có) thành benchmark chính thức:
- data/benchmark/v1/train.jsonl / dev.jsonl / test.jsonl
- Split theo nguồn + thư viện, tránh leak intent gần giống nhau giữa các split
  (dedup bằng normalized intent, ngưỡng similarity tự chọn và giải thích).
- Cân bằng: giới hạn tỉ lệ is_pandas_heavy (ARCADE) để không át các thư viện khác.
- Schema v1 (data/benchmark/v1/SCHEMA.md): định nghĩa từng trường, quy tắc gán nhãn (rule version),
  và cam kết: nhãn = đo đạc, không phải annotation thủ công.

Acceptance criteria:
- Không có intent trùng lặp cross-split (kiểm chứng bằng script).
- Báo cáo: số mẫu/split, phân phối gt_profile và thư viện/split, độ dài intent.
- test.jsonl giữ nguyên, không dùng để tune.

Effort: medium. Model: reasoning mạnh cho quyết định split/dedup; code là deterministic.
CHẠY TUẦN TỰ sau T2.2.
```

---

### T3.2 — Baseline evaluation của recommender trên benchmark v1
**Phụ thuộc:** T3.1. **Song song với:** T3.3.

```text
Bạn đang làm việc trong workspace /Users/mthang1201/Documents/datn/intent-spawner.

Nhiệm vụ: chạy recommender hiện có (recommender/) trên data/benchmark/v1/test.jsonl,
so khớp prediction với gt_profile/gt_image. Metrics: accuracy@1, accuracy@3,
ma trận confusion profile, tỉ lệ over-provision (predict lớn hơn gt) và under-provision
(predict nhỏ hơn gt — cần báo cáo riêng vì nguy hiểm hơn).
Output: results_v5/baseline_benchmark_v1/ gồm metrics.json + confusion.png + file
prediction từng mẫu.

Acceptance criteria:
- metrics.json có đủ metric trên, kèm timestamp + commit hash của recommender.
- Viết 1 đoạn kết luận 10 dòng: recommender yếu nhất ở đâu, theo thư viện nào.

Effort: medium. Model: reasoning mạnh cho phân tích kết quả, phần chạy eval là script.
ĐỘC LẬP với T3.3, cần T3.1 xong.
```

---

### T3.3 — Tài liệu hóa dataset (để hội đồng tin nhãn)
**Phụ thuộc:** T3.1. **Song song với:** T3.2.

```text
Bạn đang làm việc trong workspace /Users/mthang1201/Documents/datn/intent-spawner.

Nhiệm vụ: viết docs/benchmark_v1.md (trình bày dùng được cho chương dataset của luận văn):
1. Bảng nguồn dữ liệu + trạng thái license (DS-1000, ARCADE, DistilKaggle CC BY 4.0, Alibaba trace).
2. Pipeline tạo nhãn: text → đo peak resource (harness E4) → rule gán profile nhỏ nhất đủ dùng.
   Nhấn mạnh: nhãn dựa trên đo đạc, tái lập được (rule version + script + catalog version).
3. Thống kê benchmark v1: kích thước, phân phối, ví dụ 5 mẫu đa dạng.
4. Giới hạn đã biết: thiên lệch pandas (ARCADE), DistilKaggle thiếu nhãn GPU gốc, Alibaba chỉ giả lập.

Acceptance criteria:
- Mọi con số trong doc trích từ metrics.json/thống kê thực tế, không ước lượng.
- Trích nguồn kèm URL cho từng dataset.

Effort: low–medium. Model: reasoning mạnh (văn học thuật). ĐỘC LẬP với T3.2.
```

## 3. Tổng hợp effort / model / phụ thuộc

| Task | Effort | Model | Phụ thuộc | Song song được |
|---|---|---|---|---|
| T0.1 Tải dữ liệu | medium | bình thường | — | T0.2 |
| T0.2 Catalog v1 | low–medium | reasoning mạnh | — | T0.1 |
| T1.1 DS-1000 | low | bình thường | T0.1 | T1.2, T1.3 |
| T1.2 ARCADE | low–medium | bình thường | T0.1 | T1.1, T1.3 |
| T1.3 DistilKaggle | high | bình thường + review mạnh | T0.1 | T1.1, T1.2 |
| T2.1 Harness đo | high | reasoning mạnh | T0.1, T1.1, T1.2 | — |
| T2.2 Gán nhãn rule | medium | reasoning mạnh | T2.1, T0.2 | — |
| T2.3 Giả lập Alibaba | medium | bình thường | T0.1 | toàn bộ |
| T3.1 Benchmark v1 | medium | reasoning mạnh | T2.2 | — |
| T3.2 Baseline eval | medium | reasoning mạnh | T3.1 | T3.3 |
| T3.3 Viết docs | low–medium | reasoning mạnh | T3.1 | T3.2 |

## 4. Rủi ro chính

1. **ARCADE existing_tasks cần Kaggle API key** → chuẩn bị key trước T0.1.
2. **T2.1 tốn thờigian thực** (container × ~2.000 sample) → chạy pilot 50 mẫu trước, ước lượng rồi mới chạy full.
3. **Catalog quá thô** → nhãn tập trung 1 profile; giải pháp: catalog_v2 chi tiết hơn (thảo luận với cố vấn trước T0.2).
4. **DistilKaggle không có nhãn GPU** → chỉ là mở rộng quy mô, không phải nguồn nhãn chính.
