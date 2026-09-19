"""Guards against bugs that only appear on the Python version the containers run (3.12).

The local venv is newer and evaluates annotations lazily, so a class that defines a method named
`list` (shadowing the builtin) works locally but crashes the service at import time in Docker
when a later method annotates `-> list[...]`. This happened twice.
"""
import ast
import pathlib

BUILTIN_TYPES = {"list", "dict", "set", "tuple", "frozenset", "type", "int", "float", "str", "bool"}
ROOTS = ["app", "gateway", "fault-agent/agent"]


def test_no_class_defines_a_method_named_like_a_builtin_type():
    repo = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for root in ROOTS:
        for path in (repo / root).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
                for member in cls.body:
                    names = []
                    if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        names = [member.name]
                    elif isinstance(member, ast.Assign):
                        names = [t.id for t in member.targets if isinstance(t, ast.Name)]
                    offenders += [f"{path.relative_to(repo)}: {cls.name}.{n}" for n in names if n in BUILTIN_TYPES]
    assert not offenders, f"class attributes shadowing builtins break `list[...]` annotations on 3.12: {offenders}"
