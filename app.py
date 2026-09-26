from __future__ import annotations

import os
import socket

from src.config import settings
from src.ui import APP_THEME, CSS, build_app

app = build_app()


def available_local_port(preferred: int, attempts: int = 11) -> int:
    """Avoid serving a new build behind an older Gradio process."""
    for port in range(preferred, preferred + attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise OSError(f"No free local port found between {preferred} and {preferred + attempts - 1}.")


if __name__ == "__main__":
    if os.getenv("APP_TEST_MODE") == "1":
        print("AI Sales Desk Assistant 2.5.0 entry point: OK")
    else:
        port = settings.server_port if settings.on_hugging_face else available_local_port(settings.server_port)
        print(f"AI Sales Desk Assistant build 2.5.0 is opening on http://127.0.0.1:{port}")
        app.queue(default_concurrency_limit=2, max_size=16).launch(
            server_name=settings.server_name,
            server_port=port,
            show_error=False,
            inbrowser=not settings.on_hugging_face,
            theme=APP_THEME,
            css=CSS,
        )
