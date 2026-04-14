"""
Run reports: mirror terminal logging to a UTF-8 text file with flush-after-each-record.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


class FlushFileHandler(logging.FileHandler):
    """FileHandler that flushes the stream after every emit (continuous write)."""

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


def configure_run_logging(
    *,
    repo_root: Path,
    report_dir: Path | None,
    console_level: int,
    log_format: str,
    report_basename: str,
) -> Path:
    """
    Attach console (stderr) + one flush-on-each-line report file.

    * ``report_dir`` ``None`` → directory ``<repo>/experiments/auto_<UTC>/``.
    * Relative ``report_dir`` → ``<repo>/experiments/<report_dir>/``.
    * Absolute ``report_dir`` → used as-is.

    Clears existing root handlers. Returns the path to the report ``.txt`` file.
    """
    repo_root = repo_root.resolve()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if report_dir is None:
        out_dir = (repo_root / "experiments" / f"auto_{ts}").resolve()
    else:
        rd = report_dir.expanduser()
        if rd.is_absolute():
            out_dir = rd.resolve()
        else:
            out_dir = (repo_root / "experiments" / rd).resolve()

    out_dir.mkdir(parents=True, exist_ok=True)
    report_path = out_dir / f"{report_basename}_{ts}.txt"

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)

    fmt = logging.Formatter(log_format)

    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(console_level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    fh = FlushFileHandler(report_path, mode="w", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)

    return report_path
