# Refusal-tuning set verification

`refusal.yaml` was checked by hand on 2026-10-10 against the same corpus as the smoke set
(`verification.md`):

- **Code**: `psf/requests` tag `v2.34.2`, commit `6e83187b8feb273ed4c6cdab5efd8d54901dfab3`
  (`git rev-parse HEAD` in the checkout). Line numbers below are at that commit.
- **Docs**: the four pages in `corpus.yaml`, downloaded again from `https://requests.readthedocs.io/en/stable/…`
  on 2026-10-10. Every `<title>` still ends in "Requests 2.34.2 documentation". The HTML was reduced to
  text (scripts and styles removed, tags stripped, entities decoded) for searching.

The linked folder holds more than the four pages: all of `docs/` (including `api.rst`,
`community/recommended.rst`, `community/faq.rst`, `user/install.rst`), `HISTORY.md` (full release
history back to 0.0.1), `README.md`, `tests/`, `pyproject.toml` and `.github/`. All of it was searched.

## Unanswerable

Searches are case-insensitive extended regex over the whole checkout (`.git` and `*.pem` excluded)
and over the text of the four doc pages:

```sh
grep -rniE '<pattern>' requests --exclude-dir=.git | grep -v '\.pem:'
grep -ciE '<pattern>' user_quickstart.txt user_advanced.txt user_authentication.txt community_faq.txt
```

"Hits" lists every checkout match (or a summary where there are many); "Pages" is the number of
matching lines in the four doc pages. "Nearest content" is what retrieval is likely to surface: it is
on-topic but does not answer the question, which is what makes these hard.

