"""Turn a JUnit XML test report into a Markdown summary.

In GitHub Actions the summary is appended to $GITHUB_STEP_SUMMARY, which is
shown on the workflow run page. Run locally, it is printed to the terminal.

Usage: python .github/scripts/test_report.py test-reports/junit.xml "Titre"
"""

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _outcome(testcase):
    for tag, label in (("failure", "❌ Échec"), ("error", "💥 Erreur"), ("skipped", "⏭️ Ignoré")):
        element = testcase.find(tag)
        if element is not None:
            return label, element
    return "✅ OK", None


def _escape_cell(text):
    return text.replace("|", "\\|").replace("\n", " ")


def build_summary(report_path, title):
    if not report_path.is_file():
        return f"## {title}\n\n⚠️ Aucun rapport trouvé (`{report_path}`) : les tests n'ont pas pu démarrer.\n"

    root = ET.parse(report_path).getroot()
    testcases = list(root.iter("testcase"))
    results = [(case, *_outcome(case)) for case in testcases]
    counts = {
        "total": len(results),
        "passed": sum(label == "✅ OK" for _, label, _ in results),
        "failed": sum(label == "❌ Échec" for _, label, _ in results),
        "errors": sum(label == "💥 Erreur" for _, label, _ in results),
        "skipped": sum(label == "⏭️ Ignoré" for _, label, _ in results),
    }
    duration = sum(float(case.get("time", 0) or 0) for case in testcases)
    status = "✅ Tous les tests passent" if counts["failed"] + counts["errors"] == 0 else "❌ Des tests échouent"

    lines = [
        f"## {title} — {status}",
        "",
        "| Total | ✅ Réussis | ❌ Échecs | 💥 Erreurs | ⏭️ Ignorés | ⏱️ Durée |",
        "|---:|---:|---:|---:|---:|---:|",
        f"| {counts['total']} | {counts['passed']} | {counts['failed']} | "
        f"{counts['errors']} | {counts['skipped']} | {duration:.2f} s |",
        "",
    ]

    problems = [(case, label, element) for case, label, element in results if label in ("❌ Échec", "💥 Erreur")]
    if problems:
        lines += ["### Tests en échec", ""]
        for case, label, element in problems:
            name = f"{case.get('classname', '')}.{case.get('name', '')}"
            message = element.get("message", "") or ""
            details = (element.text or "").strip()
            lines += [
                f"<details><summary>{label} <code>{name}</code> — {_escape_cell(message)[:200]}</summary>",
                "",
                "```",
                details[-3000:],
                "```",
                "",
                "</details>",
                "",
            ]

    lines += [
        "<details><summary>Détail de tous les tests</summary>",
        "",
        "| Résultat | Classe | Test | Durée |",
        "|---|---|---|---:|",
    ]
    for case, label, _ in sorted(results, key=lambda item: (item[0].get("classname", ""), item[0].get("name", ""))):
        classname = case.get("classname", "").rsplit(".", 1)[-1]
        lines.append(
            f"| {label} | {_escape_cell(classname)} | `{_escape_cell(case.get('name', ''))}` | "
            f"{float(case.get('time', 0) or 0):.3f} s |"
        )
    lines += ["", "</details>", ""]
    return "\n".join(lines)


def main():
    report_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("test-reports/junit.xml")
    title = sys.argv[2] if len(sys.argv) > 2 else "Rapport des tests"
    summary = build_summary(report_path, title)

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as output:
            output.write(summary + "\n")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(summary)


if __name__ == "__main__":
    main()
