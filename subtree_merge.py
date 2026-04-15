"""
Traverse a nested dict/list structure from YAML and overwrite the leaf key
with the generated JSON value. Missing intermediate dict keys (str path segments)
are created as empty dicts so tasks like filters.tags can merge cleanly.

Uses ruamel.yaml in round-trip mode so that comments, blank lines, and the
YAML document-start marker (---) are preserved after the merge.

Comment-preservation rules
───────────────────────────
• Comments on keys/values OUTSIDE the replaced subtree: always preserved.
• Scalar replacement (str/int/bool): surrounding comments fully preserved.
• List replacement: items that already exist (matched by `name` or `id`) are
  updated in-place, preserving their per-item comments.  Genuinely new items
  (no match) are appended without comments.
• Falls back to plain PyYAML if ruamel.yaml is not installed (comments lost).
"""

from __future__ import annotations

from copy import deepcopy
from io import StringIO
from typing import Any


# ──────────────────────────────────────────────────────────────────────────────
# Smart list merge — preserves comments on existing items
# ──────────────────────────────────────────────────────────────────────────────

def _item_key(item: Any) -> Any:
    """Return a hashable identity key for a list item, or None if unknowable."""
    if isinstance(item, dict):
        return item.get("name") or item.get("id")
    return item  # scalars are their own key


def _smart_update_field(parent: Any, key: Any, new_val: Any) -> None:
    """
    Set parent[key] = new_val while preserving ruamel.yaml comment metadata.

    • If current value is a CommentedSeq and new_val is a list  → smart list merge.
    • If current value is a CommentedMap and new_val is a dict  → recursive key-by-key
      update so the CommentedMap (and all its per-key comment attributes) survives.
    • Otherwise → plain assignment (scalar values, type mismatches, etc.).

    All three branches recurse, so comments are preserved at every nesting level.
    """
    try:
        from ruamel.yaml.comments import CommentedSeq, CommentedMap  # type: ignore
        current = parent.get(key) if hasattr(parent, "get") else None

        # List → smart merge to keep per-item comments
        if isinstance(current, CommentedSeq) and isinstance(new_val, list):
            parent[key] = _smart_merge_list(current, new_val)
            return

        # Dict → update key-by-key so the CommentedMap (with its per-key comment
        # attributes) is not discarded; recurse for any nested structures
        if isinstance(current, CommentedMap) and isinstance(new_val, dict):
            for k, v in new_val.items():
                _smart_update_field(current, k, v)
            # Remove keys the new value intentionally dropped
            for k in list(current.keys()):
                if k not in new_val:
                    del current[k]
            return  # current was updated in-place; parent[key] already points to it

    except (ImportError, AttributeError, KeyError):
        pass

    parent[key] = new_val  # scalar, type mismatch, or ruamel not available


def _smart_merge_list(original_seq: Any, new_list: list) -> Any:
    """
    Merge new_list into original_seq, preserving ruamel.yaml comment metadata
    on items that are present in both — recursively down all nesting levels.

    Strategy
    ────────
    1. Build a lookup: identity-key → original ruamel.yaml item (CommentedMap).
    2. For each item in new_list:
       a. If it matches an original item (by name/id), update the original item
          field-by-field via _smart_update_field so nested lists also go through
          this same smart-merge path (recursive comment preservation).
       b. Otherwise append it as a new (comment-free) item.
    3. Preserve any sequence-level comment attributes from the original seq.
    """
    try:
        from ruamel.yaml.comments import CommentedSeq, CommentedMap  # type: ignore
    except ImportError:
        return new_list  # ruamel not available — plain replacement

    # Build key → original-item lookup
    lookup: dict[Any, Any] = {}
    for orig_item in original_seq:
        k = _item_key(orig_item)
        if k is not None:
            lookup[k] = orig_item

    result = CommentedSeq()

    for new_item in new_list:
        k = _item_key(new_item)
        orig = lookup.get(k) if k is not None else None

        if orig is not None and isinstance(orig, CommentedMap) and isinstance(new_item, dict):
            # Update existing item in-place, recursing into nested lists
            for field, val in new_item.items():
                _smart_update_field(orig, field, val)
            # Remove fields the new value intentionally dropped
            for field in list(orig.keys()):
                if field not in new_item:
                    del orig[field]
            result.append(orig)
        else:
            result.append(new_item)

    # Copy sequence-level comment attributes (comments above first item, etc.)
    if hasattr(original_seq, "ca") and hasattr(result, "ca"):
        try:
            result.ca.items.update(original_seq.ca.items)
        except Exception:
            pass

    return result


# ──────────────────────────────────────────────────────────────────────────────
# insert_subtree
# ──────────────────────────────────────────────────────────────────────────────

def insert_subtree(original_dict: Any, insertion_path: list, generated_json: Any) -> Any:
    """
    Traverse original_dict using insertion_path and overwrite the final key
    with generated_json. Returns the same object (mutated in-place).

    When the current value at the target path is a ruamel.yaml CommentedSeq
    and generated_json is a list, a smart merge is performed to preserve
    comments on existing items.
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
        # Smart list merge: if replacing a list, preserve comments on existing items
        _smart_update_field(target, final_key, generated_json)

    return original_dict


# ──────────────────────────────────────────────────────────────────────────────
# merge_yaml_file
# ──────────────────────────────────────────────────────────────────────────────

def merge_yaml_file(
    repo_root: Any,
    context_file: str,
    insertion_path: list,
    generated_json: Any,
) -> str:
    """
    Load YAML from repo_root/context_file, merge generated_json at
    insertion_path, and return the serialised YAML string.

    Tries ruamel.yaml first (preserves comments + `---` marker).
    Falls back to PyYAML if ruamel.yaml is unavailable.
    """
    from pathlib import Path

    path = Path(repo_root) / context_file

    # ── ruamel.yaml round-trip (comment-preserving) ───────────────────────────
    try:
        from ruamel.yaml import YAML  # type: ignore

        # Read raw text once so we can detect the document-start marker
        raw = path.read_text(encoding="utf-8")
        had_doc_start = raw.lstrip().startswith("---")

        ryaml = YAML()
        ryaml.preserve_quotes = True
        ryaml.width = 1000
        ryaml.best_sequence_indent = 2
        ryaml.best_map_flow_style  = False

        doc = ryaml.load(raw)

        if not isinstance(doc, dict):
            raise ValueError(f"Expected dict at root of {context_file}")

        insert_subtree(doc, insertion_path, generated_json)

        buf = StringIO()
        ryaml.dump(doc, buf)
        result = buf.getvalue()

        # Restore the document-start marker if the original had one
        if had_doc_start and not result.lstrip().startswith("---"):
            result = "---\n" + result

        return result

    except ImportError:
        pass  # fall through to PyYAML

    # ── PyYAML fallback (comments will be lost) ───────────────────────────────
    import yaml  # type: ignore

    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

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
