# Third-party licenses

Trustbook is Apache-2.0. This file lists what the app image bundles. It is regenerated each phase
(Python: `importlib.metadata` over `uv sync --no-dev --extra ml`; frontend: `pnpm licenses list --prod`).
License policy: SPEC §2 and docs/DECISIONS.md D-009, D-027.

## Bundled models and data (baked into the image, pinned)

| Use | Model / data | Revision or checksum | License | Source |
|---|---|---|---|---|
| Embeddings | google/embeddinggemma-2 | 914f7f89142e33e77833254d9c9b90c3cef7303b | Apache-2.0 | https://huggingface.co/google/embeddinggemma-2 |
| Reranker | mixedbread-ai/mxbai-rerank-xsmall-v1 | b5c6e9da73abc3711f593f705371cdbe9e0fe422 | Apache-2.0 | https://huggingface.co/mixedbread-ai/mxbai-rerank-xsmall-v1 |
| Grounding checker | vectara/hallucination_evaluation_model (HHEM-2.1-Open) | de9a5e6ba9ba49531a022dba17e35ffeb2a6fad8 | Apache-2.0 | https://huggingface.co/vectara/hallucination_evaluation_model |
| Grounding tokenizer | google/flan-t5-base (tokenizer + config only) | 7bcac572ce56db69c1ea7c8af255c5d7c9672fc2 | Apache-2.0 | https://huggingface.co/google/flan-t5-base |
| Speech-to-text | Cactus-Compute/whistle | ca5287601bef25af26dcf2e1b2bdc0843a7c19e5 | Apache-2.0 | https://huggingface.co/Cactus-Compute/whistle |
| Speech-to-text engine | Cactus-Compute/needle3 (`libneedle3.so` from cactus_needle-3.3.0 wheel) | 8354babbfb92d719ee559f2999d133f11fb9374c | Apache-2.0 | https://huggingface.co/Cactus-Compute/needle3 |
| Document layout | docling-project/docling-layout-heron | 8f39ad3c0b4c58e9c2d2c84a38465abf757272d8 | Apache-2.0 | https://huggingface.co/docling-project/docling-layout-heron |
| Table structure | docling-project/docling-models (TableFormer, accurate) | 2199320848bb9a8a519d22e4b528185a4f9a6f64 (dual-licensing commit) | Apache-2.0 | https://huggingface.co/docling-project/docling-models |
| OCR | RapidOCR PP-OCRv6 small (torch, en) | SHA-256 pinned in docker/download_models.py | Apache-2.0 | https://www.modelscope.cn/models/RapidAI/RapidOCR |
| Speed-test speech sample | JFK inaugural address excerpt (`samples/jfk.wav` from whisper.cpp) | SHA-256 59dfb9a4… | Public domain (US government work) | https://github.com/ggml-org/whisper.cpp |

## Separate container (not bundled)

