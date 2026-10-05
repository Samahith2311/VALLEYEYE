from __future__ import annotations

import ast
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PRODUCTION_SOURCE = REPOSITORY_ROOT / "src" / "valleyeye"
EVALUATION_SOURCE = REPOSITORY_ROOT / "evaluation"


def test_production_source_has_no_case_study_or_evaluation_references() -> None:
    forbidden_terms = ("emsr927", "trishuli", "rasuwa", "2026-08")
    violations: list[str] = []
    for path in sorted(PRODUCTION_SOURCE.rglob("*.py")):
        text = path.read_text(encoding="utf-8").casefold()
        for term in forbidden_terms:
            if term in text:
                violations.append(f"{path.relative_to(REPOSITORY_ROOT)} contains {term}")
    assert not violations, "\n".join(violations)


def test_production_source_never_imports_the_evaluation_package() -> None:
    violations: list[str] = []
    for path in sorted(PRODUCTION_SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            if any(
                module == "evaluation" or module.startswith("evaluation.") for module in modules
            ):
                violations.append(f"{path.relative_to(REPOSITORY_ROOT)}:{node.lineno}")
    assert not violations, "Production source imports evaluation: " + ", ".join(violations)


def test_evaluation_package_does_not_import_production_or_settings() -> None:
    violations: list[str] = []
    for path in sorted(EVALUATION_SOURCE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            if any(module == "valleyeye" or module.startswith("valleyeye.") for module in modules):
                violations.append(f"{path.relative_to(REPOSITORY_ROOT)}:{node.lineno}")
    assert not violations, "Evaluation imports production modules: " + ", ".join(violations)
