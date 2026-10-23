## 📊 Recommendation Quality Evaluation Results

| System | Total Cases | Profile Match (Top-1) | Image Match | Joint Match | Fallback Rate | Median Latency |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **P1 (Rule-based Baseline)** | 83 | 53.0% (44/83) | 41.0% (34/83) | **38.6%** (32/83) | 0.0% | 0.06 ms |
| **P2 (Intent + Hybrid Retrieval)** | 83 | 47.0% (39/83) | 55.4% (46/83) | **41.0%** (34/83) | 16.9% | 2.23 ms |

### 🌐 Language Robustness Analysis (English vs Vietnamese)

| System | Language | Cases | Top-1 Profile | Image Match | Joint Match |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **P1** | EN | 27 | 63.0% (17/27) | 55.6% (15/27) | **51.9%** (14/27) |
| **P1** | VI | 56 | 48.2% (27/56) | 33.9% (19/56) | **32.1%** (18/56) |
| **P2** | EN | 27 | 55.6% (15/27) | 70.4% (19/27) | **51.9%** (14/27) |
| **P2** | VI | 56 | 42.9% (24/56) | 48.2% (27/56) | **35.7%** (20/56) |
