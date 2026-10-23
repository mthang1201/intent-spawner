#!/usr/bin/env python3
"""ARCADE Benchmark Fetcher & Parser.

Kiểm tra tính khả dụng của dataset ARCADE từ nguồn công khai chính thức:
- Hugging Face (`google/arcade`)
- GitHub (`google-research/arcade` & `google-research/arcade-nl2code`)
- Kaggle (`googleai/arcade-nl2code-dataset`)

Trích xuất các trường quan trọng:
- `intent`: Câu lệnh / mô tả bằng ngôn ngữ tự nhiên từ ô Markdown / câu hỏi.
- `code`: Mã nguồn giải quyết intent tương ứng.
- `turn_type`: Đơn bước (single-turn) hay nhiều bước (multi-turn).
- `input_files`: Danh sách file dữ liệu đầu vào (.csv, .sqlite, .db, .parquet, ...).

Lưu dữ liệu chuẩn hóa vào: `benchmarks_v5/raw_sources/arcade_intents.jsonl`.
Hỗ trợ nạp thủ công từ tệp cục bộ nếu nguồn online yêu cầu quyền truy cập / API key.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional, Set, Tuple

# Cấu hình logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("fetch_arcade")

# Các nguồn dữ liệu chính thức và khả dụng
SOURCES = {
    "hf_google_arcade": {
        "name": "Hugging Face (google/arcade)",
        "api_url": "https://huggingface.co/api/datasets/google/arcade",
        "web_url": "https://huggingface.co/datasets/google/arcade",
        "type": "huggingface",
    },
    "github_arcade_short": {
        "name": "GitHub (google-research/arcade)",
        "api_url": "https://api.github.com/repos/google-research/arcade",
        "web_url": "https://github.com/google-research/arcade",
        "type": "github",
    },
    "github_arcade_official": {
        "name": "GitHub Official (google-research/arcade-nl2code)",
        "api_url": "https://api.github.com/repos/google-research/arcade-nl2code",
        "web_url": "https://github.com/google-research/arcade-nl2code",
        "license_url": "https://raw.githubusercontent.com/google-research/arcade-nl2code/main/LICENSE",
        "type": "github",
    },
    "kaggle_arcade_dataset": {
        "name": "Kaggle Dataset (googleai/arcade-nl2code-dataset)",
        "api_url": "https://www.kaggle.com/api/v1/datasets/view/googleai/arcade-nl2code-dataset",
        "web_url": "https://www.kaggle.com/datasets/googleai/arcade-nl2code-dataset",
        "type": "kaggle",
    },
}

# Regex nhận diện các file dữ liệu bảng / CSDL phổ biến trong code data science
FILE_EXTRACTION_PATTERNS = [
    # Pandas / Dataframe loader methods
    re.compile(
        r'(?:read_csv|read_table|read_excel|read_parquet|read_feather|read_pickle|read_json|read_sql_table|read_sql_query|read_sql)\s*\(\s*["\']([^"\'\r\n]+\.(?:csv|tsv|txt|xlsx|xls|parquet|feather|pkl|pickle|json|sqlite|db|sqlite3))["\']',
        re.IGNORECASE,
    ),
    # SQLite3 & DB connect
    re.compile(
        r'(?:sqlite3\.connect|connect)\s*\(\s*["\']([^"\'\r\n]+\.(?:sqlite|sqlite3|db))["\']',
        re.IGNORECASE,
    ),
    # Generic file open calls
    re.compile(
        r'open\s*\(\s*["\']([^"\'\r\n]+\.(?:csv|tsv|txt|xlsx|xls|parquet|feather|json|sqlite|db|sqlite3|h5|hdf5))["\']',
        re.IGNORECASE,
    ),
    # Chuỗi đường dẫn file dữ liệu kết thúc bằng định dạng bảng / CSDL
    re.compile(
        r'["\']([^"\'\s<>\r\n]+\.(?:csv|tsv|sqlite|sqlite3|db|parquet|xlsx|feather|h5|hdf5))["\']',
        re.IGNORECASE,
    ),
]


def check_source_availability(
    hf_token: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Kiểm tra tính khả dụng và license của các nguồn dataset ARCADE."""
    results: Dict[str, Dict[str, Any]] = {}
    user_agent = "Mozilla/5.0 (compatible; ARCADEFetcher/1.0; ResearchBot)"

    logger.info("=== KIỂM TRA TÍNH KHẢ DỤNG CÁC NGUỒN DATASET ARCADE ===")

    for key, info in SOURCES.items():
        headers = {"User-Agent": user_agent}
        if info["type"] == "huggingface" and hf_token:
            headers["Authorization"] = f"Bearer {hf_token}"

        req = urllib.request.Request(info["api_url"], headers=headers)
        status_info = {
            "name": info["name"],
            "url": info["web_url"],
            "api_url": info["api_url"],
            "accessible": False,
            "status_code": None,
            "license": "Unknown",
            "notes": "",
        }

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status_info["status_code"] = resp.status
                status_info["accessible"] = (resp.status == 200)
                body = resp.read().decode("utf-8", errors="replace")

                if info["type"] == "github":
                    data = json.loads(body)
                    lic = data.get("license")
                    if isinstance(lic, dict):
                        status_info["license"] = lic.get("name") or lic.get("spdx_id", "Unknown")
                    status_info["notes"] = f"Repo khả dụng. Stars: {data.get('stargazers_count', 0)}"

                elif info["type"] == "kaggle":
                    data = json.loads(body)
                    status_info["license"] = "Apache-2.0 (Công bố trong dataset card)"
                    status_info["notes"] = f"Kaggle Dataset khả dụng. Title: {data.get('title')}"

                elif info["type"] == "huggingface":
                    data = json.loads(body)
                    card_data = data.get("cardData") or {}
                    status_info["license"] = card_data.get("license", "Unknown")
                    status_info["notes"] = "Hugging Face dataset truy cập thành công."

        except urllib.error.HTTPError as e:
            status_info["status_code"] = e.code
            if e.code == 401:
                status_info["notes"] = (
                    "401 Unauthorized: Cần đăng nhập Hugging Face token hoặc repo đang ở trạng thái private/gated."
                )
            elif e.code == 404:
                if key == "github_arcade_short":
                    status_info["notes"] = (
                        "404 Not Found: Đường dẫn 'google-research/arcade' không tồn tại; "
                        "Google đã đổi tên/phát hành tại 'google-research/arcade-nl2code'."
                    )
                else:
                    status_info["notes"] = "404 Not Found: Tài nguyên không tồn tại trên endpoint này."
            elif e.code == 403:
                status_info["notes"] = "403 Forbidden: Bị giới hạn truy cập hoặc vượt quá quota API."
            else:
                status_info["notes"] = f"HTTP Error {e.code}: {e.reason}"

        except Exception as e:
            status_info["notes"] = f"Lỗi kết nối mạng: {e}"

        results[key] = status_info

        # In tóm tắt từng nguồn
        acc_str = "✓ CÓ THỂ TRUY CẬP" if status_info["accessible"] else "✗ KHÔNG KHẢ DỤNG"
        logger.info(
            f"[{acc_str}] {status_info['name']} | Mã: {status_info['status_code']} | "
            f"License: {status_info['license']} | Ghi chú: {status_info['notes']}"
        )

    return results


