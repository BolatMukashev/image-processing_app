#!/usr/bin/env python3
"""Графический интерфейс для main.py (обработка фото) и video.py (фото -> видео)."""

import os
import sys
import time
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

from PIL import Image, ImageTk

import main    # обработка фото
import video   # генерация видео

PREVIEW_SIZE = (360, 360)

DEFAULT_VIDEO_PROMPT = """Animate this photograph with extremely subtle, natural motion.

Preserve the person's exact facial identity and appearance from the original image. The face must remain unchanged throughout the entire video: same facial proportions, eyes, nose, mouth, jawline, skin texture, age and expression.

The person remains almost completely still.
Only subtle natural breathing, one gentle blink and a very slight natural eye movement.
No head turn. No smile. No speaking. No change of facial expression.

Keep the hairstyle, clothing, body proportions, background, lighting and composition exactly as in the original photograph.

Camera is completely static.
No zoom, no camera movement, no perspective change.

The animation should feel like the original photograph has naturally come to life, not like a newly generated person.

Very low motion. Photorealistic. Natural and restrained.
No face morphing, no identity change, no facial distortion, no beautification, no regeneration of facial features, no new details."""


def open_in_system(path: str, select: bool = False):
    """Открывает файл (или показывает его в папке) в системной программе."""
    if sys.platform.startswith("win"):
        if select:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        else:
            os.startfile(path)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", path] if select else ["open", path])
    else:
        subprocess.Popen(["xdg-open", str(Path(path).parent if select else path)])


