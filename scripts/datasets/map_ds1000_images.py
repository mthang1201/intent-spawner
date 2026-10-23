#!/usr/bin/env python3
"""CLI runner script for mapping DS-1000 problems to JupyterHub Image Catalog."""

import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation_v5.dataset_pipeline.ds1000_image_mapper import main

if __name__ == "__main__":
    sys.exit(main())
