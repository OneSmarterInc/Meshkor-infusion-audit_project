"""Backend CLI wrapper to build the configured market report."""
import sys
from pathlib import Path

# Add project root and backend to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

import build_market_report

if __name__ == "__main__":
    build_market_report.main()