class QueueWriter:
    """Перенаправляет print() из main.py / video.py в окно лога."""

    def __init__(self, q: queue.Queue):
        self.q = q

    def write(self, text):
        if text:
            self.q.put(("log", text))

    def flush(self):
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Фото и видео")
        self.geometry("860x820")
        self.minsize(800, 760)

        self.input_path = None
        self.output_path = None
        self.busy = False
        self.q = queue.Queue()
        sys.stdout = QueueWriter(self.q)

        self._build_ui()
        self.after(100, self._poll_queue)

    # ---------- интерфейс ----------
    def _build_ui(self):
        top = ttk.Frame(self, padding=10)
        top.pack(fill="x")
        ttk.Button(top, text="Выбрать фото…", command=self.choose_file).grid(row=0, column=0, padx=(0, 10))
        self.file_label = ttk.Label(top, text="Файл не выбран", foreground="gray")
        self.file_label.grid(row=0, column=1, sticky="w")

        # вкладки режимов
        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="x", padx=10)

        photo_tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(photo_tab, text="Фото")
        ttk.Label(photo_tab, text="Промпт:").grid(row=0, column=0, sticky="w")
        self.prompt_var = tk.StringVar()
        values = [f"{n}: {Path(p).stem}" for n, p in sorted(main.PROMPT_FILES.items())]
        self.prompt_box = ttk.Combobox(photo_tab, textvariable=self.prompt_var, values=values,
                                       state="readonly", width=40)
        self.prompt_box.grid(row=0, column=1, sticky="w", padx=10)
        if values:
            self.prompt_box.current(0)

        video_tab = ttk.Frame(self.tabs, padding=10)
        self.tabs.add(video_tab, text="Видео")
        ttk.Label(video_tab, text="Промпт:").grid(row=0, column=0, sticky="nw")
        self.video_prompt = tk.Text(video_tab, height=7, width=70, wrap="word")
        self.video_prompt.insert("1.0", DEFAULT_VIDEO_PROMPT)
        self.video_prompt.grid(row=0, column=1, columnspan=6, sticky="we", padx=10)

        ttk.Label(video_tab, text="Длительность, сек:").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.dur_var = tk.StringVar(value=str(video.DURATION or ""))
        ttk.Entry(video_tab, textvariable=self.dur_var, width=6).grid(row=1, column=1, sticky="w", padx=10, pady=(8, 0))

        ttk.Label(video_tab, text="Разрешение:").grid(row=1, column=2, sticky="e", pady=(8, 0))
        self.res_var = tk.StringVar(value=video.RESOLUTION or "")
        ttk.Entry(video_tab, textvariable=self.res_var, width=8).grid(row=1, column=3, sticky="w", padx=10, pady=(8, 0))

        ttk.Label(video_tab, text="Пропорции:").grid(row=1, column=4, sticky="e", pady=(8, 0))
        self.aspect_var = tk.StringVar(value=video.ASPECT_RATIO or "")
        ttk.Entry(video_tab, textvariable=self.aspect_var, width=8).grid(row=1, column=5, sticky="w", padx=10, pady=(8, 0))

        ttk.Label(video_tab, text="Пустое поле = значение модели по умолчанию. Видео делается долго (минуты).",
                  foreground="gray").grid(row=2, column=1, columnspan=6, sticky="w", padx=10, pady=(4, 0))

        # кнопка запуска и индикатор
        run_row = ttk.Frame(self, padding=(10, 10, 10, 0))
        run_row.pack(fill="x")
        self.run_btn = ttk.Button(run_row, text="Запустить", command=self.run)
        self.run_btn.pack(side="left")
        self.progress = ttk.Progressbar(run_row, mode="indeterminate", length=250)
        self.progress.pack(side="left", padx=10)

        # превью
        previews = ttk.Frame(self, padding=(10, 0))
        previews.pack(fill="both", expand=True)
        previews.columnconfigure((0, 1), weight=1)
        previews.rowconfigure(0, weight=1)

        self.src_label = ttk.Label(previews, text="Оригинал", anchor="center", relief="groove")
        self.src_label.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        self.res_label = ttk.Label(previews, text="Результат", anchor="center", relief="groove")
        self.res_label.grid(row=0, column=1, padx=5, pady=5, sticky="nsew")

        btns = ttk.Frame(previews)
        btns.grid(row=1, column=1, sticky="e", pady=(0, 5))
        self.play_btn = ttk.Button(btns, text="Открыть видео", command=self.open_result, state="disabled")
        self.play_btn.pack(side="left", padx=5)
        self.open_btn = ttk.Button(btns, text="Показать в папке", command=self.open_result_folder,
                                   state="disabled")
        self.open_btn.pack(side="left")

        # лог
        self.log = tk.Text(self, height=8, state="disabled", wrap="word")
        self.log.pack(fill="x", padx=10, pady=10)

    # ---------- действия ----------
    def choose_file(self):
        path = filedialog.askopenfilename(
            title="Выберите фото",
            filetypes=[("Изображения", "*.jpg *.jpeg *.png *.webp *.bmp"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        self.input_path = path
        self.file_label.config(text=path, foreground="black")
        self._show_image(self.src_label, path)
        self._reset_result()

    def _reset_result(self):
        self.res_label.config(image="", text="Результат")
        self.res_label.image = None
        self.open_btn.config(state="disabled")
        self.play_btn.config(state="disabled")

    def run(self):
        if self.busy:
            return
        if not self.input_path:
            messagebox.showwarning("Нет файла", "Сначала выберите фото.")
            return

        is_video = self.tabs.index(self.tabs.select()) == 1
        self._reset_result()

        if is_video:
            prompt = self.video_prompt.get("1.0", "end").strip()
            if not prompt:
                messagebox.showwarning("Нет промпта", "Введите промпт для видео.")
                return
            try:
                video.DURATION = int(self.dur_var.get()) if self.dur_var.get().strip() else None
            except ValueError:
                messagebox.showwarning("Ошибка", "Длительность должна быть числом.")
                return
            video.RESOLUTION = self.res_var.get().strip() or None
            video.ASPECT_RATIO = self.aspect_var.get().strip() or None

            src = Path(self.input_path)
            stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
            self.output_path = str(src.with_name(f"{src.stem}_{stamp}.mp4"))
            target, args = video.generate_video, (self.input_path, prompt, self.output_path)
            kind = "video"
        else:
            if not self.prompt_var.get():
                messagebox.showwarning("Нет промпта", "Выберите промпт.")
                return
            number = int(self.prompt_var.get().split(":")[0])
            try:
                prompt = main.load_prompt(number)
            except Exception as e:
                messagebox.showerror("Ошибка промпта", str(e))
                return
            self.output_path = main.make_output_path(self.input_path)
            self._log(f"Промпт №{number}: {main.PROMPT_FILES[number]}\n")
            target, args = main.process_image, (self.input_path, prompt, self.output_path)
            kind = "photo"

        self._set_busy(True)
        threading.Thread(target=self._worker, args=(target, args, kind), daemon=True).start()

    def _worker(self, target, args, kind):
        try:
            target(*args)
            self.q.put(("done", kind))
        except Exception as e:
            self.q.put(("error", str(e)))

    def open_result(self):
        if self.output_path and os.path.exists(self.output_path):
            try:
                open_in_system(self.output_path)
            except Exception as e:
                messagebox.showerror("Ошибка", str(e))

    def open_result_folder(self):
        if self.output_path:
            try:
                open_in_system(self.output_path, select=True)
            except Exception as e:
                messagebox.showerror("Ошибка", str(e))

    # ---------- вспомогательное ----------
    def _set_busy(self, busy: bool):
        self.busy = busy
        self.run_btn.config(state="disabled" if busy else "normal")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _show_image(self, label, path):
        try:
            img = Image.open(path)
            img.thumbnail(PREVIEW_SIZE)
            photo = ImageTk.PhotoImage(img)
            label.config(image=photo, text="")
            label.image = photo  # держим ссылку, иначе картинка исчезнет
        except Exception as e:
            self._log(f"Не удалось показать превью: {e}\n")

    def _log(self, text):
        self.log.config(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.config(state="disabled")

    def _poll_queue(self):
        try:
            while True:
                kind, data = self.q.get_nowait()
                if kind == "log":
                    self._log(data)
                elif kind == "done":
                    self._set_busy(False)
                    self.open_btn.config(state="normal")
                    if data == "photo":
                        self._show_image(self.res_label, self.output_path)
                    else:
                        self.res_label.config(text=f"Видео готово:\n{Path(self.output_path).name}")
                        self.play_btn.config(state="normal")
                elif kind == "error":
                    self._set_busy(False)
                    self._log(f"Ошибка: {data}\n")
                    messagebox.showerror("Ошибка", data)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)


if __name__ == "__main__":
    App().mainloop()
