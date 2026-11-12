"""Synthetic, counted HTTP wire tests; run in a source-only snapshot (no DB)."""
import asyncio
import base64
from dataclasses import FrozenInstanceError, replace
import json
import logging
import socket
import sqlite3
from typing import Any, cast

import httpx
import pytest

from app.services import decision_transport as dt

SECRET = "synthetic-key-sensitive"
PRIVATE = "synthetic-private-context"
QUESTION = dt.AssetDecisionQuestion("portrait", {"a1": "Smiling portrait", "none": "No fitting asset"})


@pytest.fixture(autouse=True)
def no_network_or_database(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("Network/DB forbidden in transport unit tests")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket.socket, "connect_ex", deny)
    monkeypatch.setattr(socket.socket, "sendto", deny)
    monkeypatch.setattr(sqlite3, "connect", deny)


def route(provider="google", **kwargs):
    return dt.DecisionRoute(provider, "vendor/full/model-slug", SECRET, **kwargs)


def answer(choice="a1", confidence=0.9) -> dict[str, Any]:
    return {"type": "choice", "choice": choice, "confidence": confidence,
            "probabilities": {"a1": 0.1, "none": 0.9} if choice == "none" else {"a1": 0.9, "none": 0.1}}


def envelope(provider="google", item: dict[str, Any] | None = None) -> dict[str, Any]:
    item = answer() if item is None else item
    if provider in ("google", "gemini", "openrouter"):
        return {"answers": {"portrait": item}}
    item = dict(item, name="portrait")
    if "probabilities" in item:
        item["probabilities"] = [{"value": k, "probability": v}
                                 for k, v in item["probabilities"].items()]
    return {"answers": [item]}


def invoke(response=None, *, provider="google", selected_route=None, questions=None,
           context=None, raw=None, status=200, error=None, images=None):
    requests = []
    def handler(request):
        requests.append(request)
        if error is not None:
            raise error
        if raw is not None:
            return httpx.Response(status, content=raw)
        return httpx.Response(status, json=envelope(provider) if response is None else response)
    async def run():
        return await dt.decide_assets(selected_route or route(provider),
            context={"scene": PRIVATE} if context is None else context,
            questions=[QUESTION] if questions is None else questions,
            images=images,
            _transport=httpx.MockTransport(handler))
    return run, requests


@pytest.mark.parametrize("provider,url", [
    ("google", "https://generativelanguage.googleapis.com/v1beta/decisions"),
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/decisions"),
    ("openai", "https://api.openai.com/v1/decisions"),
    ("openrouter", "https://openrouter.ai/api/alpha/decisions"),
    ("openai_compatible", "https://selected.example/v1/decisions"),
])
def test_exact_wire_contract(provider, url):
    selected = route(provider, base_url="https://selected.example/v1") if provider == "openai_compatible" else route(provider)
    run, requests = invoke(provider=provider, selected_route=selected)
    assert asyncio.run(run()) == {"portrait": "a1"}
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == url and request.method == "POST"
    assert request.extensions["timeout"] == dict(connect=8.0, read=8.0, write=8.0, pool=8.0)
    body = json.loads(request.content)
    assert body["model"] == "vendor/full/model-slug"
    if provider in ("google", "gemini"):
        assert request.headers["x-goog-api-key"] == SECRET
        assert "authorization" not in request.headers
        assert set(body) == {"model", "input", "questions"}
        assert len(body["input"]) == 1 and body["input"][0]["type"] == "text"
        assert json.loads(body["input"][0]["text"]) == {"scene": PRIVATE}
    else:
        assert request.headers["authorization"] == "Bearer " + SECRET
        assert "x-goog-api-key" not in request.headers
    if provider == "openrouter":
        assert set(body) == {"model", "state", "questions"}
        assert body["state"] == {"scene": PRIVATE}
    if provider in ("google", "gemini", "openrouter"):
        assert set(body["questions"]) == {"portrait"}
        question = body["questions"]["portrait"]
        assert set(question) == {"type", "instructions", "criteria"}
        assert question["criteria"] == QUESTION.candidates
    else:
        assert set(body) == {"model", "input", "questions"}
        assert json.loads(body["input"]) == {"scene": PRIVATE}
        assert len(body["questions"]) == 1
        question = body["questions"][0]
        assert set(question) == {"name", "type", "instructions", "choices"}
        assert question["name"] == "portrait"
        assert question["choices"] == [{"value": k, "description": v} for k, v in QUESTION.candidates.items()]
    assert question["type"] == "choice" and "portrait" in question["instructions"]


