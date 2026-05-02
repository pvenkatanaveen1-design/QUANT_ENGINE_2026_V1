from __future__ import annotations

import ast
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".cursor", "__pycache__", ".venv", "venv"}
EXPLAIN_SUFFIX = "_EXPLAIN.txt"


@dataclass
class PySummary:
    module_path: Path
    class_names: list[str]
    function_names: list[str]
    import_names: list[str]
    has_main_guard: bool
    line_count: int


def should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)


def parse_python_file(path: Path) -> PySummary:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source) if source.strip() else ast.parse("")

    class_names: list[str] = []
    function_names: list[str] = []
    import_names: list[str] = []
    has_main_guard = False

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            class_names.append(node.name)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function_names.append(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                import_names.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            import_names.append(node.module or "")
        elif isinstance(node, ast.If):
            test = node.test
            if (
                isinstance(test, ast.Compare)
                and isinstance(test.left, ast.Name)
                and test.left.id == "__name__"
                and len(test.ops) == 1
                and isinstance(test.ops[0], ast.Eq)
                and len(test.comparators) == 1
                and isinstance(test.comparators[0], ast.Constant)
                and test.comparators[0].value == "__main__"
            ):
                has_main_guard = True

    return PySummary(
        module_path=path,
        class_names=sorted(set(class_names)),
        function_names=sorted(set(function_names)),
        import_names=sorted({name for name in import_names if name}),
        has_main_guard=has_main_guard,
        line_count=len(source.splitlines()),
    )


def render_explainer(summary: PySummary) -> str:
    rel_path = summary.module_path.relative_to(PROJECT_ROOT).as_posix()
    last_updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    module_name = summary.module_path.stem

    if summary.class_names or summary.function_names:
        behavior = (
            "This module currently contains concrete symbols listed below. "
            "Update this file whenever behavior changes."
        )
    else:
        behavior = (
            "This module is currently a scaffold/placeholder. "
            "Replace this note with real behavior details once implementation starts."
        )

    classes = ", ".join(summary.class_names) if summary.class_names else "None yet"
    functions = ", ".join(summary.function_names) if summary.function_names else "None yet"
    imports = ", ".join(summary.import_names[:20]) if summary.import_names else "None yet"
    if len(summary.import_names) > 20:
        imports += ", ... (truncated)"

    return (
        f"MODULE: {module_name}\n"
        f"PYTHON_FILE: {rel_path}\n"
        f"LAST_UPDATED_UTC: {last_updated}\n"
        f"STATUS: {'Implemented' if summary.line_count > 0 else 'Empty scaffold'}\n"
        "\n"
        "WHAT_THIS_FILE_DOES:\n"
        f"- {behavior}\n"
        "\n"
        "CURRENT_STRUCTURE:\n"
        f"- Classes: {classes}\n"
        f"- Functions: {functions}\n"
        f"- Imports: {imports}\n"
        f"- Has __main__ guard: {'Yes' if summary.has_main_guard else 'No'}\n"
        f"- Approx lines: {summary.line_count}\n"
        "\n"
        "INPUTS_OUTPUTS:\n"
        "- Inputs: TODO (describe runtime inputs when implemented)\n"
        "- Outputs/Side effects: TODO (describe data writes, external calls, or state changes)\n"
        "\n"
        "DEPENDENCIES:\n"
        "- Internal modules: TODO\n"
        "- External services/APIs: TODO\n"
        "\n"
        "UPDATE_RULE:\n"
        "- Keep this file synchronized with the actual Python behavior after every code change.\n"
    )


def explainer_path_for(py_path: Path) -> Path:
    return py_path.with_name(f"{py_path.stem}{EXPLAIN_SUFFIX}")


def iter_python_files() -> list[Path]:
    files = []
    for path in PROJECT_ROOT.rglob("*.py"):
        if should_skip(path):
            continue
        if path.name.endswith(EXPLAIN_SUFFIX.replace(".txt", ".py")):
            continue
        files.append(path)
    return sorted(files)


def main() -> None:
    py_files = iter_python_files()
    created_or_updated = 0

    for py_path in py_files:
        summary = parse_python_file(py_path)
        explainer = render_explainer(summary)
        explain_path = explainer_path_for(py_path)
        explain_path.write_text(explainer, encoding="utf-8")
        created_or_updated += 1

    print(
        f"Processed {len(py_files)} Python files. "
        f"Created/updated {created_or_updated} explainer files."
    )


if __name__ == "__main__":
    main()
