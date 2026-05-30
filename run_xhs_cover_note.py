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
你是“小红书买手内容策划 + 封面导演”。

你的分工很明确：
1. 你负责识别商品是什么、适合什么场景、买点和风险边界。
2. 你负责写一篇可发布的小红书买手笔记。
3. 你负责给“背景图”下导演指令。
4. 商品主体不由图片模型生成，商品主体会由脚本把用户上传的原图固定合成进去。

重要原则：
- 不要把商品写死成任何固定品类。必须根据用户上传图片判断品类。
- 不要让背景图生成商品本体、相似替代品、包装、logo、文字或水印。
- 可以有审美和购买判断，但不能编造未提供的材质、认证、功效、销量、价格、医疗效果或绝对承诺。
- 没有证据的信息写进 uncertain_points 或 needs_user_input，不要硬编。
- 封面必须是“购买冲突 + 买手判断”，不是温和详情页。
- 输出必须是简体中文内容，且只返回有效 JSON，不要 Markdown，不要 JSON 外解释。
""".strip()


def build_user_prompt(product_params: str, image_count: int) -> str:
    params = product_params.strip() if product_params else "用户未提供额外参数，只能根据图片做保守判断。"
    knowledge = load_knowledge() or "暂无额外知识库，请按平台常识和买手内容逻辑生成。"
    image_roles = [
        "Image 1 是必须固定的商品主体图。识别商品时以它为第一优先级；后续封面合成也会直接使用这张图。",
    ]
    for idx in range(2, image_count + 1):
        image_roles.append(f"Image {idx} 是参数、详情、场景或补充参考图，只用于识别卖点和事实边界。")

    return f"""
请基于上传图片和人工参数，生成“笔记 + 1 张封面”的成品包。

图片角色：
{chr(10).join("- " + role for role in image_roles)}

人工补充参数：
{params}

知识库：
{knowledge}

严格返回这个 JSON 结构：
{{
  "product_subject": {{
    "category": "",
    "subject_name": "",
    "must_keep_visuals": [],
    "do_not_generate_in_background": [],
    "uncertain_points": []
  }},
  "title": "",
  "cover": {{
    "overlay_text": "",
    "background_prompt": "",
    "product_layer_instruction": "",
    "click_reason": ""
  }},
  "body": "",
  "hashtags_text": "",
  "comment_prompt": "",
  "compliance_notes": [],
  "needs_user_input": []
}}

字段要求：
- product_subject.category：你识别出的商品品类，不允许凭空换品类。
- product_subject.subject_name：商品主体的自然语言名称。
- product_subject.must_keep_visuals：必须保持的可见特征，比如形状、颜色、材质观感、标签、结构、配件。
- product_subject.do_not_generate_in_background：背景图里禁止生成的对象，必须根据当前商品动态填写，不要写死某个品类。
- cover.overlay_text：封面中文短句，8-16 个中文字符左右，必须有点击冲突或买手判断。
- cover.background_prompt：只描述环境、光线、构图、情绪、留白；不能要求图片模型生成商品本体。
- cover.product_layer_instruction：说明真实商品图层应该如何摆放，比如居中、下半区、略带阴影。
- body：可直接发布的小红书正文，像买手，不像详情页。
- hashtags_text：一行可复制标签。

封面规格：
- 最终图为 1080x1440，3:4 竖图。
- 背景由图片模型生成，商品由脚本固定合成，文字由脚本本地压上去。
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
    for key in ("product_subject", "title", "cover", "body", "hashtags_text", "comment_prompt"):
        if key not in plan:
            raise RuntimeError("buyer note missing required field: " + key)
    subject = plan["product_subject"]
    cover = plan["cover"]
    if not isinstance(subject, dict):
        raise RuntimeError("product_subject must be an object")
    if not isinstance(cover, dict):
        raise RuntimeError("cover must be an object")
    for key in ("category", "subject_name"):
        if not str(subject.get(key, "")).strip():
            raise RuntimeError("product_subject missing required field: " + key)
    for key in ("overlay_text", "background_prompt"):
        if not str(cover.get(key, "")).strip():
            raise RuntimeError("cover missing required field: " + key)


def call_buyer_model(images: list[Path], product_params: str) -> dict[str, Any]:
    content: list[dict[str, Any]] = [{"type": "text", "text": build_user_prompt(product_params, len(images))}]
    for idx, image in enumerate(images, start=1):
        content.append({"type": "text", "text": f"Image {idx}: see role list in prompt."})
        content.append({"type": "image_url", "image_url": {"url": MOD.file_to_data_url(str(image))}})
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


