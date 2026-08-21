#!/usr/bin/env bash
#
# Hirevia — Uninstaller (Linux / macOS)
# Removes everything the installer created. Safe to re-run.
#
# Usage:
#   bash uninstall.sh            # remove app, venv, models, cache (keeps configs)
#   bash uninstall.sh --purge    # also delete profile.yaml / companies.yaml / results
#   bash uninstall.sh --keep-cache  # keep ~/.jobradar seen-jobs cache
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PURGE=false
KEEP_CACHE=false

for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=true ;;
        --keep-cache) KEEP_CACHE=true ;;
        -h|--help)
            echo "Usage: bash uninstall.sh [--purge] [--keep-cache]"
            echo "  --purge        also delete profile.yaml, companies.yaml, results"
            echo "  --keep-cache   keep the ~/.jobradar seen-jobs cache"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            exit 1
            ;;
    esac
done

echo "── Hirevia uninstaller ──"
echo "  Target: $SCRIPT_DIR"

# 1. Virtual environment
if [ -d "$SCRIPT_DIR/.venv" ]; then
    rm -rf "$SCRIPT_DIR/.venv"
    echo "  [OK] Removed virtual environment (.venv)"
else
    echo "  [>>] No .venv found"
fi

# 2. Downloaded binaries and models
if [ -d "$SCRIPT_DIR/bin" ]; then
    rm -rf "$SCRIPT_DIR/bin"
    echo "  [OK] Removed bin/ (llama-server etc.)"
else
    echo "  [>>] No bin/ found"
fi
if [ -d "$SCRIPT_DIR/models" ]; then
    rm -rf "$SCRIPT_DIR/models"
    echo "  [OK] Removed models/ (GGUF files)"
else
    echo "  [>>] No models/ found"
fi

# 3. Python bytecode caches
find "$SCRIPT_DIR" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
echo "  [OK] Removed __pycache__ directories"

# 4. Dashboard database
if [ -f "$SCRIPT_DIR/dashboard/jobradar_dashboard.db" ]; then
    rm -f "$SCRIPT_DIR/dashboard/jobradar_dashboard.db"
    echo "  [OK] Removed dashboard database"
else
    echo "  [>>] No dashboard database found"
fi

# 5. Seen-jobs cache (unless --keep-cache)
if ! $KEEP_CACHE; then
    if [ -f "$HOME/.jobradar/seen_jobs.db" ]; then
        rm -f "$HOME/.jobradar/seen_jobs.db"
        rmdir "$HOME/.jobradar" 2>/dev/null || true
        echo "  [OK] Removed ~/.jobradar seen-jobs cache"
    else
        echo "  [>>] No ~/.jobradar cache found"
    fi
else
    echo "  [>>] Keeping ~/.jobradar cache (--keep-cache)"
fi

# 6. Config files (only with --purge)
if $PURGE; then
    for f in profile.yaml companies.yaml results.csv results.json; do
        if [ -f "$SCRIPT_DIR/$f" ]; then
            rm -f "$SCRIPT_DIR/$f"
            echo "  [OK] Removed $f"
        fi
    done
else
    echo "  [>>] Keeping profile.yaml / companies.yaml (use --purge to delete)"
fi

echo ""
echo "  ✓ Hirevia uninstalled. Your profile and companies files are still in:"
echo "    $SCRIPT_DIR"
echo "  (Re-run with --purge to remove them too.)"
