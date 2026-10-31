#!/usr/bin/env python3

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

MODEL = "openai/gpt-image-2.5-sunburst"

# Словарь промптов: номер -> путь к .txt файлу с текстом промпта.
# Чтобы добавить новый промпт, просто допишите строку с новым номером.
PROMPT_FILES = {
    1: "PROMPTS/usa.txt",
    2: "PROMPTS/restavr.txt",
    3: "PROMPTS/white_bg.txt",
}

API_URL = "https://openrouter.ai/api/v1/images"

# Параметры сжатия перед отправкой
MAX_SIDE = 1600       # максимальная сторона изображения в пикселях
JPEG_QUALITY = 85      # качество JPEG-сжатия (0-100)

# Параметры повторных попыток
MAX_ATTEMPTS = 3
RETRY_DELAY_BASE = 3   # секунд, увеличивается с каждой попыткой


def load_prompt(prompt_number: int) -> str:
    """Читает текст промпта из файла по номеру из словаря PROMPT_FILES."""
    if prompt_number not in PROMPT_FILES:
        raise KeyError(f"Промпта с номером {prompt_number} нет в PROMPT_FILES.")

    prompt_path = Path(PROMPT_FILES[prompt_number])
    if not prompt_path.exists():
        raise FileNotFoundError(f"Файл промпта не найден: {prompt_path}")

    text = prompt_path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Файл промпта пустой: {prompt_path}")

    return text


def make_output_path(input_path: str) -> str:
    """Путь для результата: рядом с оригиналом, имя с цифрой (photo.jpg -> photo_1.png).
    Если такой файл уже есть, берётся следующая цифра (photo_2.png и т.д.),
    чтобы не перезаписывать прошлые результаты."""
    src = Path(input_path)
    n = 1
    while True:
        candidate = src.with_name(f"{src.stem}_{n}.png")
        if not candidate.exists():
            return str(candidate)
        n += 1


def ask_prompt_number() -> int:
    """Показывает список доступных промптов и просит ввести номер."""
    print("Доступные промпты:")
    for number, path in sorted(PROMPT_FILES.items()):
        print(f"  {number}: {path}")

    while True:
        raw = input("Введите номер промпта: ").strip()
        if not raw.isdigit():
            print("Нужно ввести цифру. Попробуйте ещё раз.")
            continue

        number = int(raw)
        if number not in PROMPT_FILES:
            print(f"Промпта с номером {number} нет. Попробуйте ещё раз.")
            continue

        return number


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
    input_image_path = input("Введите путь к входному изображению: ").strip().strip('"')  # Например: "input.jpg"
    prompt_number = ask_prompt_number()
    prompt = load_prompt(prompt_number)
    print(f"Используется промпт №{prompt_number}: {PROMPT_FILES[prompt_number]}")

    output_image_path = make_output_path(input_image_path)

    process_image(input_image_path, prompt, output_image_path)
