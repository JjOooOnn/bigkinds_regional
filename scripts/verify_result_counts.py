from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.application.job_repository import JobRepository
from src.application.run_verification import compare_job_artifact_counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="작업의 처리 기사 수와 체크포인트·SQLite·Excel 결과 행 수를 대조합니다."
    )
    parser.add_argument("--db", required=True, type=Path, help="web_jobs.sqlite3 경로")
    parser.add_argument("--job-id", required=True, help="대조할 작업 ID")
    args = parser.parse_args()

    try:
        comparison = compare_job_artifact_counts(JobRepository(args.db), args.job_id)
    except (KeyError, FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(comparison.to_dict(), ensure_ascii=False, indent=2))
    return 0 if comparison.matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
