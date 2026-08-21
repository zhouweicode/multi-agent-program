"""Generate deterministic gkx_synthetic JSONL files without touching MySQL."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from data.synthetic_gkx import SyntheticConfig, write_dataset


def main() -> None:
    parser = argparse.ArgumentParser(description="生成可重复的 gkx_synthetic 科技知识图谱源数据")
    parser.add_argument("--output", type=Path, default=Path(".runtime/synthetic_gkx"))
    parser.add_argument("--seed", type=int, default=20260821)
    parser.add_argument("--scholars", type=int, default=2000)
    parser.add_argument("--organizations", type=int, default=100)
    parser.add_argument("--enterprises", type=int, default=300)
    parser.add_argument("--papers", type=int, default=15000)
    parser.add_argument("--projects", type=int, default=2000)
    parser.add_argument("--patents", type=int, default=5000)
    parser.add_argument("--industry-segments", type=int, default=100)
    parser.add_argument("--industry-events", type=int, default=500)
    args = parser.parse_args()
    config = SyntheticConfig(
        seed=args.seed, scholar_count=args.scholars, organization_count=args.organizations,
        enterprise_count=args.enterprises, paper_count=args.papers, project_count=args.projects,
        patent_count=args.patents, industry_segment_count=args.industry_segments,
        industry_event_count=args.industry_events,
    )
    manifest = write_dataset(args.output, config)
    print(json.dumps({"output": str(args.output), **manifest}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
