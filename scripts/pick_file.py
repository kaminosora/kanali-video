"""Native picker runs in its own main thread/process."""
import sys
import tkinter as tk
from tkinter import filedialog

sys.stdout.reconfigure(encoding="utf-8")
root = tk.Tk()
root.withdraw()
root.attributes("-topmost", True)
path = filedialog.askopenfilename(title="选择用于 PANORAMA 的视频或图片", filetypes=[
    ("视频与图片", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.png *.jpg *.jpeg *.bmp *.webp"),
    ("所有文件", "*.*")])
root.destroy()
print(path)
