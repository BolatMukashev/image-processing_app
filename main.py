#!/usr/bin/env python3
"""
Обработка изображения через OpenRouter Image API (модель openai/gpt-image-2).
input: путь к изображению + текстовый промпт
output: сохранённое обработанное изображение

Изменения:
- изображение сжимается/уменьшается перед отправкой (снижает риск обрыва
  соединения на нестабильной мобильной связи и ускоряет аплоад)
- добавлены повторные попытки (retry) с задержкой при ConnectionError
"""

import os
import io
import time
import base64
import requests
from pathlib import Path
from dotenv import load_dotenv
from PIL import Image

load_dotenv()  # Загружаем переменные окружения из .env файла

# ==== НАСТРОЙКИ ====
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

MODEL = "openai/gpt-image-2"

INPUT_IMAGE_PATH = input("Введите путь к входному изображению: ")  # Например: "input.jpg"
OUTPUT_IMAGE_PATH = "output.png"
PROMPT = "сделай фото на документы из этого фото. увеличь детализацию, осветли, фон белый. убери лишние элементы на фоне. тон кожи светлее. сохрани узнаваемыми черты лица, и не меняй одежду."  # тестовый запрос
# PROMPT = "сделай реставрацию фото, увеличь детализацию. покрась, фото должно быть цветным. не меняй положение людей, постараяйся сохранить узнаваемыми черты лица и одежды. не обрезай фото."

API_URL = "https://openrouter.ai/api/v1/images"

# Параметры сжатия перед отправкой
MAX_SIDE = 1600       # максимальная сторона изображения в пикселях
JPEG_QUALITY = 85      # качество JPEG-сжатия (0-100)

# Параметры повторных попыток
MAX_ATTEMPTS = 4
RETRY_DELAY_BASE = 3   # секунд, увеличивается с каждой попыткой


def resize_and_encode_image(image_path: str, max_side: int = MAX_SIDE, quality: int = JPEG_QUALITY) -> str:
    """Уменьшает изображение, сжимает в JPEG и кодирует в data URL (base64)."""
    img = Image.open(image_path)

    # Убираем альфа-канал/палитру, чтобы корректно сохранить в JPEG
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    img.thumbnail((max_side, max_side))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality, optimize=True)
    img_bytes = buf.getvalue()

    size_kb = len(img_bytes) / 1024
    print(f"Изображение подготовлено: {img.size[0]}x{img.size[1]}, {size_kb:.0f} КБ")

    b64 = base64.b64encode(img_bytes).decode("utf-8")
    return f"data:image/jpeg;base64,{b64}"


def process_image_once(input_path: str, prompt: str, output_path: str):
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Входной файл не найден: {input_path}")

    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY не найден. Проверьте .env файл.")

    data_url = resize_and_encode_image(input_path)

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MODEL,
        "prompt": prompt,
        "input_references": [
            {
                "type": "image_url",
                "image_url": {"url": data_url},
            }
        ],
        "quality": "high",
        "output_format": "png",
    }

    print("Отправляю запрос в OpenRouter Image API...")
    response = requests.post(API_URL, headers=headers, json=payload, timeout=180)

    if response.status_code != 200:
        raise RuntimeError(
            f"Ошибка API ({response.status_code}): {response.text}"
        )

    result = response.json()
    images = result.get("data")

    if not images:
        raise RuntimeError(f"В ответе нет изображений: {result}")

    img_bytes = base64.b64decode(images[0]["b64_json"])
    with open(output_path, "wb") as f:
        f.write(img_bytes)

    cost = result.get("usage", {}).get("cost")
    print(f"Готово! Изображение сохранено: {output_path}" + (f" (стоимость: ${cost})" if cost else ""))


def process_image(input_path: str, prompt: str, output_path: str, max_attempts: int = MAX_ATTEMPTS):
    """Обёртка с повторными попытками при обрыве соединения."""
    for attempt in range(1, max_attempts + 1):
        try:
            process_image_once(input_path, prompt, output_path)
            return
        except requests.exceptions.ConnectionError as e:
            print(f"Попытка {attempt}/{max_attempts} не удалась (обрыв соединения): {e}")
            if attempt == max_attempts:
                raise
            delay = RETRY_DELAY_BASE * attempt
            print(f"Повтор через {delay} сек...")
            time.sleep(delay)
        except requests.exceptions.Timeout as e:
            print(f"Попытка {attempt}/{max_attempts} не удалась (таймаут): {e}")
            if attempt == max_attempts:
                raise
            delay = RETRY_DELAY_BASE * attempt
            print(f"Повтор через {delay} сек...")
            time.sleep(delay)


if __name__ == "__main__":
    process_image(INPUT_IMAGE_PATH, PROMPT, OUTPUT_IMAGE_PATH)