"""Async ComfyUI HTTP client.

Endpoints: POST /prompt, GET /history/{id}, GET /view, POST /upload/image, GET /system_stats,
POST /interrupt. Ported from bakeoff/comfy.py: a /prompt reply with node_errors is a failure,
and a history entry whose status is "error" raises ComfyError carrying the status payload so the
caller can inspect the exception type (SkinToken raises AssertionError for rigid meshes).
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import shutil
import time
from pathlib import Path
from typing import Any, Callable

import httpx


class ComfyError(RuntimeError):
    def __init__(self, message: str, status: dict[str, Any] | None = None, node_errors: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status or {}
        self.node_errors = node_errors or {}

    @property
    def exception_types(self) -> list[str]:
        """Exception type names reported in execution_error messages, if any."""
        out = []
        for msg in self.status.get("messages", []):
            if isinstance(msg, (list, tuple)) and len(msg) == 2 and isinstance(msg[1], dict):
                t = msg[1].get("exception_type")
                if t:
                    out.append(str(t))
        return out

    @property
    def exception_messages(self) -> list[str]:
        out = []
        for msg in self.status.get("messages", []):
            if isinstance(msg, (list, tuple)) and len(msg) == 2 and isinstance(msg[1], dict):
                m = msg[1].get("exception_message")
                if m:
                    out.append(str(m))
        return out

    def is_assertion(self) -> bool:
        return any(t.endswith("AssertionError") for t in self.exception_types)


log = logging.getLogger(__name__)


class ComfyClient:
    """One long-lived httpx.AsyncClient per instance (created on first use, closed by aclose(), which JobQueue.stop
    calls), so the once-a-second /history polls reuse a connection. Each request passes its own timeout."""

    def __init__(self, base_url: str, name: str = "", timeout: float = 30.0, input_dir: Path | str | None = None,
                 output_dir: Path | str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.name = name
        self.timeout = timeout
        self.input_dir = Path(input_dir) if input_dir else None
        self.output_dir = Path(output_dir) if output_dir else None
        self._http: httpx.AsyncClient | None = None
        self._http_loop: asyncio.AbstractEventLoop | None = None
        self._vram_cache: tuple[float, dict[str, Any]] | None = None

    def _client(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        # A client's connections belong to the loop that opened them; a different loop (a test, a one-off asyncio.run)
        # gets its own client instead of reusing pooled connections from a dead loop.
        if self._http is None or self._http.is_closed or self._http_loop is not loop:
            self._http = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)
            self._http_loop = loop
        return self._http

    async def aclose(self) -> None:
        http, self._http, self._http_loop = self._http, None, None
        if http is not None and not http.is_closed:
            try:
                await http.aclose()
            except Exception as e:  # noqa: BLE001  (a client from another, closed loop cannot be closed cleanly)
                log.debug("instance %s: closing the HTTP client failed: %s", self.name, e)

    async def system_stats(self) -> dict[str, Any]:
        """Raw /system_stats payload (devices with vram_total, vram_free, and so on)."""
        r = await self._client().get("/system_stats", timeout=3.0)
        r.raise_for_status()
        return r.json()

    def forget_cache(self) -> None:
        """Drop the cached /system_stats answer (the instance was just started or stopped)."""
        self._vram_cache = None

    async def cached_vram_summary(self, max_age: float = 5.0) -> dict[str, Any]:
        """vram_summary(), reused for `max_age` seconds: /api/health is polled every 2 s."""
        now = time.monotonic()
        if self._vram_cache is not None and now - self._vram_cache[0] < max_age:
            return self._vram_cache[1]
        summary = await self.vram_summary()
        self._vram_cache = (time.monotonic(), summary)
        return summary

    async def vram_summary(self) -> dict[str, Any]:
        """Reachability plus VRAM per device. Never raises."""
        try:
            stats = await self.system_stats()
        except Exception as e:  # connection refused, timeout, bad JSON
            return {"reachable": False, "error": str(e), "devices": []}
        devices = []
        for d in stats.get("devices", []):
            devices.append(
                {
                    "name": d.get("name"),
                    "type": d.get("type"),
                    "vram_total": d.get("vram_total"),
                    "vram_free": d.get("vram_free"),
                    "torch_vram_total": d.get("torch_vram_total"),
                    "torch_vram_free": d.get("torch_vram_free"),
                }
            )
        system = stats.get("system", {})
        return {
            "reachable": True,
            "comfyui_version": system.get("comfyui_version"),
            "python_version": system.get("python_version"),
            "devices": devices,
        }

    async def queue_prompt(self, graph: dict[str, Any], client_id: str | None = None) -> str:
        body: dict[str, Any] = {"prompt": graph}
        if client_id:
            body["client_id"] = client_id
        r = await self._client().post("/prompt", json=body)
        if r.status_code >= 400:
            try:
                data = r.json()
            except Exception:
                data = {}
            raise ComfyError(f"/prompt returned {r.status_code}: {r.text[:2000]}", node_errors=data.get("node_errors") or {})
        data = r.json()
        if data.get("node_errors"):
            raise ComfyError("node_errors: " + str(data["node_errors"])[:4000], node_errors=data["node_errors"])
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise ComfyError(f"/prompt did not return a prompt_id: {data}")
        return prompt_id

    async def history(self, prompt_id: str) -> dict[str, Any]:
        r = await self._client().get(f"/history/{prompt_id}")
        r.raise_for_status()
        return r.json()

    async def queue_state(self) -> dict[str, Any]:
        r = await self._client().get("/queue", timeout=5.0)
        r.raise_for_status()
        return r.json()

    async def wait(self, prompt_id: str, poll_seconds: float = 1.0, timeout: float | None = None,
                   on_poll: Callable[[float], None] | None = None) -> dict[str, Any]:
        """Poll /history until the prompt completes. Returns the history entry.

        Raises ComfyError if the prompt ended in error or the timeout elapsed.
        """
        t0 = time.monotonic()
        while True:
            hist = await self.history(prompt_id)
            if prompt_id in hist:
                entry = hist[prompt_id]
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise ComfyError(f"prompt {prompt_id} failed: {str(status)[:3000]}", status=status)
                if status.get("completed", True) or entry.get("outputs"):
                    entry["elapsed_seconds"] = round(time.monotonic() - t0, 1)
                    return entry
            elapsed = time.monotonic() - t0
            if on_poll:
                on_poll(elapsed)
            if timeout is not None and elapsed > timeout:
                raise ComfyError(f"prompt {prompt_id} timed out after {timeout}s")
            await asyncio.sleep(poll_seconds)

    async def run(self, graph: dict[str, Any], poll_seconds: float = 1.0, timeout: float | None = None,
                  on_poll: Callable[[float], None] | None = None) -> dict[str, Any]:
        """Queue a graph and wait for it. Returns the history entry (with "outputs" and "elapsed_seconds")."""
        prompt_id = await self.queue_prompt(graph)
        entry = await self.wait(prompt_id, poll_seconds=poll_seconds, timeout=timeout, on_poll=on_poll)
        entry["prompt_id"] = prompt_id
        return entry

    @staticmethod
    def outputs_from_history(entry: dict[str, Any]) -> list[dict[str, Any]]:
        """Flatten every output file reference (images, gltf, mesh, files) from a history entry."""
        files: list[dict[str, Any]] = []
        for node_id, out in entry.get("outputs", {}).items():
            for key, items in out.items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict) and "filename" in item:
                        files.append({"node": node_id, "output_key": key, **item})
        return files

    @staticmethod
    def images_of(entry: dict[str, Any], node: str | None = None) -> list[dict[str, Any]]:
        outs = entry.get("outputs", {})
        if node is not None:
            outs = {node: outs.get(node, {})}
        return [i for o in outs.values() for i in o.get("images", [])]

    @staticmethod
    def files_of(entry: dict[str, Any], key: str) -> list[Any]:
        """Collect output entries under an arbitrary key such as '3d', 'result' or 'text'."""
        return [i for o in entry.get("outputs", {}).values() for i in o.get(key, [])]

    @staticmethod
    def texts_of(entry: dict[str, Any], node: str) -> list[str]:
        out = entry.get("outputs", {}).get(node, {})
        return [str(t) for t in out.get("text", [])]

    async def download_output(self, item: dict[str, Any] | str, dest_path: Path | str) -> Path:
        if isinstance(item, str):
            item = {"filename": Path(item).name, "subfolder": Path(item).parent.as_posix() if Path(item).parent != Path(".") else "", "type": "output"}
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        params = {"filename": item["filename"], "subfolder": item.get("subfolder") or "", "type": item.get("type") or "output"}
        async with self._client().stream("GET", "/view", params=params, timeout=600.0) as r:
            if r.status_code >= 400:
                raise ComfyError(f"/view returned {r.status_code} for {item['filename']}")
            with open(dest, "wb") as f:
                async for chunk in r.aiter_bytes():
                    f.write(chunk)
        return dest

    async def upload_image(self, path: Path | str, subfolder: str = "", overwrite: bool = True) -> dict[str, Any]:
        """POST /upload/image. Returns {"name", "subfolder", "type"} as ComfyUI reports them."""
        p = Path(path)
        mime = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        data = {"overwrite": "true" if overwrite else "false"}
        if subfolder:
            data["subfolder"] = subfolder
        with open(p, "rb") as f:
            files = {"image": (p.name, f, mime)}
            r = await self._client().post("/upload/image", data=data, files=files, timeout=120.0)
        if r.status_code >= 400:
            raise ComfyError(f"/upload/image returned {r.status_code}: {r.text[:500]}")
        return r.json()

    async def stage_input(self, src: Path | str, name: str | None = None) -> str:
        """Make a local file available to LoadImage / VGACLoadTrimesh and return the name they expect.

        Copies into the shared input directory when configured (works for any file type, as the
        bakeoff did), otherwise uploads through /upload/image (images only).
        """
        src_p = Path(src)
        name = name or ("vgac_" + src_p.name)
        if self.input_dir is not None:
            self.input_dir.mkdir(parents=True, exist_ok=True)
            dest = self.input_dir / name
            await asyncio.to_thread(shutil.copy, src_p, dest)
            return name
        info = await self.upload_image(src_p)
        sub = info.get("subfolder") or ""
        return (sub + "/" + info["name"]) if sub else info["name"]

    async def interrupt(self) -> None:
        try:
            await self._client().post("/interrupt", timeout=5.0)
        except Exception as e:  # noqa: BLE001
            log.warning("instance %s: interrupt failed: %s", self.name, e)

    async def free(self) -> bool:
        """POST /free {"unload_models": true, "free_memory": true}: ComfyUI drops its loaded models and releases the
        card's memory (after the prompt it is running, if any). Used when a card is turned off in the app. Never
        raises (the instance may be down); returns whether ComfyUI accepted it."""
        try:
            r = await self._client().post("/free", json={"unload_models": True, "free_memory": True}, timeout=10.0)
            r.raise_for_status()
            self._vram_cache = None  # the next health read shows the freed memory
            return True
        except Exception as e:  # noqa: BLE001
            log.info("instance %s: /free failed (it may be down): %s", self.name, e)
            return False

    def last_vram_summary(self) -> dict[str, Any] | None:
        """The last cached vram_summary(), however old, without a request (None before the first)."""
        return self._vram_cache[1] if self._vram_cache is not None else None
