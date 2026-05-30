#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import asyncio
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import requests

BASE = Path('/home/ubuntu/video-shopping/scripts/build_video.py')
SPEC = importlib.util.spec_from_file_location('build_video_base', BASE)
MOD = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MOD)

PROVIDERS_PATH = Path('/home/ubuntu/video-shopping/work/providers.json')
PROVIDER_NAME = os.environ.get('PROVIDER', '').strip()
if PROVIDERS_PATH.exists():
    MOD.apply_provider(PROVIDER_NAME, str(PROVIDERS_PATH))

DEFAULT_PRODUCT_IMAGE = '/home/ubuntu/video-shopping/work/refs/product_main_ref1.png'
DEFAULT_MODEL_IMAGES = [
    '/home/ubuntu/video-shopping/work/refs/product_main_ref1.png',
    '/home/ubuntu/video-shopping/work/refs/model_fixed_board1.png',
]
DEFAULT_OUT_DIR = '/home/ubuntu/video-shopping/out/images-only-run'
AD_DIRECTOR_MODEL = os.environ.get('AD_DIRECTOR_MODEL', 'gpt-5.4-mini').strip()

AD_DIRECTOR_SYSTEM_PROMPT = """
You are an AI short-video creative director, product value visualization director, and visual consistency director.

Your job is not to imitate ordinary human product videos, write generic image prompts, or create final ad posters. Your job is to inspect the uploaded product, understand its real category and visible value, then turn that value into a comfortable, memorable, short-video-oriented production storyboard.

Creative principle:
- The picture language may be imaginative, cinematic, symbolic, rhythmic, and visually heightened.
- The product claim must remain truthful. Exaggerate the visual expression, not the factual promise.
- You may visualize a feeling, benefit, or usage experience as a metaphor, but you must not imply nonexistent specifications, medical effects, safety ratings, durability levels, certifications, quantitative results, or guaranteed outcomes.
- The goal is not "looks like a real creator shot it"; the goal is emotion rhythm, visual memory, product understanding efficiency, and a frame sequence people can comfortably keep watching.

Input roles:
- Product image: the only source of the product. This defines category, color, material, shape, markings, labels, structure, and all must-keep details.
- Fixed model references: define only model identity, face, hairstyle, hair length, body type, styling temperament, clothing family, and posing style.
- Any object held by the model in fixed model references is a prop, not the product. Ignore bottles, devices, placeholders, sample items, or accessories in the model references.

Directing rules:
- First identify the product category, then choose category-specific advertising shots.
- For shoes: use on-foot display, walking/landing motion, low-angle tracking, outsole/upper close-ups, outfit match, side-step and turn shots.
- For phone cases: use installation, hand grip, corner protection, camera cutout, material reflection, desk scene, rotation, and transition by light sweep or hand turn.
- For bags: use shoulder/hand carry, opening/capacity, texture close-up, commute scene, outfit match, and movement transitions.
- For beauty products: use texture, use step, mirror result, before/after, hand application, and soft light close-ups.
- For small appliances: use button/interaction close-up, function demo, before/after, home scene, indicator details, and operation motion.
- If category is uncertain, be conservative and do not invent features.
- Separate every idea into three layers before writing prompts:
  1. Truth layer: what can be safely inferred from the product image and user input.
  2. Experience layer: what feeling, convenience, protection, comfort, texture, atmosphere, or usage value can be visualized.
  3. Boundary layer: what must not be implied as a factual claim.
- Use AI-native visual imagination where useful: impact ripples, light sweeps, alignment locks, texture macro windows, rhythm lines, environmental transitions, split-second before/after reveals, product-centered motion graphics, and surreal but understandable product-value metaphors.
- Keep the video comfortable to watch: clear subject priority, clean backgrounds, controlled motion, no chaotic over-editing, no pointless visual tricks, no art-film abstraction that hides product understanding.
- The storyboard is not the final photographic work. It is a director's shooting execution board for a photographer/video team.
- A storyboard panel must show planned composition plus execution notes, not just a beautiful finished ad image.
- Each storyboard panel must include readable concise labels for shot number, shot size/camera angle, camera movement, model action, product presentation, transition, copy beat, sound cue, and value metaphor.
- Keep the same model identity as fixed references. Do not turn the model into a generic fashion model. Preserve short dark bob/shoulder-length hair, face shape, age impression, calm expression style, cream knit top, and light gray trousers unless the product category makes clothing adaptation unavoidable.
- Before writing storyboard prompts, extract a concrete model identity lock from the fixed model references: face shape, eye shape, nose/mouth impression, hairstyle, hair parting, hair length, expression temperament, clothing color family, and body proportion. Put this identity lock into the storyboard prompt.
- Do not introduce bangs/fringe, long hair, different hair color, different age impression, different face shape, heavy makeup, or a different fashion-model face unless those features are clearly present in the fixed model references.
- When the model appears, keep the fixed model recognizable. If exact facial similarity is hard, reduce face-dominant close-ups and use product/hand/body shots instead of inventing a new face.
- Preserve category-appropriate brand aesthetics. If the product naturally calls for premium, editorial, cinematic, or minimalist advertising language, use it, but keep it as a shooting execution board rather than a final artwork.
- Do not default every product into premium/editorial/cinematic language. Match the product: street-market, practical, cute, cheap, rugged, home-use, tech, luxury, beauty, or lifestyle products should receive different rhythm, backgrounds, camera distance, and visual metaphors.
- The storyboard image must contain exactly 8 panels, clearly numbered 1 through 8. Missing panels or repeated numbers are not acceptable.
- For phone cases and small accessories, do not use bag, zipper, pocket, purse, or storage insertion scenes unless the product itself is a bag/storage item. Those scenes hide the product and weaken execution clarity.
- In every functional demonstration panel, keep the product mostly visible and identifiable. Do not bury, cover, crop away, or obscure the product.

Output requirements:
- Return valid JSON only. No markdown. No explanation outside JSON.
- Product consistency has priority over visual prettiness.
- Model identity consistency has priority over glamour retouching.
- Product truth has priority over visual imagination.
- The image prompts must explicitly forbid category replacement, blank outputs, placeholders, template pages, and using model-reference props as the product.
- The storyboard_prompt must explicitly request a production storyboard board with annotated execution fields, not a pure photo collage and not a final campaign layout.
- The storyboard_prompt must require exactly 8 numbered panels, 1 through 8, while preserving the product's proper brand-level shooting style.
- The storyboard_prompt must include a model identity lock section and explicitly forbid changing hairstyle, bangs/fringe, face shape, age impression, and clothing family from the fixed model references.
- The storyboard_prompt must include a truth boundary section: visual metaphors are allowed, but factual claims must not be invented or overstated.
- The storyboard_prompt must include emotion rhythm, sound cues, transitions, and product-value visualization in the panel notes.
- For phone cases, the storyboard_prompt must favor installation, hand grip, camera cutout, corner lip, button detail, texture/perforation, tabletop, commute hand-held use, and screen/camera protection. It must avoid bag/pocket/zipper insertion shots.
""".strip()


