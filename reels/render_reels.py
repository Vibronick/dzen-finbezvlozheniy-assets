"""Генератор вертикальных роликов 1080x1920 (MP4 с русской озвучкой edge-tts или PNG-слайды --slides-only) + пуш в GitHub.
Запуск локально (только проверка слайдов): python tools/render_reels.py tools/reels_pack.json videos_preview --slides-only
Запуск в облаке / с ffmpeg + edge-tts:   python tools/render_reels.py tools/reels_pack.json videos
Пуш скриптов в GitHub для Colab:         python tools/render_reels.py --push
"""
import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
BG, BG2, CARD = (11, 31, 75), (20, 48, 110), (16, 40, 94)
YEL, WHITE, GREY = (255, 200, 30), (255, 255, 255), (185, 200, 226)
BRAND = "Финансы без вложений"
VOICE = "ru-RU-DmitryNeural"


def font(size, bold=True):
    candidates = (
        (
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        )
        if bold
        else (
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        )
    )
    for path in candidates:
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
    return ImageFont.load_default()


def wrap(d, text, f, maxw):
    lines, cur = [], ""
    for w in text.split():
        cand = (cur + " " + w).strip()
        if d.textlength(cand, font=f) <= maxw:
            cur = cand
        else:
            if cur:
                lines.append(cur)
            cur = w
    return lines + [cur] if cur else lines


def fit_block(d, text, sizes, maxw, max_lines, bold=True):
    for sz in sizes:
        f = font(sz, bold)
        lines = wrap(d, text, f, maxw)
        if len(lines) <= max_lines and all(d.textlength(ln, font=f) <= maxw for ln in lines):
            return f, lines
    f = font(sizes[-1], bold)
    return f, wrap(d, text, f, maxw)[:max_lines]


def render_slide(scene, idx, total, out_png):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)

    # Диагональный фон и левый фирменный акцент
    d.polygon([(W - 440, 0), (W, 0), (W, H), (W - 760, H)], fill=BG2)
    d.rectangle([0, 0, 22, H], fill=YEL)

    # Безопасная зона Дзен-роликов: контент строго в y=245..1540, x=76..950
    bar_x0, bar_w, gap = 76, 870, 14
    seg_w = (bar_w - gap * (total - 1)) / total
    for i in range(total):
        sx0 = bar_x0 + i * (seg_w + gap)
        color = YEL if i <= idx else (45, 76, 138)
        d.rounded_rectangle([sx0, 248, sx0 + seg_w, 260], 6, fill=color)

    # Тег статьи НК / рубрики
    tf = font(34, True)
    kicker = scene["kicker"]
    tw = d.textlength(kicker, font=tf)
    d.rounded_rectangle([76, 304, 76 + tw + 44, 372], 12, fill=CARD, outline=YEL, width=3)
    d.text((98, 338), kicker, font=tf, fill=YEL, anchor="lm")

    # Заголовок сцены (3–4 строки крупно)
    hf, hlines = fit_block(d, scene["headline"], (68, 60, 54, 48), 860, 4, bold=True)
    y = 420
    lh = int(hf.size * 1.24)
    for ln in hlines:
        d.text((76, y), ln, font=hf, fill=WHITE)
        y += lh

    # Главная жёлтая плашка с цифрой / нормой
    cap_y = max(y + 42, 775)
    ff, _ = fit_block(d, scene["figure"], (84, 72, 62, 52, 44), 790, 1, bold=True)
    fw = d.textlength(scene["figure"], font=ff)
    cap_w = min(fw + 84, 870)
    cap_h = ff.size + 64
    d.rounded_rectangle([76, cap_y, 76 + cap_w, cap_y + cap_h], 22, fill=YEL)
    d.text((76 + cap_w / 2, cap_y + cap_h / 2), scene["figure"], font=ff, fill=BG, anchor="mm")

    # Карточка пояснения в нижней части безопасной зоны
    card_y = max(cap_y + cap_h + 52, 1020)
    sf, slines = fit_block(d, scene["subtext"], (40, 36, 32), 790, 4, bold=False)
    slh = int(sf.size * 1.32)
    card_h = max(175, len(slines) * slh + 64)
    d.rounded_rectangle([76, card_y, 946, card_y + card_h], 20, fill=CARD, outline=(52, 86, 152), width=2)
    d.rectangle([76, card_y + 22, 88, card_y + card_h - 22], fill=YEL)
    sy = card_y + 30
    for ln in slines:
        d.text((112, sy), ln, font=sf, fill=WHITE)
        sy += slh

    # Подпись канала строго выше нижней границы интерфейса Дзена (1920 - 340 = 1580)
    fy = 1475
    d.ellipse([76, fy, 118, fy + 42], fill=YEL)
    rf = font(26, True)
    rw = d.textlength("₽", font=rf)
    d.text((97 - rw / 2, fy + 5), "₽", font=rf, fill=BG)
    d.text((134, fy + 4), BRAND, font=font(30, False), fill=GREY)

    im.save(out_png)


