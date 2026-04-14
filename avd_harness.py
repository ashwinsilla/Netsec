#!/usr/bin/env python3
"""
Legacy entry point — superseded by the split pipeline:

  generate_configs.py  — OpenRouter generation, JSON syntax check, sub-tree merge, artifacts
  validate_configs.py  — ansible-playbook build.yml semantic check per artifact

Task definitions and quadrants live in avd_tasks.py.
See env.txt for OPENROUTER_API_KEY template.
"""

import sys


def main() -> None:
    print(
        "avd_harness.py is deprecated.\n"
        "  python3 generate_configs.py\n"
        "  python3 validate_configs.py\n",
        file=sys.stderr,
    )
    sys.exit(2)


if __name__ == "__main__":
    main()
