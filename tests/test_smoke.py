import os
import subprocess
import sys
from pathlib import Path

import gradio as gr

from app import available_local_port
from src.ui import build_app

ROOT = Path(__file__).resolve().parents[1]


def test_gradio_interface_builds():
    app = build_app()
    assert isinstance(app, gr.Blocks)
    config = app.get_config_file()
    labels = str(config)
    assert "Overview" in labels
    assert "Research Agent" in labels
    assert "About" in labels
    assert "Documents / RAG" not in labels
    assert "Refresh" in labels
    assert "AI Desk Report" in labels
    assert "Demo (historical)" not in labels


def test_python_entry_point():
    env = os.environ.copy()
    env["APP_TEST_MODE"] = "1"
    result = subprocess.run(
        [sys.executable, "app.py"], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=40, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "entry point: OK" in result.stdout


def test_local_port_falls_forward_when_preferred_is_busy():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as occupied:
        occupied.bind(("127.0.0.1", 0))
        preferred = occupied.getsockname()[1]
        assert available_local_port(preferred, attempts=2) == preferred + 1
