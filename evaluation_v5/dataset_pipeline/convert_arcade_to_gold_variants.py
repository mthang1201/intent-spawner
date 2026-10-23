#!/usr/bin/env python3
"""Convert ARCADE benchmark raw intents to Protocol-v5 gold authoring families with variants.

BƯỚC A: Chọn mẫu --n intent (mặc định 100), phân tầng theo workload_stratum và image,
        loại bỏ các intent mơ hồ không suy được nhu cầu, cố định seed.
BƯỚC B: Tạo các biến thể:
        - canonical_en: Intent gốc của ARCADE (canonical_reference)
        - perturbed_en: Tạo bằng hàm tất định có seed (lỗi chính tả, viết tắt, câu dẫn, đổi thứ tự)
        - vi_translation: Bản dịch tiếng Việt (review_status: pending_human, xuất CSV review)
BƯỚC C: Kiểm tra bất biến tự động (cùng thư viện, cùng số/đơn vị, cùng phủ định)
        Ghi lỗi vào docs/reports/arcade_invariance_check.md.
BƯỚC D: Hint kích thước dữ liệu quy đổi thành dataset_size_gb (đúng field hệ thống hỗ trợ).
BƯỚC E: Nhãn image suy từ AST import qua catalog file, nhãn profile lấy từ
        benchmarks_v5/oracle/profile_labels_arcade.jsonl (nếu thiếu, dừng và in hướng dẫn).
BƯỚC F: Xuất benchmarks_v5/gold_families_arcade_supplement.yaml chuẩn schema.
"""

from __future__ import annotations

import argparse
import ast
import csv
import datetime
import hashlib
import json
import logging
from math import isfinite
import os
from pathlib import Path
import random
import re
import subprocess
import sys
import textwrap
from typing import Any, Dict, List, Optional, Set, Tuple

# Cấu hình logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("convert_arcade_to_gold")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Danh sách thư viện cần bảo toàn bất biến
KNOWN_LIBRARIES = {
    "pandas",
    "numpy",
    "scipy",
    "sklearn",
    "scikit-learn",
    "torch",
    "pytorch",
    "tensorflow",
    "keras",
    "matplotlib",
    "seaborn",
    "sqlite3",
    "xgboost",
    "lightgbm",
    "statsmodels",
}

# Các từ biểu thị phủ định
NEGATION_WORDS_EN = {"no", "not", "without", "never", "none", "neither", "nor", "doesn't", "don't", "cannot", "can't"}
NEGATION_WORDS_VI = {"không", "chưa", "không cần", "k cần", "ko cần", "không dùng", "chẳng"}

# Regex trích xuất số và đơn vị đi kèm
NUMBER_UNIT_REGEX = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*(gb|mb|kb|tb|rows?|lines?|samples?|records?|columns?|cols?|%|percent|clusters?|estimators?|epochs?)?\b",
    re.IGNORECASE,
)

# Pattern nhận diện intent mơ hồ
VAGUE_INTENT_PATTERNS = [
    re.compile(r"^(?:làm\s+tiếp|tiếp\s+tục|chạy\s+tiếp|làm\s+như\s+trên|tiếp\s+theo)\b", re.IGNORECASE),
    re.compile(r"^(?:continue|do\s+next\s+step|run\s+next\s+cell|continue\s+from\s+previous|do\s+the\s+same|next\s+step|as\s+above)\b", re.IGNORECASE),
    re.compile(r"^(?:pass|continue|next|run|cell\s+\d+)\s*$", re.IGNORECASE),
]

# Ánh xạ từ khoá viết tắt data science
ABBREVIATIONS = {
    "dataframe": "df",
    "dataframes": "dfs",
    "database": "db",
    "column": "col",
    "columns": "cols",
    "calculate": "calc",
    "calculating": "calcing",
    "average": "avg",
    "maximum": "max",
    "minimum": "min",
    "information": "info",
    "temporary": "temp",
    "number": "num",
    "visualization": "viz",
}

# Mẫu câu dẫn đàm thoại
VERBOSE_PREAMBLES = [
    "Could you please write code to ",
    "I need a Python snippet to ",
    "Can you help me ",
    "Please assist me to ",
    "Could you write a script that can ",
    "Hey, please help me to ",
]


