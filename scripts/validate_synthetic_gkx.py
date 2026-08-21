"""Validate generated JSONL before it is imported into MySQL."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from data.synthetic_gkx import read_dataset
from data.synthetic_validation import validate_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="校验 gkx_synthetic 数据完整性和图关系")
    parser.add_argument("--input", type=Path, default=Path(".runtime/synthetic_gkx"))
    args = parser.parse_args()
    result = validate_dataset(read_dataset(args.input))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
