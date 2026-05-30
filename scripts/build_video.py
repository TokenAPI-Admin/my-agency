#!/usr/bin/env python3
"""video-shopping build pipeline.

Generates scene images with gpt-image-2, turns them into short video clips
with Doubao Seedance 1.5 pro, creates a voiceover with Edge TTS, and
assembles the final vertical video with FFmpeg.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import requests

# Provider config helper (no network). This enables fast upstream switching.

def load_providers_config(path: str):
    p = Path(path)
    # Be tolerant of UTF-8 BOM (PowerShell sometimes writes BOM by default).
    data = json.loads(p.read_text(encoding='utf-8-sig'))
    default = data.get('default')
    providers = data.get('providers', {})
    if not isinstance(providers, dict):
        raise ValueError('providers.json invalid: providers must be an object')
    return default, providers


def apply_provider(provider_name: str, providers_path: str):
    default_name, providers = load_providers_config(providers_path)
    name = provider_name or default_name
    if not name:
        raise ValueError('No provider specified and no default provider set')
    if name not in providers:
        raise ValueError('Unknown provider: ' + name)
    cfg = providers[name] or {}

    # Apply to module globals used by generation functions.
    globals()['OPENAI_BASE_URL'] = str(cfg.get('openai_base_url', OPENAI_BASE_URL)).rstrip('/')
    globals()['OPENAI_IMAGE_MODEL'] = str(cfg.get('openai_image_model', OPENAI_IMAGE_MODEL))
    globals()['OPENAI_IMAGE_RESPONSE_FORMAT'] = str(cfg.get('openai_image_response_format', OPENAI_IMAGE_RESPONSE_FORMAT))
    globals()['OPENAI_IMAGE_SIZE'] = str(cfg.get('openai_image_size', OPENAI_IMAGE_SIZE))
    globals()['OPENAI_IMAGE_QUALITY'] = str(cfg.get('openai_image_quality', OPENAI_IMAGE_QUALITY))

    if cfg.get('seedance_base_url'):
        globals()['SEEDANCE_BASE_URL'] = str(cfg.get('seedance_base_url')).rstrip('/')
    if cfg.get('seedance_model'):
        globals()['SEEDANCE_MODEL'] = str(cfg.get('seedance_model'))

    return name, cfg


# Hard safety gate: prevent accidental paid generation during debugging.
BILLING_OK = os.environ.get('BILLING_OK', '').strip().lower() in {'1','true','yes','on'}
def _require_billing_ok(feature: str):
    if not BILLING_OK:
        raise RuntimeError('BILLING_OK is not set; refusing to run paid generation for: ' + feature + '. Set BILLING_OK=1 to enable.')

import hashlib
import hmac
from datetime import datetime, timezone

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"
PYTHON = shutil.which("python3") or "python3"

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_ACCESS_KEY_ID = os.environ.get("OPENAI_ACCESS_KEY_ID", "").strip()
OPENAI_SECRET_ACCESS_KEY = os.environ.get("OPENAI_SECRET_ACCESS_KEY", "").strip()
OPENAI_AK_REGION = os.environ.get("OPENAI_AK_REGION", "cn-north-1").strip()
OPENAI_AK_SERVICE = os.environ.get("OPENAI_AK_SERVICE", "air").strip()
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
OPENAI_IMAGE_MODEL = os.environ.get("OPENAI_IMAGE_MODEL", "gpt-image-2")
OPENAI_IMAGE_SIZE = os.environ.get("OPENAI_IMAGE_SIZE", "1024x1536")
OPENAI_IMAGE_QUALITY = os.environ.get("OPENAI_IMAGE_QUALITY", "high")
OPENAI_IMAGE_RESPONSE_FORMAT = os.environ.get("OPENAI_IMAGE_RESPONSE_FORMAT", "url")
OPENAI_SEQUENTIAL_IMAGE_GENERATION = os.environ.get("OPENAI_SEQUENTIAL_IMAGE_GENERATION", "disabled")
OPENAI_IMAGE_WATERMARK = os.environ.get("OPENAI_IMAGE_WATERMARK", "0").strip().lower() in {"1", "true", "yes"}
OPENAI_HTTP_TIMEOUT = int(os.environ.get("OPENAI_HTTP_TIMEOUT", "600"))

SEEDANCE_API_KEY = os.environ.get("SEEDANCE_API_KEY", os.environ.get("ARK_API_KEY", "")).strip()
SEEDANCE_BASE_URL = os.environ.get("SEEDANCE_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
SEEDANCE_MODEL = os.environ.get("SEEDANCE_MODEL", "doubao-seedance-1-5-pro-251215")
SEEDANCE_RESOLUTION = os.environ.get("SEEDANCE_RESOLUTION", "480p")
SEEDANCE_RATIO = os.environ.get("SEEDANCE_RATIO", "9:16")
SEEDANCE_DRAFT = os.environ.get("SEEDANCE_DRAFT", "1") != "0"
SEEDANCE_GENERATE_AUDIO = os.environ.get("SEEDANCE_GENERATE_AUDIO", "0") != "0"
SEEDANCE_CAMERA_FIXED = os.environ.get("SEEDANCE_CAMERA_FIXED", "0") != "1"
SEEDANCE_WATERMARK = os.environ.get("SEEDANCE_WATERMARK", "0") != "0"
SEEDANCE_POLL_INTERVAL = float(os.environ.get("SEEDANCE_POLL_INTERVAL", "5"))
SEEDANCE_TIMEOUT = int(os.environ.get("SEEDANCE_TIMEOUT", "1800"))
SEEDANCE_MAX_SCENES = int(os.environ.get("SEEDANCE_MAX_SCENES", "1"))

SFX_MAP = {
    "silence": None,
    "sizzle": "sfx/sizzle.mp3",
    "clink": "sfx/clink.mp3",
    "water": "sfx/water.mp3",
    "whoosh": "sfx/whoosh.mp3",
    "thud": "sfx/thud.mp3",
    "ding": "sfx/ding.mp3",
}


def run_cmd(cmd, cwd=None):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr}"
        )
    return proc


def ffprobe_duration(path):
    if not os.path.exists(path):
        return 0.0
    cmd = [
        FFPROBE,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        return 0.0
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return 0.0


def file_to_data_url(path):
    suffix = Path(path).suffix.lower().lstrip('.')
    mime = {
        'png': 'image/png',
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'webp': 'image/webp',
        'bmp': 'image/bmp',
        'tif': 'image/tiff',
        'tiff': 'image/tiff',
        'gif': 'image/gif',
        'heic': 'image/heic',
        'heif': 'image/heif',
    }.get(suffix, 'image/png')
    with open(path, 'rb') as f:
        encoded = base64.b64encode(f.read()).decode('ascii')
    return f'data:{mime};base64,{encoded}'


def download_file(url, output_path, timeout=300):
    resp = requests.get(url, stream=True, timeout=timeout)
    resp.raise_for_status()
    with open(output_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)
    return output_path


def _seedream_v4_sign_headers(method, host, path, body, access_key, secret_key, region='cn-north-1', service='air'):
    x_date = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    short_date = x_date[:8]
    content_sha256 = hashlib.sha256(body.encode('utf-8')).hexdigest()
    signed_headers = 'content-type;host;x-content-sha256;x-date'
    canonical_request = '\n'.join([
        method,
        path,
        '',
        f'content-type:application/json\nhost:{host}\nx-content-sha256:{content_sha256}\nx-date:{x_date}',
        '',
        signed_headers,
        content_sha256,
    ])
    hashed_canonical = hashlib.sha256(canonical_request.encode('utf-8')).hexdigest()
    credential_scope = f'{short_date}/{region}/{service}/request'
    string_to_sign = '\n'.join(['HMAC-SHA256', x_date, credential_scope, hashed_canonical])
    k_date = hmac.new(secret_key.encode('utf-8'), short_date.encode('utf-8'), hashlib.sha256).digest()
    k_region = hmac.new(k_date, region.encode('utf-8'), hashlib.sha256).digest()
    k_service = hmac.new(k_region, service.encode('utf-8'), hashlib.sha256).digest()
    k_signing = hmac.new(k_service, b'request', hashlib.sha256).digest()
    signature = hmac.new(k_signing, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    auth = f'HMAC-SHA256 Credential={access_key}/{credential_scope}, SignedHeaders={signed_headers}, Signature={signature}'
    return {
        'Host': host,
        'Content-Type': 'application/json',
        'X-Date': x_date,
        'X-Content-Sha256': content_sha256,
        'Authorization': auth,
    }


def make_placeholder_image(output_path, size='1080x1920'):
    cmd = [
        FFMPEG,
        '-y',
        '-f', 'lavfi',
        '-i', f'color=c=black:s={size}:d=1',
        '-frames:v', '1',
        output_path,
    ]
    run_cmd(cmd)
    return output_path


def make_silent_audio(duration, output_path):
    duration = max(0.1, float(duration))
    cmd = [
        FFMPEG,
        '-y',
        '-f', 'lavfi',
        '-i', 'anullsrc=r=44100:cl=mono',
        '-t', f'{duration:.3f}',
        '-c:a', 'libmp3lame',
        '-b:a', '128k',
        output_path,
    ]
    run_cmd(cmd)
    return output_path


def ensure_audio_track(video_path, output_path):
    cmd = [
        FFMPEG,
        '-y',
        '-i', video_path,
        '-f', 'lavfi',
        '-i', 'anullsrc=r=44100:cl=mono',
        '-shortest',
        '-c:v', 'copy',
        '-c:a', 'libmp3lame',
        '-b:a', '128k',
        output_path,
    ]
    run_cmd(cmd)
    return output_path


async def gpt_image_generate(prompt, api_key, output_path, size=None):
    _require_billing_ok('image')
    print(f"  [gpt-image-2] generating: {prompt[:80]}...")
    url = f"{OPENAI_BASE_URL}/images/generations"
    payload = {
        'model': OPENAI_IMAGE_MODEL,
        'prompt': prompt,
        'size': size or OPENAI_IMAGE_SIZE,
        'response_format': OPENAI_IMAGE_RESPONSE_FORMAT,
    }
    model_lower = str(OPENAI_IMAGE_MODEL).lower()
    if 'seedream' in model_lower:
        payload['sequential_image_generation'] = OPENAI_SEQUENTIAL_IMAGE_GENERATION
        payload['watermark'] = OPENAI_IMAGE_WATERMARK
    elif OPENAI_IMAGE_QUALITY:
        payload['quality'] = OPENAI_IMAGE_QUALITY
    body = json.dumps(payload, ensure_ascii=False)
    host = url.split('://', 1)[1].split('/', 1)[0]
    path = '/' + url.split('://', 1)[1].split('/', 1)[1]
    if OPENAI_ACCESS_KEY_ID and OPENAI_SECRET_ACCESS_KEY:
        headers = _seedream_v4_sign_headers('POST', host, path, body, OPENAI_ACCESS_KEY_ID, OPENAI_SECRET_ACCESS_KEY, OPENAI_AK_REGION, OPENAI_AK_SERVICE)
    else:
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }
    resp = requests.post(url, headers=headers, data=body.encode('utf-8'), timeout=OPENAI_HTTP_TIMEOUT)
    req_id = resp.headers.get('x-oai-request-id') or resp.headers.get('X-Oai-Request-Id')
    if req_id:
        print(f"  [gpt-image-2] request-id: {req_id}")
    if resp.status_code >= 400:
        raise RuntimeError(f"gpt-image-2 failed: {resp.status_code} {resp.text}")

    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"gpt-image-2 returned non-json payload: {resp.text[:2000]}")

    print(f"  [gpt-image-2] raw response keys: {list(data.keys())}")
    if 'data' in data and isinstance(data['data'], list) and data['data']:
        item = data['data'][0]
        if item.get('b64_json'):
            image_bytes = base64.b64decode(item['b64_json'])
            with open(output_path, 'wb') as f:
                f.write(image_bytes)
            return output_path
        if item.get('url'):
            return download_file(item['url'], output_path)
        if item.get('output_url'):
            return download_file(item['output_url'], output_path)
        if item.get('content') and isinstance(item['content'], dict):
            content = item['content']
            for key in ('b64_json', 'url', 'output_url', 'image_url'):
                if content.get(key):
                    if key == 'b64_json':
                        image_bytes = base64.b64decode(content['b64_json'])
                        with open(output_path, 'wb') as f:
                            f.write(image_bytes)
                        return output_path
                    return download_file(content[key], output_path)
        raise RuntimeError(f"gpt-image-2 returned unsupported data item: {item}")
    for key in ('url', 'output_url'):
        if data.get(key):
            return download_file(data[key], output_path)
    if data.get('b64_json'):
        image_bytes = base64.b64decode(data['b64_json'])
        with open(output_path, 'wb') as f:
            f.write(image_bytes)
        return output_path
    if data.get('content') and isinstance(data['content'], dict):
        content = data['content']
        for key in ('url', 'output_url', 'image_url'):
            if content.get(key):
                return download_file(content[key], output_path)
        if content.get('b64_json'):
            image_bytes = base64.b64decode(content['b64_json'])
            with open(output_path, 'wb') as f:
                f.write(image_bytes)
            return output_path
    if data.get('result') and isinstance(data['result'], dict):
        result = data['result']
        for key in ('url', 'output_url', 'image_url'):
            if result.get(key):
                return download_file(result[key], output_path)
        if result.get('b64_json'):
            image_bytes = base64.b64decode(result['b64_json'])
            with open(output_path, 'wb') as f:
                f.write(image_bytes)
            return output_path
    raise RuntimeError(f"gpt-image-2 returned unsupported payload: {data}")


async def gpt_image_generate_with_inputs(prompt, api_key, output_path, input_images, size=None):
    _require_billing_ok('image')
    refs = [str(p) for p in (input_images or []) if str(p).strip()]
    if not refs:
        raise RuntimeError('gpt-image-2 input images required')
    for p in refs:
        if not os.path.exists(p):
            raise RuntimeError('input image not found: ' + p)

    print(f"  [gpt-image-2] generating with {len(refs)} input image(s): {prompt[:80]}...")
    url = f"{OPENAI_BASE_URL}/images/generations"
    payload = {
        'model': OPENAI_IMAGE_MODEL,
        'prompt': prompt,
        'size': size or OPENAI_IMAGE_SIZE,
        'response_format': OPENAI_IMAGE_RESPONSE_FORMAT,
        'input_images': [file_to_data_url(p) for p in refs],
    }
    model_lower = str(OPENAI_IMAGE_MODEL).lower()
    if 'seedream' in model_lower:
        payload['sequential_image_generation'] = OPENAI_SEQUENTIAL_IMAGE_GENERATION
        payload['watermark'] = OPENAI_IMAGE_WATERMARK
    elif OPENAI_IMAGE_QUALITY:
        payload['quality'] = OPENAI_IMAGE_QUALITY

    body = json.dumps(payload, ensure_ascii=False)
    host = url.split('://', 1)[1].split('/', 1)[0]
    path = '/' + url.split('://', 1)[1].split('/', 1)[1]
    if OPENAI_ACCESS_KEY_ID and OPENAI_SECRET_ACCESS_KEY:
        headers = _seedream_v4_sign_headers('POST', host, path, body, OPENAI_ACCESS_KEY_ID, OPENAI_SECRET_ACCESS_KEY, OPENAI_AK_REGION, OPENAI_AK_SERVICE)
    else:
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }
    resp = requests.post(url, headers=headers, data=body.encode('utf-8'), timeout=OPENAI_HTTP_TIMEOUT)
    req_id = resp.headers.get('x-oai-request-id') or resp.headers.get('X-Oai-Request-Id')
    if req_id:
        print(f"  [gpt-image-2] request-id: {req_id}")
    if resp.status_code >= 400:
        raise RuntimeError(f"gpt-image-2 with inputs failed: {resp.status_code} {resp.text}")

    try:
        data = resp.json()
    except Exception:
        raise RuntimeError(f"gpt-image-2 with inputs returned non-json payload: {resp.text[:2000]}")

    print(f"  [gpt-image-2] raw response keys: {list(data.keys())}")
    if 'data' in data and isinstance(data['data'], list) and data['data']:
        item = data['data'][0]
        if item.get('b64_json'):
            image_bytes = base64.b64decode(item['b64_json'])
            with open(output_path, 'wb') as f:
                f.write(image_bytes)
            return output_path
        if item.get('url'):
            return download_file(item['url'], output_path)
        if item.get('output_url'):
            return download_file(item['output_url'], output_path)
        if item.get('content') and isinstance(item['content'], dict):
            content = item['content']
            for key in ('b64_json', 'url', 'output_url', 'image_url'):
                if content.get(key):
                    if key == 'b64_json':
                        image_bytes = base64.b64decode(content['b64_json'])
                        with open(output_path, 'wb') as f:
                            f.write(image_bytes)
                        return output_path
                    return download_file(content[key], output_path)
        raise RuntimeError(f"gpt-image-2 with inputs returned unsupported data item: {item}")
    for key in ('url', 'output_url'):
        if data.get(key):
            return download_file(data[key], output_path)
    if data.get('b64_json'):
        image_bytes = base64.b64decode(data['b64_json'])
        with open(output_path, 'wb') as f:
            f.write(image_bytes)
        return output_path
    if data.get('content') and isinstance(data['content'], dict):
        content = data['content']
        for key in ('url', 'output_url', 'image_url'):
            if content.get(key):
                return download_file(content[key], output_path)
        if content.get('b64_json'):
            image_bytes = base64.b64decode(content['b64_json'])
            with open(output_path, 'wb') as f:
                f.write(image_bytes)
            return output_path
    if data.get('result') and isinstance(data['result'], dict):
        result = data['result']
        for key in ('url', 'output_url', 'image_url'):
            if result.get(key):
                return download_file(result[key], output_path)
        if result.get('b64_json'):
            image_bytes = base64.b64decode(result['b64_json'])
            with open(output_path, 'wb') as f:
                f.write(image_bytes)
            return output_path
    raise RuntimeError(f"gpt-image-2 with inputs returned unsupported payload: {data}")




def select_seedance_scene_indices(scenes):
    preferred = []
    for idx, scene in enumerate(scenes):
        engine = str(scene.get('video_engine') or scene.get('motion_engine') or '').strip().lower()
        if scene.get('use_seedance') is True or engine == 'seedance':
            preferred.append(idx)

    if not preferred:
        preferred = [0] if scenes else []

    chosen = []
    for idx in preferred:
        if idx not in chosen:
            chosen.append(idx)
        if len(chosen) >= SEEDANCE_MAX_SCENES:
            break
    return set(chosen)
def seedance_duration_value(duration):
    """Return a duration accepted by the configured Seedance model."""
    if SEEDANCE_MODEL.startswith('doubao-seedance-1-5-pro'):
        return -1
    return int(max(2, min(15, math.ceil(float(duration)))))


async def seedance_generate_video(image_path, prompt, api_key, output_path, duration):
    _require_billing_ok('video')
    print(f"  [Seedance] generating from: {os.path.basename(image_path)}")
    url = f"{SEEDANCE_BASE_URL}/contents/generations/tasks"
    payload = {
        'model': SEEDANCE_MODEL,
        'content': [
            {
                'type': 'text',
                'text': prompt,
            },
            {
                'type': 'image_url',
                'image_url': {
                    'url': file_to_data_url(image_path),
                    'role': 'first_frame',
                },
            },
        ],
        'resolution': SEEDANCE_RESOLUTION,
        'ratio': SEEDANCE_RATIO,
        'duration': seedance_duration_value(duration),
        'generate_audio': SEEDANCE_GENERATE_AUDIO,
        'draft': SEEDANCE_DRAFT,
        'camera_fixed': SEEDANCE_CAMERA_FIXED,
        'watermark': SEEDANCE_WATERMARK,
    }

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    if resp.status_code >= 400:
        raise RuntimeError(f"Seedance create failed: {resp.status_code} {resp.text}")

    create_data = resp.json()
    task_id = create_data.get('id') or create_data.get('task_id') or create_data.get('data', {}).get('id') or create_data.get('data', {}).get('task_id')
    if not task_id:
        raise RuntimeError(f"Seedance create returned no task id: {create_data}")

    print(f"  [Seedance] task id: {task_id}")
    poll_url = f"{url}/{task_id}"
    deadline = time.time() + SEEDANCE_TIMEOUT

    while True:
        poll_resp = requests.get(poll_url, headers=headers, timeout=60)
        if poll_resp.status_code >= 400:
            raise RuntimeError(f"Seedance poll failed: {poll_resp.status_code} {poll_resp.text}")
        poll_data = poll_resp.json()
        task = poll_data.get('data', poll_data)
        status = task.get('status')
        print(f"  [Seedance] status: {status}")

        if status == 'succeeded':
            video_url = task.get('video_url')
            if not video_url and isinstance(task.get('content'), dict):
                video_url = task['content'].get('video_url')
            if not video_url and isinstance(task.get('result'), dict):
                video_url = task['result'].get('video_url')
            if not video_url:
                raise RuntimeError(f"Seedance succeeded without video_url: {poll_data}")

            raw_path = f"{output_path}.raw.mp4"
            download_file(video_url, raw_path, timeout=600)
            ensure_audio_track(raw_path, output_path)
            try:
                os.remove(raw_path)
            except OSError:
                pass
            return output_path

        if status in {'failed', 'expired', 'canceled', 'cancelled'}:
            raise RuntimeError(f"Seedance task ended with status {status}: {poll_data}")

        if time.time() > deadline:
            raise RuntimeError(f"Seedance task timed out after {SEEDANCE_TIMEOUT}s: {task_id}")

        await asyncio.sleep(SEEDANCE_POLL_INTERVAL)


async def generate_voiceover(scenes, output_dir):
    full_text = ' '.join(s.get('narration', '').strip() for s in scenes if s.get('narration'))
    total_duration = sum(float(s.get('duration', 0) or 0) for s in scenes)
    audio_path = os.path.join(output_dir, 'voiceover.mp3')

    if not full_text:
        make_silent_audio(total_duration or 1.0, audio_path)
        return audio_path, total_duration

    cmd = [
        PYTHON,
        '-m',
        'edge_tts',
        '--voice',
        'zh-CN-XiaoxiaoNeural',
        '--text',
        full_text,
        '--write-media',
        audio_path,
        '--rate',
        '+15%',
    ]
    print('  [TTS] generating voiceover...')
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8')
    if proc.returncode != 0:
        print(f"  [TTS] error: {proc.stderr}")
        make_silent_audio(total_duration or 1.0, audio_path)
        return audio_path, total_duration

    duration = ffprobe_duration(audio_path)
    print(f"  [TTS] voiceover: {duration:.1f}s")
    return audio_path, duration


def make_silent_clip(duration, output_path):
    cmd = [
        FFMPEG,
        '-y',
        '-f', 'lavfi',
        '-i', f'color=c=black:s=1080x1920:d={float(duration):.3f}',
        '-f', 'lavfi',
        '-i', 'anullsrc=r=44100:cl=mono',
        '-shortest',
        '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'libmp3lame',
        '-b:a', '128k',
        output_path,
    ]
    run_cmd(cmd)
    return output_path


def make_image_clip(image_path, duration, output_path):
    cmd = [
        FFMPEG,
        '-y',
        '-loop', '1',
        '-i', image_path,
        '-f', 'lavfi',
        '-i', 'anullsrc=r=44100:cl=mono',
        '-t', f'{float(duration):.3f}',
        '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-shortest',
        output_path,
    ]
    run_cmd(cmd)
    return output_path


def assemble_video(clip_paths, audio_path, subtitle_path, output_path):
    output_path = os.path.abspath(output_path)
    audio_path = os.path.abspath(audio_path)
    subtitle_path = os.path.abspath(subtitle_path)
    work_dir = os.path.dirname(output_path)

    concat_file = os.path.join(work_dir, 'concat.txt')
    with open(concat_file, 'w', encoding='utf-8') as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    local_srt = os.path.join(work_dir, '_subs.srt')
    shutil.copy2(subtitle_path, local_srt)

    cmd = [
        FFMPEG,
        '-y',
        '-f', 'concat',
        '-safe', '0',
        '-i', concat_file,
        '-i', audio_path,
        '-vf', 'scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,subtitles=_subs.srt:original_size=1080x1920',
        '-map', '0:v',
        '-map', '1:a',
        '-c:v', 'libx264',
        '-preset', 'fast',
        '-crf', '23',
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-shortest',
        output_path,
    ]
    print('  [FFmpeg] assembling final video...')
    run_cmd(cmd, cwd=work_dir)
    print(f"  [FFmpeg] done: {output_path}")
    return output_path


def generate_srt(scenes, output_path):
    lines = []
    index = 1
    current_time = 0.0

    for scene in scenes:
        narration = scene.get('narration', '').strip()
        dur = float(scene.get('duration', 0) or 0)

        if not narration:
            current_time += dur
            continue

        start = current_time
        end = current_time + dur
        start_ts = f"{int(start // 3600):02d}:{int((start % 3600) // 60):02d}:{int(start % 60):02d},{int((start % 1) * 1000):03d}"
        end_ts = f"{int(end // 3600):02d}:{int((end % 3600) // 60):02d}:{int(end % 60):02d},{int((end % 1) * 1000):03d}"
        lines.extend([str(index), f"{start_ts} --> {end_ts}", narration, ''])
        index += 1
        current_time += dur

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"  [SRT] subtitles written: {output_path}")
    return output_path


def add_sound_effects(clip_paths, scenes, output_dir):
    sfx_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'assets', 'sfx')
    processed = []

    for i, (clip_path, scene) in enumerate(zip(clip_paths, scenes)):
        sfx_tag = scene.get('sfx', 'silence')
        sfx_file = SFX_MAP.get(sfx_tag)
        sfx_full = os.path.join(sfx_dir, sfx_file) if sfx_file else None

        if not sfx_full or not os.path.exists(sfx_full):
            processed.append(clip_path)
            continue

        out_path = os.path.join(output_dir, f'scene_{i + 1:02d}_sfx.mp4')
        cmd = [
            FFMPEG,
            '-y',
            '-i',
            clip_path,
            '-i',
            sfx_full,
            '-filter_complex', '[1:a]volume=0.3[sfx];[0:a][sfx]amix=inputs=2:duration=first',
            '-c:v', 'copy',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-shortest',
            out_path,
        ]
        run_cmd(cmd)
        processed.append(out_path)

    return processed


async def main():
    parser = argparse.ArgumentParser(description='video-shopping build pipeline')
    parser.add_argument('--storyboard', required=True, help='Path to storyboard JSON')
    parser.add_argument('--openai-key', default='', help='OpenAI / gpt-image-2 API key')
    parser.add_argument('--seedance-key', default='', help='Seedance API key')
    parser.add_argument('--jimeng-key', default='', help='Backward-compatible alias for --openai-key')
    parser.add_argument('--kling-key', default='', help='Backward-compatible alias for --seedance-key')
    parser.add_argument('--openai-base-url', default=OPENAI_BASE_URL, help='OpenAI-compatible base URL')
    parser.add_argument('--seedance-base-url', default=SEEDANCE_BASE_URL, help='Seedance base URL')
    parser.add_argument('--product-images', default='', help='Directory with product images')
    parser.add_argument('--output', default='', help='Output directory override')
    parser.add_argument('--providers-config', default=str(Path(__file__).resolve().parents[1] / 'work' / 'providers.json'), help='Path to providers.json')
    parser.add_argument('--provider', default='', help='Provider name from providers.json')
    parser.add_argument('--dry-run', action='store_true', help='Print resolved provider settings and exit (no generation)')
    args = parser.parse_args()

    image_key = args.openai_key or args.jimeng_key or OPENAI_API_KEY
    video_key = args.seedance_key or args.kling_key or SEEDANCE_API_KEY
    openai_base_url = args.openai_base_url.rstrip('/')
    seedance_base_url = args.seedance_base_url.rstrip('/')

    # Provider selection (no network). Keys come from env vars named in providers.json.
    provider_cfg = None
    provider_name = None
    if args.providers_config and os.path.exists(args.providers_config):
        provider_name, provider_cfg = apply_provider(args.provider, args.providers_config)

    if provider_cfg:
        image_key_env = str(provider_cfg.get('openai_key_env', '')).strip()
        seedance_key_env = str(provider_cfg.get('seedance_key_env', '')).strip()
        if image_key_env:
            image_key = os.environ.get(image_key_env, '').strip() or image_key
        if seedance_key_env:
            video_key = os.environ.get(seedance_key_env, '').strip() or video_key
        openai_base_url = OPENAI_BASE_URL
        seedance_base_url = SEEDANCE_BASE_URL

    if args.dry_run:
        print('DRY_RUN provider=' + str(provider_name or args.provider))
        print('OPENAI_BASE_URL=' + OPENAI_BASE_URL)
        print('OPENAI_IMAGE_MODEL=' + str(OPENAI_IMAGE_MODEL))
        print('OPENAI_IMAGE_RESPONSE_FORMAT=' + str(OPENAI_IMAGE_RESPONSE_FORMAT))
        print('OPENAI_IMAGE_SIZE=' + str(OPENAI_IMAGE_SIZE))
        print('SEEDANCE_BASE_URL=' + SEEDANCE_BASE_URL)
        print('SEEDANCE_MODEL=' + str(SEEDANCE_MODEL))
        print('BILLING_OK=' + str(BILLING_OK))
        sys.exit(0)

    with open(args.storyboard, 'r', encoding='utf-8-sig') as f:
        sb = json.load(f)

    product = sb.get('product', 'product')
    route = sb.get('route', 'pov')
    scenes = sb.get('scenes', [])

    if not scenes:
        print('Error: storyboard has no scenes')
        sys.exit(1)

    out_dir = args.output or os.path.dirname(args.storyboard)
    os.makedirs(out_dir, exist_ok=True)

    print(f"\n{'=' * 60}")
    print("Video Shopping Pipeline")
    print(f"  Product: {product}")
    print(f"  Route:   {route}")
    print(f"  Scenes:  {len(scenes)}")
    print(f"  Output:  {out_dir}")
    print(f"{'=' * 60}")

    print('[1/5] Generating scene images (gpt-image-2)...')
    image_paths = []
    for scene in scenes:
        prompt = scene.get('image_prompt', '')
        img_path = os.path.join(out_dir, f"scene_{scene['id']:02d}.png")
        if image_key and prompt:
            try:
                await gpt_image_generate(prompt, image_key, img_path)
            except Exception as exc:
                print(f'  [gpt-image-2] failed, using placeholder: {exc}')
                make_placeholder_image(img_path)
        else:
            print('  [gpt-image-2] skipped (no key or no prompt)')
            make_placeholder_image(img_path)
        image_paths.append(img_path)

    print('[2/5] Generating video clips (Seedance)...')
    clip_paths = []
    seedance_scene_indices = select_seedance_scene_indices(scenes)
    seedance_budget = SEEDANCE_MAX_SCENES
    for i, (scene, img_path) in enumerate(zip(scenes, image_paths)):
        clip_path = os.path.join(out_dir, f"scene_{scene['id']:02d}.mp4")
        prompt = scene.get('video_prompt', scene.get('kling_prompt', scene.get('image_prompt', '')))
        duration = float(scene.get('duration', 2.0) or 2.0)
        want_seedance = i in seedance_scene_indices
        if want_seedance and seedance_budget > 0 and video_key and os.path.exists(img_path):
            seedance_budget -= 1
            try:
                await seedance_generate_video(img_path, prompt, video_key, clip_path, duration)
            except Exception as exc:
                print(f'  [Seedance] failed, falling back to still clip: {exc}')
                make_image_clip(img_path, max(2.0, duration), clip_path)
        elif os.path.exists(img_path):
            make_image_clip(img_path, max(2.0, duration), clip_path)
        else:
            make_silent_clip(max(2.0, duration), clip_path)
        clip_paths.append(clip_path)
        print(f'  [{i + 1}/{len(scenes)}] scene {scene["id"]}')

    print('[3/5] Generating voiceover (Edge TTS)...')
    audio_path, _ = await generate_voiceover(scenes, out_dir)

    print('[4/5] Generating subtitles...')
    srt_path = generate_srt(scenes, os.path.join(out_dir, 'subtitles.srt'))

    print('[5/5] Adding sound effects & assembling...')
    clip_paths = add_sound_effects(clip_paths, scenes, out_dir)
    final_path = assemble_video(clip_paths, audio_path, srt_path, os.path.join(out_dir, 'final.mp4'))

    print(f"\n{'=' * 60}")
    print(f"Done! Final video: {final_path}")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    asyncio.run(main())
