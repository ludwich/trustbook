"""D-057: the bodies of functions the context's code calls join the context (code-05 shape)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from app.db.engine import get_engine
from app.db.migrate import migrate
from app.index.memory import InMemorySpaceIndex
from app.retrieval.callees import (
    MAX_CALLEES,
    called_names,
    callee_extras,
    code_path,
    defined_functions,
    defines,
    enclosing_class,
    is_test_file,
)
from app.retrieval.fusion import CONTEXT_CHUNKS
from app.retrieval.service import RetrievedChunk, retrieve, select_context, with_callees
from tests.chat_helpers import KeywordEmbedder, OverlapReranker, add_chunk, add_source

AUTH = "src/requests/auth.py"

BODY = '''def _basic_auth_str(username, password):
    """Returns a Basic Auth string."""
    if isinstance(username, str):
        username = username.encode("latin1")
    if isinstance(password, str):
        password = password.encode("latin1")
    authstr = "Basic " + to_native_string(
        b64encode(b":".join((username, password))).strip()
    )
    return authstr'''

CALL = """    def __call__(self, r):
        r.headers["Authorization"] = _basic_auth_str(self.username, self.password)
        return r"""

TEST = """    def test_set_basicauth(self, httpbin, username, password):
        auth = (username, password)
        url = httpbin("get")
        r = requests.Request("GET", url, auth=auth)
        p = r.prepare()
        assert p.headers["Authorization"] == _basic_auth_str(username, password)"""


def header(path: str, start: int, end: int, symbol: str | None) -> str:
    where = f"{path} · {symbol}" if symbol else path
    return f"File: {where} (lines {start}-{end})"


def code_chunk(
    cid: str,
    path: str,
    start: int,
    end: int,
    body: str,
    *,
    symbol: str | None = None,
    kind: str | None = None,
    source: str = "s",
    score: float = 0.5,
    tokens: int = 100,
) -> RetrievedChunk:
    locator: dict[str, object] = {
        "file": path,
        "start_line": start,
        "end_line": end,
        "symbol": symbol,
    }
    if kind is not None:
        locator["kind"] = kind
    text = header(path, start, end, symbol) + "\n" + body
    return RetrievedChunk(cid, source, "requests", text, locator, tokens, score)


def doc_chunk(cid: str, text: str, *, path: str = "docs/user/advanced.rst") -> RetrievedChunk:
    locator: dict[str, object] = {"file": path, "start_line": 1, "end_line": 40, "symbol": None}
    return RetrievedChunk(cid, "s", "requests", f"File: {path}\n{text}", locator, 100, 0.6)


def body_chunk(**kw: object) -> RetrievedChunk:
    return code_chunk("body", AUTH, 34, 75, BODY, symbol="_basic_auth_str", **kw)  # type: ignore[arg-type]


def call_chunk() -> RetrievedChunk:
    return code_chunk(
        "call", AUTH, 111, 113, CALL, symbol="HTTPBasicAuth.__call__", score=0.26, tokens=40
    )


def unit_test_chunk() -> RetrievedChunk:
    return code_chunk(
        "test",
        "tests/test_requests.py",
        554,
        570,
        TEST,
        symbol="TestRequests.test_set_basicauth",
        score=0.56,
    )


# --- syntax helpers --------------------------------------------------------------------------


def test_defined_functions_across_languages() -> None:
    code = """
def plain(a):
async def coro(b):
    async def inner():
