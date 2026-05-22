from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "docs" / "reference"


def _get_source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _find_class(module: ast.Module, class_name: str) -> ast.ClassDef:
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    raise ValueError(f"Class not found: {class_name}")


def _doc_of(node: ast.AST) -> str:
    return ast.get_docstring(node) or "No description."


def _sig_of(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = []
    pos = fn.args.args
    defaults = [None] * (len(pos) - len(fn.args.defaults)) + list(fn.args.defaults)
    for a, d in zip(pos, defaults):
        name = a.arg
        if d is not None:
            try:
                default_src = ast.unparse(d)
            except Exception:
                default_src = "..."
            args.append(f"{name}={default_src}")
        else:
            args.append(name)
    if fn.args.vararg:
        args.append(f"*{fn.args.vararg.arg}")
    if fn.args.kwarg:
        args.append(f"**{fn.args.kwarg.arg}")
    return f"({', '.join(args)})"


def _method_block(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    return f"### `{fn.name}{_sig_of(fn)}`\n\n{_doc_of(fn)}\n"


def _class_page(title: str, class_node: ast.ClassDef, methods: list[str]) -> str:
    method_nodes = {
        n.name: n
        for n in class_node.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith("_")
    }
    lines = [f"# {title}", "", _doc_of(class_node), "", "## Methods", ""]
    for name in methods:
        fn = method_nodes.get(name)
        if fn is None:
            lines.append(f"### `{name}(...)`\n\nMethod not found in source.\n")
            continue
        lines.append(_method_block(fn))
    return "\n".join(lines).strip() + "\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    client_mod = ast.parse(_get_source(ROOT / "shrecknet_client" / "client.py"))
    init_mod = ast.parse(_get_source(ROOT / "shrecknet_client" / "__init__.py"))
    resources_mod = ast.parse(_get_source(ROOT / "shrecknet_client" / "resources.py"))

    pages = {
        "async-client.md": _class_page(
            "AsyncShrecknetClient",
            _find_class(client_mod, "AsyncShrecknetClient"),
            ["set_token", "clear_token", "raw_request", "bootstrap_status", "register_user", "login", "me"],
        ),
        "shrecknet.md": _class_page(
            "Shrecknet",
            _find_class(init_mod, "Shrecknet"),
            ["login", "me", "raw_request", "set_token", "clear_token"],
        ),
        "worlds.md": _class_page(
            "WorldsAPI",
            _find_class(resources_mod, "WorldsAPI"),
            ["list", "get"],
        ),
        "ontologies.md": _class_page(
            "OntologiesAPI",
            _find_class(resources_mod, "OntologiesAPI"),
            ["create", "list", "get", "update", "delete", "world_stats"],
        ),
        "ontology-instances.md": _class_page(
            "OntologyInstancesAPI",
            _find_class(resources_mod, "OntologyInstancesAPI"),
            ["create", "list", "get", "update", "delete", "count", "search", "basic", "resolve_entities", "scene_counts"],
        ),
    }

    for name, content in pages.items():
        (OUT_DIR / name).write_text(content, encoding="utf-8")

    (OUT_DIR / "index.md").write_text(
        """# API Reference (Generated)\n\nGenerated from SDK source docstrings and method signatures.\n\n- [AsyncShrecknetClient](./async-client.md)\n- [Shrecknet](./shrecknet.md)\n- [WorldsAPI](./worlds.md)\n- [OntologiesAPI](./ontologies.md)\n- [OntologyInstancesAPI](./ontology-instances.md)\n""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
