"""ComfyUI backend for ACE-Step 1.5: find the audio workflow, map nodes by class_type, inject settings, submit, wait, fetch the file.

Blocking functions on purpose (urllib); the UI runs them with run_in_executor so the window never waits.
Cancel only touches the job this program submitted: it is removed from the queue if it still waits, and /interrupt is sent only when
that same job is the one running (an unrelated running job is never interrupted).
"""
from __future__ import annotations

import copy
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .model import SongSettings

TEXT_CLS = "TextEncodeAceStepAudio1.5"
LATENT_CLS = "EmptyAceStep1.5LatentAudio"
SAMPLER_CLS = "KSampler"
SAVE_CLS = "SaveAudioAdvanced"
_REQUIRED = {"text": TEXT_CLS, "latent": LATENT_CLS, "sampler": SAMPLER_CLS, "save": SAVE_CLS}

CLIENT_ID = uuid.uuid4().hex                      # this program run; jobs are tracked by prompt id


class ComfyError(RuntimeError):
    pass


def _url(host: str, path: str) -> str:
    return f"http://{host.strip() or '127.0.0.1:8188'}{path}"


def _get_json(host: str, path: str, timeout: float = 10):
    with urllib.request.urlopen(_url(host, path), timeout=timeout) as r:
        return json.loads(r.read())


def is_connected(host: str) -> bool:
    try:
        _get_json(host, "/system_stats", 3)
        return True
    except Exception:
        return False


# ── workflows ────────────────────────────────────────────────────────────────────────────────────────────
def find_nodes(wf: dict) -> dict:
    """{'text': [ids], 'latent': [...], 'sampler': [...], 'save': [...]} by class_type, never by node id."""
    out = {k: [] for k in _REQUIRED}
    for nid, node in wf.items():
        if isinstance(node, dict):
            for k, cls in _REQUIRED.items():
                if node.get("class_type") == cls:
                    out[k].append(nid)
    return out


def validate_mapping(wf: dict) -> list[str]:
    """Problems that make the workflow unusable (empty = fine)."""
    m = find_nodes(wf)
    probs = []
    for k, cls in _REQUIRED.items():
        if not m[k]:
            probs.append(f"The workflow has no {cls} node")
        elif len(m[k]) > 1:
            probs.append(f"The workflow has {len(m[k])} {cls} nodes (expected one)")
    return probs


def scan_workflows(folder: Path) -> list[Path]:
    """Workflow files in comfy_apis/audio that contain an ACE-Step 1.5 text node."""
    res = []
    if folder.is_dir():
        for f in sorted(folder.glob("*.json")):
            try:
                wf = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(wf, dict) and find_nodes(wf)["text"]:
                    res.append(f)
            except Exception:
                continue
    return res


def load_workflow(path: Path) -> dict:
    wf = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(wf, dict) or "nodes" in wf:
        raise ComfyError("This is not an API-format workflow (use Workflow > Export (API) in ComfyUI)")
    return wf


# ── building the job ─────────────────────────────────────────────────────────────────────────────────────
def effective_seeds(s: SongSettings) -> tuple[int, int]:
    """(text-encode seed, KSampler seed) that will really be sent."""
    text_seed = int(s.seed)
    return text_seed, (text_seed if s.seed_mode == "same" else int(s.ksampler_seed))


def build_workflow(base: dict, s: SongSettings, tags: str, lyrics: str, filename_prefix: str = "audio/WorkflowPadMusic") -> tuple[dict, dict]:
    """Clone `base` and inject the settings. Returns (workflow, effective values actually written)."""
    probs = validate_mapping(base)
    if probs:
        raise ComfyError("; ".join(probs))
    wf = copy.deepcopy(base)
    m = find_nodes(wf)
    text_seed, ks_seed = effective_seeds(s)
    t = wf[m["text"][0]]["inputs"]
    t.update({"tags": tags, "lyrics": lyrics, "bpm": int(s.bpm), "duration": int(s.duration), "timesignature": str(s.timesignature),
              "language": s.language, "keyscale": s.keyscale, "seed": text_seed, "cfg_scale": float(s.text_cfg),
              "temperature": float(s.temperature), "top_p": float(s.top_p), "top_k": int(s.top_k), "min_p": float(s.min_p)})
    wf[m["latent"][0]]["inputs"]["seconds"] = int(s.duration)           # always equal to duration
    k = wf[m["sampler"][0]]["inputs"]
    k.update({"seed": ks_seed, "steps": int(s.steps), "cfg": float(s.cfg)})
    sv = wf[m["save"][0]]["inputs"]
    sv["filename_prefix"] = filename_prefix
    sv["format"] = s.format
    used = {"seeds": {"text_encode": text_seed, "ksampler": ks_seed}, "duration": int(s.duration), "bpm": int(s.bpm), "steps": int(s.steps),
            "cfg": float(s.cfg), "format": s.format, "language": s.language}
    return wf, used


