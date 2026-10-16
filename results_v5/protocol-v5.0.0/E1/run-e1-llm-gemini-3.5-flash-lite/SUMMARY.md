## 📊 Kết Quả Đánh Giá Chất Lượng Gợi Ý (Recommendation Quality)

| Hệ Thống (System) | Tổng số mẫu | Profile Match (Top-1) | Image Match | Khớp Đồng Thời (Joint) | Tỉ lệ Fallback | Thời gian xử lý (Median) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **P1 (Rule-based Baseline)** | 83 | 53.0% (44/83) | 41.0% (34/83) | **38.6%** (32/83) | 0.0% | 0.06 ms |
| **P2 (Intent + Hybrid Retrieval)** | 83 | 49.4% (41/83) | 42.2% (35/83) | **34.9%** (29/83) | 77.1% | 1439.06 ms |

### 🌐 Phân Tích Độ Bền Ngôn Ngữ (Language Robustness: English vs Vietnamese)

| Hệ Thống | Ngôn Ngữ | Số mẫu | Top-1 Profile | Image Match | Khớp Đồng Thời |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **P1** | EN | 27 | 63.0% (17/27) | 55.6% (15/27) | **51.9%** (14/27) |
| **P1** | VI | 56 | 48.2% (27/56) | 33.9% (19/56) | **32.1%** (18/56) |
| **P2** | EN | 27 | 59.3% (16/27) | 55.6% (15/27) | **48.1%** (13/27) |
| **P2** | VI | 56 | 44.6% (25/56) | 35.7% (20/56) | **28.6%** (16/56) |
