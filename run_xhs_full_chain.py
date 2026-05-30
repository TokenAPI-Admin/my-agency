#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


BASE_DIR = Path("/home/ubuntu/video-shopping")
UI_DIR = BASE_DIR / "workflow_ui"
CONTENT_RUNS_DIR = UI_DIR / "content_runs"
KNOWLEDGE_DIR = UI_DIR / "xhs_knowledge"
BUILD_VIDEO_PATH = BASE_DIR / "scripts" / "build_video.py"
PROVIDERS_PATH = BASE_DIR / "work" / "providers.json"
SERVICE_PATH = Path("/etc/systemd/system/workflow-ui.service")


def load_build_video_module():
    spec = importlib.util.spec_from_file_location("build_video_base", BUILD_VIDEO_PATH)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    if PROVIDERS_PATH.exists():
        mod.apply_provider(os.environ.get("PROVIDER", "").strip(), str(PROVIDERS_PATH))
    return mod


MOD = load_build_video_module()


def service_env(name: str) -> str:
    if not SERVICE_PATH.exists():
        return ""
    pattern = re.compile(rf"^Environment={re.escape(name)}=(.*)$")
    for line in SERVICE_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = pattern.match(line.strip())
        if match:
            return match.group(1).strip()
    return ""


def openai_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip() or service_env("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY not found")
    return key


def base_url() -> str:
    url = os.environ.get("OPENAI_BASE_URL", "").strip() or service_env("OPENAI_BASE_URL") or MOD.OPENAI_BASE_URL
    return url.rstrip("/")


def buyer_model() -> str:
    return os.environ.get("XHS_BUYER_MODEL", "").strip() or service_env("XHS_BUYER_MODEL") or "gpt-5.4-mini"


def load_knowledge(max_chars: int = 24000) -> str:
    parts: list[str] = []
    total = 0
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            continue
        chunk = f"\n\n# Knowledge: {path.name}\n{text}"
        if total + len(chunk) > max_chars:
            remain = max(0, max_chars - total)
            if remain > 200:
                parts.append(chunk[:remain])
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n".join(parts).strip()


SYSTEM_PROMPT = """
你是“小红书买手内容策划 + 图文成片导演”。

你的任务是根据用户上传的商品主体图和参数图，产出一套可进入出图和发布的小红书买手笔记成品包。

架构分工：
1. 你负责识别商品、判断场景、组织购买理由、写成品文案。
2. 你负责给每张图的“背景/环境”下导演指令。
3. 图片模型只生成无商品主体的背景。
4. 商品主体由脚本把用户上传的商品图固定合成进去，不能让图片模型重画或替换商品。

内容原则：
- 必须按图片识别商品品类，不允许写死某个品类。
- 可以种草，可以有审美判断，但不能编造材质、认证、销量、医疗功效、绝对效果或真实亲测。
- 没有证据的信息写进 uncertain_points 或 needs_user_input。
- 图文要像小红书买手内容，不像电商详情页，不像品牌硬广。
- 封面负责点击；三张场景图分别负责代入、相信、判断。
- 只返回有效 JSON，不要 Markdown，不要 JSON 外解释。
""".strip()


def build_user_prompt(product_params: str) -> str:
    params = product_params.strip() if product_params else "用户未提供额外参数，只能根据图片保守判断。"
    knowledge = load_knowledge() or "暂无额外知识库，请按平台常识和买手内容逻辑生成。"
    return f"""
请基于 Image 1 商品主体图、Image 2 参数/详情图，以及人工补充参数，产出一套完整“小红书买手笔记成品包”。

图片角色：
- Image 1 是必须固定的商品主体图，识别商品时以它为第一优先级，后续每张成品图都会直接合成这张商品图。
- Image 2 是参数/详情图，只用于提取事实、卖点和合规边界。

人工补充参数：
{params}

知识库：
{knowledge}

严格返回 JSON：
{{
  "product_subject": {{
    "category": "",
    "subject_name": "",
    "must_keep_visuals": [],
    "do_not_generate_in_background": [],
    "uncertain_points": []
  }},
  "title": "",
  "image_cards": [
    {{
      "type": "封面图",
      "background_prompt": "",
      "overlay_text": "",
      "product_layer_instruction": "",
      "caption": "",
      "purpose": "点击"
    }},
    {{
      "type": "真实使用场景图",
      "background_prompt": "",
      "overlay_text": "",
      "product_layer_instruction": "",
      "caption": "",
      "purpose": "代入"
    }},
    {{
      "type": "细节证据图",
      "background_prompt": "",
      "overlay_text": "",
      "product_layer_instruction": "",
      "caption": "",
      "purpose": "相信"
    }},
    {{
      "type": "购买判断图",
      "background_prompt": "",
      "overlay_text": "",
      "product_layer_instruction": "",
      "caption": "",
      "purpose": "判断"
    }}
  ],
  "body": "",
  "hashtags_text": "",
  "comment_prompt": "",
  "compliance_notes": [],
  "needs_user_input": []
}}

要求：
- image_cards 必须正好 4 张。
- 每张 background_prompt 只能描述背景、环境、光线、构图、情绪和留白，不能让图片模型生成商品主体、包装、logo、文字。
- product_subject.do_not_generate_in_background 必须根据当前商品动态填写，用来禁止背景里出现相似替代品。
- overlay_text 必须是中文短句，少而准，有点击、代入或购买判断。
- 图片规格统一为 1080x1440，3:4 竖图。背景需给下半区商品图层和上方文字留安全空间。
- body 必须是可直接发布的小红书正文，不要分析口吻。
""".strip()


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
        raise RuntimeError("non-object JSON")
    return data