| Component | License | Notes |
|---|---|---|
| SearXNG (`docker.io/searxng/searxng`, pinned by digest in `compose.yaml`) | AGPL-3.0 | Unmodified, separate container; Trustbook only mounts a settings file (SPEC §1 #8). |

## Tools in the image

| Tool | License | Notes |
|---|---|---|
| Python 3.12 (official `python:3.12-slim-trixie` image, Debian packages) | PSF-2.0 / various Debian licenses | Base image; Chromium's shared libraries come from Debian |
| uv 0.13.0 | MIT OR Apache-2.0 | Package installer |
| gitleaks 8.30.1 | MIT | Secret scan of linked folders; release tarball pinned by SHA-256 |
| Chromium headless shell (via Playwright 1.63) | BSD-3-Clause | Third-party notices (incl. LGPL) shipped as `LICENSE.headless_shell` in `/opt/pw-browsers`; Playwright's video ffmpeg removed (D-025, D-027) |
| fonts-liberation | SIL OFL 1.1 | Font for rendered pages |

## Python packages (app image)

| Package | Version | License | Note |
|---|---|---|---|
| accelerate | 1.15.0 | Apache Software License |  |
| annotated-doc | 0.0.5 | MIT |  |
| annotated-types | 0.8.0 | MIT |  |
| anyio | 4.15.1 | MIT |  |
| attrs | 26.1.0 | MIT |  |
| av | 19.0.1 | BSD-3-Clause | bundles LGPL FFmpeg, dynamically linked (D-027) |
| babel | 2.18.0 | BSD License |  |
| cactus-needle | 3.2.0 | Apache-2.0 |  |
| certifi | 2026.7.22 | Mozilla Public License 2.0 (MPL 2.0) | MPL-2.0, unmodified (D-009) |
| charset-normalizer | 3.5.2 | MIT |  |
| click | 8.5.0 | BSD-3-Clause |  |
| cloudpickle | 3.1.2 | BSD License |  |
| colorlog | 6.12.0 | MIT License |  |
| courlan | 1.4.0 | Apache-2.0 |  |
| dateparser | 1.4.3 | BSD-3-Clause |  |
| defusedxml | 0.7.1 | Python Software Foundation License | PSF-2.0 (D-009) |
| deprecation | 2.1.0 | Apache Software License |  |
| doclang | 0.7.3 | Apache-2.0 |  |
| docling-core | 2.101.1 | MIT |  |
| docling-ibm-models | 4.0.3 | MIT |  |
| docling-parse | 7.22.2 | MIT |  |
| docling-slim | 2.137.0 | MIT |  |
| dulwich | 1.2.17 | Apache-2.0 OR GPL-2.0-or-later | used under Apache-2.0 (COPYING offers either); commit counting without the GPL git binary (D-047) |
| et-xmlfile | 2.0.0 | MIT License |  |
| fastapi | 0.143.0 | MIT |  |
| filelock | 4.1.0 | MIT |  |
| filetype | 1.2.0 | MIT License |  |
| fsspec | 2026.9.0 | BSD-3-Clause |  |
| greenlet | 3.5.6 | MIT AND PSF-2.0 | MIT AND PSF-2.0 (D-009) |
| h11 | 0.16.0 | MIT License |  |
| hf-xet | 1.7.0 | Apache-2.0 |  |
| htmldate | 1.11.0 | Apache-2.0 |  |
| httpcore | 1.0.9 | BSD-3-Clause |  |
| httptools | 0.9.0 | MIT |  |
| httpx | 0.28.1 | BSD License |  |
| huggingface-hub | 1.33.0 | Apache Software License |  |
| idna | 3.20 | BSD-3-Clause |  |
| jinja2 | 3.1.6 | BSD License |  |
| joblib | 1.6.0 | BSD-3-Clause |  |
| jsonref | 1.1.0 | MIT |  |
| jsonschema | 4.26.0 | MIT |  |
| jsonschema-specifications | 2025.9.1 | MIT |  |
| justext | 3.0.2 | BSD License |  |
| lance-namespace | 0.13.0 | Apache-2.0 |  |
| lance-namespace-urllib3-client | 0.13.0 | Apache-2.0 |  |
| lancedb | 0.40.0 | Apache Software License |  |
| langcodes | 3.5.1 | MIT License |  |
| latex2mathml | 3.81.1 | MIT |  |
| lxml | 6.1.3 | BSD-3-Clause |  |
| lxml-html-clean | 0.4.5 | BSD-3-Clause |  |
| markdown-it-py | 4.2.0 | MIT License |  |
| markupsafe | 3.0.4 | BSD-3-Clause |  |
| mdurl | 0.1.2 | MIT License |  |
| mpmath | 1.3.0 | BSD License |  |
| narwhals | 2.27.1 | MIT |  |
| networkx | 3.7 | BSD-3-Clause |  |
| numpy | 2.5.3 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 |  |
| omegaconf | 2.4.0 | BSD License |  |
| opencv-python-headless | 5.0.0.93 | Apache Software License | bundles LGPL-2.1 FFmpeg, GCC runtime libs, OpenSSL 1.1.1k (D-027) |
| openpyxl | 3.1.5 | MIT License |  |
| opentelemetry-api | 1.45.1 | Apache-2.0 | no-op API only; no SDK/exporter installed (D-014) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |  |
| pandas | 3.0.6 | BSD License |  |
| pillow | 12.3.0 | MIT-CMU | HPND/MIT-CMU (D-009) |
| playwright | 1.63.0 | Apache-2.0 | Python driver; browser listed under Tools |
| pluggy | 1.6.0 | MIT License |  |
| psutil | 7.2.2 | BSD-3-Clause |  |
| pyarrow | 26.0.0 | Apache-2.0 |  |
| pyclipper | 1.4.0 | OSI Approved; MIT License |  |
| pydantic | 2.14.0 | MIT |  |
| pydantic-core | 2.50.0 | MIT |  |
| pydantic-settings | 2.15.0 | MIT |  |
| pyee | 13.0.1 | MIT License |  |
| pygments | 2.21.0 | BSD-2-Clause |  |
| pylatexenc | 2.11 | MIT License |  |
| pypdf | 6.20.0 | BSD-3-Clause |  |
| pypdfium2 | 5.14.0 | BSD-3-Clause, Apache-2.0, dependency licenses | bundles PDFium and its permissive third-party libraries (FreeType under FTL) |
| python-dateutil | 2.9.0.post0 | BSD License; Apache Software License |  |
| python-docx | 1.2.0 | MIT License |  |
| python-dotenv | 1.2.4 | BSD-3-Clause |  |
| python-multipart | 0.0.32 | Apache-2.0 |  |
| python-pptx | 1.0.2 | MIT License |  |
| pytz | 2026.5 | MIT License |  |
| pyyaml | 6.0.3 | MIT License |  |
| rapidocr | 3.10.0 | Apache-2.0 |  |
| referencing | 0.37.0 | MIT |  |
| regex | 2026.9.29 | Apache-2.0 AND CNRI-Python | includes CNRI-Python (D-009) |
| requests | 2.34.2 | Apache Software License |  |
| rich | 15.0.0 | MIT License |  |
| rpds-py | 2026.9.1 | MIT |  |
| rtree | 1.4.1 | MIT |  |
| safetensors | 0.8.0 | Apache Software License |  |
| scikit-learn | 1.9.1 | BSD-3-Clause |  |
| scipy | 1.18.1 | BSD License |  |
| sentence-transformers | 6.1.0 | Apache-2.0 |  |
| setuptools | 84.0.0 | MIT |  |
| shapely | 2.2.0 | BSD-3-Clause |  |
| shellingham | 1.5.4 | ISC License (ISCL) | ISC (D-009) |
| six | 1.17.0 | MIT License |  |
| sqlalchemy | 2.1.4 | MIT |  |
| sqlmodel | 0.0.48 | MIT |  |
| starlette | 1.7.0 | BSD-3-Clause |  |
| sympy | 1.14.0 | BSD License |  |
| tabulate | 0.10.0 | MIT |  |
| threadpoolctl | 3.7.0 | BSD-3-Clause |  |
| tld | 0.13.2 | MPL-1.1 OR GPL-2.0-only OR LGPL-2.1-or-later | used under MPL-1.1, unmodified (D-027) |
| tokenizers | 0.23.3 | Apache Software License |  |
| torch | 2.14.1+cpu | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT |  |
| torchvision | 0.29.1+cpu | BSD |  |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | MPL-2.0 AND MIT, unmodified (D-009) |
| trafilatura | 2.3.1 | Apache-2.0 |  |
| transformers | 5.19.0 | Apache 2.0 License |  |
| tree-sitter | 0.26.0 | MIT | code chunking (D-047) |
| tree-sitter-c-sharp | 0.23.5 | MIT | grammar (D-047) |
| tree-sitter-javascript | 0.25.0 | MIT | grammar (D-047) |
| tree-sitter-python | 0.25.0 | MIT | grammar (D-047) |
| tree-sitter-typescript | 0.23.2 | MIT | grammars for TypeScript and TSX (D-047) |
| typer | 0.26.8 | MIT |  |
| typing-extensions | 4.16.0 | PSF-2.0 | PSF-2.0 (D-009) |
| typing-inspection | 0.4.4 | MIT |  |
| tzlocal | 5.4.4 | MIT |  |
| urllib3 | 2.8.0 | MIT |  |
| uvicorn | 0.54.0 | BSD-3-Clause |  |
| uvloop | 0.23.0 | Apache Software License; MIT License |  |
| watchfiles | 1.3.0 | MIT License |  |
| websockets | 17.2 | BSD-3-Clause |  |
| xlsxwriter | 3.2.9 | BSD License |  |
| yt-dlp | 2026.8.19 | Unlicense | installed without the [default] extra (no GPL mutagen) |

## Frontend packages (bundled into the static UI)

| Package | Version | License |
|---|---|---|
| @floating-ui/core | 1.8.0 | MIT |
| @floating-ui/dom | 1.8.0 | MIT |
| @floating-ui/react-dom | 2.1.9 | MIT |
| @floating-ui/utils | 0.2.12 | MIT |
| @radix-ui/primitive | 1.1.7 | MIT |
| @radix-ui/react-alert-dialog | 1.1.24 | MIT |
| @radix-ui/react-arrow | 1.1.16 | MIT |
| @radix-ui/react-collection | 1.1.16 | MIT |
| @radix-ui/react-compose-refs | 1.1.5 | MIT |
| @radix-ui/react-context | 1.2.2 | MIT |
| @radix-ui/react-dialog | 1.2.0 | MIT |
| @radix-ui/react-direction | 1.1.5 | MIT |
| @radix-ui/react-dismissable-layer | 1.1.20 | MIT |
| @radix-ui/react-dropdown-menu | 2.1.25 | MIT |
| @radix-ui/react-focus-guards | 1.1.6 | MIT |
| @radix-ui/react-focus-scope | 1.2.0 | MIT |
| @radix-ui/react-id | 1.1.4 | MIT |
| @radix-ui/react-menu | 2.1.25 | MIT |
| @radix-ui/react-popper | 1.3.8 | MIT |
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
| @radix-ui/react-use-rect | 1.1.4 | MIT |
| @radix-ui/react-use-size | 1.1.5 | MIT |
| @radix-ui/rect | 1.1.3 | MIT |
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
