"""Mini Claude Code — a minimal coding agent built from scratch."""

__version__ = "1.0.0"


def _ensure_numpy_compat() -> None:
    """Apply lightweight NumPy alias compatibility for downstream dependencies."""
    try:
        import numpy as np  # type: ignore
    except Exception:
        return

    if not hasattr(np, "bool8"):
        np.bool8 = np.bool_


_ensure_numpy_compat()