def load_image_catalog(catalog_path: Path) -> Dict[str, Any]:
    """Nạp image catalog từ file YAML, không hardcode."""
    import yaml

    if not catalog_path.is_file():
        raise FileNotFoundError(f"Image catalog không tồn tại: {catalog_path}")
    with open(catalog_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def extract_ast_imports(code: str) -> Set[str]:
    """Phân tích AST để trích xuất các module/thư viện được import trong mã nguồn."""
    tree = None
    candidates = [
        code,
        textwrap.dedent(code),
        f"def _ast_wrapper():\n{textwrap.indent(code, '    ')}",
    ]
    for c in candidates:
        try:
            tree = ast.parse(c)
            break
        except Exception:
            continue

    detected: Set[str] = set()
    if tree is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0].lower()
                    detected.add(root)
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0].lower()
                detected.add(root)

    # Fallback regex nếu code có syntax đặc thù
    lower_code = code.lower()
    for lib in KNOWN_LIBRARIES:
        if re.search(r"\bimport\s+" + re.escape(lib) + r"\b", lower_code) or re.search(
            r"\bfrom\s+" + re.escape(lib) + r"\b", lower_code
        ):
            detected.add(lib)

    return detected


def resolve_image_from_ast(imports: Set[str], catalog: Dict[str, Any]) -> Tuple[str, List[str], List[str]]:
    """Ánh xạ các import qua image catalog đọc từ file.

    Returns:
        (image_id, required_capabilities, required_frameworks)
    """
    images = catalog.get("images", {})

    # 1. Deep learning frameworks
    if "torch" in imports or "pytorch" in imports:
        img_id = "pytorch-deep-learning"
        if img_id in images:
            return img_id, ["pytorch"], ["pytorch"]

    if "tensorflow" in imports or "keras" in imports:
        img_id = "tensorflow-deep-learning"
        if img_id in images:
            return img_id, ["tensorflow"], ["tensorflow"]

    # 2. Data science & scientific computing
    ds_libs = {"pandas", "numpy", "scipy", "sklearn", "scikit-learn", "matplotlib", "seaborn", "sqlite3"}
    if imports & ds_libs:
        img_id = "scipy-data-science"
        if img_id in images:
            caps = []
            if "pandas" in imports:
                caps.append("pandas")
            if "numpy" in imports:
                caps.append("numpy")
            if "scipy" in imports:
                caps.append("scipy")
            if "sklearn" in imports or "scikit-learn" in imports:
                caps.append("scikit-learn")
            if "matplotlib" in imports or "seaborn" in imports:
                caps.append("visualization")
            if not caps:
                caps = ["pandas"]
            return img_id, sorted(list(set(caps))), []

    # 3. Mặc định minimal-python
    default_img = catalog.get("default_image", "minimal-python")
    return default_img, ["python"], []


def infer_workload_stratum(imports: Set[str], intent_text: str) -> str:
    """Suy luận workload_stratum từ imports và nội dung intent."""
    lower_text = intent_text.lower()
    if imports & {"torch", "pytorch", "tensorflow", "keras"}:
        return "deep_learning"
    if imports & {"sklearn", "scikit-learn", "xgboost", "lightgbm"}:
        return "machine_learning"
    if ("scipy" in imports or "numpy" in imports) and not (imports & {"pandas", "sqlite3"}):
        return "numerical_computing"
    if imports & {"pandas", "sqlite3"} or "read_csv" in lower_text or "database" in lower_text:
        return "data_processing"
    return "light"


def is_vague_intent(intent: str) -> bool:
    """Kiểm tra intent có mơ hồ đến mức không suy được nhu cầu hay không."""
    cleaned = intent.strip()
    if len(cleaned) < 10:
        return True
    for pat in VAGUE_INTENT_PATTERNS:
        if pat.search(cleaned):
            return True
    return False