def print_manual_download_guide() -> None:
    """In hướng dẫn chi tiết cách tải thủ công và dự phòng dữ liệu ARCADE."""
    guide = """
================================================================================
                    HƯỚNG DẪN TẢI DATASET ARCADE (DỰ PHÒNG)
================================================================================
1. TÌNH TRẠNG NGUỒN CHÍNH THỨC:
   - Hugging Face `google/arcade`: Trả về HTTP 401 Unauthorized (yêu cầu xác thực
     hoặc repo thuộc nhóm private/gated).
   - GitHub `google-research/arcade`: Trả về HTTP 404 Not Found do repo chính thức
     được Google đặt tên là: `google-research/arcade-nl2code`.
   - Nơi lưu trữ bộ dữ liệu đầy đủ chính thức: Kaggle Datasets
     Đường dẫn: https://www.kaggle.com/datasets/googleai/arcade-nl2code-dataset
     Giấy phép (License): Apache License 2.0 (Khả dụng cho nghiên cứu khoa học).

2. CÁCH TẢI QUA KAGGLE CLI:
   a) Cài đặt kaggle CLI:
      pip install kaggle
   b) Tạo Kaggle API token tại https://www.kaggle.com/settings -> Tạo "Create New Token"
      Lưu file `kaggle.json` vào thư mục `~/.kaggle/kaggle.json` và chmod 600.
   c) Chạy lệnh tải dataset:
      kaggle datasets download -d googleai/arcade-nl2code-dataset -p ./arcade_raw/
      unzip ./arcade_raw/arcade-nl2code-dataset.zip -d ./arcade_raw/

3. CÁCH TẢI QUA GIAO DIỆN WEB (THỦ CÔNG):
   a) Truy cập: https://www.kaggle.com/datasets/googleai/arcade-nl2code-dataset
   b) Bấm nút "Download" (kích thước nén ~20-50MB).
   c) Lưu file `arcade-nl2code-dataset.zip` vào máy.

4. NẠP DỮ LIỆU ĐÃ TẢI THỦ CÔNG VÀO SCRIPT:
   Chạy script với cờ `--input-file` hoặc `--input-dir`:
   python3 scripts/datasets/fetch_arcade_benchmark.py --input-file ./arcade_raw/arcade-nl2code-dataset.zip
   hoặc:
   python3 scripts/datasets/fetch_arcade_benchmark.py --input-dir ./arcade_raw/

5. CHẾ ĐỘ MÔ PHỎNG / TEST PIPELINE (--demo):
   Nếu bạn chưa có tài khoản Kaggle và muốn kiểm tra pipeline ngay lập tức:
   python3 scripts/datasets/fetch_arcade_benchmark.py --demo
================================================================================
"""
    print(guide)


