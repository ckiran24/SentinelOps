"""Render Cloud Run manifests without printing secret values or applying them."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("work/gcp-rendered"))
    args = parser.parse_args()
    source = Path(__file__).resolve().parent
    args.output.mkdir(parents=True, exist_ok=True)
    for template in sorted(source.glob("*.yaml")):
        contents = template.read_text()
        variables = set(re.findall(r"\$\{([A-Z_]+)\}", contents))
        missing = sorted(name for name in variables if not os.environ.get(name))
        if missing:
            raise SystemExit(f"{template.name}: missing variables: {', '.join(missing)}")
        for name in variables:
            value = os.environ[name]
            if "\n" in value or "\r" in value or "${" in value:
                raise SystemExit(f"{name}: multiline or nested substitutions are not supported")
            contents = contents.replace("${" + name + "}", value)
        (args.output / template.name).write_text(contents)
        print(f"Rendered {template.name}")


if __name__ == "__main__":
    main()
