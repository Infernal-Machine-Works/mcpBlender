"""Install bundled skills into a client root without overwriting existing skills."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil


def install(source: Path, destination: Path, client: str, server_name: str) -> list[Path]:
    if client not in {"codex", "pqc"}:
        raise ValueError("Unknown client")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", server_name):
        raise ValueError("Invalid server name")
    sources = sorted(source.glob("*/SKILL.md"))
    if not sources:
        raise ValueError("No bundled skills found")
    prepared = []
    for skill in sources:
        target = destination / skill.parent.name
        if target.exists() or target.is_symlink():
            raise FileExistsError(target)
        text = skill.read_text()
        if client == "pqc":
            if not text.startswith("---\n") or "\n---\n" not in text[4:]:
                raise ValueError(f"Invalid skill frontmatter: {skill}")
            front, body = text[4:].split("\n---\n", 1)
            description = next((line.partition(":")[2].strip() for line in front.splitlines()
                                if line.startswith("description:")), "")
            if not description:
                raise ValueError(f"Missing skill description: {skill}")
            text = (f"---\n{front}\nwhen_to_use: {description}\n"
                    f"requires_mcp_servers:\n  - {server_name}\n---\n{body}")
        prepared.append((skill.parent, target, text))
    destination.mkdir(parents=True, exist_ok=True)
    installed = []
    for source_dir, target, text in prepared:
        shutil.copytree(source_dir, target)
        (target / "SKILL.md").write_text(text)
        installed.append(target)
    return installed


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True, choices=["codex", "pqc"])
    parser.add_argument("--dest", type=Path, required=True, help="Client skill root")
    parser.add_argument("--server-name", default="mcpblender")
    args = parser.parse_args()
    for path in install(Path(__file__).resolve().parents[1] / "skill",
                        args.dest.expanduser(), args.client, args.server_name):
        print(path)
