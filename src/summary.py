from __future__ import annotations

from collections import Counter, defaultdict

from .models import AuditRow


def calculate_summary(rows: list[AuditRow]) -> dict:
    total = len(rows)
    normal = sum(row.verdict == "정상" for row in rows)
    grouped = defaultdict(lambda: {"issues": set(), "links": 0, "normal": 0, "errors": 0, "region_order": 0})
    for row in rows:
        key = (row.requested_date, row.region)
        item = grouped[key]
        item["issues"].add(row.issue_order)
        item["links"] += 1
        item["normal"] += row.verdict == "정상"
        item["errors"] += row.verdict != "정상"
        item["region_order"] = row.region_order
    details = []
    for (requested_date, region), item in sorted(grouped.items(), key=lambda pair: (pair[0][0], pair[1]["region_order"])):
        details.append({
            "requested_date": requested_date, "region": region, "issues": len(item["issues"]),
            "links": item["links"], "normal": item["normal"], "errors": item["errors"],
            "rate": item["normal"] / item["links"] if item["links"] else 0,
        })
    return {
        "total": total, "normal": normal, "errors": total - normal,
        "rate": normal / total if total else 0,
        "details": details, "verdict_counts": Counter(row.verdict for row in rows),
    }