def fresh_seed() -> int:
    return random.randint(0, 2**32 - 1)


# ── running it ───────────────────────────────────────────────────────────────────────────────────────────
def submit(host: str, wf: dict) -> str:
    req = urllib.request.Request(_url(host, "/prompt"), data=json.dumps({"prompt": wf, "client_id": CLIENT_ID}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["prompt_id"]
    except urllib.error.HTTPError as e:
        raise ComfyError(f"ComfyUI rejected the workflow (HTTP {e.code}): {e.read().decode(errors='ignore')[:800]}") from None
    except urllib.error.URLError as e:
        raise ComfyError(f"Cannot reach ComfyUI at {host}: {e.reason}") from None


def _node_exists(host: str, class_type: str) -> bool:
    try:
        return bool(_get_json(host, f"/object_info/{urllib.parse.quote(class_type)}", 8))
    except Exception:
        return False


def _ram_free_mb(host: str) -> int | None:
    try:
        return int(_get_json(host, "/system_stats", 5)["system"]["ram_free"] // 2**20)
    except Exception:
        return None


def free_memory(host: str, timeout_s: float = 90.0) -> dict:
    """Clear ComfyUI's RAM / VRAM before a song. Best effort, never raises. Returns what was done:
    {'free_api': bool, 'cleanup_nodes': [class types run], 'ram_free_before_mb': n, 'ram_free_after_mb': n}.

    POST /free alone only releases models that ComfyUI's own model manager knows. A local LLM (llama.cpp, comfyui-llm-session) keeps its model in RAM outside of it, so a
    tiny cleanup workflow is also run when the nodes exist on this server: UnloadLLMModelNode (unloads that LLM) and VRAM_Debug (unload_all_models + gc + empty cache, ComfyUI-KJNodes).
    Nodes that are not installed are simply skipped. Only this cleanup workflow is submitted; nothing else in the queue is touched."""
    info = {"free_api": False, "cleanup_nodes": [], "ram_free_before_mb": _ram_free_mb(host), "ram_free_after_mb": None}
    try:
        req = urllib.request.Request(_url(host, "/free"), data=json.dumps({"unload_models": True, "free_memory": True}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10).close()
        info["free_api"] = True
    except Exception:
        pass
    try:
        wf, last = {}, None
        if _node_exists(host, "UnloadLLMModelNode"):
            wf["1"] = {"class_type": "UnloadLLMModelNode", "inputs": {"unload_now": True}}
            last = "1"
            info["cleanup_nodes"].append("UnloadLLMModelNode")
        if _node_exists(host, "VRAM_Debug") and _node_exists(host, "PreviewAny"):
            vi = {"empty_cache": True, "gc_collect": True, "unload_all_models": True}
            if last:
                vi["any_input"] = [last, 0]
            wf["2"] = {"class_type": "VRAM_Debug", "inputs": vi}
            wf["3"] = {"class_type": "PreviewAny", "inputs": {"source": ["2", 0]}}
            info["cleanup_nodes"].append("VRAM_Debug")
        if wf:
            pid = submit(host, wf)
            wait_for_result(host, pid, lambda: False, None, timeout_s)
    except Exception:
        pass
    info["ram_free_after_mb"] = _ram_free_mb(host)
    return info


def queue_state(host: str, pid: str) -> str:
    """'running' | 'queued' | 'unknown' for our job (history is checked separately)."""
    try:
        q = _get_json(host, "/queue", 5)
    except Exception:
        return "unknown"
    for item in q.get("queue_running", []):
        if len(item) > 1 and item[1] == pid:
            return "running"
    for item in q.get("queue_pending", []):
        if len(item) > 1 and item[1] == pid:
            return "queued"
    return "unknown"


def cancel(host: str, pid: str) -> str:
    """Cancel OUR job only. Returns what was done: 'removed' | 'interrupted' | 'none'."""
    st = queue_state(host, pid)
    try:
        if st == "queued":
            req = urllib.request.Request(_url(host, "/queue"), data=json.dumps({"delete": [pid]}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=5)
            return "removed"
        if st == "running":
            req = urllib.request.Request(_url(host, "/interrupt"), data=json.dumps({"prompt_id": pid}).encode(),
                                         headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(req, timeout=5)
            return "interrupted"
    except Exception:
        pass
    return "none"


def poll(host: str, pid: str) -> dict | None:
    """The history entry once the job has finished (success or error), else None."""
    try:
        h = _get_json(host, f"/history/{pid}", 10)
    except Exception:
        return None
    return h.get(pid)


def audio_outputs(entry: dict) -> list[dict]:
    """[{'filename','subfolder','type'}] of every audio file the job produced."""
    files = []
    for out in (entry.get("outputs") or {}).values():
        for key in ("audio", "audios"):
            for f in out.get(key, []) or []:
                if isinstance(f, dict) and f.get("filename"):
                    files.append(f)
    return files


def job_error(entry: dict) -> str:
    st = entry.get("status") or {}
    if st.get("status_str") == "error":
        for msg in st.get("messages", []):
            if msg and msg[0] == "execution_error":
                d = msg[1] if len(msg) > 1 else {}
                return f'{d.get("node_type", "")}: {d.get("exception_message", "execution error")}'.strip(": ")
        return "ComfyUI reported an execution error"
    return ""


def fetch_file(host: str, f: dict, dest: Path) -> None:
    p = urllib.parse.urlencode({"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")})
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(_url(host, f"/view?{p}"), timeout=120) as r, open(tmp, "wb") as out:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
    tmp.replace(dest)


def wait_for_result(host: str, pid: str, should_stop, on_state=None, timeout_s: float = 3600.0, lost_after_s: float = 30.0, gone_after_s: float = 45.0) -> dict:
    """Blocks (call from an executor). Returns the history entry; raises ComfyError on cancel, timeout, or when ComfyUI can no longer finish the job:
    - ComfyUI does not answer for `lost_after_s` seconds in a row (crashed / closed): the wait ends with a clear message instead of spinning for an hour. on_state("unreachable") is
      reported while it is silent, so the UI can say so; a ComfyUI that answers again before the limit simply carries on.
    - ComfyUI answers but our job is neither in its queue nor in its history for `gone_after_s` seconds (it was restarted and lost the queue): the job is gone, say so."""
    t0 = time.time()
    last = ""
    down_since = absent_since = None
    while time.time() - t0 < timeout_s:
        if should_stop():
            raise ComfyError("cancelled")
        try:
            hist = _get_json(host, f"/history/{pid}", 10)
            queue = _get_json(host, "/queue", 5)
        except Exception:
            now = time.time()
            absent_since = None
            if down_since is None:
                down_since = now
            if on_state and last != "unreachable":
                last = "unreachable"
                on_state("unreachable")
            if now - down_since >= lost_after_s:
                raise ComfyError(f"ComfyUI stopped responding ({int(now - down_since)} s) before the song was finished. Start ComfyUI again and press Generate; nothing was saved for this attempt.")
            time.sleep(1.0)
            continue
        down_since = None
        entry = hist.get(pid)
        if entry is not None:
            return entry
        st = "unknown"
        for name, key in (("running", "queue_running"), ("queued", "queue_pending")):
            if any(len(item) > 1 and item[1] == pid for item in queue.get(key, [])):
                st = name
        if st == "unknown":
            absent_since = absent_since or time.time()
            if time.time() - absent_since >= gone_after_s:
                raise ComfyError("ComfyUI no longer has this job (it was probably restarted and its queue was lost). Press Generate to try again.")
        else:
            absent_since = None
        if on_state and st != last and st != "unknown":
            last = st
            on_state(st)
        elif on_state and last == "unreachable" and st == "unknown":
            last = ""
        time.sleep(1.0)
    raise ComfyError("Timed out waiting for ComfyUI")