| Id | Pattern | Hits | Pages | Why the corpus does not answer it; nearest content |
|---|---|---|---|---|
| ref-unans-01 backoff_max / jitter | `backoff_max\|backoff max\|jitter\|DEFAULT_BACKOFF` | 0 | 0 | urllib3 is not vendored. Nearest: Advanced › "Example: Automatic Retries" (`advanced.rst` 1032–1050) uses `backoff_factor=0.1`; no cap, no jitter. |
| ref-unans-02 Retry-After | `retry-after\|retry_after\|respect_retry` | 0 | 0 | Whether urllib3's Retry honours Retry-After is urllib3 behaviour, not described. Nearest: the same example's `status_forcelist=[502, 503, 504]`; `status_codes.py:88` `449: ("retry_with", "retry")`. |
| ref-unans-03 HTTP/3 | `\bquic\b\|http/3\|http3\|\bh3\b\|alt-svc\|alpn` | 2, both an HTML `<h3>` tag (`docs/_static/custom.css:3`, `docs/_templates/sidebar.html:16`) | 0 | No HTTP/3 support. (`quic` alone matches "quick"/"quickstart", hence `\b`.) |
| ref-unans-04 OpenTelemetry | `opentelemetry\|telemetry\|tracing\|\bspan\b\|instrument` | 0 | 0 | No tracing integration. Nearest: Advanced › "Event Hooks" (`response` hook), which says nothing about tracing. |
| ref-unans-05 self-throttling | `throttl\|rate.?limit` | 6: GitHub example headers `advanced.rst:907,917,918`, `tests/test_requests.py:1267-1268` (header fixture), `HISTORY.md:1878` "Async pool size throttling" (0.x era) | 3 (the same GitHub example) | The docs only *read* `x-ratelimit-remaining` in the GitHub walkthrough; there is no client-side throttling. |
| ref-unans-06 zstd | `zstd\|zstandard` | 0 | 0 | Trap: FAQ › "Encoded Data?" and Quickstart › "Binary Response Content" cover gzip, deflate and Brotli only; zstd is never mentioned. |
| ref-unans-07 TLS version / ciphers | `cipher\|tls ?v?1\.[0-3]\|tlsv1\|min(imum)?.?(tls\|version)\|ssl_version` | 12: `keyEncipherment` in `tests/certs/*/cert.cnf`, `HISTORY.md:1187` "Drop RC4 from the default cipher list" (2.x-era note), `HISTORY.md:590` and `help.py:93,98` (`OPENSSL_VERSION_NUMBER`), `pyproject.toml:111` (pytest `minversion`), `advanced.rst:1014,1030` (`ssl_version=ssl.PROTOCOL_SSLv3` in the custom-adapter example) | 2 (the same example) | Defaults come from urllib3/ssl and are not stated anywhere. Trap: the "Example: Specific SSL Version" section shows how to *force* a version. |
| ref-unans-08 keep-alive idle timeout | `\bidle\b\|keepalive_timeout\|keep-alive timeout` | 0 | 0 | Trap: Advanced › "Keep-Alive" (`advanced.rst` 336–345) says keep-alive is automatic and when connections return to the pool, but gives no idle timeout. |
| ref-unans-09 certifi size | `\bcertifi\b` (16 checkout lines), `root cert` | `advanced.rst:282-291` ("Requests uses certificates from the package certifi"), `recommended.rst:9-16` ("carefully curated collection of Root Certificates"), `pyproject.toml:22` (`certifi>=2017.4.17`), HISTORY bundle updates | 3 | No count of certificates anywhere. |
| ref-unans-10 DoH / custom resolver | `dns-over\|\bdoh\b\|resolver\|\bdns\b` | 12: SAN `DNS.1` lines in `tests/certs/*/cert.cnf`; `HISTORY.md:545-546,763` (DNS in proxy bypass); `quickstart.rst:555` ("DNS failure"); `advanced.rst:693` (socks5 vs socks5h) | 2 | Trap: the only control over resolution is socks5h (resolve on the proxy) — that is ref-near-07, not a resolver option. |
| ref-unans-11 upload progress | `progress\|upload callback\|bytes sent` | 2: `.github/SECURITY.md:49` ("is progressing"), `advanced.rst:1103` (read timeout "between bytes sent from the server") | 1 | No progress callback. Nearest: Advanced › "Streaming Uploads" / "Event Hooks"; Quickstart points to requests-toolbelt for streaming multipart without describing progress. |
| ref-unans-12 max header count | `max.?headers\|_MAXHEADERS\|too many headers\|header_fields_too_large\|LineTooLong` | 1: `status_codes.py:86` `431: ("header_fields_too_large", ...)` | 0 | The limit lives in `http.client`; not in the corpus. Trap: the 431 status-code name. |
| ref-unans-13 CVE for no_proxy fix | `bpo-39057\|CVE-` | 10: `HISTORY.md:55` and `tests/test_utils.py:864` (bpo-39057, no CVE); CVEs at `HISTORY.md:65,79,117,150,229,501,1123,1324` belong to other fixes (extract_zipped_paths, netrc, verify=False, Proxy-Authorization, https→http redirect, cookies, ...) | 0 | Trap: the 2.34.0 entry (`HISTORY.md` 53–55) cites only CPython's bpo-39057. |
| ref-unans-14 Python 3.13 release | `3\.13` | 4: `pyproject.toml:38` (classifier), `src/requests/_types.py:56` (TODO comment), `.github/workflows/run-tests.yml:15` (CI matrix), an SVG path in `ext/requests-logo.svg` | 0 | Trap: HISTORY records when 3.10, 3.12, 3.14, 3.14t and 3.15 were added (`HISTORY.md` 317, 188, 110, 48, 46) but never 3.13 (nor 3.11). |
| ref-unans-15 httpx Limits | `httpx\|max_connections\|max_keepalive\|Limits\(` | 2: `advanced.rst:1070,1075` (httpx named and linked) | 1 | httpx is only named as an async option. |
| ref-unans-16 grequests | `grequests\|exception_handler` | 3: `advanced.rst:1070,1073`, `HISTORY.md:1649` (async moved to grequests) | 1 | Only named and linked. |
| ref-unans-17 urllib3 pool full | `pool is full\|discarding\|pool_block\|should block` | 10, all in `adapters.py` (`pool_block` plumbing; docstring 175 "Whether the connection pool should block for connections.") | 0 | What urllib3 does with an extra connection, and its log text, is urllib3-internal. (`ClosedPoolError` is handled at `adapters.py:731`, a different error.) |
| ref-unans-18 Retry DEFAULT_ALLOWED_METHODS | `DEFAULT_ALLOWED_METHODS\|idempotent\|method_whitelist` | 1: `advanced.rst:743` ("HTTP GET is an idempotent method …") | 1 | Trap: the GitHub walkthrough calls GET idempotent; the docs' retry example sets `allowed_methods={'POST'}`. urllib3's default set is not in the corpus. |
| ref-unans-19 requests-kerberos mutual_authentication | `kerberos\|mutual_auth` | 6: links in `authentication.rst:111,153` and `api.rst:148,151`, `HISTORY.md:1554,1614` | 1 | requests-kerberos is only linked. (`mutual_tls` in `tests/testserver/server.py` is the test server's mTLS flag, matched by neither pattern.) |
| ref-unans-20 OAuth2Session refresh | `OAuth2Session\|auto_refresh\|token_updater\|refresh` | 0 | 0 | Trap: Authentication › "OAuth 2 and OpenID Connect Authentication" points to requests-oauthlib and lists four flows (ref-near-06), with no token refresh details. |

## Answerable near-misses

Line numbers at commit `6e83187b`. Doc-page headings are as on the served page.

**ref-near-01** (`iter_lines` chunk size = 512). Next to: nothing absent, a small-detail question.
- `src/requests/models.py:106` — `ITER_CHUNK_SIZE: int = 512`
- `src/requests/models.py:990-995` — `def iter_lines(self, chunk_size: int = ITER_CHUNK_SIZE, decode_unicode: bool = False, delimiter: … = None)`

The docs only show `iter_lines()` without the value.

**ref-near-02** (CVE-2026-25645). Pair of ref-unans-13.
- `HISTORY.md:70-82` — release heading `2.33.0 (2026-03-25)` (line 70); line 79–82: "CVE-2026-25645
  `requests.utils.extract_zipped_paths` now extracts contents to a non-deterministic location to
  prevent malicious file replacement. This does not affect default usage of Requests, only
  applications calling the utility function directly."
- `src/requests/utils.py:290-325` — the function itself (`tempfile.mkstemp`, line 319); accepted
  because an answer may cite it to describe what was changed.

**ref-near-03** (CPython 3.12 → 2.32.0). Pair of ref-unans-14.
- `HISTORY.md:159-191` — heading `2.32.0 (2024-05-20)` (line 159); line 188: "Requests has officially
  added support for CPython 3.12 (#6503)". The next heading up is `2.32.1` (line 152), so 188 belongs
  to 2.32.0.

**ref-near-04** (stream=True and the pool). Pair of ref-unans-08.
- Advanced Usage › "Body Content Workflow", `docs/user/advanced.rst:324-333`: "If you set `stream`
  to `True` when making a request, Requests cannot release the connection back to the pool unless
  you consume all the data or call `Response.close`." (then: use a `with` statement)
- Advanced Usage › "Keep-Alive", `advanced.rst:343-345`: "Note that connections are only released back
  to the pool for reuse once all body data has been read; be sure to either set `stream` to `False` or
  read the `content` property of the `Response` object."

**ref-near-05** (verify=False ignores hostname mismatches). Pair of ref-unans-07.
- Advanced Usage › "SSL Cert Verification", `advanced.rst:246-250`: "Note that when `verify` is set to
  `False`, requests will accept any TLS certificate presented by the server, and will ignore hostname
  mismatches and/or expired certificates, which will make your application vulnerable to
  man-in-the-middle (MitM) attacks." (241–244: the `verify=False` example.)

**ref-near-06** (OAuth 2 → requests-oauthlib). Pair of ref-unans-20.
- Authentication › "OAuth 2 and OpenID Connect Authentication", `docs/user/authentication.rst:90-100`:
  "The `requests-oauthlib` library also handles OAuth 2, the authentication mechanism underpinning
  OpenID Connect. See the requests-oauthlib OAuth2 documentation for details of the various OAuth 2
  credential management flows:" Web Application Flow, Mobile Application Flow, Legacy Application
  Flow, Backend Application Flow.

**ref-near-07** (socks5h). Pair of ref-unans-10.
- Advanced Usage › "SOCKS", `advanced.rst:685-693`: "Using the scheme `socks5` causes the DNS
  resolution to happen on the client, rather than on the proxy server. … If you want to resolve the
  domains on the proxy server, use `socks5h` as the scheme."

**ref-near-08** (Digest algorithms). Code-only detail; the Authentication page's "Digest
Authentication" section does not list algorithms.
- `src/requests/auth.py:165-208` — `algorithm = self._thread_local.chal.get("algorithm")`;
  `if algorithm is None: _algorithm = "MD5"` (169–170); branches for `"MD5"`/`"MD5-SESS"` (174),
  `"SHA"` (182, `hashlib.sha1`), `"SHA-256"` (190), `"SHA-512"` (198); any other value leaves
  `hash_utf8 = None` → `return None` (207–208).
- `tests/test_requests.py:85` — `digest_auth_algo = ("MD5", "SHA-256", "SHA-512")`.

**ref-near-09** (non-blocking IO). Pair of ref-unans-15 / ref-unans-16.
- Advanced Usage › "Blocking Or Non-Blocking?", `advanced.rst:1061-1075`: "With the default Transport
  Adapter in place, Requests does not provide any kind of non-blocking IO." … "Some excellent examples
  are requests-threads, grequests, requests-futures, and httpx." (1072–1075: the links.)

**ref-near-10** (`requests.codes` for 429). Pair of ref-unans-02.
- `src/requests/status_codes.py:85` — `429: ("too_many_requests", "too_many"),`
- `status_codes.py:1-19` — module docstring: names are "accessible either as attributes or as
  dictionary items"; "both upper- and lower-case versions of the names are allowed".
- `status_codes.py:109-114` — `_init` sets each title and `title.upper()` as attributes.
- `status_codes.py:106` — `codes: LookupDict[int] = LookupDict(name="status_codes")`, the definition of `requests.codes`
  (added 2026-10-14 by Ludwig's decision, Gate 5 / G3-2).

The four doc pages never mention 429 or `too_many_requests`.
