import json
import sys
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.document_splitter.evaluation import (
    build_splitter_regression_snapshot,
    evaluate_splitter_regression_snapshot,
    write_regression_artifact,
)


FIXTURE_ROOT = BACKEND_DIR / "tests" / "fixtures" / "splitter_regression"
EXPECTED_ROOT = FIXTURE_ROOT / "expected"


def build_case_snapshot(case: dict) -> dict:
    text = ""
    if "path" in case:
        sample_path = FIXTURE_ROOT / case["path"]
        text = sample_path.read_text(encoding="utf-8")

    return build_splitter_regression_snapshot(
        text,
        case["file_type"],
        pdf_path=str(FIXTURE_ROOT / case["pdf_path"]) if "pdf_path" in case else None,
        spreadsheet_path=(
            str(FIXTURE_ROOT / case["spreadsheet_path"])
            if "spreadsheet_path" in case
            else None
        ),
        word_path=str(FIXTURE_ROOT / case["word_path"]) if "word_path" in case else None,
    )


def main() -> None:
    cases = json.loads((FIXTURE_ROOT / "cases.json").read_text(encoding="utf-8"))

    for case in cases:
        snapshot = build_case_snapshot(case)
        metrics = evaluate_splitter_regression_snapshot(snapshot)

        write_regression_artifact(
            EXPECTED_ROOT / f"{case['name']}.snapshot.json",
            snapshot,
        )
        write_regression_artifact(
            EXPECTED_ROOT / f"{case['name']}.metrics.json",
            metrics,
        )

        print(f"generated: {case['name']}")


if __name__ == "__main__":
    main()