@pytest.mark.parametrize("provider", ["google", "openrouter", "openai"])
@pytest.mark.parametrize("item", [answer("none"), {"type": "refusal"}])
def test_abstention_is_not_unavailability(provider, item):
    run, requests = invoke(envelope(provider, item), provider=provider)
    assert asyncio.run(run()) == {"portrait": None}
    assert len(requests) == 1


@pytest.mark.parametrize("provider", ["google", "openrouter", "openai"])
def test_provider_confidence_is_not_a_shared_probability_threshold(provider):
    # TypeSafe's normalized confidence can be below .5 while p(selected) > .5.
    item = answer(confidence=0.2)
    item["probabilities"] = {"a1": 0.6, "none": 0.4}
    run, _ = invoke(envelope(provider, item), provider=provider)
    assert asyncio.run(run()) == {"portrait": "a1"}


@pytest.mark.parametrize("provider", ["google", "openrouter", "openai"])
def test_low_selected_probability_abstains_and_rounded_distribution_is_valid(provider):
    question = dt.AssetDecisionQuestion("portrait", {"a1": "A", "a2": "B", "none": "No suitable image"})
    item = answer(confidence=0.9)
    item["probabilities"] = {"a1": 0.49, "a2": 0.26, "none": 0.24}
    run, _ = invoke(envelope(provider, item), provider=provider, questions=[question])
    assert asyncio.run(run()) == {"portrait": None}


def test_nonmaximal_selected_option_is_not_an_authentic_choice():
    item = answer()
    item["probabilities"] = {"a1": 0.1, "none": 0.9}
    run, _ = invoke(envelope(item=item))
    with pytest.raises(dt.DecisionUnavailable):
        asyncio.run(run())


@pytest.mark.parametrize("provider", ["google", "openrouter", "openai"])
@pytest.mark.parametrize("change", [
    {"type": "score"}, {"type": None}, {"choice": "unknown"}, {"choice": None},
    {"choice": True}, {"confidence": "0.9"}, {"confidence": True},
    {"confidence": -0.01}, {"confidence": 1.01}, {"confidence": None},
    {"probabilities": {"a1": 0.9}}, {"probabilities": {"a1": 0.9, "bad": 0.1}},
    {"probabilities": {"a1": True, "none": 0}},
    {"probabilities": {"a1": 1.1, "none": -0.1}},
    {"probabilities": {"a1": 0.1, "none": 0.1}},
    {"type": "refusal"}, {"refusal": "private error"},
])
def test_invalid_member_rejects_whole_response(provider, change):
    item = answer()
    item.update(change)
    run, requests = invoke(envelope(provider, item), provider=provider)
    with pytest.raises(dt.DecisionUnavailable, match="^Asset decision unavailable$"):
        asyncio.run(run())
    assert len(requests) == 1


@pytest.mark.parametrize("raw", [
    b'not JSON', b'[]', b'null', b'{}', b'{"answers":null}',
    b'{"answers":{},"answers":{}}',
    b'{"answers":{"portrait":{},"portrait":{}}}',
    b'{"answers":{},"usage":NaN}', b'{"answers":{},"usage":Infinity}',
    b'{"answers":{},"usage":-Infinity}', b'{"answers":{},"usage":1e9999}',
    b'\xff',
])
def test_strict_json(raw):
    run, requests = invoke(raw=raw)
    with pytest.raises(dt.DecisionUnavailable):
        asyncio.run(run())
    assert len(requests) == 1


