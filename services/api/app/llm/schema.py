"""Provider-safe JSON schemas generated from the Pydantic output models.

Structured output accepts a subset of JSON Schema: no numeric or length constraints, no
`oneOf`, and every object must forbid additional properties. Unsupported keywords are dropped
here; the full Pydantic model is still enforced on the response (gateway step 1).
"""

from typing import Any

# Any is restricted to heterogeneous JSON payloads at validated schema/provider boundaries.
from pydantic import BaseModel

UNSUPPORTED = frozenset(
    {
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "minItems",
        "maxItems",
        "uniqueItems",
        "pattern",
        "title",
        "default",
        "discriminator",
    }
)


def _clean(node: Any) -> Any:
    if isinstance(node, list):
        return [_clean(item) for item in node]
    if not isinstance(node, dict):
        return node
    out: dict[str, Any] = {}
    for key, value in node.items():
        if key in UNSUPPORTED:
            continue
        if key == "properties" and isinstance(value, dict):
            # Property names are data, not keywords: never drop a property called "title".
            out[key] = {name: _clean(schema) for name, schema in value.items()}
            continue
        out["anyOf" if key == "oneOf" else key] = _clean(value)
    if out.get("type") == "object":
        out["additionalProperties"] = False
    return out


def provider_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema: dict[str, Any] = _clean(model.model_json_schema())
    return schema
