"""Standard library of pre-built compensation functions (undos) for StateGuard.

These functions return Callables that can be passed directly to the
`@saga.step(compensate=...)` decorator.
"""

import os
import logging
from typing import Callable, Any, Dict, Optional
import urllib.request
import json

logger = logging.getLogger("stateguard.stdlib.compensations")


def delete_file(filepath: str, ignore_missing: bool = True) -> Callable[..., Any]:
    """Returns a compensation function that deletes the specified file."""

    def compensate(*args: Any, **kwargs: Any) -> None:
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.info(f"[STATEGUARD UNDO] Deleted file: {filepath}")
            elif not ignore_missing:
                logger.warning(f"[STATEGUARD UNDO] File not found to delete: {filepath}")
        except Exception as e:
            logger.error(f"[STATEGUARD UNDO] Failed to delete file {filepath}: {e}")

    return compensate


def webhook_rollback(
    url: str, payload: Dict[str, Any], method: str = "POST", timeout: int = 5
) -> Callable[..., Any]:
    """Returns a compensation function that fires a webhook to an external system.
    
    Useful for undoing actions in systems like Make.com, n8n, or external APIs.
    """

    def compensate(*args: Any, **kwargs: Any) -> None:
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, method=method.upper(), headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                if response.getcode() and response.getcode() >= 400:
                    logger.error(
                        f"[STATEGUARD UNDO] Webhook rollback to {url} failed with status {response.getcode()}"
                    )
                else:
                    logger.info(f"[STATEGUARD UNDO] Webhook rollback fired to {url}")
        except Exception as e:
            logger.error(f"[STATEGUARD UNDO] Webhook rollback to {url} failed: {e}")

    return compensate


def log_warning(message: str) -> Callable[..., Any]:
    """Returns a compensation function that loudly logs a warning.
    
    Useful when a manual intervention is required for a rollback.
    """

    def compensate(*args: Any, **kwargs: Any) -> None:
        logger.warning(f"[STATEGUARD UNDO MANUAL INTERVENTION] {message}")

    return compensate