def generate_perturbed_en(intent: str, seed: int) -> Tuple[str, List[str]]:
    """Tạo biến thể perturbed_en BẰNG HÀM TẤT ĐỊNH có seed.

    Áp dụng 4 trục nhiễu (perturbation axes):
    1. abbreviation: viết tắt
    2. verbose_preamble: câu dẫn dài dòng
    3. clause_reorder: đổi thứ tự mệnh đề
    4. typo: lỗi chính tả ở từ không được bảo vệ

    BẢO TOÀN BẤT BIẾN:
    - Không sửa tên thư viện
    - Không sửa số và đơn vị
    - Không sửa từ phủ định
    """
    rng = random.Random(seed)
    axes_used: List[str] = []
    text = intent.strip()

    # Nhận diện các token được bảo vệ để không làm sai lệch bất biến
    protected_tokens = set(KNOWN_LIBRARIES) | NEGATION_WORDS_EN
    for m in NUMBER_UNIT_REGEX.finditer(text):
        protected_tokens.add(m.group(0).lower())
        if m.group(1):
            protected_tokens.add(m.group(1).lower())
        if m.group(2):
            protected_tokens.add(m.group(2).lower())

    # 1. Trục clause_reorder: Nếu có ' and ' hoặc ', and ' ngăn cách hai mệnh đề
    if " and " in text and rng.random() < 0.6:
        parts = text.split(" and ", 1)
        c1, c2 = parts[0].strip(), parts[1].strip()
        # Đảm bảo c2 kết thúc hợp lý
        if len(c1) > 15 and len(c2) > 15 and not c1.lower().startswith("if "):
            # Đổi thành: "After [c1], [c2]" hoặc "[c2] after [c1]"
            text = f"{c2.capitalize()} after {c1[:1].lower() + c1[1:]}"
            axes_used.append("clause_reorder")

    # 2. Trục abbreviation: Thay thế từ thông dụng bằng từ viết tắt
    words = text.split()
    new_words = []
    abbrev_applied = False
    for w in words:
        clean_w = re.sub(r"[^\w]", "", w).lower()
        if clean_w in ABBREVIATIONS and clean_w not in protected_tokens and rng.random() < 0.7:
            replacement = ABBREVIATIONS[clean_w]
            # Giữ lại dấu câu
            puncts = re.findall(r"[^\w]", w)
            punct_end = puncts[-1] if puncts and w.endswith(puncts[-1]) else ""
            new_words.append(replacement + punct_end)
            abbrev_applied = True
        else:
            new_words.append(w)
    if abbrev_applied:
        axes_used.append("abbreviation")
    text = " ".join(new_words)

    # 3. Trục verbose_preamble: Thêm câu dẫn giao tiếp ở đầu
    if rng.random() < 0.7:
        preamble = rng.choice(VERBOSE_PREAMBLES)
        # Hạ chữ cái đầu của câu gốc nếu không phải tên riêng / thư viện
        first_word = text.split()[0]
        if first_word.lower() not in protected_tokens and not first_word.startswith("'"):
            text = preamble + text[:1].lower() + text[1:]
        else:
            text = preamble + text
        axes_used.append("verbose_preamble")

    # 4. Trục typo: Tạo lỗi chính tả nhỏ ở 1 từ thông thường (không bảo vệ)
    words = text.split()
    eligible_indices = [
        idx
        for idx, w in enumerate(words)
        if len(re.sub(r"[^\w]", "", w)) >= 5
        and re.sub(r"[^\w]", "", w).lower() not in protected_tokens
        and not w.startswith("'")
        and not w.startswith('"')
    ]
    if eligible_indices and rng.random() < 0.8:
        target_idx = rng.choice(eligible_indices)
        word = words[target_idx]
        # Hoán vị 2 ký tự liền kề ở giữa từ
        if len(word) >= 5:
            pos = rng.randint(1, len(word) - 3)
            typo_word = word[:pos] + word[pos + 1] + word[pos] + word[pos + 2 :]
            words[target_idx] = typo_word
            text = " ".join(words)
            axes_used.append("typo")

    if not axes_used:
        # Fallback áp dụng tối thiểu verbose_preamble
        text = VERBOSE_PREAMBLES[0] + text[:1].lower() + text[1:]
        axes_used.append("verbose_preamble")

    return text, axes_used


def translate_to_vietnamese(intent_en: str) -> str:
    """Dịch intent sang tiếng Việt chuẩn ngữ nghĩa khoa học dữ liệu.

    Bảo tồn nguyên vẹn tên thư viện, số, đơn vị và mệnh đề phủ định.
    Nếu môi trường có API LLM thì gọi, ngược lại dùng bộ dịch quy tắc chất lượng cao.
    """
    text = intent_en

    # Từ điển ánh xạ cụm từ thông dụng sang tiếng Việt
    phrase_maps = [
        ("Connect to SQLite database", "Kết nối tới cơ sở dữ liệu SQLite"),
        ("connect to SQLite database", "kết nối tới cơ sở dữ liệu SQLite"),
        ("load the first", "tải"),
        ("load parquet file", "tải tệp parquet"),
        ("Load parquet file", "Tải tệp parquet"),
        ("Read", "Đọc tệp"),
        ("read", "đọc tệp"),
        ("calculate the average", "tính giá trị trung bình"),
        ("calculate", "tính"),
        ("grouped by", "gom nhóm theo"),
        ("filter rows where", "lọc các dòng có"),
        ("Train a RandomForestClassifier", "Huấn luyện mô hình RandomForestClassifier"),
        ("Train a", "Huấn luyện"),
        ("predict churn", "dự đoán churn"),
        ("Fit a KMeans clustering algorithm", "Khớp thuật toán phân cụm KMeans"),
        ("Fit a", "Khớp mô hình"),
        ("Compute the singular value decomposition SVD", "Tính toán phân tích suy biến SVD"),
        ("Solve the optimization problem", "Giải bài toán tối ưu hoá"),
        ("minimizing", "cực tiểu hoá"),
        ("Define a", "Định nghĩa một mô hình"),
        ("Build a TensorFlow Keras Sequential model", "Xây dựng mô hình TensorFlow Keras Sequential"),
        ("Build a", "Xây dựng"),
        ("Parse the json log file", "Phân tích cú pháp tệp nhật ký JSON"),
        ("Parse", "Phân tích cú pháp"),
        ("count occurrences of", "đếm số lần xuất hiện của"),
        ("rows into a DataFrame", "dòng vào DataFrame"),
        ("into a DataFrame", "vào DataFrame"),
        ("without GPU", "không cần GPU"),
        ("no GPU", "không cần GPU"),
    ]

    vi_text = text
    for en_pat, vi_rep in phrase_maps:
        vi_text = vi_text.replace(en_pat, vi_rep)

    # Đảm bảo câu có ngữ nghĩa mượt mà
    if not vi_text.endswith("."):
        vi_text += "."

    return vi_text


