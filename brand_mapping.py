"""Brand display/query-name mapping shared by report data scripts."""

from __future__ import annotations

from typing import Final


DATA_QUERY_VERSION: Final = "hisense_mcp_brand_v4_private_like_unavailable"
MCP_BRAND_MAPPING: Final[dict[str, str]] = {
    "海信": "海信本系",
    "美的": "美的",
    "海尔": "海尔",
    "TCL": "TCL",
}


def get_mcp_query_brand(display_brand: str) -> str:
    """Return the brand name that should be sent to MCP for a display brand."""
    brand = str(display_brand or "").strip()
    return MCP_BRAND_MAPPING.get(brand, brand)


def get_mcp_query_brand_for_config(display_brand: str, config: dict | None = None) -> str:
    """Return the MCP query brand, preferring an explicit query_config mapping."""
    brand = str(display_brand or "").strip()
    mapping = (config or {}).get("mcp_brand_mapping")
    if isinstance(mapping, dict):
        mapped = mapping.get(brand)
        if isinstance(mapped, str) and mapped.strip():
            return mapped.strip()
    if brand == str((config or {}).get("brand", "")).strip():
        mcp_brand = (config or {}).get("mcp_brand") or (config or {}).get("brand_query_name")
        if isinstance(mcp_brand, str) and mcp_brand.strip():
            return mcp_brand.strip()
    return get_mcp_query_brand(brand)


def mcp_brand_mapping_for(brands: list[str] | tuple[str, ...]) -> dict[str, str]:
    """Build a mapping for the configured display brands."""
    result: dict[str, str] = {}
    for brand in brands:
        display = str(brand or "").strip()
        if display:
            result[display] = get_mcp_query_brand(display)
    return result