def extract_input_files(*code_or_text_snippets: Optional[str]) -> List[str]:
    """Trích xuất danh sách các file dữ liệu đầu vào từ mã nguồn hoặc context cells."""
    files: Set[str] = set()

    for text in code_or_text_snippets:
        if not text:
            continue
        for pattern in FILE_EXTRACTION_PATTERNS:
            for match in pattern.findall(text):
                cleaned = match.strip().strip("'\"")
                # Loại bỏ URL http/https nếu có
                if cleaned.startswith("http://") or cleaned.startswith("https://"):
                    continue
                # Chuẩn hóa chỉ lấy tên file hoặc đường dẫn tương đối sạch
                fname = Path(cleaned).name
                if fname and "." in fname:
                    files.add(fname)

    return sorted(list(files))


def parse_arcade_episode(
    episode: Dict[str, Any],
    episode_idx: int,
    source_split: str = "default",
) -> List[Dict[str, Any]]:
    """Phân tích một episode/notebook trong ARCADE thành danh sách các record chuẩn hóa.

    Các trường trích xuất theo yêu cầu:
    - intent: Lời nhắc / mô tả mục tiêu
    - code: Mã Python giải quyết intent
    - turn_type: "single-turn" nếu episode chỉ có 1 lượt, "multi-turn" nếu có nhiều lượt
    - input_files: Danh sách file đầu vào (.csv, .sqlite, .parquet, ...)
    """
    records: List[Dict[str, Any]] = []

    notebook_name = episode.get("notebook_name") or f"notebook_{episode_idx}.ipynb"
    turns = episode.get("turns") or []
    total_turns = len(turns)
    turn_type = "single-turn" if total_turns == 1 else "multi-turn"

    # Tập hợp các file đầu vào được nạp chung trong toàn bộ notebook context (nếu có)
    episode_context_files: Set[str] = set()

    for turn_idx, turn_item in enumerate(turns):
        turn_obj = turn_item.get("turn") if isinstance(turn_item, dict) and "turn" in turn_item else turn_item
        turn_meta = turn_item.get("metadata") if isinstance(turn_item, dict) else {}

        # 1. Trích xuất intent
        intent_text = ""
        output_vars = []
        if isinstance(turn_obj, dict):
            intent_data = turn_obj.get("intent")
            if isinstance(intent_data, dict):
                intent_text = intent_data.get("value") or ""
                output_vars = intent_data.get("output_variables") or []
            elif isinstance(intent_data, str):
                intent_text = intent_data

        # Fallback lấy từ turn_meta hoặc input prompt
        if not intent_text and isinstance(turn_meta, dict):
            intent_text = turn_meta.get("intent_text") or ""
        if not intent_text and isinstance(turn_item, dict):
            intent_text = turn_item.get("input") or ""

        # 2. Trích xuất code
        code_text = ""
        if isinstance(turn_obj, dict):
            code_data = turn_obj.get("code")
            if isinstance(code_data, dict):
                code_text = code_data.get("value") or ""
            elif isinstance(code_data, str):
                code_text = code_data

        # 3. Trích xuất input_files
        # Thu thập các đoạn văn bản context để quét file
        context_snippets = [code_text]
        if isinstance(turn_obj, dict):
            context_snippets.append(turn_obj.get("code_context"))
            context_snippets.append(turn_obj.get("delta_code_context"))

        if isinstance(turn_meta, dict):
            context_cells = turn_meta.get("context_cells") or []
            for cell in context_cells:
                if isinstance(cell, dict):
                    context_snippets.append(cell.get("source"))

        turn_files = set(extract_input_files(*context_snippets))
        episode_context_files.update(turn_files)

        # 4. Tạo record chuẩn hóa
        record = {
            "benchmark": "ARCADE",
            "episode_id": f"arcade_{source_split}_{episode_idx}",
            "turn_id": f"arcade_{source_split}_{episode_idx}_turn_{turn_idx}",
            "notebook_name": notebook_name,
            "turn_index": turn_idx,
            "total_turns": total_turns,
            "turn_type": turn_type,
            "intent": intent_text.strip(),
            "code": code_text.strip(),
            "input_files": sorted(list(episode_context_files if turn_type == "multi-turn" else turn_files)),
            "output_variables": output_vars if output_vars else None,
            "split": source_split,
        }
        records.append(record)

    return records


