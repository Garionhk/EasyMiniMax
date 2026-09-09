"""The Ollama client, against a stub that speaks the real endpoints.

There is no Ollama on the machine this was written on, and there will not be one
on most machines this runs on. A stub is not a substitute for having watched a
real model leave VRAM, but it does exercise the actual request shapes against
the actual endpoints, and it catches the things that would otherwise only show
up on a user's machine: a wait that returns early, a wait that never returns, a
dead port that hangs the window.

The one behaviour worth being strict about is `free_vram`. Asking Ollama to
unload returns immediately whether or not anything happened, so a version of
this that trusted the request would look correct in every test and still hand a
full graphics card to ComfyUI.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.llm.ollama import (
    DEFAULT_URL, LlmError, Ollama, free_vram, normalise_url,
)


class _Stub:
    """An Ollama-shaped server on a free port."""

    def __init__(self, *, tags=("qwen2.5vl:7b", "llama3.2:3b"),
                 loaded_for=0, never_frees=False, generate_status=200,
                 answer="a red fox in deep snow"):
        self.tags = list(tags)
        #: How many /api/ps polls still report a resident model.
        self.loaded_for = loaded_for
        self.never_frees = never_frees
        self.generate_status = generate_status
        self.answer = answer
        #: Every payload posted to /api/generate, for asserting on shapes.
        self.generate_calls: list[dict] = []
        self.ps_polls = 0
        self.unload_calls: list[str] = []

        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass                       # keep the test output readable

            def _send(self, status, body):
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == "/api/tags":
                    self._send(200, {"models": [{"name": n} for n in stub.tags]})
                elif self.path == "/api/ps":
                    stub.ps_polls += 1
                    resident = stub.never_frees or stub.ps_polls <= stub.loaded_for
                    self._send(200, {"models": (
                        [{"name": "qwen2.5vl:7b", "size_vram": 6_000_000_000}]
                        if resident else [])})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
                if self.path == "/api/generate":
                    stub.generate_calls.append(payload)
                    if payload.get("keep_alive") == 0 and "prompt" not in payload:
                        stub.unload_calls.append(payload.get("model", ""))
                        self._send(200, {"response": ""})
                        return
                    if stub.generate_status != 200:
                        self._send(stub.generate_status, {"error": "nope"})
                        return
                    self._send(200, {"response": stub.answer})
                else:
                    self._send(404, {"error": "not found"})

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub():
    server = _Stub()
    yield server
    server.close()


#: A port with nothing on it. Bind and release so it is genuinely free.
@pytest.fixture
def dead_url():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    return f"http://127.0.0.1:{port}"


# -- addresses -------------------------------------------------------------

@pytest.mark.parametrize("given, expected", [
    ("", DEFAULT_URL),
    ("127.0.0.1:11434", "http://127.0.0.1:11434"),
    ("http://localhost:11434/", "http://localhost:11434"),
    ("  http://box:1234  ", "http://box:1234"),
])
def test_addresses_are_tidied(given, expected):
    assert normalise_url(given) == expected


# -- asking it things ------------------------------------------------------

def test_a_live_server_is_recognised(stub):
    assert Ollama(stub.url).is_alive() is True


def test_a_dead_port_is_not_alive_and_does_not_hang(dead_url):
    import time
    started = time.monotonic()
    assert Ollama(dead_url).is_alive() is False
    assert time.monotonic() - started < 3, "a missing server must cost nothing"


def test_the_model_list_comes_back_sorted(stub):
    assert Ollama(stub.url).models() == ["llama3.2:3b", "qwen2.5vl:7b"]


def test_a_dead_server_raises_a_readable_error_for_the_model_list(dead_url):
    with pytest.raises(LlmError) as caught:
        Ollama(dead_url).models()
    assert "Could not ask" in str(caught.value)


def test_what_is_loaded_is_reported_with_its_memory(stub):
    stub.loaded_for = 5
    resident = Ollama(stub.url).loaded()
    assert len(resident) == 1
    assert resident[0].name == "qwen2.5vl:7b"
    assert resident[0].gigabytes == 6.0


def test_an_unreachable_server_reports_nothing_loaded(dead_url):
    """So a render is never blocked waiting on a server that will not answer."""
    assert Ollama(dead_url).loaded() == []


# -- generating ------------------------------------------------------------

def test_generate_sends_the_shape_ollama_wants(stub):
    Ollama(stub.url).generate("qwen2.5vl:7b", "describe this",
                              system="be brief", images=["AAAA"], max_words=100)
    payload = stub.generate_calls[-1]
    assert payload["model"] == "qwen2.5vl:7b"
    assert payload["stream"] is False
    assert payload["think"] is False
    # The model must not stay resident after answering.
    assert payload["keep_alive"] == 0
    assert payload["system"] == "be brief"
    assert payload["images"] == ["AAAA"]
    assert payload["options"]["num_predict"] > 100


def test_no_word_budget_means_no_num_predict(stub):
    Ollama(stub.url).generate("m", "hello")
    assert "options" not in stub.generate_calls[-1]


def test_generate_returns_the_answer(stub):
    assert Ollama(stub.url).generate("m", "hi") == "a red fox in deep snow"


def test_generate_without_a_model_is_refused_before_the_network(stub):
    with pytest.raises(LlmError) as caught:
        Ollama(stub.url).generate("", "hi")
    assert "No model chosen" in str(caught.value)
    assert not stub.generate_calls


def test_a_missing_model_says_how_to_get_it(stub):
    stub.generate_status = 404
    with pytest.raises(LlmError) as caught:
        Ollama(stub.url).generate("nope:1b", "hi")
    assert "ollama pull nope:1b" in str(caught.value)


def test_a_server_error_becomes_a_sentence_not_a_traceback(stub):
    stub.generate_status = 500
    with pytest.raises(LlmError) as caught:
        Ollama(stub.url).generate("m", "hi")
    assert "500" in str(caught.value)


def test_an_empty_answer_is_an_error_not_an_empty_prompt(stub):
    """Otherwise a blank reply silently wipes the user's description."""
    stub.answer = ""
    with pytest.raises(LlmError) as caught:
        Ollama(stub.url).generate("m", "hi")
    assert "nothing at all" in str(caught.value)