function jsFunc(x) {
export const arrow = (a, b) => a + b;
const single = x => x * 2;
let anon = function (y) { return y; };
public static string Encode(string user, string password)
private async Task<int> CountAsync<T>(IEnumerable<T> items) {
  render(props: Props): string {
procedure PostInvoice(DocNo: Code[20])
"""
    assert defined_functions(code) == {
        "plain",
        "coro",
        "inner",
        "jsFunc",
        "arrow",
        "single",
        "anon",
        "Encode",
        "CountAsync",
        "render",
        "PostInvoice",
    }


def test_calls_and_statements_are_not_definitions() -> None:
    code = """
    return helper(x)
    raise ValueError("bad")
    assert check(1)
    class Foo(Base):
    if (ready) {
    result = compute(a, b)
    await Send(message);
    yield return Next(item);
    var total = Sum(values);
    helper(x);
    new Widget(size);
"""
    assert defined_functions(code) == set()


def test_called_names_resolve_receivers_and_skip_noise() -> None:
    chunk = code_chunk(
        "c",
        AUTH,
        1,
        20,
        """    def handle(self, r):
        if len(r.body) > LIMIT:
            self.reset(r)
        value = _basic_auth_str(user, password)
        r.prepare_auth(value)
        obj = new Widget(3)
        helper = lambda: 0
        def nested(x):
            return nested(x - 1)
        print(value)
        return str(value)""",
        symbol="HTTPBasicAuth.handle",
    )
    assert called_names(chunk) == [
        ("HTTPBasicAuth.reset",),
        ("_basic_auth_str", "HTTPBasicAuth._basic_auth_str"),
    ]
    module = code_chunk("m", AUTH, 1, 3, "x = build(1)\ny = self.other()")
    assert called_names(module) == [("build",)]  # no class: self.other cannot be resolved


def test_enclosing_class_uses_kind_when_present() -> None:
    assert enclosing_class({"symbol": "Client.send"}) == "Client"
    assert enclosing_class({"symbol": "send"}) == ""
    assert enclosing_class({"symbol": "Client", "kind": "class"}) == "Client"
    assert enclosing_class({"symbol": None}) == ""


def test_test_files() -> None:
    for path in (
        "tests/test_requests.py",
        "src/pkg/test_utils.py",
        "pkg/utils_test.go",
        "tests/conftest.py",
        "web/src/app.test.ts",
        "web/src/app.spec.tsx",
        "src/Contoso.Tests/InvoiceTests.cs",
        "src/__tests__/x.js",
    ):
        assert is_test_file(path), path
    for path in ("src/requests/auth.py", "src/testimony.py", "lib/contest.ts", "src/Latest.cs"):
        assert not is_test_file(path), path


def test_only_chunks_from_a_code_chunker_are_code() -> None:
    assert code_path(call_chunk()) == AUTH
    assert code_path(doc_chunk("d", "def init_poolmanager(self):")) is None
    web = RetrievedChunk("w", "s", "Docs", "Basic auth", {"heading": "Basic"}, 10, 0.5)
    assert code_path(web) is None


def test_defines_by_symbol_kind_or_text() -> None:
    names = ("_basic_auth_str",)
    assert defines(body_chunk(), names)
    assert defines(body_chunk(kind="function"), names)
    assert not defines(body_chunk(kind="class"), names)
    assert not defines(body_chunk(), ("other",))
    # Symbol matches but the text is a class, not a function (no kind recorded).
    cls = code_chunk("k", AUTH, 1, 9, "class Auth(AuthBase):\n    pass", symbol="Auth")
    assert not defines(cls, ("Auth",))
    # Recorded as a method: the kind is enough.
    method = code_chunk("m", AUTH, 1, 3, "    @property\n    def x(self): ...", symbol="A.x")
    assert defines(code_chunk("m2", AUTH, 1, 2, "...", symbol="A.x", kind="method"), ("A.x",))
    assert defines(method, ("A.x",))
    # Module-level code of a parsed file (no symbol) is not a definition, even with a stub inside.
    stub = code_chunk(
        "st", AUTH, 1, 9, "try:\n    pass\nexcept ImportError:\n    def socks(*a): ..."
    )
    assert not defines(stub, ("socks",))
    # A fallback-chunked file (no grammar) is matched by its text.
    al = code_chunk("al", "src/Invoice.al", 1, 9, "procedure PostInvoice(DocNo: Code[20])\nbegin")
    assert defines(al, ("PostInvoice",))


# --- choosing callees ------------------------------------------------------------------------


def test_code_05_shape_adds_the_body_the_call_sites_call() -> None:
    body = body_chunk(score=0.276)
    selected = [
        unit_test_chunk(),
        doc_chunk("doc", "Basic Authentication: auth=('user', 'pass')"),
        call_chunk(),
    ]
    ranked = [*selected, body]
    extras = callee_extras(selected, ranked)
    assert [(c.chunk_id, name, callers) for c, name, callers in extras] == [
        ("body", "_basic_auth_str", ("test", "call"))
    ]


def test_callee_must_be_a_ranked_candidate() -> None:
    selected = [call_chunk()]
    assert callee_extras(selected, selected) == []  # the body was not retrieved for this question


def test_callee_rules_source_test_files_and_context() -> None:
    other_source = code_chunk(
        "o", AUTH, 34, 75, BODY, symbol="_basic_auth_str", source="other", score=0.3
    )
    in_tests = code_chunk("t", "tests/helpers.py", 1, 10, BODY, symbol="_basic_auth_str", score=0.3)
    selected = [call_chunk()]
    assert callee_extras(selected, [*selected, other_source, in_tests]) == []
    already = body_chunk()
    assert callee_extras([*selected, already], [*selected, already]) == []


def test_document_examples_neither_call_nor_define() -> None:
    example = doc_chunk("doc", "    def init_poolmanager(self):\n        _basic_auth_str(a, b)")
    selected = [example]
    assert callee_extras(selected, [*selected, body_chunk()]) == []
    helper = doc_chunk("doc2", "def helper(x):\n    return x")
    caller = code_chunk("c", AUTH, 1, 3, "    return helper(1)", symbol="f")
    assert callee_extras([caller], [caller, helper]) == []


def test_order_by_callers_then_rank_and_at_most_two() -> None:
    def fn(cid: str, name: str, score: float) -> RetrievedChunk:
        return code_chunk(
            cid, AUTH, 1, 3, f"def {name}(x):\n    return x", symbol=name, score=score
        )

    callers = [
        code_chunk("c1", AUTH, 10, 12, "    a(1)\n    b(2)\n    c(3)", symbol="one"),
        code_chunk("c2", AUTH, 20, 22, "    c(4)", symbol="two"),
    ]
    ranked = [*callers, fn("fa", "a", 0.4), fn("fb", "b", 0.3), fn("fc", "c", 0.2)]
    extras = callee_extras(callers, ranked)
    assert [(c.chunk_id, ids) for c, _, ids in extras] == [("fc", ("c1", "c2")), ("fa", ("c1",))]
    assert MAX_CALLEES == 2
    assert len(callee_extras(callers, ranked, max_callees=5)) == 3


def test_same_file_definition_is_preferred() -> None:
    caller = code_chunk("c", AUTH, 1, 3, "    return helper(1)", symbol="f")
    elsewhere = code_chunk(
        "e", "src/other.py", 1, 3, "def helper(x): ...", symbol="helper", score=0.9
    )
    here = code_chunk("h", AUTH, 5, 7, "def helper(x): ...", symbol="helper", score=0.1)
    assert [c.chunk_id for c, _, _ in callee_extras([caller], [caller, elsewhere, here])] == ["h"]


def test_self_calls_and_implicit_receivers_find_methods() -> None:
    caller = code_chunk(
        "c",
        "src/Client.cs",
        1,
        9,
        "        var s = Encode(user);\n        this.Flush();",
        symbol="Client.Send",
    )
    encode = code_chunk(
        "e", "src/Client.cs", 10, 20, "    private string Encode(string user)\n    {",
        symbol="Client.Encode", score=0.2,
    )  # fmt: skip
    flush = code_chunk(
        "f", "src/Client.cs", 21, 30, "    void Flush() { }", symbol="Client.Flush", kind="method"
    )
    ids = {c.chunk_id for c, _, _ in callee_extras([caller], [caller, encode, flush])}
    assert ids == {"e", "f"}


# --- fitting into the context ----------------------------------------------------------------


def test_with_callees_keeps_scores_marks_the_name_and_fits_the_budget() -> None:
    top = unit_test_chunk()
    call = call_chunk()
    body = body_chunk(score=0.276, tokens=472)
    result = with_callees([top, call], [top, call, body], max_tokens=6000)
    assert [c.chunk_id for c in result] == ["test", "call", "body"]
    assert result[-1].definition_of == "_basic_auth_str" and result[-1].score == 0.276
    # Over budget: the body may displace low-ranked chunks, never the top chunk or the caller.
    filler = code_chunk("fill", "src/x.py", 1, 2, "x = 1", symbol=None, tokens=300)
    tight = with_callees([top, call, filler], [top, call, filler, body], max_tokens=700)
    assert [c.chunk_id for c in tight] == ["test", "call", "body"]
    assert with_callees([top], [top, body], max_tokens=150) == [top]  # does not fit: left out


def test_with_callees_never_displaces_constants_added_before() -> None:
    top = unit_test_chunk()
    call = call_chunk()
    constant = code_chunk("const", AUTH, 1, 2, "LIMIT = 3", tokens=200)
    constant = RetrievedChunk(**{**constant.__dict__, "definition_of": "LIMIT"})
    body = body_chunk(tokens=200)
    result = with_callees([top, call, constant], [top, call, body], max_tokens=500)
    assert [c.chunk_id for c in result] == ["test", "call", "const"]


@pytest.fixture
def engine(tmp_path: Path) -> Engine:
    db = tmp_path / "space.db"
    migrate(db, "space")
    return get_engine(db)


def test_retrieve_adds_the_called_body_below_the_context_cut(engine: Engine) -> None:
    index = InMemorySpaceIndex()
    source = add_source(engine, "requests", source_type="folder", group="code")

    def add(text: str, ordinal: int, path: str, start: int, end: int, symbol: str | None) -> str:
        locator = {"file": path, "start_line": start, "end_line": end, "symbol": symbol}
        full = header(path, start, end, symbol) + "\n" + text
        return add_chunk(engine, index, source, full, ordinal=ordinal, locator=locator)

    call = add(CALL + "  # basic authorization header", 0, AUTH, 111, 113, "HTTPBasicAuth.__call__")
    body = add(BODY + "\n# basic", 1, AUTH, 34, 75, "_basic_auth_str")
    for i in range(CONTEXT_CHUNKS + 2):  # outrank the body
        add(f"basic authorization header note {i}", 2 + i, f"src/n{i}.py", 1, 2, None)
    question = "How does it build the basic authorization header?"
    result = retrieve(engine, index, KeywordEmbedder(), OverlapReranker(), question)
    ranked_ids = [c.chunk_id for c in result.ranked]
    assert body in ranked_ids and body not in [c.chunk_id for c in select_context(result.ranked)]
    ids = [c.chunk_id for c in result.chunks]
    assert call in ids and ids[-1] == body
    assert result.chunks[-1].definition_of == "_basic_auth_str"
    assert result.top_score == result.ranked[0].score