def validate_plan(plan: dict[str, Any]) -> None:
    for key in ("product_subject", "title", "image_cards", "body", "hashtags_text", "comment_prompt"):
        if key not in plan:
            raise RuntimeError("buyer plan missing required field: " + key)
    subject = plan["product_subject"]
    if not isinstance(subject, dict):
        raise RuntimeError("product_subject must be an object")
    for key in ("category", "subject_name"):
        if not str(subject.get(key, "")).strip():
            raise RuntimeError("product_subject missing required field: " + key)
    cards = plan.get("image_cards")
    if not isinstance(cards, list) or len(cards) != 4:
        raise RuntimeError("buyer plan must include exactly 4 image_cards")
    for idx, card in enumerate(cards, start=1):
        if not isinstance(card, dict):
            raise RuntimeError(f"image card {idx} must be an object")
        for key in ("type", "background_prompt", "overlay_text", "purpose"):
            if not str(card.get(key, "")).strip():
                raise RuntimeError(f"image card {idx} missing required field: {key}")


def call_buyer_model(product_image: Path, params_image: Path, product_params: str) -> dict[str, Any]:
    content = [
        {"type": "text", "text": build_user_prompt(product_params)},
        {"type": "text", "text": "Image 1: fixed product subject image. Use as primary product identity."},
        {"type": "image_url", "image_url": {"url": MOD.file_to_data_url(str(product_image))}},
        {"type": "text", "text": "Image 2: parameter/detail image. Extract supported facts conservatively."},
        {"type": "image_url", "image_url": {"url": MOD.file_to_data_url(str(params_image))}},
    ]
    payload = {
        "model": buyer_model(),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        "temperature": 0.35,
        "response_format": {"type": "json_object"},
    }
    resp = requests.post(
        f"{base_url()}/chat/completions",
        headers={"Authorization": f"Bearer {openai_key()}", "Content-Type": "application/json"},
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        timeout=int(os.environ.get("XHS_BUYER_TIMEOUT", "240")),
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"buyer model failed: {resp.status_code} {resp.text}")
    plan = extract_json_object(resp.json().get("choices", [{}])[0].get("message", {}).get("content", ""))
    validate_plan(plan)
    return plan


