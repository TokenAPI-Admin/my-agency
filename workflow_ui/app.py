from __future__ import annotations

import base64
import json
import mimetypes
import os
import shutil
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import requests


BASE_DIR = Path("/home/ubuntu/video-shopping")
UI_DIR = BASE_DIR / "workflow_ui"
RUNS_DIR = UI_DIR / "runs"
CONTENT_RUNS_DIR = UI_DIR / "content_runs"
XHS_KNOWLEDGE_DIR = UI_DIR / "xhs_knowledge"
STATIC_DIR = UI_DIR / "static"
SCRIPT_PATH = BASE_DIR / "work" / "run_images_only.py"

DEFAULT_PRODUCT_IMAGE = BASE_DIR / "work" / "refs" / "product_main_ref1.png"
DEFAULT_MODEL_IMAGE = BASE_DIR / "work" / "refs" / "model_fixed_board1.png"

runs: dict[str, dict[str, Any]] = {}
content_runs: dict[str, dict[str, Any]] = {}
run_lock = threading.Lock()
active_run_id: str | None = None

OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8318/v1").rstrip("/")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
XHS_BUYER_MODEL = os.environ.get("XHS_BUYER_MODEL", os.environ.get("AD_DIRECTOR_MODEL", "gpt-5.4-mini"))
XHS_BUYER_TIMEOUT = int(os.environ.get("XHS_BUYER_TIMEOUT", "180"))
XHS_KNOWLEDGE_MAX_CHARS = int(os.environ.get("XHS_KNOWLEDGE_MAX_CHARS", "24000"))

XHS_BUYER_SYSTEM_PROMPT = """
你是“小红书买手内容策划官”。

你的任务是根据用户上传的产品图和产品参数，生成一套以出单为目标的小红书买手笔记成稿包。

你不是普通文案，不是品牌广告编辑，也不是硬广写手。你要像一个有审美、有判断力、懂平台内容节奏的买手，替用户完成购买判断：这个产品适合谁，为什么值得看，怎么用，哪些人不适合，买前要注意什么。

你的内容目标：
1. 让用户愿意点进来。
2. 让用户快速代入使用场景。
3. 让用户相信产品价值。
4. 让用户完成购买判断。
5. 引导评论、私信、进店或下单。

固定产物结构：
- 1张视觉冲击封面图方向
- 1句封面金句
- 3张试用场景图方向
- 小红书标题
- 开头钩子
- 买手判断
- 正文
- 适合人群
- 不适合人群
- 购买建议
- 评论区引导
- 标签
- 合规边界

内容原则：
- 可以种草，但不能虚构。
- 可以放大感受，但不能夸大事实。
- 可以做审美判断，但不能编造真实使用体验。
- 可以根据产品图做合理推断，但必须标出不确定信息。
- 不能编造材质、功效、认证、价格、销量、尺码、耐用性、医学效果或绝对承诺。
- 没有用户提供的数据，不要写成确定事实。

图片方向原则：
- 封面图负责点击。
- 场景图1负责真实使用代入。
- 场景图2负责细节证据。
- 场景图3负责购买判断。
- 每张图都要说明画面主体、场景、构图、图上短句和转化目的。

语言风格：
- 像小红书买手，不像淘宝详情页。
- 克制、自然、有判断，不油腻。
- 少用空泛形容词，多用具体场景和购买建议。
- 不使用过度营销话术。

只返回有效 JSON，不要 Markdown，不要 JSON 外的解释。
""".strip()


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def ensure_dirs() -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    CONTENT_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    XHS_KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)


def save_upload(upload: UploadFile, dst: Path) -> None:
    with dst.open("wb") as f:
        shutil.copyfileobj(upload.file, f)


