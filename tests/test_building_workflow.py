import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.mark.blender
@pytest.mark.skipif(os.environ.get("MCPBLENDER_TEST_BLENDER") != "1" or not shutil.which("blender") or not shutil.which("ffprobe"),
                    reason="Requires opt-in installed Blender and ffprobe")
def test_real_textured_building_animation_vse_and_asset_api():
    if os.environ.get("MCPBLENDER_EVIDENCE_DIR"):
        root = Path(os.environ["MCPBLENDER_EVIDENCE_DIR"])
        result = subprocess.run([sys.executable, "tools/building_workflow.py", str(root)], capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, result.stdout + result.stderr
        assert (root / "library/walkthrough.mp4").stat().st_size > 1000
        return
    with tempfile.TemporaryDirectory(prefix="mb-building-test-") as directory:
        root = Path(directory) / "case"
        result = subprocess.run([sys.executable, "tools/building_workflow.py", str(root)], capture_output=True, text=True, timeout=300)
        assert result.returncode == 0, result.stdout + result.stderr
        assert (root / "library/building.blend").is_file()
        assert (root / "library/building.glb").read_bytes()[:4] == b"glTF"
        assert (root / "library/walkthrough.mp4").stat().st_size > 1000