@pytest.mark.parametrize("provider", ["google", "openai", "openrouter"])
def test_full_coverage_and_unknown_question_ids(provider):
    for mode in ("missing", "extra", "unknown", "duplicate"):
        data = envelope(provider)
        if isinstance(data["answers"], dict):
            if mode == "duplicate":
                continue  # Duplicate object members covered by raw JSON test.
            if mode == "missing": data["answers"] = {}
            elif mode == "extra": data["answers"]["other"] = answer()
            else: data["answers"] = {"other": answer()}
        else:
            if mode == "missing": data["answers"] = []
            elif mode == "duplicate": data["answers"] *= 2
            elif mode == "extra": data["answers"].append(dict(data["answers"][0], name="other"))
            else: data["answers"][0]["name"] = "other"
        run, requests = invoke(data, provider=provider)
        with pytest.raises(dt.DecisionUnavailable): asyncio.run(run())
        assert len(requests) == 1


def test_duplicate_probability_alias_and_missing_fields():
    data = envelope("openai")
    data["answers"][0]["probabilities"].append({"value": "a1", "probability": 0.9})
    run, _ = invoke(data, provider="openai")
    with pytest.raises(dt.DecisionUnavailable): asyncio.run(run())
    for field in ("type", "choice", "confidence", "probabilities"):
        item = answer()
        del item[field]
        run, _ = invoke(envelope(item=item))
        with pytest.raises(dt.DecisionUnavailable): asyncio.run(run())


def test_multiple_answers_validate_atomically():
    second = dt.AssetDecisionQuestion("background", {"b1": "Forest", "none": "No background"})
    data = {"answers": {"portrait": {"type": "refusal"},
                        "background": dict(answer(), choice="b1", probabilities={"b1": 0.9, "none": 0.1})}}
    run, _ = invoke(data, questions=[QUESTION, second])
    assert asyncio.run(run()) == {"portrait": None, "background": "b1"}
    data["answers"]["background"]["confidence"] = "invalid"
    run, _ = invoke(data, questions=[QUESTION, second])
    with pytest.raises(dt.DecisionUnavailable): asyncio.run(run())


@pytest.mark.parametrize("status", [301, 302, 307, 400, 401, 429, 500, 503])
def test_http_failure_one_send_no_leaks(status, caplog):
    caplog.set_level(logging.DEBUG)
    run, requests = invoke(raw=(SECRET + PRIVATE).encode(), status=status)
    with pytest.raises(dt.DecisionUnavailable) as failure:
        asyncio.run(run())
    assert len(requests) == 1
    assert failure.value.__context__ is None and failure.value.__cause__ is None
    assert SECRET not in repr(failure.value) + caplog.text
    assert PRIVATE not in str(failure.value) + caplog.text


@pytest.mark.parametrize("error", [httpx.ReadTimeout(SECRET + PRIVATE), httpx.ConnectError(SECRET + PRIVATE)])
def test_network_failure_one_send_no_leaks(error, caplog):
    run, requests = invoke(error=error)
    with pytest.raises(dt.DecisionUnavailable) as failure: asyncio.run(run())
    assert len(requests) == 1 and failure.value.__context__ is None
    assert SECRET not in caplog.text + repr(failure.value)
    assert PRIVATE not in caplog.text + repr(failure.value)


def test_total_deadline_and_cancellation(monkeypatch):
    calls = []
    async def handler(request):
        calls.append(request)
        await asyncio.sleep(1)
        return httpx.Response(200, json=envelope())
    monkeypatch.setattr(dt, "_TIMEOUT", 0.01)
    with pytest.raises(dt.DecisionUnavailable):
        asyncio.run(dt.decide_assets(route(), context={}, questions=[QUESTION], _transport=httpx.MockTransport(handler)))
    assert len(calls) == 1
    async def cancelled(request):
        raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(dt.decide_assets(route(), context={}, questions=[QUESTION], _transport=httpx.MockTransport(cancelled)))