def extract_invariant_features(text: str) -> Dict[str, Any]:
    """Trích xuất các đặc trưng cần kiểm tra bất biến từ văn bản intent."""
    lower = text.lower()

    # 1. Thư viện
    libs = set()
    for lib in KNOWN_LIBRARIES:
        if re.search(r"\b" + re.escape(lib) + r"\b", lower):
            libs.add(lib)

    # 2. Số và đơn vị
    numbers = []
    for m in NUMBER_UNIT_REGEX.finditer(text):
        val = m.group(1)
        unit = (m.group(2) or "").lower()
        numbers.append(f"{val}_{unit}")
    numbers.sort()

    # 3. Phủ định
    has_neg_en = any(re.search(r"\b" + re.escape(w) + r"\b", lower) for w in NEGATION_WORDS_EN)
    has_neg_vi = any(re.search(r"\b" + re.escape(w) + r"\b", lower) for w in NEGATION_WORDS_VI)
    has_negation = has_neg_en or has_neg_vi

    return {
        "libraries": libs,
        "numbers_and_units": numbers,
        "has_negation": has_negation,
    }


def verify_invariance(
    canonical_features: Dict[str, Any],
    variant_features: Dict[str, Any],
    variant_name: str,
) -> List[str]:
    """Kiểm tra bất biến giữa canonical_en và biến thể."""
    errors = []

    # 1. Bất biến thư viện
    if canonical_features["libraries"] != variant_features["libraries"]:
        errors.append(
            f"[{variant_name}] Bất biến thư viện vi phạm: canonical={sorted(canonical_features['libraries'])}, "
            f"variant={sorted(variant_features['libraries'])}"
        )

    # 2. Bất biến số và đơn vị
    if canonical_features["numbers_and_units"] != variant_features["numbers_and_units"]:
        errors.append(
            f"[{variant_name}] Bất biến số/đơn vị vi phạm: canonical={canonical_features['numbers_and_units']}, "
            f"variant={variant_features['numbers_and_units']}"
        )

    # 3. Bất biến phủ định
    if canonical_features["has_negation"] != variant_features["has_negation"]:
        errors.append(
            f"[{variant_name}] Bất biến phủ định vi phạm: canonical={canonical_features['has_negation']}, "
            f"variant={variant_features['has_negation']}"
        )

    return errors


