"""Ideation (SPEC §10): the grilling engine, the growing branch map and idea briefs.

Grounding layer 1 holds here too: nothing in this package may reach search, scraping, fetch,
ingestion or the index writers (``tests/test_ideation_isolation.py``). Briefs are written to
``ideas/<session>/brief.md`` and are never a source. Chat adapters, retrieval and the verifier are
injected, never built here.
"""
