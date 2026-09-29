#!/usr/bin/env python3
"""
Генерация видео из фото + текстового промпта через OpenRouter Video API.
input: путь к изображению + промпт
output: сохранённый mp4-файл

Изображение передаётся как первый кадр видео (frame_images / first_frame).
"""

import os
import io
import time
import base64
import requests
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

# 135 тг самая дешевая модель, 200 тг средняя

# ==== НАСТРОЙКИ ====
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# MODEL = "bytedance/seedance-1-5-pro"
MODEL = "minimax/hailuo-3-max"
API_URL = "https://openrouter.ai/api/v1/videos"
time_now = time.strftime("%Y-%m-%d_%H-%M-%S")
OUTPUT_VIDEO_PATH = f"{time_now}.mp4"

# Необязательные параметры. Значения должны поддерживаться моделью
# (список: GET https://openrouter.ai/api/v1/videos/models), иначе будет ошибка 400.
# Если None — параметр не отправляется, модель берёт значение по умолчанию.
DURATION = 5       # например 4, 5, 8
RESOLUTION = "480p"     # например "720p"
ASPECT_RATIO = None   # например "16:9", "9:16"

# Сжатие изображения перед отправкой
MAX_SIDE = 1600
JPEG_QUALITY = 85

# Опрос статуса
POLL_INTERVAL = 5      # секунд
POLL_MAX_ATTEMPTS = 240  # 240 * 5 сек = 20 минут максимум

# Повторные попытки при обрыве соединения
MAX_ATTEMPTS = 3
RETRY_DELAY_BASE = 3


def resize_and_encode_image(image_path: str, max_side: int = MAX_SIDE, quality: int = JPEG_QUALITY) -> str:
    """Уменьшает изображение, сжимает в JPEG и кодирует в data URL (base64)."""
    img = Image.open(image_path)

    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    img.thumbnail((max_side, max_side))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    img_bytes = buf.getvalue()

    print(f"Изображение подготовлено: {img.size[0]}x{img.size[1]}, {len(img_bytes) / 1024:.0f} КБ")

    b64 = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def submit_job(data_url: str, prompt: str) -> dict:
    """Шаг 1: отправка задачи на генерацию видео."""
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MODEL,
        "prompt": prompt,
        "frame_images": [
            {
                "type": "image_url",
                "image_url": {"url": data_url},
                "frame_type": "first_frame",
            }
        ],
    }
    if DURATION:
        payload["duration"] = DURATION
    if RESOLUTION:
        payload["resolution"] = RESOLUTION
    if ASPECT_RATIO:
        payload["aspect_ratio"] = ASPECT_RATIO

    print("Отправляю задачу в OpenRouter Video API...")
    response = requests.post(API_URL, headers=headers, json=payload, timeout=180)

    if response.status_code not in (200, 201, 202):
        raise RuntimeError(f"Ошибка API ({response.status_code}): {response.text}")

    job = response.json()
    print(f"Задача отправлена: {job['id']}")
    return job


def wait_for_job(job: dict) -> dict:
    """Шаг 2: опрос статуса до завершения."""
    polling_url = job["polling_url"]
    headers = {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}

    for _ in range(POLL_MAX_ATTEMPTS):
        resp = requests.get(polling_url, headers=headers, timeout=60)
        if resp.status_code != 200:
            raise RuntimeError(f"Ошибка при опросе ({resp.status_code}): {resp.text}")

        status_data = resp.json()
        status = status_data["status"]
        print(f"Статус: {status}")

        if status == "completed":
            return status_data
        if status in ("failed", "cancelled", "expired"):
            raise RuntimeError(f"Генерация не удалась ({status}): {status_data.get('error', 'Unknown error')}")

        time.sleep(POLL_INTERVAL)

    raise TimeoutError("Видео не сгенерировалось за отведённое время.")


def download_video(job: dict, output_path: str):
    """Шаг 3: скачивание готового mp4."""
    urls = job.get("unsigned_urls") or []
    video_url = urls[0] if urls else f"https://openrouter.ai/api/v1/videos/{job['id']}/content?index=0"

    headers = {}
    if video_url.startswith("https://openrouter.ai/api/"):
        headers["Authorization"] = f"Bearer {OPENROUTER_API_KEY}"

    resp = requests.get(video_url, headers=headers, timeout=300)
    if resp.status_code != 200:
        raise RuntimeError(f"Ошибка скачивания ({resp.status_code}): {resp.text}")

    with open(output_path, "wb") as f:
        f.write(resp.content)

    print(f"Готово! Видео сохранено: {output_path} ({len(resp.content) / 1024 / 1024:.1f} МБ)")


def generate_video_once(input_path: str, prompt: str, output_path: str):
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Входной файл не найден: {input_path}")

    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY не найден. Проверьте .env файл.")

    data_url = resize_and_encode_image(input_path)
    job = submit_job(data_url, prompt)
    completed = wait_for_job(job)
    download_video(completed, output_path)


def generate_video(input_path: str, prompt: str, output_path: str, max_attempts: int = MAX_ATTEMPTS):
    """Обёртка с повторными попытками при обрыве соединения."""
    for attempt in range(1, max_attempts + 1):
        try:
            generate_video_once(input_path, prompt, output_path)
            return
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            print(f"Попытка {attempt}/{max_attempts} не удалась: {e}")
            if attempt == max_attempts:
                raise
            delay = RETRY_DELAY_BASE * attempt
            print(f"Повтор через {delay} сек...")
            time.sleep(delay)


if __name__ == "__main__":
    input_image = input("Введите путь к входному изображению: ").strip().strip('"').strip("'")
    prompt = "оживи фотографию, добавь легкие движения тела и эмоции. не меняй черты лица и сохрани стиль оригинала"
    generate_video(input_image, prompt, OUTPUT_VIDEO_PATH)

