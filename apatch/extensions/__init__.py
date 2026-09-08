"""Open local APatch Extension Host contracts.

The host is intentionally independent from account, entitlement, and managed
catalog services. Extension processes never receive APatch session capabilities.
"""

from apatch.extensions.catalog import (
    DEFAULT_WORKSPACE_LOCK,
    ExtensionCatalog,
    load_explicit_lock,
)
from apatch.extensions.authoring import (
    pin_local_extension,
    scaffold_local_extension,
)
from apatch.extensions.schema import (
    EXTENSION_PROTOCOL,
    ExtensionContractError,
    package_digest,
    validate_json_value,
    validate_lock,
    validate_manifest,
    verify_lock_entry,
    verify_manifest_file,
)

__all__ = [
    "DEFAULT_WORKSPACE_LOCK",
    "ExtensionCatalog",
    "pin_local_extension",
    "scaffold_local_extension",
    "load_explicit_lock",
    "EXTENSION_PROTOCOL",
    "ExtensionContractError",
    "package_digest",
    "validate_json_value",
    "validate_lock",
    "validate_manifest",
    "verify_lock_entry",
    "verify_manifest_file",
]
