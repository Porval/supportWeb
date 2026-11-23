"""Compatibility import for the business Agent-owned catalogue module."""
from business_agent.data_agent_catalog.catalog import *
from business_agent.data_agent_catalog.catalog import (
    _read_json, _replace_cache, _status, _normalize, _lookup,
    _source_key, _text_cell_refs, _catalog_identity,
)

if __name__ == '__main__':
    main()