def file_to_data_url(path: Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def extract_json_object(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise RuntimeError("model returned non-object JSON")
    return data


def load_xhs_knowledge() -> str:
    if not XHS_KNOWLEDGE_DIR.exists():
        return ""
    parts: list[str] = []
    total = 0
    for path in sorted(XHS_KNOWLEDGE_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            continue
        chunk = f"\n\n# Knowledge: {path.name}\n{text}"
        if total + len(chunk) > XHS_KNOWLEDGE_MAX_CHARS:
            remaining = max(0, XHS_KNOWLEDGE_MAX_CHARS - total)
            if remaining > 200:
                parts.append(chunk[:remaining])
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n".join(parts).strip()


def append_log(run_id: str, line: str) -> None:
    state = runs.get(run_id)
    if not state:
        return
    logs = state["logs"]
    logs.append(line.rstrip("\n"))
    if len(logs) > 500:
        del logs[:120]


def set_run_done(run_id: str, status: str, error: str | None = None) -> None:
    global active_run_id
    state = runs.get(run_id)
    if not state:
        return
    state["status"] = status
    state["ended_at"] = now_iso()
    if error:
        state["error"] = error
    with run_lock:
        if active_run_id == run_id:
            active_run_id = None


def append_content_log(content_id: str, line: str) -> None:
    state = content_runs.get(content_id)
    if not state:
        return
    logs = state["logs"]
    logs.append(line.rstrip("\n"))
    if len(logs) > 300:
        del logs[:80]


def set_content_done(content_id: str, status: str, error: str | None = None) -> None:
    state = content_runs.get(content_id)
    if not state:
        return
    state["status"] = status
    state["ended_at"] = now_iso()
    if error:
        state["error"] = error


def build_xhs_buyer_user_prompt(product_params: str, tone: str) -> str:
    params = product_params.strip() if product_params else "用户未提供额外产品参数，只能根据产品图做保守判断。"
    tone_text = tone.strip() if tone else "克制买手感"
    knowledge_context = load_xhs_knowledge() or "暂无额外知识库，请只根据系统身份、产品图和产品参数生成。"
    return f"""
请根据上传的产品图和以下产品参数，生成一套完整的小红书买手笔记成稿包。

产品参数：
{params}

期望语气：
{tone_text}

可用知识库：
{knowledge_context}

请严格返回这个 JSON 结构：
{{
  "product_understanding": {{
    "category": "",
    "visible_features": [],
    "provided_facts": [],
    "uncertain_points": []
  }},
  "sales_logic": {{
    "target_user": [],
    "core_purchase_reason": "",
    "main_objection": "",
    "conversion_path": "点击 -> 代入 -> 相信 -> 判断 -> 行动"
  }},
  "cover": {{
    "image_direction": "",
    "golden_line": "",
    "cover_text": "",
    "click_reason": ""
  }},
  "scene_images": [
    {{
      "role": "真实使用代入",
      "image_direction": "",
      "caption": "",
      "why_it_converts": ""
    }},
    {{
      "role": "细节证据",
      "image_direction": "",
      "caption": "",
      "why_it_converts": ""
    }},
    {{
      "role": "购买判断",
      "image_direction": "",
      "caption": "",
      "why_it_converts": ""
    }}
  ],
  "note_content": {{
    "title_options": [],
    "opening_hook": "",
    "buyer_judgement": "",
    "body": "",
    "suitable_for": [],
    "not_suitable_for": [],
    "buying_advice": "",
    "comment_prompt": "",
    "hashtags": []
  }},
  "finished_buyer_note": {{
    "title": "",
    "cover": {{
      "final_image_prompt": "",
      "overlay_text": "",
      "purpose": "点击"
    }},
    "image_cards": [
      {{
        "type": "封面图",
        "final_image_prompt": "",
        "overlay_text": "",
        "caption": "",
        "purpose": "点击"
      }},
      {{
        "type": "真实使用场景图",
        "final_image_prompt": "",
        "overlay_text": "",
        "caption": "",
        "purpose": "代入"
      }},
      {{
        "type": "细节证据图",
        "final_image_prompt": "",
        "overlay_text": "",
        "caption": "",
        "purpose": "相信"
      }},
      {{
        "type": "购买判断图",
        "final_image_prompt": "",
        "overlay_text": "",
        "caption": "",
        "purpose": "判断"
      }}
    ],
    "body": "",
    "hashtags_text": "",
    "comment_prompt": "",
    "publish_notes": []
  }},
  "compliance_boundary": {{
    "do_not_claim": [],
    "needs_user_input": []
  }}
}}

要求：
- 封面图方向必须是 1 张强点击视觉。
- scene_images 必须正好 3 张，分别承担真实使用代入、细节证据、购买判断。
- 正文要能直接作为小红书买手笔记基础稿使用。
- finished_buyer_note 必须是可以直接进入“出图 + 发布”的成品包，不要只写策划分析。
- finished_buyer_note.image_cards 必须正好 4 张：封面图、真实使用场景图、细节证据图、购买判断图。
- 每张 image_cards 都必须包含可直接给图片模型使用的 final_image_prompt、图上文字 overlay_text、图片说明 caption、转化目的 purpose。
- finished_buyer_note.body 必须是可直接发布的小红书正文，不要再写 JSON 分析口吻。
- finished_buyer_note.hashtags_text 必须是一行可直接复制的标签。
- 如果产品图或参数不足，不要硬编，写进 uncertain_points 或 needs_user_input。
- 不要调用图片生成，不要输出图片文件路径。
""".strip()


def validate_xhs_buyer_plan(plan: dict[str, Any]) -> None:
    for key in (
        "product_understanding",
        "sales_logic",
        "cover",
        "scene_images",
        "note_content",
        "finished_buyer_note",
        "compliance_boundary",
    ):
        if key not in plan:
            raise RuntimeError("xhs buyer note missing required field: " + key)
    if not isinstance(plan["scene_images"], list) or len(plan["scene_images"]) != 3:
        raise RuntimeError("xhs buyer note requires exactly 3 scene_images")
    finished = plan["finished_buyer_note"]
    if not isinstance(finished, dict):
        raise RuntimeError("finished_buyer_note must be an object")
    cards = finished.get("image_cards")
    if not isinstance(cards, list) or len(cards) != 4:
        raise RuntimeError("finished_buyer_note requires exactly 4 image_cards")
    for field in ("title", "body", "hashtags_text", "comment_prompt"):
        if not str(finished.get(field, "")).strip():
            raise RuntimeError("finished_buyer_note missing required field: " + field)


def call_xhs_buyer_model(
    product_image: Path,
    product_params: str,
    tone: str,
    params_image: Path | None = None,
) -> dict[str, Any]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    user_content: list[dict[str, Any]] = [
        {"type": "text", "text": build_xhs_buyer_user_prompt(product_params, tone)},
        {"type": "text", "text": "Image 1 is the product image. Use it as the primary visual source."},
        {"type": "image_url", "image_url": {"url": file_to_data_url(product_image)}},
    ]
    if params_image is not None:
        user_content.extend(
            [
                {
                    "type": "text",
                    "text": "Image 2 is the product-parameter/detail image. Extract only supported product facts from it, and put uncertain items in uncertain_points.",
                },
                {"type": "image_url", "image_url": {"url": file_to_data_url(params_image)}},
            ]
        )
    payload = {
        "model": XHS_BUYER_MODEL,
        "messages": [
            {"role": "system", "content": XHS_BUYER_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.35,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(
        f"{OPENAI_BASE_URL}/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=XHS_BUYER_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"xhs buyer model failed: {resp.status_code} {resp.text}")
    data = resp.json()
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    plan = extract_json_object(content)
    validate_xhs_buyer_plan(plan)
    return plan


def content_worker(
    content_id: str,
    product_image: Path,
    product_params: str,
    tone: str,
    params_image: Path | None = None,
) -> None:
    state = content_runs[content_id]
    run_dir = Path(state["run_dir"])
    output_dir = run_dir / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        append_content_log(content_id, f"[start] xhs buyer note model={XHS_BUYER_MODEL}")
        plan = call_xhs_buyer_model(product_image, product_params, tone, params_image)
        output_path = output_dir / "xhs_buyer_note.json"
        output_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        state["outputs"] = {"buyer_note": f"/content-files/{content_id}/outputs/xhs_buyer_note.json"}
        append_content_log(content_id, "[done] " + str(output_path))
        set_content_done(content_id, "succeeded")
    except Exception as exc:
        append_content_log(content_id, "[error] " + str(exc))
        set_content_done(content_id, "failed", error=str(exc))


def worker(run_id: str, product_image: Path, three_view_size: str, storyboard_size: str) -> None:
    state = runs[run_id]
    run_dir = Path(state["run_dir"])
    output_dir = run_dir / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["BILLING_OK"] = env.get("BILLING_OK", "1")
    env["PRODUCT_IMAGE"] = str(product_image)
    env["MODEL_IMAGE_1"] = str(DEFAULT_PRODUCT_IMAGE)
    env["MODEL_IMAGE_2"] = str(DEFAULT_MODEL_IMAGE)
    cmd = [
        "python3",
        str(SCRIPT_PATH),
        "--product-image",
        str(product_image),
        "--model-image",
        str(DEFAULT_PRODUCT_IMAGE),
        "--model-image",
        str(DEFAULT_MODEL_IMAGE),
        "--output-dir",
        str(output_dir),
        "--three-view-size",
        three_view_size,
        "--storyboard-size",
        storyboard_size,
        "--min-width",
        "512",
        "--min-height",
        "512",
    ]

    append_log(run_id, "[start] " + " ".join(cmd))
    proc = subprocess.Popen(
        cmd,
        cwd=str(BASE_DIR),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    assert proc.stdout is not None
    for line in proc.stdout:
        append_log(run_id, line)
    code = proc.wait()
    state["exit_code"] = code

    three_view = output_dir / "kent_three_view.png"
    storyboard = output_dir / "kent_storyboard_overview.png"
    state["outputs"] = {
        "three_view": f"/files/{run_id}/outputs/kent_three_view.png" if three_view.exists() else None,
        "storyboard": f"/files/{run_id}/outputs/kent_storyboard_overview.png" if storyboard.exists() else None,
    }

    if code == 0 and three_view.exists() and storyboard.exists():
        set_run_done(run_id, "succeeded")
    else:
        set_run_done(run_id, "failed", error="generation failed; check logs")


app = FastAPI(title="Commerce AI Workflow UI")


@app.on_event("startup")
def on_startup() -> None:
    ensure_dirs()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "active_run_id": active_run_id,
        "content_module": {
            "enabled": True,
            "model": XHS_BUYER_MODEL,
            "base_url": OPENAI_BASE_URL,
            "knowledge_dir": str(XHS_KNOWLEDGE_DIR),
            "knowledge_files": [p.name for p in sorted(XHS_KNOWLEDGE_DIR.glob("*.md"))] if XHS_KNOWLEDGE_DIR.exists() else [],
        },
        "script": str(SCRIPT_PATH),
        "default_product_image": str(DEFAULT_PRODUCT_IMAGE),
        "default_model_image": str(DEFAULT_MODEL_IMAGE),
    }


@app.get("/api/status/{run_id}")
def status(run_id: str) -> dict[str, Any]:
    state = runs.get(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="run not found")
    return state


@app.get("/api/xhs/status/{content_id}")
def xhs_status(content_id: str) -> dict[str, Any]:
    state = content_runs.get(content_id)
    if not state:
        raise HTTPException(status_code=404, detail="content run not found")
    return state


@app.post("/api/xhs/buyer-note")
async def xhs_buyer_note(
    product_params: str = Form(""),
    tone: str = Form("克制买手感"),
    product_image: UploadFile | None = File(default=None),
    params_image: UploadFile | None = File(default=None),
) -> JSONResponse:
    if product_image is None:
        raise HTTPException(status_code=400, detail="product_image is required")
    if not tone or "\ufffd" in tone:
        tone = "克制买手感"

    content_id = datetime.utcnow().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    run_dir = CONTENT_RUNS_DIR / content_id
    input_dir = run_dir / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    resolved_product = input_dir / "product.png"
    save_upload(product_image, resolved_product)
    resolved_params_image: Path | None = None
    if params_image is not None:
        resolved_params_image = input_dir / "params.png"
        save_upload(params_image, resolved_params_image)

    (input_dir / "product_params.txt").write_text(product_params or "", encoding="utf-8")
    (input_dir / "tone.txt").write_text(tone or "", encoding="utf-8")

    content_runs[content_id] = {
        "content_id": content_id,
        "status": "running",
        "created_at": now_iso(),
        "ended_at": None,
        "error": None,
        "logs": [],
        "run_dir": str(run_dir),
        "inputs": {
            "product_image": str(resolved_product),
            "params_image": str(resolved_params_image) if resolved_params_image else None,
            "product_params": product_params,
            "tone": tone,
            "model": XHS_BUYER_MODEL,
        },
        "outputs": {
            "buyer_note": None,
        },
    }

    thread = threading.Thread(
        target=content_worker,
        args=(content_id, resolved_product, product_params, tone, resolved_params_image),
        daemon=True,
    )
    thread.start()
    return JSONResponse({"ok": True, "content_id": content_id})


@app.post("/api/run")
async def run_workflow(
    use_defaults: bool = Form(False),
    three_view_size: str = Form("1024x1024"),
    storyboard_size: str = Form("1536x1024"),
    product_image: UploadFile | None = File(default=None),
) -> JSONResponse:
    global active_run_id

    with run_lock:
        if active_run_id is not None:
            raise HTTPException(status_code=409, detail="another run is in progress")
        run_id = datetime.utcnow().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        active_run_id = run_id

    run_dir = RUNS_DIR / run_id
    input_dir = run_dir / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)

    if use_defaults:
        if not DEFAULT_PRODUCT_IMAGE.exists() or not DEFAULT_MODEL_IMAGE.exists():
            with run_lock:
                active_run_id = None
            raise HTTPException(status_code=400, detail="default refs not found on server")
        resolved_product = DEFAULT_PRODUCT_IMAGE
    else:
        if not DEFAULT_PRODUCT_IMAGE.exists() or not DEFAULT_MODEL_IMAGE.exists():
            with run_lock:
                active_run_id = None
            raise HTTPException(status_code=400, detail="fixed model refs not found on server")
        if product_image is None:
            with run_lock:
                active_run_id = None
            raise HTTPException(status_code=400, detail="product_image is required")
        resolved_product = input_dir / "product.png"
        save_upload(product_image, resolved_product)

    runs[run_id] = {
        "run_id": run_id,
        "status": "running",
        "created_at": now_iso(),
        "ended_at": None,
        "error": None,
        "logs": [],
        "exit_code": None,
        "run_dir": str(run_dir),
        "inputs": {
            "use_defaults": use_defaults,
            "product_image": str(resolved_product),
            "model_image_1": str(DEFAULT_PRODUCT_IMAGE),
            "model_image_2": str(DEFAULT_MODEL_IMAGE),
            "three_view_size": three_view_size,
            "storyboard_size": storyboard_size,
        },
        "outputs": {
            "three_view": None,
            "storyboard": None,
        },
    }

    thread = threading.Thread(
        target=worker,
        args=(run_id, resolved_product, three_view_size, storyboard_size),
        daemon=True,
    )
    thread.start()
    return JSONResponse({"ok": True, "run_id": run_id})


@app.get("/files/{run_id}/{rest_of_path:path}")
def files(run_id: str, rest_of_path: str) -> FileResponse:
    run_dir = RUNS_DIR / run_id
    path = (run_dir / rest_of_path).resolve()
    if not str(path).startswith(str(run_dir.resolve())):
        raise HTTPException(status_code=403, detail="forbidden")
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path)


@app.get("/content-files/{content_id}/{rest_of_path:path}")
def content_files(content_id: str, rest_of_path: str) -> FileResponse:
    run_dir = CONTENT_RUNS_DIR / content_id
    path = (run_dir / rest_of_path).resolve()
    if not str(path).startswith(str(run_dir.resolve())):
        raise HTTPException(status_code=403, detail="forbidden")
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
