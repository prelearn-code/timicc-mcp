import json

import pytest

from timicc_worker import core


def candidate(patch=""):
    return {"summary": "ok", "patch": patch, "tests": [], "assumptions": [], "risks": []}


def response(value, *, direct=False):
    text = json.dumps(value)
    result = {"status": "completed", "model": "gpt-5.6-sol", "usage": {"total_tokens": 10}}
    if direct:
        result["output_text"] = text
    else:
        result["output"] = [{"type": "reasoning"}, {"type": "message", "content": [{"type": "output_text", "text": text}]}]
    return result


def test_generate_patch_uses_responses_payload(monkeypatch):
    captured = {}
    def fake_post(url, api_key, payload, timeout, **kwargs):
        captured.update(url=url, api_key=api_key, payload=payload, timeout=timeout)
        return response(candidate(), direct=True)
    monkeypatch.setattr(core, "_post_stream", fake_post)
    result = core.generate_patch("change it", "FILE a.py\npass", ["a.py"], api_key="secret")
    assert captured["url"] == "https://timicc.com/responses"
    assert captured["payload"]["text"]["format"] == {"type": "json_object"}
    assert captured["payload"]["store"] is False
    assert captured["timeout"] == 1_500.0
    assert result["model"] == "gpt-5.6-sol"
    assert result["worker"] == "timicc"
    assert len(result["patch_sha256"]) == 64


def test_nested_responses_output_and_valid_patch(monkeypatch):
    patch = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-pass\n+print('ok')\n"
    monkeypatch.setattr(core, "_post_stream", lambda *args, **kwargs: response(candidate(patch)))
    result = core.generate_patch("change it", "FILE a.py\npass", ["a.py"], api_key="secret")
    assert result["patch"] == patch
    assert result["changed_files"] == ["a.py"]


def test_rejects_mismatched_marker_path(monkeypatch):
    patch = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/b.py\n@@ -1 +1 @@\n-pass\n+ok\n"
    monkeypatch.setattr(core, "_post_stream", lambda *args, **kwargs: response(candidate(patch)))
    with pytest.raises(core.TimiccError, match="outside allowed_paths"):
        core.generate_patch("change it", "FILE a.py\npass", ["a.py"], api_key="secret")


def test_rejects_patch_outside_allowed_paths(monkeypatch):
    patch = "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -0,0 +1 @@\n+bad\n"
    monkeypatch.setattr(core, "_post_stream", lambda *args, **kwargs: response(candidate(patch)))
    with pytest.raises(core.TimiccError, match="outside allowed_paths"):
        core.generate_patch("change it", "FILE a.py\npass", ["a.py"], api_key="secret")


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "C:\\secret"])
def test_rejects_unsafe_allowed_paths(path):
    with pytest.raises(ValueError, match="unsafe repository path"):
        core.generate_patch("change it", "context", [path], api_key="secret")


def test_rejects_unconfigured_model():
    with pytest.raises(ValueError, match="unsupported model"):
        core.generate_patch("change it", "context", ["a.py"], model="gpt-9", api_key="secret")


def test_incomplete_response_is_rejected(monkeypatch):
    monkeypatch.setattr(core, "_post_stream", lambda *args, **kwargs: {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}})
    with pytest.raises(core.TimiccError, match="did not complete"):
        core.generate_patch("change it", "context", ["a.py"], api_key="secret")


class FakeStreamResponse:
    status = 200

    def __init__(self, lines):
        self.lines = [line.encode() for line in lines]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(self.lines)


def test_post_stream_collects_deltas(monkeypatch):
    completed = {"status": "completed", "model": "gpt-5.6-sol", "usage": {"total_tokens": 3}}
    lines = [
        'data: {"type":"response.output_text.delta","delta":"{\\"summary\\":\\"ok\\","}\n',
        'data: {"type":"response.output_text.delta","delta":"\\"patch\\":\\"\\",\\"tests\\":[],\\"assumptions\\":[],\\"risks\\":[]}"}\n',
        "data: " + json.dumps({"type": "response.completed", "response": completed}) + "\n",
    ]
    monkeypatch.setattr(core.urllib.request, "urlopen", lambda *args, **kwargs: FakeStreamResponse(lines))
    result = core._post_stream("https://example.test/responses", "secret", {}, 30)
    assert json.loads(result["output_text"])["summary"] == "ok"
    assert result["status"] == "completed"


def test_post_stream_collects_completion_with_progress_callback(monkeypatch):
    completed = {"status": "completed", "output_text": json.dumps(candidate())}
    lines = ["data: " + json.dumps({"type": "response.completed", "response": completed}) + "\n"]
    monkeypatch.setattr(core.urllib.request, "urlopen", lambda *args, **kwargs: FakeStreamResponse(lines))
    events = []
    result = core._post_stream(
        "https://example.test/responses",
        "secret",
        {},
        30,
        progress=lambda *args: events.append(args),
    )
    assert result["status"] == "completed"
    assert events[0][0] == "response.completed"


def test_post_stream_rejects_missing_completion(monkeypatch):
    monkeypatch.setattr(
        core.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeStreamResponse(['data: {"type":"response.in_progress"}\n']),
    )
    with pytest.raises(core.TimiccError, match="without response.completed"):
        core._post_stream("https://example.test/responses", "secret", {}, 30)