async def synth_voice(text, out_mp3):
    import edge_tts

    comm = edge_tts.Communicate(text, VOICE, rate="+5%")
    await comm.save(out_mp3)


def audio_duration(path):
    res = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(res.stdout.strip())


def render_reel_mp4(reel, outdir, tmpdir):
    slug = reel["slug"]
    scenes = reel["scenes"]
    clip_paths = []
    for idx, sc in enumerate(scenes):
        png = os.path.join(tmpdir, f"{slug}_s{idx + 1}.png")
        mp3 = os.path.join(tmpdir, f"{slug}_s{idx + 1}.mp3")
        mp4 = os.path.join(tmpdir, f"{slug}_s{idx + 1}.mp4")
        render_slide(sc, idx, len(scenes), png)
        asyncio.run(synth_voice(sc["voiceover"], mp3))
        dur = audio_duration(mp3) + 0.35
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                png,
                "-i",
                mp3,
                "-vf",
                "fps=30,format=yuv420p",
                "-af",
                "apad",
                "-t",
                f"{dur:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-ar",
                "44100",
                "-ac",
                "2",
                mp4,
            ],
            check=True,
            capture_output=True,
        )
        clip_paths.append(mp4)

    list_file = os.path.join(tmpdir, f"{slug}_list.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{os.path.abspath(p)}'\n")

    final_mp4 = os.path.join(outdir, f"{slug}.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", final_mp4],
        check=True,
        capture_output=True,
    )
    return final_mp4, audio_duration(final_mp4)


def push_cloud_pipeline(repo="Vibronick/dzen-finbezvlozheniy-assets", branch="main"):
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    if tools_dir not in sys.path:
        sys.path.insert(0, tools_dir)
    from sync import get_token

    token = get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "dzen-finbezvlozheniy-reels-uploader",
    }

    def api(method, path, payload=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        for attempt in range(3):
            try:
                req = urllib.request.Request(f"https://api.github.com/repos/{repo}{path}", data=data, headers=headers, method=method)
                with urllib.request.urlopen(req, timeout=45) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception:
                if attempt == 2:
                    raise

    ref = api("GET", f"/git/ref/heads/{branch}")
    parent_sha = ref["object"]["sha"]
    parent_commit = api("GET", f"/git/commits/{parent_sha}")
    base_tree = parent_commit["tree"]["sha"]

    files = ["render_reels.py", "reels_pack.json", "render_reels.ipynb"]
    tree_items = []
    for fname in files:
        raw = open(os.path.join(tools_dir, fname), "rb").read()
        res = api("POST", "/git/blobs", {"content": base64.b64encode(raw).decode("ascii"), "encoding": "base64"})
        tree_items.append({"path": f"reels/{fname}", "mode": "100644", "type": "blob", "sha": res["sha"]})

    new_tree = api("POST", "/git/trees", {"base_tree": base_tree, "tree": tree_items})
    if new_tree["sha"] != base_tree:
        commit = api(
            "POST",
            "/git/commits",
            {"message": "Sync cloud reels Colab pipeline (26.11-29.11)", "tree": new_tree["sha"], "parents": [parent_sha]},
        )
        api("PATCH", f"/git/refs/heads/{branch}", {"sha": commit["sha"]})

    print(f"PUSHED reels pipeline -> https://colab.research.google.com/github/{repo}/blob/{branch}/reels/render_reels.ipynb")


if __name__ == "__main__":
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    pos = [a for a in sys.argv[1:] if not a.startswith("--")]

    if "--push" in flags:
        push_cloud_pipeline()
        if not pos:
            sys.exit(0)

    src = pos[0] if len(pos) > 0 else os.path.join(os.path.dirname(__file__), "reels_pack.json")
    outdir = pos[1] if len(pos) > 1 else os.path.join(os.path.dirname(__file__), "..", "videos")
    os.makedirs(outdir, exist_ok=True)
    reels = json.load(open(src, encoding="utf-8"))

    if "--slides-only" in flags:
        count = 0
        for reel in reels:
            for idx, sc in enumerate(reel["scenes"]):
                render_slide(sc, idx, len(reel["scenes"]), os.path.join(outdir, f"{reel['slug']}_s{idx + 1}.png"))
                count += 1
        print(f"SLIDES_OK ({len(reels)} reels, {count} PNGs) -> {os.path.abspath(outdir)}")
    else:
        with tempfile.TemporaryDirectory() as tmpdir:
            for reel in reels:
                mp4_path, dur = render_reel_mp4(reel, outdir, tmpdir)
                mb = os.path.getsize(mp4_path) / (1024 * 1024)
                print(f"MP4_OK: {os.path.basename(mp4_path)} | {dur:.1f}s | {mb:.2f} MB")