@pytest.mark.parametrize("selected", [
    route("unsupported"), route("openai_compatible"), replace(route(), api_key=""),
    route(base_url=""), route(base_url="https://user:password@example.com/v1"),
    route(base_url="https://example.com/v1?key=secret"), route(base_url="https://example.com/#secret"),
    route(base_url="http://remote.example/v1"), route(keyless_local=True),
    replace(route("openai_compatible", base_url="https://remote.example/v1", keyless_local=True), api_key=""),
])
def test_invalid_route_never_sends(selected, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-key")
    monkeypatch.setenv("OPENAI_API_BASE", "https://ambient.example/v1")
    run, requests = invoke(selected_route=selected)
    with pytest.raises(dt.DecisionUnavailable): asyncio.run(run())
    assert requests == []


def test_explicit_local_keyless_and_router_version_translation():
    selected = dt.DecisionRoute("openai_compatible", "full/model", "", "http://127.0.0.1:8129/v1", True)
    run, requests = invoke(provider="openai_compatible", selected_route=selected)
    assert asyncio.run(run()) == {"portrait": "a1"}
    assert "authorization" not in requests[0].headers
    for base in ("https://chosen.example/api", "https://chosen.example/api/v1", "https://chosen.example/api/alpha"):
        run, requests = invoke(provider="openrouter", selected_route=route("openrouter", base_url=base))
        asyncio.run(run())
        assert str(requests[0].url) == "https://chosen.example/api/alpha/decisions"


@pytest.mark.parametrize("questions", [[], [QUESTION, QUESTION],
    [dt.AssetDecisionQuestion("bad/id", QUESTION.candidates)],
    [dt.AssetDecisionQuestion("q", {"a1": "one"})],
    [dt.AssetDecisionQuestion("q", {"a1": "one", "none": ""})],
    [dt.AssetDecisionQuestion("q", {"bad/alias": "one", "none": "none"})],
])
def test_invalid_questions_never_send(questions):
    run, requests = invoke(questions=questions)
    with pytest.raises(dt.DecisionUnavailable): asyncio.run(run())
    assert requests == []


@pytest.mark.parametrize("context", [{"x": float("nan")}, {"x": float("inf")}, {1: "nonstring key"}, {"x": object()}])
def test_invalid_context_never_sends(context):
    run, requests = invoke(context=context)
    with pytest.raises(dt.DecisionUnavailable): asyncio.run(run())
    assert requests == []


def test_owned_client_and_transport_policy(monkeypatch):
    client_calls, transport_calls, sent = [], [], []
    real_client = httpx.AsyncClient
    def transport(**kwargs):
        transport_calls.append(kwargs)
        def handler(request):
            sent.append(request)
            return httpx.Response(200, json=envelope())
        return httpx.MockTransport(handler)
    clients = []
    def client(**kwargs):
        client_calls.append(kwargs)
        instance = real_client(**kwargs)
        clients.append(instance)
        return instance
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", transport)
    monkeypatch.setattr(httpx, "AsyncClient", client)
    monkeypatch.setenv("HTTP_PROXY", "http://must-not-be-used.invalid")
    assert asyncio.run(dt.decide_assets(route(), context={}, questions=[QUESTION])) == {"portrait": "a1"}
    assert transport_calls == [{"retries": 0, "trust_env": False}]
    assert len(client_calls) == len(sent) == 1
    assert client_calls[0]["trust_env"] is False and client_calls[0]["follow_redirects"] is False
    assert client_calls[0]["timeout"] <= 8 and clients[0].is_closed


def test_frozen_route_and_secret_repr():
    with pytest.raises(FrozenInstanceError): setattr(route(), "model", "other")
    with pytest.raises(FrozenInstanceError): setattr(QUESTION, "id", "other")
    assert SECRET not in repr(route())


def test_redirect_location_is_never_followed():
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://unselected.example/decisions"})
    with pytest.raises(dt.DecisionUnavailable):
        asyncio.run(dt.decide_assets(route(), context={}, questions=[QUESTION],
                                    _transport=httpx.MockTransport(handler)))
    assert len(requests) == 1
    assert requests[0].url.host == "generativelanguage.googleapis.com"


def test_response_size_bound(monkeypatch):
    monkeypatch.setattr(dt, "_MAX_RESPONSE_BYTES", 20)
    run, requests = invoke()
    with pytest.raises(dt.DecisionUnavailable): asyncio.run(run())
    assert len(requests) == 1


@pytest.mark.parametrize("provider", ["google", "openrouter", "openai"])
@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity", "1e9999"])
def test_nonfinite_on_otherwise_valid_wire_response(provider, token):
    raw = json.dumps(envelope(provider)).replace('"confidence": 0.9', '"confidence": ' + token).encode()
    run, requests = invoke(raw=raw, provider=provider)
    with pytest.raises(dt.DecisionUnavailable): asyncio.run(run())
    assert len(requests) == 1


def test_duplicate_probability_json_key_rejected():
    raw = json.dumps(envelope()).replace('"none": 0.1', '"none": 0.1, "a1": 0.9').encode()
    run, requests = invoke(raw=raw)
    with pytest.raises(dt.DecisionUnavailable): asyncio.run(run())
    assert len(requests) == 1


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
# Header-only synthetic fixtures: transport is NOT the storage image decoder.
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00synthetic-image-private"
WEBP = b"RIFF" + (16).to_bytes(4, "little") + b"WEBPVP8 " + (4).to_bytes(4, "little") + b"test"
SAMPLES = [("image/png", PNG), ("image/jpeg", JPEG), ("image/webp", WEBP)]


def vision_route(provider="google", **kwargs):
    return route(provider, input_modalities=("text", "image"),
                 image_mime_types=("image/png", "image/jpeg", "image/webp"), **kwargs)


@pytest.mark.parametrize("provider", ["google", "gemini", "openai", "openrouter", "openai_compatible"])
@pytest.mark.parametrize("mime,data", SAMPLES)
def test_vision_exact_wire(provider, mime, data):
    selected = vision_route(provider, **({"base_url": "https://selected.example/v1"}
                                        if provider == "openai_compatible" else {}))
    images = [dt.DecisionImage("a1", data, mime), dt.DecisionImage("b2", PNG, "image/png")]
    run, requests = invoke(provider=provider, selected_route=selected, images=images)
    assert asyncio.run(run()) == {"portrait": "a1"}
    assert len(requests) == 1 and requests[0].method == "POST"
    body = json.loads(requests[0].content)
    original = json.loads(dt._request(selected, {"scene": PRIVATE}, [QUESTION])[4])
    assert body["questions"] == original["questions"]
    assert body["model"] == selected.model and set(body) == set(original)
    if provider in ("google", "gemini"):
        parts = body["input"]
        assert parts[0]["type"] == "text"
        assert json.loads(parts[0]["text"]) == {"scene": PRIVATE}
    elif provider == "openrouter":
        parts = body["state"]
        assert json.loads(parts[0]) == {"scene": PRIVATE}
    else:
        assert len(body["input"]) == 1 and body["input"][0]["role"] == "user"
        assert set(body["input"][0]) == {"role", "content"}
        parts = body["input"][0]["content"]
        assert parts[0]["type"] == "input_text"
        assert json.loads(parts[0]["text"]) == {"scene": PRIVATE}
    assert len(parts) == 5
    for index, image in enumerate(images):
        label, part = parts[1 + index * 2:3 + index * 2]
        if provider in ("google", "gemini"):
            assert label == {"type": "text", "text": f"Asset ID: {image.id}"}
            assert set(part) == {"type", "data", "mime_type", "resolution"}
            assert part["type"] == "image" and part["resolution"] == "low"
            assert part["mime_type"] == image.mime_type
            encoded = part["data"]
        else:
            if provider == "openrouter":
                assert label == f"Asset ID: {image.id}"
                assert set(part) == {"type", "image_url"} and part["type"] == "image_url"
                assert set(part["image_url"]) == {"url", "detail"}
                assert part["image_url"]["detail"] == "low"
                uri = part["image_url"]["url"]
            else:
                assert label == {"type": "input_text", "text": f"Asset ID: {image.id}"}
                assert set(part) == {"type", "image_url", "detail"}
                assert part["type"] == "input_image" and part["detail"] == "low"
                uri = part["image_url"]
            prefix, encoded = uri.split(",", 1)
            assert prefix == f"data:{image.mime_type};base64"
        assert base64.b64decode(encoded, validate=True) == image.data


@pytest.mark.parametrize("provider", ["google", "openai", "openrouter"])
def test_zero_images_preserves_text(provider):
    for selected in (route(provider), vision_route(provider)):
        for images in (None, []):
            run, requests = invoke(provider=provider, selected_route=selected, images=images)
            assert asyncio.run(run()) == {"portrait": "a1"} and len(requests) == 1
            assert requests[0].content == dt._request(route(provider), {"scene": PRIVATE}, [QUESTION])[4]


@pytest.mark.parametrize("selected", [route(), replace(vision_route(), input_modalities=("text",)),
    replace(vision_route(), input_modalities=None), replace(vision_route(), image_mime_types=()),
    replace(vision_route(), image_mime_types=None), replace(vision_route(), image_mime_types=("image/jpeg",))])
def test_unknown_image_capability_zero_post(selected):
    run, requests = invoke(selected_route=selected, images=[dt.DecisionImage("a1", PNG, "image/png")])
    with pytest.raises(dt.DecisionUnavailable): asyncio.run(run())
    assert requests == []


@pytest.mark.parametrize("images", [
    [dt.DecisionImage("a1", PNG, "image/jpeg")],
    [dt.DecisionImage("a1", b"bad-magic-private", "image/png")],
    [dt.DecisionImage("a1", b"", "image/png")],
    [dt.DecisionImage("a1", PNG[:8], "image/png")],
    [dt.DecisionImage("a1", WEBP[:-1], "image/webp")],
    [dt.DecisionImage("a1", b"GIF89a" + b"x" * 30, "image/gif")],
    [dt.DecisionImage("a1", cast(Any, "https://private.example/original.png"), "image/png")],
    [dt.DecisionImage("a1", cast(Any, bytearray(PNG)), "image/png")],
    [dt.DecisionImage("none", PNG, "image/png")],
    [dt.DecisionImage("bad/alias", PNG, "image/png")],
    [dt.DecisionImage("", PNG, "image/png")],
    [dt.DecisionImage("x" * 65, PNG, "image/png")],
    [dt.DecisionImage("a1", PNG, "image/png")] * 2,
    [dt.DecisionImage(str(i), PNG, "image/png") for i in range(9)],
    [dt.DecisionImage("a1", PNG + b"x" * (256 * 1024), "image/png")],
    [dt.DecisionImage(str(i), PNG + b"x" * (256 * 1024 - len(PNG)), "image/png") for i in range(5)],
    (dt.DecisionImage("a1", PNG, "image/png"),), ["not-an-image"],
])
def test_bad_images_zero_post_before_encoding(images, monkeypatch):
    def forbidden(*args): pytest.fail("invalid images reached base64 allocation")
    monkeypatch.setattr(dt.base64, "b64encode", forbidden)
    run, requests = invoke(selected_route=vision_route(), images=images)
    with pytest.raises(dt.DecisionUnavailable) as failure: asyncio.run(run())
    assert requests == [] and failure.value.__context__ is None


@pytest.mark.parametrize("mime,data", SAMPLES)
def test_jev_router_exact_mime_capability(mime, data):
    selected = replace(vision_route("openrouter"), model="typesafe/jev-1.14",
                       image_mime_types=("image/png", "image/jpeg"))
    run, requests = invoke(provider="openrouter", selected_route=selected,
                           images=[dt.DecisionImage("a1", data, mime)])
    if mime == "image/webp":
        with pytest.raises(dt.DecisionUnavailable): asyncio.run(run())
        assert requests == []
    else:
        assert asyncio.run(run()) == {"portrait": "a1"} and len(requests) == 1


@pytest.mark.parametrize("provider", ["google", "openai", "openrouter"])
def test_maximum_images_total_and_wire_bytes(provider):
    data = PNG + b"x" * (128 * 1024 - len(PNG))
    images = [dt.DecisionImage(f"a{i}", data, "image/png") for i in range(8)]
    run, requests = invoke(provider=provider, selected_route=vision_route(provider), images=images,
                           context={"scene": "x" * 200_000})
    assert asyncio.run(run()) == {"portrait": "a1"} and len(requests) == 1
    assert sum(len(image.data) for image in images) == dt._MAX_TOTAL_IMAGE_BYTES
    assert dt._MAX_TOTAL_IMAGE_BYTES < len(requests[0].content) <= dt._MAX_REQUEST_BYTES


@pytest.mark.parametrize("provider", ["google", "openai", "openrouter"])
def test_text_and_encoded_wire_bounds_zero_post(provider, monkeypatch):
    run, requests = invoke(provider=provider, context={"scene": "x" * dt._MAX_TEXT_BYTES})
    with pytest.raises(dt.DecisionUnavailable): asyncio.run(run())
    assert requests == []
    monkeypatch.setattr(dt, "_MAX_REQUEST_BYTES", 100)
    run, requests = invoke(provider=provider, selected_route=vision_route(provider),
                           images=[dt.DecisionImage("a1", PNG, "image/png")])
    with pytest.raises(dt.DecisionUnavailable): asyncio.run(run())
    assert requests == []


@pytest.mark.parametrize("provider", ["google", "openai", "openrouter"])
@pytest.mark.parametrize("item", [answer("none"), {"type": "refusal"}])
def test_vision_abstention_no_fallback(provider, item):
    run, requests = invoke(envelope(provider, item), provider=provider,
                           selected_route=vision_route(provider), images=[dt.DecisionImage("a1", PNG, "image/png")])
    assert asyncio.run(run()) == {"portrait": None} and len(requests) == 1


def test_image_repr_and_error_privacy(caplog):
    image = dt.DecisionImage("a1", JPEG, "image/jpeg")
    with pytest.raises(FrozenInstanceError): setattr(image, "data", b"replacement")
    assert "synthetic-image-private" not in repr(image)
    caplog.set_level(logging.DEBUG)
    encoded = base64.b64encode(image.data).decode()
    run, requests = invoke(selected_route=vision_route(), images=[image],
                           error=httpx.ReadError(SECRET + encoded + "synthetic-image-private"))
    with pytest.raises(dt.DecisionUnavailable) as failure: asyncio.run(run())
    assert len(requests) == 1
    assert failure.value.__context__ is None and failure.value.__cause__ is None
    for sentinel in (SECRET, encoded, "synthetic-image-private"):
        assert sentinel not in caplog.text + repr(failure.value)


@pytest.mark.parametrize("mode", ["cancel", "timeout"])
def test_vision_cancel_and_timeout_close_transport(mode, monkeypatch):
    calls, closed = [], []
    async def handler(request):
        calls.append(request)
        if mode == "cancel": raise asyncio.CancelledError
        await asyncio.sleep(1)
        return httpx.Response(200, json=envelope())
    class TrackedTransport(httpx.MockTransport):
        async def aclose(self):
            closed.append(True)
            await super().aclose()
    monkeypatch.setattr(dt, "_TIMEOUT", 0.01)
    with pytest.raises(asyncio.CancelledError if mode == "cancel" else dt.DecisionUnavailable):
        asyncio.run(dt.decide_assets(vision_route(), context={}, questions=[QUESTION],
                    images=[dt.DecisionImage("a1", PNG, "image/png")], _transport=TrackedTransport(handler)))
    assert len(calls) == 1 and closed == [True]
