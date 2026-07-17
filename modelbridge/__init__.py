"""ModelBridge — a China-first AI Agent compatibility gateway / CLI.

CLI entry points: ``mbridge`` (preferred) and ``modelbridge`` (alias).
"""

import warnings

# Pydantic v2 warns when a field name shadows a (deprecated) BaseModel
# method like ``json``. Our public YAML schema uses ``json`` as a
# capability flag — the shadowing is benign and the field continues to
# round-trip correctly. Silence the warning so users don't see it.
warnings.filterwarnings(
    "ignore",
    message=r'Field name "json" .*shadows an attribute in parent "BaseModel"',
)

__version__ = "1.2.3"
__all__ = ["__version__"]
