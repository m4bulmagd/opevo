# Contract fixtures

Each version directory contains one golden JSON fixture for every supported
top-level wire contract. Fixtures are produced by the canonical contract
instances in `contract_cases.py` and consumed by their public parsers.

Additive fields do not require a schema-version bump. Breaking semantics require
a new version. Before any producer emits v2, a complete `v2/` fixture matrix is
required. Roll out changes in this order: consumer support for v1 and v2, then
producer v2, then later removal of v1.