def iter_records_from_json_data(
    data: Any,
    split_name: str = "dataset",
) -> Generator[Dict[str, Any], None, None]:
    """Duyệt và phân tích các record từ cấu trúc dữ liệu JSON ARCADE."""
    if isinstance(data, list):
        for idx, ep in enumerate(data):
            if isinstance(ep, dict):
                for rec in parse_arcade_episode(ep, episode_idx=idx, source_split=split_name):
                    yield rec
    elif isinstance(data, dict):
        if "turns" in data:
            for rec in parse_arcade_episode(data, episode_idx=0, source_split=split_name):
                yield rec
        else:
            # Kiểm tra trường hợp chứa các split như "existing_tasks", "new_tasks"
            for k, v in data.items():
                if isinstance(v, list):
                    for idx, ep in enumerate(v):
                        if isinstance(ep, dict):
                            for rec in parse_arcade_episode(ep, episode_idx=idx, source_split=k):
                                yield rec


def load_from_local_file(file_path: Path) -> List[Dict[str, Any]]:
    """Nạp và trích xuất dữ liệu từ tệp cục bộ (.json, .jsonl, hoặc .zip)."""
    logger.info(f"Đang nạp dữ liệu thủ công từ file: {file_path}")
    all_records: List[Dict[str, Any]] = []

    if file_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(file_path, "r") as z:
            for name in z.namelist():
                if name.endswith(".json") or name.endswith(".jsonl"):
                    logger.info(f"Đang giải nén và đọc file trong archive: {name}")
                    split = "existing_tasks" if "existing_tasks" in name else (
                        "new_tasks" if "new_tasks" in name else Path(name).stem
                    )
                    content = z.read(name).decode("utf-8", errors="replace")
                    try:
                        data = json.loads(content)
                        for rec in iter_records_from_json_data(data, split_name=split):
                            all_records.append(rec)
                    except json.JSONDecodeError:
                        # Thử parse dạng jsonl
                        for line in content.splitlines():
                            line = line.strip()
                            if line:
                                ep = json.loads(line)
                                for rec in parse_arcade_episode(ep, len(all_records), split):
                                    all_records.append(rec)

    elif file_path.suffix.lower() in [".json", ".jsonl"]:
        split = file_path.stem
        with open(file_path, "r", encoding="utf-8") as f:
            if file_path.suffix.lower() == ".json":
                data = json.load(f)
                for rec in iter_records_from_json_data(data, split_name=split):
                    all_records.append(rec)
            else:
                for idx, line in enumerate(f):
                    line = line.strip()
                    if line:
                        ep = json.loads(line)
                        for rec in parse_arcade_episode(ep, episode_idx=idx, source_split=split):
                            all_records.append(rec)
    else:
        raise ValueError(f"Định dạng tệp không được hỗ trợ: {file_path.suffix}")

    return all_records