def resolve_file(path_text, label):
    if not path_text:
        raise RuntimeError(label + ' path is empty')
    p = Path(path_text).expanduser()
    if not p.exists():
        raise RuntimeError(label + ' not found: ' + str(p))
    return str(p)


def probe_image_resolution(path_text):
    cmd = [
        MOD.FFPROBE,
        '-v',
        'error',
        '-select_streams',
        'v:0',
        '-show_entries',
        'stream=width,height',
        '-of',
        'csv=s=x:p=0',
        path_text,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError('ffprobe failed for image: ' + path_text + ' :: ' + proc.stderr.strip())
    txt = proc.stdout.strip()
    if 'x' not in txt:
        raise RuntimeError('invalid image resolution from ffprobe: ' + path_text + ' :: ' + txt)
    width_text, height_text = txt.split('x', 1)
    return int(width_text), int(height_text)


def validate_resolution(path_text, label, min_width, min_height):
    width, height = probe_image_resolution(path_text)
    print(f"{label}: {path_text} ({width}x{height})")
    if width < min_width or height < min_height:
        raise RuntimeError(
            f"{label} resolution too small: {width}x{height}. "
            f"Minimum required is {min_width}x{min_height}."
        )
    return width, height


def read_rgb_pixels(path_text):
    width, height = probe_image_resolution(path_text)
    proc = subprocess.run(
        [
            MOD.FFMPEG,
            '-v',
            'error',
            '-i',
            path_text,
            '-f',
            'rawvideo',
            '-pix_fmt',
            'rgb24',
            '-',
        ],
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError('ffmpeg raw read failed: ' + path_text + ' :: ' + proc.stderr.decode('utf-8', 'ignore'))
    return width, height, proc.stdout


def find_non_white_bbox(path_text, threshold=246):
    width, height, data = read_rgb_pixels(path_text)
    min_x, min_y = width, height
    max_x, max_y = -1, -1
    total = width * height
    non_white = 0
    for idx in range(total):
        off = idx * 3
        r, g, b = data[off], data[off + 1], data[off + 2]
        if r < threshold or g < threshold or b < threshold:
            x = idx % width
            y = idx // width
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
            non_white += 1
    if max_x < 0:
        return None, 0.0
    return (min_x, min_y, max_x, max_y), non_white / max(1, total)


def make_focused_product_image(product_image, out_root):
    bbox, coverage = find_non_white_bbox(product_image)
    if bbox is None or coverage < 0.005:
        print('product focus crop: skipped; no clear non-white subject detected')
        return product_image

    width, height = probe_image_resolution(product_image)
    min_x, min_y, max_x, max_y = bbox
    box_w = max_x - min_x + 1
    box_h = max_y - min_y + 1
    pad = max(24, int(max(box_w, box_h) * 0.12))
    x = max(0, min_x - pad)
    y = max(0, min_y - pad)
    crop_w = min(width - x, box_w + pad * 2)
    crop_h = min(height - y, box_h + pad * 2)
    if crop_w <= 0 or crop_h <= 0:
        return product_image

    focused_path = str(Path(out_root) / 'product_focus.png')
    vf = (
        f'crop={crop_w}:{crop_h}:{x}:{y},'
        'scale=1024:1024:force_original_aspect_ratio=decrease,'
        'pad=1024:1024:(ow-iw)/2:(oh-ih)/2:color=white'
    )
    proc = subprocess.run(
        [MOD.FFMPEG, '-y', '-v', 'error', '-i', product_image, '-vf', vf, focused_path],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print('product focus crop: failed; using original :: ' + proc.stderr.strip())
        return product_image
    print(f'product focus image: {focused_path} crop={crop_w}x{crop_h}+{x}+{y} coverage={coverage:.4f}')
    return focused_path


def assert_not_blank_image(path_text, label):
    bbox, coverage = find_non_white_bbox(path_text)
    print(f'{label} non-white coverage: {coverage:.4f}')
    if bbox is None or coverage < 0.01:
        raise RuntimeError(f'{label} appears blank; stopping before downstream generation')


def extract_json_object(text):
    text = (text or '').strip()
    if text.startswith('```'):
        text = text.strip('`')
        if text.lower().startswith('json'):
            text = text[4:].strip()
    try:
        return json.loads(text)
    except Exception:
        start = text.find('{')
        end = text.rfind('}')
        if start >= 0 and end > start:
            return json.loads(text[start:end + 1])
        raise


def build_director_user_prompt():
    return """
Inspect the product image and fixed model references.

Create a director plan for generating:
1. a product three-view image
2. a horizontal 8-shot advertising shooting storyboard board

The storyboard must be category-specific and short-video-native. Do not use a fixed generic template. For example, shoes need on-foot rhythm, landing, outsole/upper value visualization, walking transitions, and outfit proportion; phone cases need installation, grip, camera cutout, button feedback, corner protection feeling, texture, and daily hand-held use.

Core creative boundary:
- Visual expression can be exaggerated, symbolic, and more imaginative than real shooting.
- Product capability cannot be exaggerated as fact.
- If you use visual effects such as shields, ripples, airflow, light locks, pressure waves, or material windows, write them as metaphorical product-value visualization, not factual proof.
- Never invent numbers, certifications, medical/repair effects, impossible durability, impossible waterproofing, or guaranteed results.
- The storyboard should make people comfortable to keep watching through emotion rhythm, visual memory, sound cues, clean composition, and clear product understanding.

Important storyboard purpose:
- It is not the final ad artwork.
- It is not a clean photo collage.
- It is a director's execution board that tells a photographer/video team exactly how to shoot.
- Each of the 8 panels must include a planned visual frame plus concise production notes.
- Required per-panel notes must be written as clear English labels: shot number, shot size/camera angle, camera movement, model action, product presentation, transition, copy beat, sound cue, value metaphor.
- The image should still be visually polished, but it must read as a storyboard/execution board.
- The storyboard prompt must require exactly 8 panels numbered 1/8 through 8/8. Do not omit any number.
- Do not flatten a product's natural brand tone. Premium/editorial/cinematic camera language is allowed when it matches the product category, as long as the board remains executable.

Return this exact JSON shape:
{
  "product_profile": {
    "category": "",
    "visual_summary": "",
    "must_keep": [],
    "must_not_be": [],
    "risk_notes": []
  },
  "directing_strategy": {
    "ad_style": "",
    "model_usage": "",
    "emotional_curve": [],
    "retention_strategy": "",
    "camera_language": [],
    "sound_cues": [],
    "transitions": []
  },
  "truth_boundary": {
    "real_claims": [],
    "visual_exaggerations_allowed": [],
    "forbidden_implications": []
  },
  "three_view_prompt": "",
  "storyboard_prompt": ""
}

Prompt requirements:
- three_view_prompt must tell the image model to draw the exact uploaded product in front, 45-degree, and side views.
- storyboard_prompt must tell the image model to draw 8 production storyboard panels, each with a real planned visual frame plus readable execution labels.
- storyboard_prompt must explicitly forbid a pure photo collage/final campaign layout with no annotations.
- storyboard_prompt must explicitly require the fixed model's identity and short dark bob/shoulder-length hairstyle to remain consistent across panels.
- storyboard_prompt must explicitly include: truth boundary, emotional rhythm, sound cues, transitions, and product-value visualization.
- storyboard_prompt must explicitly say that visual metaphors are allowed only as metaphors, not factual claims.
- Both prompts must explicitly state that fixed model reference props are not the product.
- Both prompts must explicitly preserve the product category and must-keep visual details.
""".strip()


def validate_director_plan(plan):
    three_prompt = str(plan.get('three_view_prompt', '')).strip()
    storyboard_prompt = str(plan.get('storyboard_prompt', '')).strip()
    if not three_prompt or not storyboard_prompt:
        raise RuntimeError('ad director JSON missing required prompts')
    for key in ('product_profile', 'directing_strategy', 'truth_boundary'):
        if not isinstance(plan.get(key), dict):
            raise RuntimeError('ad director JSON missing required object: ' + key)
    truth_boundary = plan.get('truth_boundary', {})
    for key in ('real_claims', 'visual_exaggerations_allowed', 'forbidden_implications'):
        if key not in truth_boundary:
            raise RuntimeError('ad director truth_boundary missing required field: ' + key)

    lowered_prompt = storyboard_prompt.lower()
    required_term_groups = [
        ('shot number', ['shot number', 'panel number', 'numbered', '1/8', '1 through 8']),
        ('camera', ['camera', 'shot size', 'camera angle']),
        ('camera movement', ['camera movement', 'camera motion', 'push-in', 'pull-back', 'pan', 'tracking']),
        ('model action', ['model action', 'model movement', 'hand action', 'body action']),
        ('product presentation', ['product presentation', 'product display', 'product visibility', 'product remains visible']),
        ('transition', ['transition', 'cut', 'match cut', 'light sweep', 'hand turn']),
        ('copy beat', ['copy beat', 'caption beat', 'subtitle beat', 'text beat']),
        ('sound cue', ['sound cue', 'audio cue', 'sfx', 'sound effect']),
        ('value metaphor', ['value metaphor', 'visual metaphor', 'value visualization', 'product-value visualization']),
        ('truth boundary', ['truth boundary', 'factual claims', 'not factual claims', 'do not invent']),
    ]
    missing = [
        label
        for label, alternatives in required_term_groups
        if not any(term in lowered_prompt for term in alternatives)
    ]
    if missing:
        raise RuntimeError('ad director storyboard prompt missing execution fields: ' + ', '.join(missing))

    banned_phrases = [
        'fully rendered, photo-real advertising shot',
        'each frame must be a fully rendered',
        'guaranteed result',
        'certified protection',
        'medical repair',
        'waterproof rating:',
        'drop-proof from',
    ]
    bad = [phrase for phrase in banned_phrases if phrase in lowered_prompt]
    if bad:
        raise RuntimeError('ad director storyboard prompt overstates product proof or looks like final artwork: ' + ', '.join(bad))


def call_ad_director(product_image, model_images, out_root):
    if not AD_DIRECTOR_MODEL:
        return None

    url = f"{MOD.OPENAI_BASE_URL}/chat/completions"
    content = [
        {'type': 'text', 'text': build_director_user_prompt()},
        {'type': 'text', 'text': 'Image 1 is the uploaded product image. All later images are fixed model references.'},
        {'type': 'image_url', 'image_url': {'url': MOD.file_to_data_url(product_image)}},
    ]
    for idx, image_path in enumerate(model_images, start=1):
        content.append({'type': 'text', 'text': f'Fixed model reference {idx}. Use only model identity and styling, ignore any held object.'})
        content.append({'type': 'image_url', 'image_url': {'url': MOD.file_to_data_url(image_path)}})

    payload = {
        'model': AD_DIRECTOR_MODEL,
        'messages': [
            {'role': 'system', 'content': AD_DIRECTOR_SYSTEM_PROMPT},
            {'role': 'user', 'content': content},
        ],
        'temperature': 0.2,
        'response_format': {'type': 'json_object'},
    }
    body = json.dumps(payload, ensure_ascii=False)
    headers = {
        'Authorization': f'Bearer {MOD.OPENAI_API_KEY}',
        'Content-Type': 'application/json',
    }
    print(f'ad director: calling {AD_DIRECTOR_MODEL}')
    resp = requests.post(url, headers=headers, data=body.encode('utf-8'), timeout=MOD.OPENAI_HTTP_TIMEOUT)
    req_id = resp.headers.get('x-oai-request-id') or resp.headers.get('X-Oai-Request-Id')
    if req_id:
        print(f'ad director request-id: {req_id}')
    if resp.status_code >= 400:
        raise RuntimeError(f'ad director failed: {resp.status_code} {resp.text}')
    data = resp.json()
    message = data.get('choices', [{}])[0].get('message', {})
    content_text = message.get('content', '')
    plan = extract_json_object(content_text)
    if not isinstance(plan, dict):
        raise RuntimeError('ad director returned non-object JSON')
    raw_plan_path = Path(out_root) / 'ad_director_plan.raw.json'
    raw_plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    validate_director_plan(plan)

    plan_path = Path(out_root) / 'ad_director_plan.json'
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding='utf-8')
    print('ad director plan: ' + str(plan_path))
    print('product profile: ' + json.dumps(plan.get('product_profile', {}), ensure_ascii=False)[:500])
    return plan


def resolve_model_images(cli_model_images):
    if cli_model_images:
        values = cli_model_images
    else:
        env1 = os.environ.get('MODEL_IMAGE_1', '').strip()
        env2 = os.environ.get('MODEL_IMAGE_2', '').strip()
        values = [env1, env2] if env1 and env2 else DEFAULT_MODEL_IMAGES
    resolved = [resolve_file(v, f'model image {idx + 1}') for idx, v in enumerate(values) if str(v).strip()]
    if len(resolved) < 2:
        raise RuntimeError('fixed model images require at least 2 files')
    return resolved


async def main(product_image, model_images, out_dir, three_view_size, storyboard_size, min_width, min_height):
    os.environ.setdefault('OPENAI_HTTP_TIMEOUT', '600')
    os.environ.setdefault('OPENAI_IMAGE_RESPONSE_FORMAT', 'b64_json')

    validate_resolution(product_image, 'product image', min_width, min_height)
    for idx, image_path in enumerate(model_images, start=1):
        validate_resolution(image_path, f'model image {idx}', min_width, min_height)

    out_root = Path(out_dir)
    out_root.mkdir(parents=True, exist_ok=True)
    focused_product_image = make_focused_product_image(product_image, out_root)
    validate_resolution(focused_product_image, 'focused product image', min_width, min_height)
    three_view_path = str(out_root / 'kent_three_view.png')
    storyboard_path = str(out_root / 'kent_storyboard_overview.png')
    director_plan = call_ad_director(focused_product_image, model_images, out_root)
    if not director_plan:
        raise RuntimeError('ad director is required; no static prompt fallback is available')
    three_view_prompt = str(director_plan['three_view_prompt']).strip()
    storyboard_prompt = str(director_plan['storyboard_prompt']).strip()
    (out_root / 'three_view_prompt.txt').write_text(three_view_prompt, encoding='utf-8')
    (out_root / 'storyboard_prompt.txt').write_text(storyboard_prompt, encoding='utf-8')

    await MOD.gpt_image_generate_with_inputs(
        three_view_prompt,
        MOD.OPENAI_API_KEY,
        three_view_path,
        [focused_product_image],
        size=three_view_size,
    )
    assert_not_blank_image(three_view_path, 'three-view output')
    print(three_view_path)

    storyboard_inputs = [focused_product_image, three_view_path] + model_images
    await MOD.gpt_image_generate_with_inputs(
        storyboard_prompt,
        MOD.OPENAI_API_KEY,
        storyboard_path,
        storyboard_inputs,
        size=storyboard_size,
    )
    print(storyboard_path)


def cli():
    global AD_DIRECTOR_MODEL

    parser = argparse.ArgumentParser(
        description='Generate 2 images only: product three-view, then storyboard overview'
    )
    parser.add_argument(
        '--product-image',
        default=os.environ.get('PRODUCT_IMAGE', DEFAULT_PRODUCT_IMAGE),
        help='Product reference image path',
    )
    parser.add_argument(
        '--model-image',
        action='append',
        dest='model_images',
        help='Fixed model image path, provide at least 2 times',
    )
    parser.add_argument(
        '--output-dir',
        default=DEFAULT_OUT_DIR,
        help='Output directory',
    )
    parser.add_argument(
        '--three-view-size',
        default=os.environ.get('THREE_VIEW_IMAGE_SIZE', '1024x1024'),
        help='Three-view output size',
    )
    parser.add_argument(
        '--storyboard-size',
        default=os.environ.get('STORYBOARD_IMAGE_SIZE', '1536x1024'),
        help='Storyboard output size (default larger for readable Chinese text)',
    )
    parser.add_argument(
        '--min-width',
        type=int,
        default=int(os.environ.get('MIN_INPUT_WIDTH', '768')),
        help='Minimum input image width',
    )
    parser.add_argument(
        '--min-height',
        type=int,
        default=int(os.environ.get('MIN_INPUT_HEIGHT', '768')),
        help='Minimum input image height',
    )
    parser.add_argument(
        '--ad-director-model',
        default=os.environ.get('AD_DIRECTOR_MODEL', AD_DIRECTOR_MODEL),
        help='Vision/language model used to create product-specific prompts',
    )
    args = parser.parse_args()

    if args.ad_director_model:
        AD_DIRECTOR_MODEL = args.ad_director_model.strip()

    product_image = resolve_file(args.product_image, 'product image')
    model_images = resolve_model_images(args.model_images)
    asyncio.run(
        main(
            product_image,
            model_images,
            args.output_dir,
            args.three_view_size,
            args.storyboard_size,
            max(1, args.min_width),
            max(1, args.min_height),
        )
    )


if __name__ == '__main__':
    cli()
