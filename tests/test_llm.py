from applypilot import llm


def test_parse_json_response_accepts_fence_and_preamble() -> None:
    raw = 'Here is the result:\n```json\n{"full_description":"A role", "application_url":null}\n```'

    assert llm.parse_json_response(raw) == {"full_description": "A role", "application_url": None}


def test_parse_json_response_uses_content_after_thinking_block() -> None:
    raw = '<think>internal reasoning</think>\n{"statement":"Evidence-led statement"}'

    assert llm.parse_json_response(raw) == {"statement": "Evidence-led statement"}


def test_structured_json_repairs_invalid_model_response(monkeypatch) -> None:
    responses = ["not json", '{"statement":"Repaired statement"}']
    calls: list[list[dict]] = []

    def fake_chat_json(messages, **kwargs):
        calls.append(messages)
        return responses.pop(0)

    monkeypatch.setattr(llm, "chat_json", fake_chat_json)

    result = llm.structured_json(
        [{"role": "user", "content": "Return a statement."}],
        required_keys=("statement",),
        task_name="test statement",
    )

    assert result == {"statement": "Repaired statement"}
    assert len(calls) == 2
    assert "previous response did not satisfy" in calls[1][-1]["content"]
