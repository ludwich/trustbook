# Third-party licenses

Trustbook is Apache-2.0. This file lists what the app image bundles. It is regenerated each phase
(Python: `importlib.metadata` over `uv sync --no-dev --extra ml`; frontend: `pnpm licenses list --prod`).
License policy: SPEC §2 and docs/DECISIONS.md D-009.

## Bundled models (baked into the image, pinned by commit)

| Capability | Model | Revision | License | Source |
|---|---|---|---|---|
| Embeddings | google/embeddinggemma-2 | 914f7f89142e33e77833254d9c9b90c3cef7303b | Apache-2.0 | https://huggingface.co/google/embeddinggemma-2 |
| Reranker | mixedbread-ai/mxbai-rerank-xsmall-v1 | b5c6e9da73abc3711f593f705371cdbe9e0fe422 | Apache-2.0 | https://huggingface.co/mixedbread-ai/mxbai-rerank-xsmall-v1 |
| Grounding checker | vectara/hallucination_evaluation_model (HHEM-2.1-Open) | de9a5e6ba9ba49531a022dba17e35ffeb2a6fad8 | Apache-2.0 | https://huggingface.co/vectara/hallucination_evaluation_model |
| Grounding tokenizer | google/flan-t5-base (tokenizer + config only) | 7bcac572ce56db69c1ea7c8af255c5d7c9672fc2 | Apache-2.0 | https://huggingface.co/google/flan-t5-base |
| Speech-to-text | Cactus-Compute/whistle | ca5287601bef25af26dcf2e1b2bdc0843a7c19e5 | Apache-2.0 | https://huggingface.co/Cactus-Compute/whistle |
| Speech-to-text engine | Cactus-Compute/needle3 (`libneedle3.so` from cactus_needle-3.3.0 wheel) | 8354babbfb92d719ee559f2999d133f11fb9374c | Apache-2.0 | https://huggingface.co/Cactus-Compute/needle3 |

## Separate container (not bundled)