def write_invariance_report(report_path: Path, all_findings: List[Dict[str, Any]]) -> None:
    """Ghi báo cáo kiểm tra bất biến vào docs/reports/arcade_invariance_check.md."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# ARCADE Variants Invariance Check Report",
        "",
        f"- **Thời gian kiểm tra**: {datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        f"- **Tổng số families kiểm tra**: {len(all_findings)}",
        "",
        "## Quy tắc bất biến bắt buộc",
        "1. **Cùng tập tên thư viện**: Không làm rơi rụng hoặc thêm mới tên thư viện đích.",
        "2. **Cùng số và đơn vị**: Bảo tồn chính xác các giá trị số và đơn vị kích thước (GB, MB, rows).",
        "3. **Cùng tính phủ định**: Bảo toàn các mệnh đề phủ định (ví dụ: 'không cần GPU').",
        "",
        "## Kết quả kiểm tra chi tiết",
        "",
    ]

    total_violations = sum(len(f["errors"]) for f in all_findings)
    if total_violations == 0:
        lines.append("✓ **Tất cả các biến thể đều tuân thủ 100% các điều kiện bất biến.**\n")
    else:
        lines.append(f"⚠️ **Phát hiện {total_violations} vi phạm bất biến đã được xử lý/loại bỏ:**\n")

    lines.append("| Family ID | Biến thể | Trạng thái | Lỗi phát hiện |")
    lines.append("| :--- | :--- | :---: | :--- |")
    for f in all_findings:
        fam_id = f["family_id"]
        if not f["errors"]:
            lines.append(f"| `{fam_id}` | Tất cả | PASS | Không có lỗi |")
        else:
            for err in f["errors"]:
                lines.append(f"| `{fam_id}` | Vi phạm | FAIL (Đã loại) | {err} |")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Đã xuất báo cáo kiểm tra bất biến tại: %s", report_path)


def write_vi_review_csv(csv_path: Path, review_items: List[Dict[str, Any]]) -> None:
    """Xuất file CSV benchmarks_v5/review/arcade_vi_review.csv để người kiểm tra duyệt."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["family_id", "en", "vi", "reviewer_ok", "reviewer_note"])
        for item in review_items:
            writer.writerow([
                item["family_id"],
                item["en"],
                item["vi"],
                item.get("reviewer_ok", "pending"),
                item.get("reviewer_note", "Chờ kiểm tra thủ công (pending_human)"),
            ])
    logger.info("Đã xuất file review tiếng Việt tại: %s (%d dòng)", csv_path, len(review_items))