async def generate_background(plan: dict[str, Any], out_dir: Path) -> Path:
    os.environ.setdefault("BILLING_OK", "1")
    os.environ.setdefault("OPENAI_HTTP_TIMEOUT", "600")
    cover = plan["cover"]
    subject = plan["product_subject"]
    forbidden = list_text(subject.get("do_not_generate_in_background"))
    visual_anchors = list_text(subject.get("must_keep_visuals"))
    prompt = (
        "Create a Xiaohongshu buyer-note cover BACKGROUND ONLY, vertical 1024x1536. "
        "The image is only the environment behind a product layer that will be composited later. "
        "Do not generate the product subject, any similar substitute product, packaging, logo, text, labels, words, watermark, or UI. "
        f"Recognized product category to avoid drawing: {subject.get('category', '')}. "
        f"Recognized product subject to avoid drawing: {subject.get('subject_name', '')}. "
        f"Must not appear in background: {forbidden}. "
        f"Product visual anchors reserved for the fixed layer, not for generation: {visual_anchors}. "
        "Leave clean empty placement space in the lower center for the real product layer, and clean text space near the upper-left. "
        f"Background direction: {cover.get('background_prompt', '')}"
    )
    out_path = out_dir / "xhs_cover_background.png"
    await MOD.gpt_image_generate(prompt, openai_key(), str(out_path), size="1024x1536")
    return out_path


def make_product_layer(product_image: Path, out_dir: Path) -> Path:
    """Keep the uploaded product subject as the fixed product layer."""
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


def cover_text(text: str) -> str:
    value = re.sub(r"\s+", "", str(text or "").strip())
    if not value:
        return "买前先看这点"
    if len(value) <= 10:
        return value
    return value[:10] + "\n" + value[10:18]


async def generate_cover(plan: dict[str, Any], images: list[Path], out_dir: Path) -> Path:
    """Create cover as AI background + fixed original product layer + local text."""
    cover = plan["cover"]
    out_path = out_dir / "xhs_cover.png"
    text_path = out_dir / "cover_text.txt"
    text_path.write_text(cover_text(str(cover.get("overlay_text", ""))), encoding="utf-8")

    font_path = Path("/home/ubuntu/video-shopping/workflow_ui/fonts/NotoSansSC-VF.ttf")
    if not font_path.exists():
        font_path = Path("/home/ubuntu/video-shopping/workflow_ui/fonts/msyh.ttc")
    if not font_path.exists():
        raise RuntimeError("Chinese font not found for cover text overlay")

    background = await generate_background(plan, out_dir)
    product_layer = make_product_layer(images[0], out_dir)

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
        raise RuntimeError("fixed product cover compose failed: " + proc.stderr.strip())
    return out_path


def esc(text: str) -> str:
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


def write_html(plan: dict[str, Any], cover_path: Path, out_dir: Path, content_id: str) -> Path:
    subject = plan.get("product_subject", {})
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>小红书买手笔记成品</title>
<style>
body{{margin:0;background:#f6f1ec;color:#211713;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
main{{max-width:860px;margin:0 auto;padding:24px 16px 60px}}
.card{{background:#fff;border:1px solid rgba(60,35,20,.12);border-radius:16px;padding:18px;margin:16px 0}}
img{{width:100%;max-width:540px;border-radius:14px;display:block;box-shadow:0 16px 48px rgba(40,24,16,.18)}}
h1{{font-size:28px;line-height:1.25}} .tags{{color:#7a3b23;font-weight:700}} .muted{{color:#7a6b64;font-size:14px}}
</style></head><body><main>
<h1>{esc(plan.get("title",""))}</h1>
<p class="muted">识别商品：{esc(subject.get("category",""))} / {esc(subject.get("subject_name",""))}</p>
<div class="card"><img src="/content-files/{content_id}/outputs/{cover_path.name}"/></div>
<div class="card"><h2>正文</h2><p>{esc(plan.get("body",""))}</p><p class="tags">{esc(plan.get("hashtags_text",""))}</p><h2>评论引导</h2><p>{esc(plan.get("comment_prompt",""))}</p></div>
</main></body></html>"""
    path = out_dir / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", action="append", required=True, help="First image is the fixed product subject; later images are references.")
    parser.add_argument("--product-params", default="")
    args = parser.parse_args()

    content_id = datetime.utcnow().strftime("%Y%m%d-%H%M%S") + "-xhscov"
    run_dir = CONTENT_RUNS_DIR / content_id
    input_dir = run_dir / "inputs"
    out_dir = run_dir / "outputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    copied: list[Path] = []
    for idx, src in enumerate(args.image, start=1):
        dst = input_dir / f"ref_{idx}{Path(src).suffix or '.png'}"
        shutil.copyfile(src, dst)
        copied.append(dst)

    print(f"[run] {content_id}")
    print("[buyer] generating note + generic cover plan")
    plan = call_buyer_model(copied, args.product_params)
    (out_dir / "buyer_note_cover.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[image] generating background-only cover and compositing fixed product layer")
    cover = await generate_cover(plan, copied, out_dir)
    html = write_html(plan, cover, out_dir, content_id)
    print("[done] " + str(html))
    print("RESULT_URL=http://119.91.223.210:8600/content-files/" + content_id + "/outputs/index.html")
    print("COVER_URL=http://119.91.223.210:8600/content-files/" + content_id + "/outputs/" + cover.name)


if __name__ == "__main__":
    asyncio.run(main())