| Component | License | Notes |
|---|---|---|
| SearXNG (`docker.io/searxng/searxng`, pinned by digest in `compose.yaml`) | AGPL-3.0 | Unmodified, separate container; Trustbook only mounts a settings file (SPEC §1 #8). |

## Python packages (app image)

| Package | Version | License | Note |
|---|---|---|---|
| annotated-doc | 0.0.5 | MIT |  |
| annotated-types | 0.8.0 | MIT |  |
| anyio | 4.15.1 | MIT |  |
| cactus-needle | 3.2.0 | Apache-2.0 |  |
| certifi | 2026.7.22 | Mozilla Public License 2.0 (MPL 2.0) | MPL-2.0, unmodified (D-009) |
| click | 8.5.0 | BSD-3-Clause |  |
| cloudpickle | 3.1.2 | BSD License |  |
| fastapi | 0.143.0 | MIT |  |
| filelock | 4.1.0 | MIT |  |
| fsspec | 2026.9.0 | BSD-3-Clause |  |
| h11 | 0.16.0 | MIT License |  |
| hf-xet | 1.7.0 | Apache-2.0 |  |
| httpcore | 1.0.9 | BSD-3-Clause |  |
| httptools | 0.9.0 | MIT |  |
| httpx | 0.28.1 | BSD License |  |
| huggingface_hub | 1.33.0 | Apache Software License |  |
| idna | 3.20 | BSD-3-Clause |  |
| jinja2 | 3.1.6 | BSD License |  |
| joblib | 1.6.0 | BSD-3-Clause |  |
| markdown-it-py | 4.2.0 | MIT License |  |
| markupsafe | 3.0.4 | BSD-3-Clause |  |
| mdurl | 0.1.2 | MIT License |  |
| mpmath | 1.3.0 | BSD License |  |
| narwhals | 2.27.1 | MIT |  |
| networkx | 3.7 | BSD-3-Clause |  |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |  |
| opentelemetry-api | 1.45.1 | Apache-2.0 | no-op API only; no SDK/exporter installed (D-014) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |  |
| pillow | 12.3.0 | MIT-CMU | HPND/MIT-CMU (D-009) |
| pydantic | 2.14.0 | MIT |  |
| pydantic_core | 2.50.0 | MIT |  |
| pygments | 2.21.0 | BSD-2-Clause |  |
| python-dotenv | 1.2.4 | BSD-3-Clause |  |
| pyyaml | 6.0.3 | MIT License |  |
| regex | 2026.9.29 | Apache-2.0 AND CNRI-Python | includes CNRI-Python (D-009) |
| rich | 15.0.0 | MIT License |  |
| safetensors | 0.8.0 | Apache Software License |  |
| scikit-learn | 1.9.1 | BSD-3-Clause |  |
| scipy | 1.18.1 | BSD License |  |
| sentence-transformers | 6.1.0 | Apache-2.0 |  |
| setuptools | 84.0.0 | MIT |  |
| shellingham | 1.5.4 | ISC License (ISCL) | ISC (D-009) |
| sqlalchemy | 2.1.4 | MIT |  |
| sqlmodel | 0.0.48 | MIT |  |
| starlette | 1.7.0 | BSD-3-Clause |  |
| sympy | 1.14.0 | BSD License |  |
| threadpoolctl | 3.7.0 | BSD-3-Clause |  |
| tokenizers | 0.23.3 | Apache Software License |  |
| torch | 2.14.1+cpu | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT |  |
| torchvision | 0.29.1+cpu | BSD |  |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | MPL-2.0 AND MIT, unmodified (D-009) |
| transformers | 5.19.0 | Apache 2.0 License |  |
| typer | 0.27.3 | MIT |  |
| typing-inspection | 0.4.4 | MIT |  |
| typing_extensions | 4.16.0 | PSF-2.0 | PSF-2.0 (D-009) |
| uvicorn | 0.54.0 | BSD-3-Clause |  |
| uvloop | 0.23.0 | Apache Software License; MIT License |  |
| watchfiles | 1.3.0 | MIT License |  |
| websockets | 17.2 | BSD-3-Clause |  |

## Frontend packages (bundled into the static UI)

| Package | Version | License |
|---|---|---|
| @radix-ui/primitive | 1.1.7 | MIT |
| @radix-ui/react-alert-dialog | 1.1.24 | MIT |
| @radix-ui/react-collection | 1.1.16 | MIT |
| @radix-ui/react-compose-refs | 1.1.5 | MIT |
| @radix-ui/react-context | 1.2.2 | MIT |
| @radix-ui/react-dialog | 1.2.0 | MIT |
| @radix-ui/react-direction | 1.1.5 | MIT |
| @radix-ui/react-dismissable-layer | 1.1.20 | MIT |
| @radix-ui/react-focus-guards | 1.1.6 | MIT |
| @radix-ui/react-focus-scope | 1.2.0 | MIT |
| @radix-ui/react-id | 1.1.4 | MIT |
| @radix-ui/react-portal | 1.1.18 | MIT |
| @radix-ui/react-presence | 1.1.11 | MIT |
| @radix-ui/react-primitive | 2.1.11 | MIT |
| @radix-ui/react-roving-focus | 1.1.20 | MIT |
| @radix-ui/react-slot | 1.4.0 | MIT |
| @radix-ui/react-switch | 1.3.8 | MIT |
| @radix-ui/react-tabs | 1.1.22 | MIT |
| @radix-ui/react-use-callback-ref | 1.1.4 | MIT |
| @radix-ui/react-use-controllable-state | 1.2.6 | MIT |
| @radix-ui/react-use-effect-event | 0.0.5 | MIT |
| @radix-ui/react-use-is-hydrated | 0.1.3 | MIT |
| @radix-ui/react-use-layout-effect | 1.1.4 | MIT |
| @radix-ui/react-use-size | 1.1.5 | MIT |
| @remix-run/route-pattern | 0.22.1 | MIT |
| @tabler/icons | 3.49.0 | MIT |
| @tabler/icons-react | 3.49.0 | MIT |
| @tanstack/query-core | 5.104.1 | MIT |
| @tanstack/react-query | 5.104.1 | MIT |
| @types/react | 19.3.0 | MIT |
| @types/react-dom | 19.3.0 | MIT |
| aria-hidden | 1.2.6 | MIT |
| class-variance-authority | 0.7.1 | Apache-2.0 |
| clsx | 2.1.1 | MIT |
| cookie-es | 3.1.1 | MIT |
| csstype | 3.2.3 | MIT |
| detect-node-es | 1.1.0 | MIT |
| get-nonce | 1.0.1 | MIT |
| react | 19.3.0 | MIT |
| react-dom | 19.3.0 | MIT |
| react-remove-scroll | 2.7.2 | MIT |
| react-remove-scroll-bar | 2.3.8 | MIT |
| react-router | 8.4.0 | MIT |
| react-style-singleton | 2.2.3 | MIT |
| scheduler | 0.28.0 | MIT |
| tailwind-merge | 3.7.0 | MIT |
| tslib | 2.8.1 | 0BSD (D-009) |
| tw-animate-css | 1.4.0 | MIT |
| use-callback-ref | 1.3.3 | MIT |
| use-sidecar | 1.1.3 | MIT |

## Tools in the image

| Tool | License | Notes |
|---|---|---|
| Python 3.12 (official `python:3.12-slim` image, Debian packages) | PSF-2.0 / various Debian licenses | Base image |
| uv | MIT OR Apache-2.0 | Package installer |
