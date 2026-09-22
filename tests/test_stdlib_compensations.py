"""Tests for the StateGuard stdlib compensations."""

import os
import json
import pytest
from unittest.mock import patch
from stateguard.stdlib import compensations


def test_delete_file_compensation(tmp_path):
    # Create a dummy file
    test_file = tmp_path / "dummy.txt"
    test_file.write_text("hello")
    assert test_file.exists()

    # Get compensation fn
    comp_fn = compensations.delete_file(str(test_file))
    
    # Execute compensation
    comp_fn()

    # File should be deleted
    assert not test_file.exists()

    # Executing again with ignore_missing=True should not crash
    comp_fn()


@patch("urllib.request.urlopen")
def test_webhook_rollback_compensation(mock_urlopen):
    # Model a real 200 response (a bare MagicMock only "passed" before because its
    # TypeError was swallowed by the compensation's own catch-all).
    mock_urlopen.return_value.__enter__.return_value.getcode.return_value = 200

    # Get compensation fn
    comp_fn = compensations.webhook_rollback("http://example.com/webhook", {"status": "cancel"})
    
    # Execute compensation
    comp_fn()

    # Verify urlopen was called correctly
    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "http://example.com/webhook"
    assert req.method == "POST"
    assert json.loads(req.data.decode("utf-8")) == {"status": "cancel"}


@patch("stateguard.stdlib.compensations.logger")
def test_log_warning_compensation(mock_logger):
    comp_fn = compensations.log_warning("Manual intervention needed!")
    comp_fn()

    mock_logger.warning.assert_called_once()
    assert "Manual intervention needed!" in mock_logger.warning.call_args[0][0]
