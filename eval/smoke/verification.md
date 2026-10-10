# Smoke set verification

Every question in `questions.yaml` was checked by hand on 2026-10-10 against:

- **Code**: `psf/requests` tag `v2.34.2`, commit `6e83187b8feb273ed4c6cdab5efd8d54901dfab3`
  (shallow fetch of that commit, as `fetch_corpus.py` does). Line numbers below are at that commit.
- **Docs**: the four pages in `corpus.yaml`, downloaded from `https://requests.readthedocs.io/en/stable/…`.
  Read the Docs reported `stable` → `6e83187b8feb` and every page's `<title>` ends in
  "Requests 2.34.2 documentation". There is no `v2.34.x` build on Read the Docs (`/en/v2.34.2/` and
  `/en/v2.34.x/` return 404), so `stable` is the only URL for this release.

Page titles as served (`<title>`, HTML entity `&#8212;` = em dash):

| URL | `<title>` |
|---|---|
| …/en/stable/user/quickstart/ | Quickstart — Requests 2.34.2 documentation |
| …/en/stable/user/advanced/ | Advanced Usage — Requests 2.34.2 documentation |
| …/en/stable/user/authentication/ | Authentication — Requests 2.34.2 documentation |
| …/en/stable/community/faq/ | Frequently Asked Questions — Requests 2.34.2 documentation |

The linked folder also contains the same docs as reStructuredText (`docs/user/*.rst`,
`docs/community/faq.rst`), so docs questions list the `requests` folder as an acceptable source with
file locators on the matching `.rst` lines.

## Docs

**docs-01** (redirects). Quickstart › "Redirection and History"; `docs/user/quickstart.rst` 480–527.
> "By default Requests will perform location redirection for all verbs except HEAD."
> "If you're using GET, OPTIONS, POST, PUT, PATCH or DELETE, you can disable redirection handling with
> the `allow_redirects` parameter" … "If you're using HEAD, you can enable redirection as well:
> `requests.head('http://github.com/', allow_redirects=True)`"

**docs-02** (timeout is not a total limit). Quickstart › "Timeouts" note; `quickstart.rst` 543–549.
> "`timeout` is not a time limit on the entire response download; rather, an exception is raised if
> the server has not issued a response for `timeout` seconds (more precisely, if no bytes have been
> received on the underlying socket for `timeout` seconds)."

Also Advanced Usage › "Timeouts" (`advanced.rst` 1086–1133): read timeout is "the number of seconds
the client will wait between bytes sent from the server"; "Neither the connect nor read timeouts are
wall clock."

**docs-03** (method-level cookies not persisted). Advanced Usage › "Session Objects";
`advanced.rst` 48–60.
> "Note, however, that method-level parameters will *not* be persisted across requests, even if using
> a session. This example will only send the cookies with the first request, but not the second"
> (example: `s.get(..., cookies={'from-my': 'browser'})` then `s.get(...)` → `'{"cookies": {}}'`).

**docs-04** (netrc). Authentication › "netrc Authentication"; `authentication.rst` 36–56.
> "Requests will search for the netrc file at `~/.netrc`, `~/_netrc`, or at the path specified by the
> `NETRC` environment variable." … "Usage of netrc file can be disabled by setting `trust_env` to
> `False` in the Requests session"

**docs-05** (compressed encodings). FAQ › "Encoded Data?"; `docs/community/faq.rst` 8–18.
> "Requests automatically decompresses gzip-encoded responses" … "When either the brotli or brotlicffi
> package is installed, requests also decodes Brotli-encoded responses."

Also Quickstart › "Binary Response Content" (`quickstart.rst` 129–132): "The `gzip` and `deflate`
transfer-encodings are automatically decoded for you. The `br` transfer-encoding is automatically
decoded for you if a Brotli library like brotli or brotlicffi is installed."

## Code

**code-01** (max redirects = 30).
- `src/requests/models.py:104` — `DEFAULT_REDIRECT_LIMIT: int = 30`
- `src/requests/sessions.py:484-488` — "Maximum number of redirects allowed. If the request exceeds
  this limit, a TooManyRedirects exception is raised. This defaults to
  requests.models.DEFAULT_REDIRECT_LIMIT, which is 30." / `self.max_redirects = DEFAULT_REDIRECT_LIMIT`
- `src/requests/sessions.py:216-219` — `if len(resp.history) >= self.max_redirects: raise TooManyRedirects(...)`

**code-02** (`Response.ok`). `src/requests/models.py:859-872`: docstring "Returns True if
`status_code` is less than 400, False if not. … This is **not** a check to see if the response code is
`200 OK`." Body: `try: self.raise_for_status() except HTTPError: return False; return True`.
(`raise_for_status` raises for 400–599: `models.py:1156-1167`.) The four docs pages only show
`r.status_code == requests.codes.ok`, not `Response.ok`.

