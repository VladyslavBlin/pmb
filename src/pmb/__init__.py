"""Personal Memory Brain — local memory for AI agents."""

__version__ = "0.1.0"

# Quiet third-party noise that otherwise leaks into CLI output:
# - HF Hub download progress bars
# - sentence-transformers "Loading weights" prints
# - HF symlink warnings on Windows
# - tokenizers parallelism warning
import os as _os
import warnings as _warnings
_os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
_os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
_os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
_os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
# huggingface_hub emits a UserWarning about unauthenticated requests on each
# model download; we don't need a token for the open MiniLM model.
# The actual message starts with "Warning: You are sending unauthenticated..."
_warnings.filterwarnings(
    "ignore",
    message=".*unauthenticated requests.*",
    category=UserWarning,
)
# Silence the underlying logger too — some versions emit via logging, not warnings
import logging as _logging
for _name in ("huggingface_hub", "huggingface_hub.utils._http", "transformers"):
    _logging.getLogger(_name).setLevel(_logging.ERROR)
del _logging, _name
del _os, _warnings

from pmb.core.engine import Engine
from pmb.core.workspace import Workspace, detect_workspace

__all__ = ["Engine", "Workspace", "detect_workspace", "__version__"]