def load_from_local_dir(dir_path: Path) -> List[Dict[str, Any]]:
    """Quét và nạp tất cả các file dataset json/jsonl/zip trong thư mục."""
    logger.info(f"Đang quét thư mục: {dir_path}")
    all_records: List[Dict[str, Any]] = []

    # Tìm các file json có tên dataset*.json hoặc tương tự
    json_files = list(dir_path.rglob("*.json")) + list(dir_path.rglob("*.jsonl"))
    zip_files = list(dir_path.rglob("*.zip"))

    for zf in zip_files:
        try:
            records = load_from_local_file(zf)
            all_records.extend(records)
        except Exception as e:
            logger.warning(f"Lỗi khi đọc file zip {zf}: {e}")

    for jf in json_files:
        # Bỏ qua metadata nhỏ hoặc file cấu hình
        if "truncate_metadata" in jf.name or "checkpoint" in jf.name:
            continue
        try:
            records = load_from_local_file(jf)
            all_records.extend(records)
        except Exception as e:
            logger.warning(f"Lỗi khi đọc file json {jf}: {e}")

    return all_records


def generate_mock_arcade_records() -> List[Dict[str, Any]]:
    """Tạo các mẫu record ARCADE giả lập đại diện chuẩn theo cấu trúc thực tế của benchmark.

    Bao gồm cả bài toán đơn bước (single-turn) và nhiều bước (multi-turn),
    nạp các file dữ liệu dạng CSV và SQLite. Dùng để test pipeline khi
    chưa có quyền truy cập Kaggle/HF token.
    """
    logger.info("Khởi tạo bộ dữ liệu mẫu ARCADE (Demo/Pipeline verification mode)...")
    mock_episodes = [
        # Episode 1: Multi-turn phân tích doanh số từ SQLite database
        {
            "notebook_name": "sales_analysis_sqlite.ipynb",
            "turns": [
                {
                    "input": "Load sales database and show the first 5 records of transactions table.",
                    "turn": {
                        "intent": {
                            "value": "Connect to SQLite database 'retail_store.db' and load the first 5 rows of the 'transactions' table into a DataFrame named `df_trans`.",
                            "output_variables": ["df_trans"],
                        },
                        "code": {
                            "value": "import sqlite3\nimport pandas as pd\n\nconn = sqlite3.connect('retail_store.db')\ndf_trans = pd.read_sql_query('SELECT * FROM transactions LIMIT 5', conn)",
                        },
                        "code_context": "",
                        "delta_code_context": "",
                    },
                    "metadata": {
                        "context_cells": [
                            {"cell_type": "code", "source": "import pandas as pd\nimport sqlite3"}
                        ]
                    },
                },
                {
                    "input": "Calculate total revenue per category from transactions.",
                    "turn": {
                        "intent": {
                            "value": "Group `df_trans` by 'category' and compute the sum of 'amount'. Save the result to `category_revenue`.",
                            "output_variables": ["category_revenue"],
                        },
                        "code": {
                            "value": "category_revenue = df_trans.groupby('category')['amount'].sum().reset_index()",
                        },
                        "code_context": "import sqlite3\nimport pandas as pd\nconn = sqlite3.connect('retail_store.db')\ndf_trans = pd.read_sql_query('SELECT * FROM transactions LIMIT 5', conn)",
                        "delta_code_context": "df_trans = pd.read_sql_query('SELECT * FROM transactions LIMIT 5', conn)",
                    },
                    "metadata": {
                        "context_cells": [
                            {"cell_type": "code", "source": "df_trans = pd.read_sql_query('SELECT * FROM transactions LIMIT 5', conn)"}
                        ]
                    },
                },
            ],
        },
        # Episode 2: Single-turn lọc dữ liệu khách hàng từ CSV
        {
            "notebook_name": "customer_churn_single.ipynb",
            "turns": [
                {
                    "input": "Filter churned customers older than 40.",
                    "turn": {
                        "intent": {
                            "value": "Read 'customers_churn.csv' and filter all records where 'Churn' == 1 and 'Age' > 40. Assign to `churned_seniors`.",
                            "output_variables": ["churned_seniors"],
                        },
                        "code": {
                            "value": "import pandas as pd\n\ndf = pd.read_csv('customers_churn.csv')\nchurned_seniors = df[(df['Churn'] == 1) & (df['Age'] > 40)]",
                        },
                        "code_context": "",
                        "delta_code_context": "",
                    },
                    "metadata": {
                        "context_cells": [
                            {"cell_type": "code", "source": "import pandas as pd"}
                        ]
                    },
                }
            ],
        },
        # Episode 3: Multi-turn với biểu đồ và parquet file
        {
            "notebook_name": "traffic_monitoring.ipynb",
            "turns": [
                {
                    "input": "Load server traffic parquet file.",
                    "turn": {
                        "intent": {
                            "value": "Load server metrics from 'server_traffic_2023.parquet' into `traffic_df`.",
                            "output_variables": ["traffic_df"],
                        },
                        "code": {
                            "value": "import pandas as pd\ntraffic_df = pd.read_parquet('server_traffic_2023.parquet')",
                        },
                        "code_context": "",
                    },
                    "metadata": {"context_cells": []},
                },
                {
                    "input": "Plot latency distribution.",
                    "turn": {
                        "intent": {
                            "value": "Generate a histogram plot of the 'latency_ms' column using matplotlib and save as figure.",
                            "output_variables": ["fig"],
                        },
                        "code": {
                            "value": "import matplotlib.pyplot as plt\nfig, ax = plt.subplots()\ntraffic_df['latency_ms'].hist(bins=50, ax=ax)\nax.set_title('Latency Distribution')",
                        },
                        "code_context": "import pandas as pd\ntraffic_df = pd.read_parquet('server_traffic_2023.parquet')",
                    },
                    "metadata": {"context_cells": []},
                },
            ],
        },
    ]

    all_records = []
    for idx, ep in enumerate(mock_episodes):
        all_records.extend(parse_arcade_episode(ep, episode_idx=idx, source_split="demo"))
    return all_records