**code-03** (HTTPAdapter pool defaults). `src/requests/adapters.py:79-81` —
`DEFAULT_POOLBLOCK = False`, `DEFAULT_POOLSIZE = 10`, `DEFAULT_RETRIES = 0`; `adapters.py:201-207` —
`pool_connections: int = DEFAULT_POOLSIZE, pool_maxsize: int = DEFAULT_POOLSIZE, …,
pool_block: bool = DEFAULT_POOLBLOCK`. Answer: 10, 10, False. Not stated in the docs pages.

**code-04** (Authorization stripped on redirect). `src/requests/sessions.py:154-184`
(`should_strip_auth`): strip if the hostname changes; keep for http→https on standard ports (80/None →
443/None); keep if the scheme is unchanged and both ports are default; otherwise strip if the port or
scheme changed. `sessions.py:324-327` (`rebuild_auth`): `if "Authorization" in headers and
self.should_strip_auth(original_url, url): del headers["Authorization"]`.

**code-05** (Basic auth encoding). `src/requests/auth.py:34-75` (`_basic_auth_str`): `str` username
and password are encoded with `"latin1"` (lines 65-69), joined with `b":"`, base64-encoded and prefixed
with `"Basic "` (lines 71-73); non-string values are converted with `str()` with a DeprecationWarning
(44-62). `auth.py:111-113`: `HTTPBasicAuth.__call__` sets `r.headers["Authorization"]` to it.

## Multi-source

**multi-01** (retry example + default Retry). Advanced Usage › "Example: Automatic Retries"
(`advanced.rst` 1032–1050): "By default, Requests does not retry failed connections." Example uses
`Retry(total=3, backoff_factor=0.1, status_forcelist=[502, 503, 504], allowed_methods={'POST'})`
mounted with `HTTPAdapter(max_retries=retries)`. Code: `adapters.py:208-209` —
`if max_retries == DEFAULT_RETRIES: self.max_retries = Retry(0, read=False)` (`DEFAULT_RETRIES = 0`,
line 81; docstring 166-174). The example's values are only in the docs; `Retry(0, read=False)` only in
the code.

**multi-02** (adapter selection). Advanced Usage › "Transport Adapters" (`advanced.rst` 968–998): "The
adapter will be chosen based on a longest prefix match"; one HTTPAdapter is attached for HTTP and one
for HTTPS. Code: `sessions.py:888-897` (`mount`: "Adapters are sorted in descending order by prefix
length", moves shorter keys after the new one); `sessions.py:870-880` (`get_adapter`: first prefix
where `url.lower().startswith(prefix.lower())`, else `InvalidSchema`); `sessions.py:500-503`
(`self.mount("https://", HTTPAdapter())`, `self.mount("http://", HTTPAdapter())`).

## Unanswerable

Searched the whole checkout (src, tests, docs, HISTORY.md, README.md; `.git` and `*.pem` excluded) and
the text of the four downloaded doc pages (HTML with tags stripped), case-insensitive:

| Question | Pattern | Code hits | Docs-page hits |
|---|---|---|---|
| unans-01 WebSocket | `websocket\|web socket\|ws://\|wss://` | 0 | 0 |
| unans-02 HTTP/2 | `http/2\|http2\|\bh2c?\b\|multiplex` | 2, both a local variable `h2` in `tests/test_requests.py:1910-1912` (an Authorization header) | 0 |
| unans-03 Session base_url | `base_url\|base url\|baseurl` | 1, a Sphinx comment in `docs/conf.py:209` ("base URL from which the finished HTML is served") | 0 |

requests has no WebSocket client, no HTTP/2 support and no `Session.base_url`. Closest content
(likely retrieval distractors, not answers): `sessions.py:237-241` joins a *relative redirect
Location* with `urljoin(resp.url, …)`; Advanced Usage › "Blocking Or Non-Blocking?" lists other
projects (requests-threads, grequests, requests-futures, httpx) but says nothing about HTTP/2 or
WebSockets.

Commands used (from the scratch copy of the corpus):

```sh
grep -rniE '<pattern>' requests --exclude-dir=.git | grep -v '\.pem:'
grep -ciE '<pattern>' docs/user_quickstart.txt docs/user_advanced.txt \
  docs/user_authentication.txt docs/community_faq.txt   # page text extracted from the HTML
```


## Additions after the first dry run (2026-10-10)

The first run against Ludwig's endpoint flagged three citations as wrong. Each was checked by hand at
commit `6e83187b`; all three state the answer, so the answer key was widened (not the system):

- **docs-03:** `tests/test_requests.py:425-429`, `test_request_cookies_not_persisted`: "Sending a request
  with cookies should not add cookies to the session" (`assert not s.cookies`).
- **code-04:** `docs/user/quickstart.rst:226` and the Quickstart page, section "Custom Headers":
  "Authorization headers will be removed if you get redirected off-host."

Source titles in `questions.yaml` now match how Trustbook titles the pages ("Quickstart", "Advanced
Usage", …). The URLs were already accepted.
- **multi-02:** `tests/test_requests.py:1659-1722`, `test_session_get_adapter_prefix_matching` and its
  mixed-case twin mount two prefixes and assert `get_adapter` returns the more specific prefix's adapter.
  They support the sentence "The Session uses its get_adapter method to evaluate the URL against the
  registered prefixes and return the correct adapter" (second dry run).
