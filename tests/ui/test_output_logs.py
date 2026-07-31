"""Isolation and fidelity checks for background log capture."""

import logging

from core.infrastructure.logging import save_diagnostic
from ui.harness.output_logs import OutputLogCapture


def test_capture_uses_real_format_and_collects_only_output_logs():
    with OutputLogCapture() as capture:
        captured_root = capture.logs_dir
        logger = capture.logger_for("capture-test")
        logger.info("✅ deterministic message")
        save_diagnostic("technical detail", target_name="capture-test")

        artifacts = capture.artifacts()

    assert artifacts[0].path == "logs/capture-test/output.log"
    assert artifacts[0].content == ("[2026-07-04 12:00:00 UTC] ✅ deterministic message\n")
    assert len(artifacts) == 1
    assert not captured_root.exists()
    assert not any(
        getattr(handler, "baseFilename", "").startswith(str(captured_root))
        for handler in logging.getLogger("scraper.capture-test").handlers
    )