# -- freeing the graphics card --------------------------------------------

def test_freeing_waits_until_the_card_is_actually_empty(stub):
    """The one that matters.

    Asking Ollama to unload returns immediately whether or not anything
    happened, so an implementation that trusted the request would pass every
    other test here and still hand a full card to ComfyUI.
    """
    stub.loaded_for = 3               # still resident for the first 3 polls
    freed, note = free_vram(Ollama(stub.url), wait=10, poll=0.05)
    assert freed is True
    assert note == ""
    assert stub.ps_polls > 3, "it returned before the model had gone"
    assert stub.unload_calls, "it never actually asked for an unload"


def test_freeing_gives_up_cleanly_on_a_server_that_never_lets_go(stub):
    import time
    stub.never_frees = True
    started = time.monotonic()
    freed, note = free_vram(Ollama(stub.url), wait=1, poll=0.05)
    elapsed = time.monotonic() - started

    assert freed is False
    assert "still holding" in note
    # It has to give up, not block a render for ever.
    assert elapsed < 5


def test_freeing_an_empty_card_costs_one_poll(stub):
    freed, note = free_vram(Ollama(stub.url), wait=10, poll=0.05)
    assert (freed, note) == (True, "")
    assert not stub.unload_calls, "nothing was loaded, so nothing to unload"


def test_freeing_with_no_server_returns_at_once(dead_url):
    """The common machine has no Ollama, and must pay nothing for it."""
    import time
    started = time.monotonic()
    freed, note = free_vram(Ollama(dead_url), wait=30)
    assert (freed, note) == (True, "")
    assert time.monotonic() - started < 3


def test_everything_resident_is_unloaded_not_just_ours(stub):
    """Another program's model in the card is the same problem."""
    stub.loaded_for = 1
    free_vram(Ollama(stub.url), wait=5, poll=0.05)
    assert stub.unload_calls == ["qwen2.5vl:7b"]


def test_the_status_line_names_what_is_being_freed(stub):
    stub.loaded_for = 1
    said = []
    free_vram(Ollama(stub.url), wait=5, poll=0.05, on_status=said.append)
    assert said and "qwen2.5vl:7b" in said[0]


# -- the worker's step, end to end ----------------------------------------