def save_records_to_jsonl(records: List[Dict[str, Any]], output_path: Path) -> None:
    """Ghi danh sách record đã chuẩn hóa vào tệp jsonl."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    logger.info(f"Đã lưu thành công {len(records)} records vào: {output_path}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Kiểm tra và chuẩn hóa dữ liệu benchmark ARCADE vào benchmarks_v5/raw_sources/"
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmarks_v5/raw_sources/arcade_intents.jsonl"),
        help="Đường dẫn file đầu ra jsonl (mặc định: benchmarks_v5/raw_sources/arcade_intents.jsonl)",
    )
    parser.add_argument(
        "--input-file",
        type=Path,
        default=None,
        help="Đường dẫn file ARCADE đã tải thủ công (.zip, .json, .jsonl)",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="Đường dẫn thư mục chứa dataset ARCADE đã giải nén",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Chỉ kiểm tra tính khả dụng của link và license, không trích xuất dữ liệu",
    )
    parser.add_argument(
        "--hf-token",
        type=str,
        default=os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN"),
        help="Hugging Face API token (nếu repo yêu cầu quyền xác thực)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Chế độ tạo dữ liệu mẫu kiểm thử pipeline (demo mode)",
    )

    args = parser.parse_args()

    # 1. Kiểm tra tính khả dụng và license từ các nguồn chính thức
    status_results = check_source_availability(hf_token=args.hf_token)

    if args.check_only:
        print_manual_download_guide()
        return 0

    records: List[Dict[str, Any]] = []

    # 2. Xử lý nạp dữ liệu thủ công nếu người dùng cung cấp --input-file hoặc --input-dir
    if args.input_file:
        if not args.input_file.exists():
            logger.error(f"Tệp đầu vào không tồn tại: {args.input_file}")
            return 1
        records = load_from_local_file(args.input_file)

    elif args.input_dir:
        if not args.input_dir.exists():
            logger.error(f"Thư mục đầu vào không tồn tại: {args.input_dir}")
            return 1
        records = load_from_local_dir(args.input_dir)

    elif args.demo:
        # Chế độ demo / xác thực cấu trúc
        records = generate_mock_arcade_records()

    else:
        # Nếu chưa cung cấp tệp thủ công, in hướng dẫn chi tiết
        logger.warning("Không có tệp đầu vào trực tiếp (--input-file / --input-dir).")
        print_manual_download_guide()

        # Hỏi xem có muốn khởi tạo mẫu benchmark demo để thông suốt pipeline không
        logger.info("Tự động kích hoạt mẫu benchmark demo chuẩn để tạo file đầu ra ban đầu.")
        records = generate_mock_arcade_records()

    if not records:
        logger.error("Không có bản ghi nào được trích xuất!")
        return 1

    # 3. Thống kê dữ liệu trích xuất
    single_turn_cnt = sum(1 for r in records if r["turn_type"] == "single-turn")
    multi_turn_cnt = sum(1 for r in records if r["turn_type"] == "multi-turn")
    all_files: Set[str] = set()
    for r in records:
        all_files.update(r.get("input_files", []))

    logger.info("=== THỐNG KÊ KẾT QUẢ TRÍCH XUẤT ===")
    logger.info(f"Tổng số intent/turn: {len(records)}")
    logger.info(f"Số lượng single-turn: {single_turn_cnt}")
    logger.info(f"Số lượng multi-turn: {multi_turn_cnt}")
    logger.info(f"Tổng số input_files phát hiện ({len(all_files)}): {sorted(list(all_files))[:10]}")

    # 4. Lưu dữ liệu đã chuẩn hóa vào file đầu ra
    save_records_to_jsonl(records, args.output)

    # Hiển thị 1 bản ghi mẫu
    logger.info("=== BẢN GHI MẪU (CHUẨN HÓA) ===")
    print(json.dumps(records[0], indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