def list_text(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def text_for_image(value: str) -> str:
    text = re.sub(r"\s+", "", str(value or "").strip())
    if not text:
        return "买前先看"
    if len(text) <= 10:
        return text
    return text[:10] + "\n" + text[10:18]


def make_product_layer(product_image: Path, out_dir: Path) -> Path:
    layer = out_dir / "product_fixed_layer.png"
    vf = "scale=820:920:force_original_aspect_ratio=decrease,format=rgba"
    proc = subprocess.run(
        [MOD.FFMPEG, "-y", "-v", "error", "-i", str(product_image), "-vf", vf, str(layer)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("product layer prepare failed: " + proc.stderr.strip())
    return layer


async def generate_background(plan: dict[str, Any], card: dict[str, Any], idx: int, out_dir: Path) -> Path:
    subject = plan["product_subject"]
    forbidden = list_text(subject.get("do_not_generate_in_background"))
    anchors = list_text(subject.get("must_keep_visuals"))
    prompt = (
        "Create a Xiaohongshu buyer-note card BACKGROUND ONLY, vertical 1024x1536. "
        "The real product layer and Chinese overlay text will be composited later. "
        "Do not generate the product subject, similar substitute products, packaging, logo, text, labels, words, watermark, or UI. "
        f"Card type: {card.get('type', '')}. Card purpose: {card.get('purpose', '')}. "
        f"Recognized product category to avoid drawing: {subject.get('category', '')}. "
        f"Recognized product subject to avoid drawing: {subject.get('subject_name', '')}. "
        f"Must not appear in background: {forbidden}. "
        f"Product visual anchors reserved for the fixed layer, not for generation: {anchors}. "
        "Leave clean product placement space in the lower center and readable text space near the upper-left. "
        f"Background direction: {card.get('background_prompt', '')}"
    )
    out_path = out_dir / f"xhs_card_{idx}_background.png"
    await MOD.gpt_image_generate(prompt, openai_key(), str(out_path), size="1024x1536")
    return out_path


def compose_card(background: Path, product_layer: Path, card: dict[str, Any], idx: int, out_dir: Path) -> Path:
    out_path = out_dir / f"xhs_card_{idx}.png"
    text_path = out_dir / f"xhs_card_{idx}_text.txt"
    text_path.write_text(text_for_image(str(card.get("overlay_text", ""))), encoding="utf-8")

    font_path = Path("/home/ubuntu/video-shopping/workflow_ui/fonts/NotoSansSC-VF.ttf")
    if not font_path.exists():
        font_path = Path("/home/ubuntu/video-shopping/workflow_ui/fonts/msyh.ttc")
    if not font_path.exists():
        raise RuntimeError("Chinese font not found for image text overlay")

    overlay_filter = (
        "[0:v]scale=1080:1440[bg];"
        "[1:v]scale=780:860:force_original_aspect_ratio=decrease[prod];"
        "[bg][prod]overlay=x=(W-w)/2:y=500[tmp];"
        "[tmp]drawbox=x=52:y=76:w=720:h=220:color=black@0.42:t=fill,"
        f"drawtext=fontfile='{font_path}':textfile='{text_path}':"
        "fontcolor=white:fontsize=64:x=84:y=112:line_spacing=12[out]"
    )
    proc = subprocess.run(
        [
            MOD.FFMPEG,
            "-y",
            "-v",
            "error",
            "-i",
            str(background),
            "-i",
            str(product_layer),
            "-filter_complex",
            overlay_filter,
            "-map",
            "[out]",
            str(out_path),
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError("card compose failed: " + proc.stderr.strip())
    return out_path


async def generate_images(plan: dict[str, Any], product_image: Path, out_dir: Path) -> list[str]:
    os.environ.setdefault("BILLING_OK", "1")
    os.environ.setdefault("OPENAI_HTTP_TIMEOUT", "600")
    product_layer = make_product_layer(product_image, out_dir)
    outputs: list[str] = []
    for idx, card in enumerate(plan["image_cards"], start=1):
        print(f"[image] {idx}/4 background-only {card.get('type','')}")
        background = await generate_background(plan, card, idx, out_dir)
        composed = compose_card(background, product_layer, card, idx, out_dir)
        outputs.append(str(composed))
    return outputs


def esc(text: str) -> str:
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


def write_html(plan: dict[str, Any], image_paths: list[str], out_dir: Path, content_id: str) -> Path:
    image_tags = []
    cards = plan.get("image_cards", [])
    for idx, path in enumerate(image_paths, start=1):
        card = cards[idx - 1] if idx - 1 < len(cards) else {}
        image_tags.append(
            f'<section><h2>{esc(card.get("type", f"图{idx}"))}</h2><img src="/content-files/{content_id}/outputs/{Path(path).name}" /><p>{esc(card.get("caption",""))}</p></section>'
        )
    body = esc(plan.get("body", ""))
    subject = plan.get("product_subject", {})
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>小红书买手笔记成品</title>
<style>
body {{ margin:0; font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:#f6f3ef; color:#241812; }}
main {{ max-width: 1080px; margin: 0 auto; padding: 28px 18px 64px; }}
h1 {{ font-size: 28px; line-height: 1.25; }}
.note, section {{ background: rgba(255,255,255,.86); border:1px solid rgba(70,40,25,.12); border-radius:16px; padding:18px; margin:18px 0; }}
img {{ width:100%; max-width:520px; display:block; border-radius:14px; box-shadow:0 16px 50px rgba(50,35,25,.16); }}
.grid {{ display:grid; grid-template-columns: repeat(auto-fit,minmax(280px,1fr)); gap:18px; align-items:start; }}
.tags {{ color:#7a3b23; font-weight:650; }}
.muted {{ color:#7a6b64; font-size:14px; }}
</style>
</head>
<body><main>
<h1>{esc(plan.get("title",""))}</h1>
<p class="muted">识别商品：{esc(subject.get("category",""))} / {esc(subject.get("subject_name",""))}</p>
<div class="grid">{''.join(image_tags)}</div>
<div class="note"><h2>正文</h2><p>{body}</p><p class="tags">{esc(plan.get("hashtags_text",""))}</p><h2>评论引导</h2><p>{esc(plan.get("comment_prompt",""))}</p></div>
</main></body></html>"""
    path = out_dir / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-image", required=True)
    parser.add_argument("--params-image", required=True)
    parser.add_argument("--product-params", default="")
    args = parser.parse_args()

    content_id = datetime.utcnow().strftime("%Y%m%d-%H%M%S") + "-xhsfull"
    run_dir = CONTENT_RUNS_DIR / content_id
    input_dir = run_dir / "inputs"
    out_dir = run_dir / "outputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    product = input_dir / "product.png"
    params = input_dir / "params.png"
    shutil.copyfile(args.product_image, product)
    shutil.copyfile(args.params_image, params)

    print(f"[run] {content_id}")
    print("[buyer] generating finished note package")
    plan = call_buyer_model(product, params, args.product_params)
    (out_dir / "finished_buyer_note.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[buyer] plan saved")

    image_paths = await generate_images(plan, product, out_dir)
    html_path = write_html(plan, image_paths, out_dir, content_id)
    print("[done] " + str(html_path))
    print("RESULT_URL=http://119.91.223.210:8600/content-files/" + content_id + "/outputs/index.html")


if __name__ == "__main__":
    asyncio.run(main())
