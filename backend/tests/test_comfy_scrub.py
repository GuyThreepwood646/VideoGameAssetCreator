"""ComfyUI's validation errors must never carry a model folder's file list into a job error."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from vgac import comfy

REPLY = {
    "error": {"type": "prompt_outputs_failed_validation", "message": "Prompt outputs failed validation", "details": "", "extra_info": {}},
    "node_errors": {
        "7": {
            "errors": [{
                "type": "value_not_in_list",
                "message": "Value not in list",
                "details": "lora_name: 'VGAC/mine/gone.safetensors' not in ['private one.safetensors', 'private [two].safetensors', 'VGAC/a.safetensors']",
                "extra_info": {"input_name": "lora_name", "input_config": [["private one.safetensors", "private [two].safetensors"], {}],
                               "received_value": "VGAC/mine/gone.safetensors"},
            }],
            "dependent_outputs": ["9"],
            "class_type": "LoraLoaderModelOnly",
        }
    },
}


def test_scrub_keeps_the_message_and_drops_the_list():
    out = comfy.scrub_node_errors(REPLY)
    text = json.dumps(out)
    assert "private" not in text and "input_config" not in text
    e = out["node_errors"]["7"]["errors"][0]
    assert e["details"] == "lora_name: 'VGAC/mine/gone.safetensors' is not one of the installed files"
    assert e["extra_info"] == {"input_name": "lora_name", "received_value": "VGAC/mine/gone.safetensors"}
    assert REPLY["node_errors"]["7"]["errors"][0]["extra_info"]["input_config"]  # the input is not changed


def test_scrub_text_fallback():
    raw = "Value not in list: lora_name: 'x.safetensors' not in ['private one.safetensors', 'b.safetensors'], next"
    out = comfy.scrub_error_text(raw)
    assert "private" not in out and "'x.safetensors'" in out and out.endswith(", next")


@pytest.mark.parametrize("status", [400, 200])
def test_queue_prompt_error_has_no_file_list(status, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        body = REPLY if status == 400 else {"prompt_id": None, "node_errors": REPLY["node_errors"]}
        return httpx.Response(status, json=body)

    client = comfy.ComfyClient("http://127.0.0.1:9", "a")
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:9")
    monkeypatch.setattr(client, "_client", lambda: http)

    async def run():
        with pytest.raises(comfy.ComfyError) as ei:
            await client.queue_prompt({"1": {}})
        await http.aclose()
        return ei.value

    err = asyncio.run(run())
    assert "private" not in str(err) and "private" not in json.dumps(err.node_errors)
    assert "gone.safetensors" in str(err)