def test_the_job_worker_frees_before_it_queues(stub, monkeypatch, tmp_path):
    """The ordering is the whole requirement.

    A render that queues first and frees afterwards would pass every other
    test in this file and still hand a full graphics card to ComfyUI.
    """
    import json as _json
    from app.jobs import FreeLlm, GenerationRequest, JobWorker
    from app.h3.timeline import Storyboard

    stub.loaded_for = 2
    order = []

    class FakeClient:
        def upload_file(self, path):
            return "x.png"

        def queue(self, graph):
            order.append(("queued", stub.ps_polls))
            raise RuntimeError("stop here - queueing is all we needed to see")

    with open("workflows/minimax_h3_director.api.json", encoding="utf-8") as f:
        graph = _json.load(f)

    worker = JobWorker.__new__(JobWorker)
    worker.client = FakeClient()
    worker.graph = graph
    worker.output_dir = tmp_path
    worker.timeout = 60
    worker._cancelled = False
    worker._prompt_id = None
    worker._notes = []
    import datetime as _dt
    worker._started = _dt.datetime.now()
    worker.request = GenerationRequest(
        storyboard=Storyboard(global_prompt="a fox"),
        free_llm=FreeLlm(url=stub.url, wait=5))

    emitted = []
    worker.progress = type("S", (), {"emit": lambda _s, *a: emitted.append(a)})()
    worker.preview = type("S", (), {"emit": lambda _s, *a: None})()

    with pytest.raises(RuntimeError):
        worker._generate()

    assert order, "it never got as far as queueing"
    _, polls_when_queued = order[0]
    assert polls_when_queued > stub.loaded_for, \
        "it queued while the language model was still resident"
    assert any("Freeing" in str(a) for a in emitted), \
        "the user was never told what the wait was for"


def test_a_render_still_goes_ahead_when_the_memory_will_not_free(stub, tmp_path):
    """Never refuse a render over this - warn and carry on."""
    import json as _json
    from app.jobs import FreeLlm, GenerationRequest, JobWorker
    from app.h3.timeline import Storyboard

    stub.never_frees = True
    queued = []

    class FakeClient:
        def queue(self, graph):
            queued.append(True)
            raise RuntimeError("far enough")

    with open("workflows/minimax_h3_director.api.json", encoding="utf-8") as f:
        graph = _json.load(f)

    worker = JobWorker.__new__(JobWorker)
    worker.client = FakeClient()
    worker.graph = graph
    worker.output_dir = tmp_path
    worker.timeout = 60
    worker._cancelled = False
    worker._prompt_id = None
    worker._notes = []
    import datetime as _dt
    worker._started = _dt.datetime.now()
    worker.request = GenerationRequest(
        storyboard=Storyboard(global_prompt="a fox"),
        free_llm=FreeLlm(url=stub.url, wait=1))
    worker.progress = type("S", (), {"emit": lambda _s, *a: None})()
    worker.preview = type("S", (), {"emit": lambda _s, *a: None})()

    with pytest.raises(RuntimeError):
        worker._generate()

    assert queued, "the render was refused because memory could not be freed"
    assert any("still holding" in n for n in worker._notes), \
        "it stayed quiet about handing over a full card"


# -- finding a helper without being asked ---------------------------------

@pytest.mark.parametrize("names, expected", [
    (["llama3.2:3b", "qwen2.5vl:7b"], "qwen2.5vl:7b"),
    (["llava:13b"], "llava:13b"),
    (["moondream:latest"], "moondream:latest"),
    (["gemma3:4b"], "gemma3:4b"),
    # No vision model: "" rather than a guess. A text-only model handed a
    # picture produces confident nonsense rather than an error, so guessing
    # wrong is worse than not guessing.
    (["llama3.2:3b", "mistral:7b"], ""),
    ([], ""),
])
def test_the_best_vision_model_is_picked_or_none_is(names, expected):
    from app.llm.ollama import best_vision_model
    assert best_vision_model(names) == expected


def test_detect_finds_a_vision_model(stub):
    from app.llm.ollama import detect
    assert detect(Ollama(stub.url)) == "qwen2.5vl:7b"


def test_detect_finds_nothing_when_only_text_models_are_there(stub):
    from app.llm.ollama import detect
    stub.tags = ["llama3.2:3b"]
    assert detect(Ollama(stub.url)) == ""


def test_detect_on_a_dead_port_is_quiet_and_quick(dead_url):
    """The common machine has no Ollama and must pay for this once, briefly."""
    import time
    from app.llm.ollama import detect
    started = time.monotonic()
    assert detect(Ollama(dead_url)) == ""
    assert time.monotonic() - started < 3
