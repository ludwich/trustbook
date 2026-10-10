"""Grounded chat: conversations, prompts, forced citations, refusals and the space overview.

Grounding layer 1: nothing in this package may reach search, scraping or fetch code
(enforced by ``tests/test_grounding_boundary.py``). Chat adapters are injected, never built here.
"""