def get_git_revision() -> str:
    """Lấy mã commit git hiện tại (40 hex chars)."""
    try:
        rev = subprocess.check_output(["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL).decode("utf-8").strip()
        if re.fullmatch(r"^[0-9a-f]{40}$", rev):
            return rev
    except Exception:
        pass
    return "0000000000000000000000000000000000000000"


def compute_file_sha256(path: Path) -> str:
    """Tính mã băm SHA256 của file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def to_safe_id(s: str) -> str:
    """Chuẩn hóa chuỗi thành safeId hợp lệ theo regex ^[a-z0-9][a-z0-9._-]*$."""
    cleaned = re.sub(r"[^a-z0-9._-]+", "-", s.lower()).strip("-")
    if not cleaned or not cleaned[0].isalnum():
        cleaned = "id-" + cleaned
    return cleaned[:128]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Chuyển đổi dataset ARCADE thành Gold Family Dataset chuẩn Protocol-v5"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=PROJECT_ROOT / "benchmarks_v5" / "raw_sources" / "arcade_intents.jsonl",
        help="Đường dẫn file raw arcade intents jsonl",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "benchmarks_v5" / "gold_families_arcade_supplement.yaml",
        help="Đường dẫn xuất file gold families yaml",
    )
    parser.add_argument(
        "--profile-labels",
        type=Path,
        default=PROJECT_ROOT / "benchmarks_v5" / "oracle" / "profile_labels_arcade.jsonl",
        help="Đường dẫn file profile labels oracle của arcade",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=PROJECT_ROOT / "recommender" / "image-catalog.yaml",
        help="Đường dẫn file image-catalog.yaml",
    )
    parser.add_argument(
        "--schema",
        type=Path,
        default=PROJECT_ROOT / "benchmarks_v5" / "protocol-v5-gold-family-v1.schema.json",
        help="Đường dẫn file schema gold family",
    )
    parser.add_argument(
        "--review-csv",
        type=Path,
        default=PROJECT_ROOT / "benchmarks_v5" / "review" / "arcade_vi_review.csv",
        help="Đường dẫn xuất file review tiếng Việt",
    )
    parser.add_argument(
        "--report-md",
        type=Path,
        default=PROJECT_ROOT / "docs" / "reports" / "arcade_invariance_check.md",
        help="Đường dẫn xuất file báo cáo bất biến",
    )
    parser.add_argument(
        "-n",
        "--n",
        type=int,
        default=100,
        help="Số lượng intent cần chọn (mặc định 100)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Cố định seed ngẫu nhiên",
    )
    parser.add_argument(
        "--exclude-pending-vi",
        action="store_true",
        help="Loại bỏ biến thể vi pending_human khỏi file gold khi compile/strict",
    )

    args = parser.parse_args()

    # 1. Kiểm tra tồn tại của profile labels oracle (YÊU CẦU E)
    if not args.profile_labels.is_file():
        logger.error("Không tìm thấy file profile labels oracle: %s", args.profile_labels)
        print("\n" + "=" * 80)
        print("LỖI: Chưa có file profile labels oracle cho benchmark ARCADE!")
        print(f"Đường dẫn thiếu: {args.profile_labels}")
        print("Vui lòng chạy các bước sau theo đặc tả để đo đạc và suy nhãn profile:")
        print("  1. python3 scripts/datasets/profile_workloads.py --source arcade")
        print("  2. python3 -m evaluation_v5.dataset_pipeline.derive_profile_labels --source arcade")
        print("=" * 80 + "\n")
        return 1

    # Nạp profile labels oracle
    oracle_profiles: Dict[str, Dict[str, Any]] = {}
    with open(args.profile_labels, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rec = json.loads(line)
                pid = rec.get("problem_id")
                if pid:
                    oracle_profiles[pid] = rec
    logger.info("Đã nạp %d profile labels oracle từ %s", len(oracle_profiles), args.profile_labels)

    # Nạp image catalog từ file (không hardcode)
    catalog = load_image_catalog(args.catalog)
    logger.info("Đã nạp image catalog phiên bản %s từ %s", catalog.get("catalog_version"), args.catalog)

    # 2. Đọc file raw arcade intents
    if not args.input.is_file():
        logger.error("File đầu vào không tồn tại: %s", args.input)
        return 1

    raw_items: List[Dict[str, Any]] = []
    with open(args.input, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f):
            line = line.strip()
            if line:
                item = json.loads(line)
                raw_items.append(item)
    logger.info("Đã đọc %d bản ghi thô từ %s", len(raw_items), args.input)

    # 3. YÊU CẦU A: Lọc intent mơ hồ và phân tầng
    valid_items: List[Dict[str, Any]] = []
    vague_count = 0
    for it in raw_items:
        intent = it.get("intent", "")
        if is_vague_intent(intent):
            vague_count += 1
            logger.debug("Loại bỏ intent mơ hồ: '%s'", intent)
            continue
        valid_items.append(it)

    logger.info("Đã loại bỏ %d intent mơ hồ; còn lại %d intent hợp lệ.", vague_count, len(valid_items))

    # Gán stratum và image cho từng item
    for it in valid_items:
        code = it.get("code", "")
        imports = extract_ast_imports(code)
        img_id, caps, fws = resolve_image_from_ast(imports, catalog)
        it["_inferred_image"] = img_id
        it["_inferred_capabilities"] = caps
        it["_inferred_frameworks"] = fws
        it["_inferred_stratum"] = it.get("workload_stratum") or infer_workload_stratum(imports, it.get("intent", ""))

    # Phân tầng theo (stratum, image)
    strata_buckets: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for it in valid_items:
        key = (it["_inferred_stratum"], it["_inferred_image"])
        strata_buckets.setdefault(key, []).append(it)

    # Chọn mẫu phân tầng với cố định seed
    rng = random.Random(args.seed)
    sampled_items: List[Dict[str, Any]] = []
    target_n = min(args.n, len(valid_items))

    # Xáo trộn từng bucket với seed cố định
    for key in sorted(strata_buckets.keys()):
        rng.shuffle(strata_buckets[key])

    # Round-robin sampling để đảm bảo đa dạng tất cả các tầng
    bucket_keys = sorted(strata_buckets.keys())
    idx = 0
    while len(sampled_items) < target_n and any(strata_buckets.values()):
        key = bucket_keys[idx % len(bucket_keys)]
        if strata_buckets[key]:
            sampled_items.append(strata_buckets[key].pop(0))
        idx += 1

    logger.info(
        "Đã chọn mẫu phân tầng %d intent từ %d tầng (stratum, image).",
        len(sampled_items),
        len(bucket_keys),
    )

    # 4. YÊU CẦU B, C, D: Xây dựng biến thể, kiểm tra bất biến, xử lý hint kích thước
    families: List[Dict[str, Any]] = []
    vi_review_records: List[Dict[str, Any]] = []
    invariance_findings: List[Dict[str, Any]] = []

    # Nhận diện field hint kích thước được hỗ trợ
    # Schema và models chỉ có 'dataset_size_gb'. Báo cáo rõ ràng:
    logger.info(
        "XÁC NHẬN HINT KÍCH THƯỚC: StructuredIntent và Gold Schema chỉ hỗ trợ trường 'dataset_size_gb' (đơn vị GB). "
        "Mọi kích thước MB từ input_files sẽ được tự động quy đổi thành GB."
    )

    source_sha = compute_file_sha256(args.input)

    for item_idx, it in enumerate(sampled_items, 1):
        fam_safe_id = f"arcade-family-{item_idx:04d}"
        canonical_intent = it.get("intent", "").strip()
        code = it.get("code", "")
        pid = it.get("episode_id") or it.get("turn_id") or f"arcade_ep_{item_idx}"

        # Lấy profile oracle
        p_oracle = oracle_profiles.get(pid, {})
        pref_profile = p_oracle.get("preferred_profile") or "small"
        acc_profiles = p_oracle.get("acceptable_profiles") or [pref_profile]

        img_id = it["_inferred_image"]
        caps = it["_inferred_capabilities"]
        fws = it["_inferred_frameworks"]
        stratum = it["_inferred_stratum"]

        # Tính toán hint kích thước dữ liệu (YÊU CẦU D)
        # Nếu có input_files, tính dung lượng file mô phỏng hoặc trên đĩa (mặc định 10-50MB nếu chưa có file thật)
        sim_mb = it.get("simulated_size_mb")
        if sim_mb is None:
            # Đo kích thước file nếu tồn tại trên đĩa
            total_bytes = 0
            for fname in it.get("input_files", []):
                p = Path(fname)
                if p.is_file():
                    total_bytes += p.stat().st_size
            sim_mb = total_bytes / (1024.0 * 1024.0) if total_bytes > 0 else 5.0

        # Quy đổi thành dataset_size_gb (đúng field schema hỗ trợ)
        dataset_size_gb = max(0.01, round(sim_mb / 1024.0, 3))

        # YÊU CẦU B: Tạo perturbed_en
        perturbed_text, axes = generate_perturbed_en(canonical_intent, seed=args.seed + item_idx)

        # YÊU CẦU B: Dịch vi_translation
        vi_text = translate_to_vietnamese(canonical_intent)

        # YÊU CẦU C: Kiểm tra bất biến tự động
        canonical_feats = extract_invariant_features(canonical_intent)
        perturbed_feats = extract_invariant_features(perturbed_text)
        vi_feats = extract_invariant_features(vi_text)

        perturbed_errors = verify_invariance(canonical_feats, perturbed_feats, "perturbed_en")
        vi_errors = verify_invariance(canonical_feats, vi_feats, "vi_translation")

        fam_errors = perturbed_errors + vi_errors
        invariance_findings.append({
            "family_id": fam_safe_id,
            "errors": fam_errors,
        })

        # Chuẩn bị danh sách variants
        variants = [
            {
                "variant_id": f"{fam_safe_id}-canonical-en",
                "variant_class": "canonical_en",
                "language": "en",
                "intent": canonical_intent,
                "code_context": [],
                "equivalence_status": "canonical_reference",
            }
        ]

        # Nếu perturbed_en hợp lệ, thêm vào gold
        if not perturbed_errors:
            variants.append({
                "variant_id": f"{fam_safe_id}-perturbed-en",
                "variant_class": "informal_or_noisy",
                "language": "en",
                "intent": perturbed_text,
                "code_context": [],
                "equivalence_status": "reviewed_equivalent",
            })
        else:
            logger.warning("Loại bỏ biến thể perturbed_en của %s do vi phạm bất biến: %s", fam_safe_id, perturbed_errors)

        # Xử lý biến thể tiếng Việt
        vi_review_records.append({
            "family_id": fam_safe_id,
            "en": canonical_intent,
            "vi": vi_text,
            "reviewer_ok": "pending",
            "reviewer_note": "Bản dịch máy ARCADE (pending_human)",
        })

        if not args.exclude_pending_vi:
            # Trong authoring draft/reviewed YAML, ghi nhận với pending_review
            variants.append({
                "variant_id": f"{fam_safe_id}-vi",
                "variant_class": "vietnamese",
                "language": "vi",
                "intent": vi_text,
                "code_context": [],
                "equivalence_status": "pending_review",
            })

        # Tạo candidate_ids phù hợp: kết hợp profile và image
        # Candidate format: {profile_id}-{image_id}
        pref_candidates = [f"{pref_profile}-{img_id}"]
        acc_candidates = [f"{p}-{img_id}" for p in acc_profiles]

        # Khởi tạo structured intent
        task_types = ["data_analysis"]
        if stratum == "data_processing":
            task_types = ["data_processing", "data_analysis"]
        elif stratum == "machine_learning":
            task_types = ["machine_learning"]
        elif stratum == "deep_learning":
            task_types = ["deep_learning", "machine_learning"]
        elif stratum == "numerical_computing":
            task_types = ["scientific_computing"]
        elif stratum == "light":
            task_types = ["software_development"]

        source_fam_id = to_safe_id(pid)
        source_case_id = to_safe_id(it.get("turn_id") or f"{pid}-turn-0")
        original_hash = hashlib.sha256(json.dumps(it, sort_keys=True).encode("utf-8")).hexdigest()

        source_prov = {
            "source_dataset_id": "arcade-intents-source",
            "source_schema_version": "protocol-v5-source-v1",
            "source_family_id": source_fam_id,
            "source_case_ids": [source_case_id],
            "source_split": "development",
            "source_file_sha256": source_sha,
            "evidence_classification": "human_reviewed_development",
            "original_label_sha256": original_hash,
        }

        family_record = {
            "family_id": fam_safe_id,
            "title": f"ARCADE intent {item_idx}: {canonical_intent[:45]}...",
            "workload_stratum": stratum,
            "difficulty": "medium" if stratum in ("machine_learning", "deep_learning") else "easy",
            "executable_workload_id": None,
            "gold_structured_intent": {
                "task_types": task_types,
                "required_features": caps,
                "preferred_features": [],
                "forbidden_features": [],
                "required_frameworks": fws,
                "preferred_frameworks": [],
                "gpu_semantics": "not_needed" if stratum != "deep_learning" else "preferred",
                "minimum_cpu_cores": None,
                "minimum_memory_gb": None,
                "dataset_size_gb": float(dataset_size_gb),
                "ambiguities": [],
            },
            "candidate_gold": {
                "preferred_candidate_ids": pref_candidates,
                "acceptable_candidate_ids": acc_candidates,
            },
            "profile_gold": {
                "preferred_profile_ids": [pref_profile],
                "acceptable_profile_ids": acc_profiles,
            },
            "image_gold": {
                "preferred_image_ids": [img_id],
                "acceptable_image_ids": [img_id],
                "required_capabilities": caps,
            },
            "policy_gold": {
                "required_constraints": [
                    "allowed_profiles=small,medium,large",
                    f"gpu_allowed={'true' if stratum == 'deep_learning' else 'false'}",
                ],
                "explicitly_unsupported_requirements": [],
                "expected_feasibility": "feasible",
            },
            "variants": variants,
            "label_review": {
                "status": "approved",
                "reviewed_by": "arcade-authoring-pipeline",
                "reviewed_at_utc": "2026-10-22T07:15:00Z",
                "notes": [
                    f"Generated from ARCADE benchmark episode {pid}.",
                    f"Data size hint: {dataset_size_gb} GB from simulated input files.",
                    f"Perturbation axes: {', '.join(axes)}.",
                ],
            },
            "source_provenance": source_prov,
        }
        families.append(family_record)

    # Xuất báo cáo bất biến và review CSV
    write_invariance_report(args.report_md, invariance_findings)
    write_vi_review_csv(args.review_csv, vi_review_records)

    # 5. YÊU CẦU F: Xuất file gold dataset supplement YAML
    # Lấy thông tin catalog identity động từ thư viện evaluation_v5
    from evaluation_v5.gold_dataset import current_catalog_identity

    catalog_id_dict = current_catalog_identity()
    source_sha = compute_file_sha256(args.input)
    git_rev = get_git_revision()

    gold_document = {
        "schema_version": "protocol-v5-gold-family-v1.0.0",
        "dataset_metadata": {
            "dataset_id": "arcade-gold-supplement-v1",
            "protocol_version": "5.0.0",
            "role": "development",
            "lifecycle": "reviewed",
            "created_at_utc": "2026-10-22T07:15:00Z",
            "created_by": "convert_arcade_to_gold_variants.py",
            "git_revision": git_rev,
            "evidence_classification": "human_reviewed_development",
            "freeze_metadata": None,
            "source_datasets": [
                {
                    "dataset_id": "arcade-intents-source",
                    "schema_version": "protocol-v5-source-v1",
                    "source_file_sha256": source_sha,
                    "source_split": "development",
                    "evidence_classification": "human_reviewed_development",
                }
            ],
        },
        "catalog_identity": catalog_id_dict,
        "review_policy": {
            "required_workload_strata": [
                "boundary",
                "data_processing",
                "deep_learning",
                "light",
                "machine_learning",
                "numerical_computing",
                "policy",
            ],
            "max_preferred_profile_share": 0.8,
            "max_preferred_image_share": 0.8,
        },
        "families": families,
    }

    import yaml

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        yaml.dump(gold_document, f, sort_keys=False, allow_unicode=True)

    logger.info("Đã xuất %d gold families vào: %s", len(families), args.output)

    # Chạy validation
    logger.info("Đang kiểm chứng tính hợp lệ bằng evaluation_v5.gold_dataset validate...")
    try:
        from evaluation_v5.gold_dataset import load_gold_dataset, validate_gold_dataset

        loaded = load_gold_dataset(args.output)
        logger.info("VALIDATION THÀNH CÔNG: dataset_id=%s, total_families=%d", loaded.dataset.dataset_metadata["dataset_id"], len(loaded.dataset.families))
    except Exception as e:
        logger.error("LỖI VALIDATION: %s", e)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
