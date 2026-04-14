"""
Traverse a nested dict/list structure from YAML and overwrite the leaf key
with the generated JSON value. Missing intermediate dict keys (str path segments)
are created as empty dicts so tasks like filters.tags can merge cleanly.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def insert_subtree(original_dict: dict, insertion_path: list, generated_json: Any) -> dict:
    """
    Traverse original_dict using insertion_path and overwrite the final key
    with generated_json. Returns the same dict (mutated).
    """
    if not insertion_path:
        raise ValueError("insertion_path must not be empty")

    target: Any = original_dict
    for i, key in enumerate(insertion_path[:-1]):
        nxt = insertion_path[i + 1]
        if isinstance(key, int):
            target = target[key]
        else:
            if key not in target or target[key] is None:
                target[key] = [] if isinstance(nxt, int) else {}
            target = target[key]

    final_key = insertion_path[-1]
    if isinstance(target, list):
        if not isinstance(final_key, int):
            raise TypeError(f"List parent requires int final key, got {final_key!r}")
        target[final_key] = generated_json
    else:
        target[final_key] = generated_json
    return original_dict


def merge_yaml_file(repo_root, context_file: str, insertion_path: list, generated_json: Any) -> str:
    """Load YAML from repo_root/context_file, deep-copy, merge, return dumped YAML string."""
    import yaml
    from pathlib import Path

    path = Path(repo_root) / context_file
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected dict at root of {context_file}")

    doc = deepcopy(data)
    insert_subtree(doc, insertion_path, generated_json)
    return yaml.dump(
        doc,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=1000,
    )
