#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LATEX_DIR="$ROOT_DIR/docs/latex"

if ! command -v lualatex >/dev/null 2>&1; then
    echo "Error: lualatex is not found in PATH."
    echo "Please ensure MacTeX / TeX Live is installed and in PATH (e.g. /Library/TeX/texbin)."
    exit 1
fi

if ! command -v bibtex >/dev/null 2>&1; then
    echo "Error: bibtex is not found in PATH."
    exit 1
fi

echo "=========================================================="
echo " Building Internship Report: docs/latex/main.pdf"
echo " Compiler: lualatex + bibtex"
echo "=========================================================="

cd "$LATEX_DIR"

# Clean stale auxiliary files to prevent LuaTeX counter corruption
rm -f main.aux

echo "==> [Pass 1/3] Compiling document structure with lualatex..."
lualatex -interaction=nonstopmode -halt-on-error main.tex > /dev/null

echo "==> Generating bibliography citations with bibtex..."
bibtex main

echo "==> [Pass 2/3] Linking citations with lualatex..."
lualatex -interaction=nonstopmode -halt-on-error main.tex > /dev/null

echo "==> [Pass 3/3] Resolving cross-references & TOC with lualatex..."
lualatex -interaction=nonstopmode -halt-on-error main.tex > /dev/null

if [[ -s "main.pdf" ]]; then
    PAGES=$(pdfinfo main.pdf 2>/dev/null | grep -i "Pages:" | awk '{print $2}' || echo "N/A")
    SIZE=$(du -h main.pdf | cut -f1 | tr -d ' ')
    echo "=========================================================="
    echo " SUCCESS: Built docs/latex/main.pdf"
    echo " File size: $SIZE | Total pages: $PAGES"
    echo " Destination: $LATEX_DIR/main.pdf"
    echo "=========================================================="
else
    echo "ERROR: main.pdf was not generated or is empty."
    exit 1
fi
