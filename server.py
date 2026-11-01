# -*- coding: utf-8 -*-
"""极简本地 AI 生成工作台 —— 只写提示词，一键出图/出片。

后端：标准库 http.server，无第三方依赖（PIL 除外，ComfyUI venv 自带）。
前端：同目录 preview.html（唯一入口；/ 与 /index.html 均 302 到它）。
依赖：ComfyUI 已在 127.0.0.1:8188 运行。
"""
import base64
import hashlib
import io
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.join(ROOT, "workflows")
COMFY = os.environ.get("AI_COMFY_URL") or "http://127.0.0.1:8188"
COMFY_DIR = os.environ.get("AI_COMFY_DIR") or os.path.abspath(os.path.join(ROOT, os.pardir, "ComfyUI"))
INPUT_DIR = os.path.join(COMFY_DIR, "input")
OUTPUT_DIR = os.path.join(COMFY_DIR, "output")
PORT = int(os.environ.get("AI_STUDIO_PORT") or 8899)

CFG = {
    "image": {"wf": "qwen_t2i.json", "timeout": 600, "label": "文生图"},
    "image_set": {"wf": "qwen_set.json", "timeout": 600, "label": "场景图"},
    "image_prop": {"wf": "qwen_prop.json", "timeout": 600, "label": "道具图"},
    "image_edit": {"wf": "qwen_i2i.json", "timeout": 600, "label": "图生图"},
    "audio_video": {"wf": "h3_r2v.json", "timeout": 2400, "label": "文生视频（含声音）"},
    "audio_video_i2v": {"wf": "h3_r2v.json", "timeout": 2400, "label": "图生视频（含声音）"},
    "audio_video_r2v": {"wf": "h3_r2v.json", "timeout": 2400, "label": "参考图生视频（含声音）"},
}

_jobs = {}
_tr_cache = {}
_lock = threading.Lock()


# ---------------- ComfyUI helpers ----------------
def comfy_get(path, timeout=10):
    with urllib.request.urlopen(COMFY + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def comfy_post(path, payload, timeout=60):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        COMFY + path, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def comfy_online():
    try:
        comfy_get("/system_stats", timeout=3)
        return True
    except Exception:
        return False


# ---------------- 翻译 ----------------
def translate_cn(text):
    """中文 -> 英文。已是英文则原样返回。翻译不可用时回退原文，不阻断生成。"""
    if not re.search(r"[\u4e00-\u9fff]", text):
        return text
    if text in _tr_cache:
        return _tr_cache[text]
    try:
        chunks = [text[i:i + 400] for i in range(0, len(text), 400)]
        out = []
        for c in chunks:
            q = urllib.parse.quote(c)
            url = "https://api.mymemory.translated.net/get?q=%s&langpair=zh-CN%%7Cen" % q
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.loads(r.read().decode("utf-8"))
            t = (d.get("responseData") or {}).get("translatedText") or ""
            if not t or "MYMEMORY WARNING" in t.upper() or "QUOTA" in t.upper():
                return text
            out.append(t.strip())
        res = " ".join(out)
        _tr_cache[text] = res
        return res
    except Exception:
        return text


# ---------------- 工作流装配 ----------------
def fill(obj, mapping):
    if isinstance(obj, dict):
        return {k: fill(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [fill(v, mapping) for v in obj]
    if isinstance(obj, str) and obj in mapping:
        return mapping[obj]
    return obj


def load_wf(name):
    with open(os.path.join(WF_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)["prompt"]


def guess_ext(b64):
    m = re.match(r"data:image/([A-Za-z]+)", b64 or "")
    ext = (m.group(1).lower() if m else "jpeg")
    return ".jpg" if ext in ("jpeg", "jpg") else "." + ext


def save_upload(b64, ext):
    raw = base64.b64decode(b64.split(",")[-1])
    fname = "upload_%d_%d%s" % (int(time.time() * 1000), random.randint(100, 999), ext)
    path = os.path.join(INPUT_DIR, fname)
    with open(path, "wb") as f:
        f.write(raw)
    return fname, path


def pick_size_h3(path):
    """H3：按 0.4MP 目标保持原图比例，32 对齐。"""
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
        ratio = w / float(h)
        hh = int((400000 / ratio) ** 0.5)
        ww = int(hh * ratio)
        ww = max(32, (ww // 32) * 32)
        hh = max(32, (hh // 32) * 32)
        return ww, hh
    except Exception:
        return (640, 640)


# ---------------- 官方 Ref2VA 范式：正文转英文 ----------------
_DLG = re.compile(r"<d>.*?</d>", re.S)
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _tr_h3_line(line):
    """保护标签 / [Shot N] / (Sx) / "资产名"，只翻含中文的散文部分。"""
    if not _CJK.search(line):
        return line
    keep = []

    def stash(m):
        keep.append(m.group(0))
        return "[[%d]]" % (len(keep) - 1)

    s = re.sub(r"<[^>]+>", stash, line)
    s = re.sub(r"\[Shot \d+\]", stash, s)
    s = re.sub(r"\(S\d+\)", stash, s)
    s = re.sub(r'"[^"]*"', stash, s)
    en = translate_cn(s)
    for i, t in enumerate(keep):
        en = en.replace("[[%d]]" % i, t)
    return en


def h3_english(text):
    """官方 Ref2VA 范式要求正文用英文，只有 <d> 内台词保留中文原样。
    中文正文会被模型当成旁白念出来，所以发送前把 <d> 之外的中文翻成英文。"""
    if not _CJK.search(_DLG.sub("", text)):
        return text
    out, pos = [], 0
    for m in _DLG.finditer(text):
        out.append("\n".join(_tr_h3_line(l) for l in text[pos:m.start()].split("\n")))
        out.append(m.group(0))
        pos = m.end()
    out.append("\n".join(_tr_h3_line(l) for l in text[pos:].split("\n")))
    res = "".join(out)
    res = re.sub(r"(?<!\s)(<d>)", " \\1", res)
    res = re.sub(r"(</d>)(?![.,;!?\s])", "\\1 ", res)
    return res


def build_h3_prompt(user_text):
    en = translate_cn(user_text)
    return (
        "integrated_multimodal_description: " + en
        + "\noverall_soundscape: natural ambient sound matching the scene"
        + "\nnon_diegetic_music: no music"
    )


DEFAULT_STYLE = "Q版萌系，游戏概念艺术，暖色电影光"
STYLE_STRIP = ("q版", "Q版", "萌系", "角色", "人物", "立绘", "头像", "半身", "全身", "设定图", "三视图")


def adapt_style(style):
    """项目画风 -> 场景/道具可用画风：只剥掉会带出人物的角色词，保留题材与光感。"""
    parts = [p.strip() for p in re.split(r"[，,、；;]", style or "")]
    kept = [p for p in parts if p and not any(t in p for t in STYLE_STRIP)]
    return "，".join(kept)


def compose_asset_prompt(kind, core, style="", world="", fill_defaults=False):
    """按资产类型拼提示词：角色带项目画风；场景/道具保留题材画风并去角色化，避免出人物。"""
    core = (core or "").strip()
    if not core:
        return ""
    if kind == "char":
        st = (style or "").strip() or DEFAULT_STYLE
        return (st + "，角色设定图：" + core +
                "，全身站姿，正面视图，纯色背景，画面里不要出现任何文字，画面里只有这一个人物")
    st = adapt_style(style) or "游戏概念艺术，暖色电影光"
    if kind == "set":
        place = "室外全景" if re.search(r"(街|外景|森林|山|屋顶|广场|码头|田野|海边|庭院|城墙)", core) else "室内全景"
        scene = core
        if world and world not in core:
            scene = world + "，" + core
        return (st + "，场景概念艺术，环境原画，建筑与室内设计，广角镜头；" + scene +
                "，空无一人，" + place +
                "，广角镜头，只有场景建筑与家具，画面中没有任何人物、人影、动物，不要出现任何文字")
    if kind == "prop":
        if fill_defaults:
            core += "，奇幻中世纪风格，做旧材质，手工质感"
        return (st + "，道具静物特写，产品摄影，柔和棚拍光；" + core +
                "，单独居中摆在纯色背景上，画面中没有任何人物、人手、身体、角色，不要出现任何文字")
    return core


def project_look(pid):
    """取项目的画风与世界观/场景，供资产拼词。"""
    data = read_project(pid) if pid else None
    if not data:
        return "", ""
    meta = data.get("meta") or {}
    return (data.get("style") or meta.get("style") or ""), (meta.get("scene") or "")


def comfy_cancel(pid):
    """排队中就从队列删除；正在执行就打断当前任务。"""
    try:
        q = comfy_get("/queue", timeout=5)
        running = [it[1] for it in (q.get("queue_running") or [])]
        pending = [it[1] for it in (q.get("queue_pending") or [])]
    except Exception:
        running, pending = [], []
    if pid in pending:
        try:
            comfy_post("/queue", {"delete": [pid]}, timeout=5)
        except Exception:
            pass
    if pid in running:
        try:
            comfy_post("/interrupt", {}, timeout=5)
        except Exception:
            pass


def collect_files(entry):
    exts = {".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image",
            ".mp4": "video", ".webm": "video", ".gif": "video",
            ".flac": "audio", ".wav": "audio", ".mp3": "audio"}
    out = []
    for node in (entry.get("outputs") or {}).values():
        for kind in ("images", "videos", "gifs", "audio"):
            for it in node.get(kind) or []:
                fn = it.get("filename", "")
                ext = os.path.splitext(fn)[1].lower()
                out.append({
                    "filename": fn,
                    "subfolder": it.get("subfolder", ""),
                    "type": it.get("type", "output"),
                    "kind": exts.get(ext, kind),
                })
    return out


# ---------------- 任务执行 ----------------
def run_job(job_id, mode, user_text, image_b64, seconds, width=None, height=None,
            project=None, kind=None, extra_images=None):
    job = _jobs[job_id]
    t0 = time.time()
    try:
        if not comfy_online():
            raise RuntimeError("ComfyUI 没在运行，请先启动 ComfyUI（默认 127.0.0.1:8188）后重试")
        cfg = CFG[mode]
        seed = random.randint(1, 2 ** 50)
        mapping = {"{{SEED}}": seed, "{{SECONDS}}": seconds}
        if mode in ("image", "image_set", "image_prop"):
            w = int(width or 1024)
            h = int(height or 1024)
            mapping["{{WIDTH}}"] = w // 32 * 32
            mapping["{{HEIGHT}}"] = h // 32 * 32
        elif mode in ("audio_video", "audio_video_r2v"):
            w = max(32, int(width or 1344) // 32 * 32)
            h = max(32, int(height or 768) // 32 * 32)
            mapping["{{WIDTH}}"] = w
            mapping["{{HEIGHT}}"] = h
            job["size"] = "%dx%d" % (w, h)

        job["stage"] = "翻译提示词"
        if mode in ("audio_video", "audio_video_i2v"):
            prompt_en = build_h3_prompt(user_text)
            job["english"] = prompt_en
        elif mode == "audio_video_r2v":
            # 官方范式：正文英文、<d> 内台词保留中文，否则中文描述会被当旁白念出来
            job["stage"] = "正文转英文（台词保留中文）"
            prompt_en = h3_english(user_text)
            job["english"] = prompt_en
        elif mode in ("image", "image_set", "image_prop", "image_edit"):
            prompt_en = user_text  # Qwen 原生支持中文，直接送模型
            job["english"] = prompt_en
        else:
            prompt_en = translate_cn(user_text)
            job["english"] = prompt_en
        mapping["{{PROMPT}}"] = prompt_en

        ref_names = []
        extra_names = []
        if mode == "audio_video_r2v":
            if not extra_images:
                raise RuntimeError("参考图生视频至少需要 1 张参考图")
            job["stage"] = "上传参考图"
            for b in extra_images[:9]:
                nm, _ = save_upload(b, guess_ext(b))
                ref_names.append(nm)
            w = max(32, int(width or 1344) // 32 * 32)
            h = max(32, int(height or 768) // 32 * 32)
            mapping["{{WIDTH}}"] = w
            mapping["{{HEIGHT}}"] = h
            job["size"] = "%dx%d" % (w, h)
            job["refs"] = len(ref_names)

        if mode in ("audio_video_i2v", "image_edit"):
            if not image_b64:
                raise RuntimeError("图生视频需要先上传首帧" if mode == "audio_video_i2v"
                                   else "图生图需要先上传一张参考图")
            job["stage"] = "上传图片"
            fname, fpath = save_upload(image_b64, guess_ext(image_b64))
            mapping["{{IMAGE}}"] = fname
            extra_names = []
            if mode == "image_edit":
                for b in (extra_images or [])[:2]:
                    n2, _ = save_upload(b, guess_ext(b))
                    extra_names.append(n2)
            if mode == "audio_video_i2v":
                w, h = pick_size_h3(fpath)
                mapping["{{WIDTH}}"] = w
                mapping["{{HEIGHT}}"] = h
                job["size"] = "%dx%d" % (w, h)
                ref_names = [fname]

        job["stage"] = "提交任务"
        wf = fill(load_wf(cfg["wf"]), mapping)
        if ref_names:
            rnode = wf.get("12")
            if isinstance(rnode, dict):
                for i, nm in enumerate(ref_names):
                    nid = "3%d" % i
                    wf[nid] = {"class_type": "LoadImage", "inputs": {"image": nm}}
                    rnode.setdefault("inputs", {})["ref_images.ref_image_%d" % i] = [nid, 0]
        if mode == "image_edit" and extra_names:
            enc = wf.get("5")
            for i, nm in enumerate(extra_names):
                nid = "19%d" % i
                wf[nid] = {"class_type": "LoadImage", "inputs": {"image": nm}}
                if isinstance(enc, dict):
                    enc.setdefault("inputs", {})["image%d" % (i + 2)] = [nid, 0]
        if project:
            safe_proj = re.sub(r"[^A-Za-z0-9_\-]", "", str(project))[:40] or "default"
            safe_kind = re.sub(r"[^A-Za-z0-9_\-]", "", str(kind or "misc"))[:20] or "misc"
            prefix = "studio/%s/%s/%s" % (safe_proj, safe_kind, safe_kind)
            for node in wf.values():
                if not isinstance(node, dict):
                    continue
                if node.get("class_type") in ("SaveImage", "SaveVideo", "SaveAnimatedWEBP", "VHS_VideoCombine"):
                    node.setdefault("inputs", {})["filename_prefix"] = prefix
            job["dest"] = prefix
        resp = comfy_post("/prompt", {"prompt": wf, "client_id": "studio"})
        pid = resp["prompt_id"]
        job["prompt_id"] = pid

        job["stage"] = "生成中"
        deadline = time.time() + cfg["timeout"]
        while time.time() < deadline:
            if job.get("cancelled"):
                job["state"] = "cancelled"
                job["stage"] = "已取消"
                job["elapsed"] = round(time.time() - t0, 1)
                return
            time.sleep(2)
            try:
                hist = comfy_get("/history/" + pid, timeout=10)
            except Exception:
                continue
            if pid not in hist:
                continue
            entry = hist[pid]
            status = (entry.get("status") or {}).get("status_str")
            files = collect_files(entry)
            if files:
                job["files"] = files
                job["elapsed"] = round(time.time() - t0, 1)
                job["state"] = "done"
                job["stage"] = "完成"
                return
            if status == "error":
                msgs = (entry.get("status") or {}).get("messages") or []
                detail = "执行失败"
                for m in msgs:
                    if m and m[0] == "execution_error":
                        info = m[1]
                        detail = "%s @ %s: %s" % (
                            info.get("node_type"), info.get("node_id"),
                            info.get("exception_message"))
                raise RuntimeError(detail)
        raise RuntimeError("超时（%d 秒），模型可能负载过高" % cfg["timeout"])
    except Exception as e:
        job["state"] = "error"
        job["error"] = str(e)
        job["elapsed"] = round(time.time() - t0, 1)


# ---------------- 项目存储 ----------------
PROJECTS_DIR = os.path.join(ROOT, "projects")


def pid_ok(pid):
    return bool(re.match(r"^[A-Za-z0-9_-]{1,64}$", pid or ""))


def project_file(pid):
    return os.path.join(PROJECTS_DIR, pid + ".json")


def _ref_exists(f):
    if not isinstance(f, dict) or not f.get("filename"):
        return True
    sub = (f.get("subfolder") or "").replace("/", "\\")
    return os.path.isfile(os.path.join(OUTPUT_DIR, sub, f["filename"]))


def _normalize_ref(f, pid, kind):
    """引用文件不在原处、但已在 studio/<pid>/<kind> 时，纠正 subfolder。"""
    if _ref_exists(f):
        return
    want = "studio\\%s\\%s" % (pid, kind)
    if os.path.isfile(os.path.join(OUTPUT_DIR, want, f["filename"])):
        f["subfolder"] = want


def normalize_refs(pid, data):
    """旧页面可能把迁移后的引用写回 web；读写前统一校正到实际存在的目录。"""
    for kind, key in (("char", "chars"), ("set", "sets"), ("prop", "props")):
        for it in data.get(key) or []:
            for f in it.get("cands") or []:
                _normalize_ref(f, pid, kind)
            _normalize_ref(it.get("image"), pid, kind)
    for s in data.get("shots") or []:
        for f in s.get("candidates") or []:
            _normalize_ref(f, pid, "shot")
        for f in s.get("vcands") or []:
            _normalize_ref(f, pid, "video")
        _normalize_ref(s.get("image"), pid, "shot")
        _normalize_ref(s.get("video"), pid, "video")
    return data


def read_project(pid):
    if not pid_ok(pid):
        return None
    path = project_file(pid)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    data["id"] = pid
    return normalize_refs(pid, data)


_PROJECT_LOCK = threading.Lock()


def write_project(pid, data):
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    data = normalize_refs(pid, dict(data))
    data["id"] = pid
    data["updated"] = int(time.time())
    path = project_file(pid)
    tmp = path + ".tmp"
    with _PROJECT_LOCK:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        for i in range(8):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                time.sleep(0.05)
        else:
            os.replace(tmp, path)
    return data


def list_projects():
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    out = []
    for fn in os.listdir(PROJECTS_DIR):
        if not fn.endswith(".json"):
            continue
        pid = fn[:-5]
        data = read_project(pid)
        if not data:
            continue
        shots = data.get("shots") or []
        out.append({
            "id": pid,
            "title": data.get("title") or "未命名项目",
            "episode": data.get("episode") or 1,
            "shots": len(shots),
            "done": sum(1 for s in shots if s.get("state") == "video"),
            "seconds": sum(int(s.get("duration") or 0) for s in shots),
            "updated": data.get("updated") or int(os.path.getmtime(project_file(pid))),
        })
    out.sort(key=lambda x: x["updated"], reverse=True)
    return out


# ---------------- 项目资产库 ----------------
LIB_MEDIA = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".mp4", ".webm"}
LIB_SKIP_DIRS = {"__pycache__", ".git"}


def library_files(pid, limit=600):
    """扫描本项目产物目录 + 旧图归档，供资产库页面统一浏览、复用。"""
    seen = set()
    items = []

    def walk(base, source):
        if not os.path.isdir(base):
            return
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in LIB_SKIP_DIRS]
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in LIB_MEDIA:
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, OUTPUT_DIR)
                if rel in seen:
                    continue
                seen.add(rel)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                kind = ""
                if source == "project":
                    tail = os.path.relpath(full, os.path.join(OUTPUT_DIR, "studio", pid))
                    parts = tail.split(os.sep)
                    kind = parts[0] if len(parts) > 1 else ""
                items.append({
                    "filename": fn,
                    "subfolder": os.path.dirname(rel),
                    "kind": kind,
                    "source": source,
                    "is_video": ext in (".mp4", ".webm"),
                    "size": st.st_size,
                    "mtime": int(st.st_mtime),
                })

    walk(os.path.join(OUTPUT_DIR, "studio", pid), "project")
    walk(os.path.join(OUTPUT_DIR, "_archive"), "archive")
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:limit]


def blank_project(title, episode):
    return {
        "title": title,
        "episode": episode,
        "style": "高质量 2D 热血漫画风",
        "meta": {"ratio": "16:9 横版", "scene": "", "pov": ""},
        "chars": [],
        "sets": [],
        "props": [],
        "scenes": [{"no": 1, "name": "场次 1", "from": 0, "to": 5}],
        "shots": [{
            "no": "1-1", "scene": 1, "duration": 5, "state": "empty",
            "chars": [], "props": [], "sets": [], "visual": "",
            "performance": "", "dialogue": "",
        }],
        "draft": "",
    }


SEED_ID = "demo-baoyu-bar"
SEED_PROJECT = {
    "title": "暴雨城酒吧奇案", "episode": 1, "style": "高质量 2D 热血漫画风",
    "meta": {"ratio": "16:9 横版", "scene": "暴雨城旧城区酒馆，夜，暖黄灯光",
             "pov": "矮人酒保第一人称旁白"},
    "chars": [
        {"id": "c1", "name": "矮人酒保", "role": "主角", "voice": "旁白", "locked": True},
        {"id": "c2", "name": "法师", "role": "主角", "voice": "对白", "locked": True},
        {"id": "c3", "name": "牧师", "role": "配角", "voice": "对白", "locked": False},
        {"id": "c4", "name": "战士", "role": "配角", "voice": "对白", "locked": False},
        {"id": "c5", "name": "盗贼", "role": "配角", "voice": "对白", "locked": False},
    ],
    "sets": [{"id": "t1", "name": "酒馆大堂", "locked": True}],
    "props": [{"id": "p1", "name": "木酒杯", "locked": False},
              {"id": "p2", "name": "抹布", "locked": False}],
    "scenes": [
        {"no": 1, "name": "雨夜推门", "from": 0, "to": 41},
        {"no": 2, "name": "对峙·打圆场", "from": 41, "to": 74},
        {"no": 3, "name": "收尾·包袱", "from": 74, "to": 104},
    ],
    "shots": [
        {"no": "1-1", "scene": 1, "duration": 15, "state": "video", "chars": ["c1"],
         "props": ["p1"], "sets": ["t1"],
         "visual": "中景，固定机位，50mm，酒馆吧台后侧，暖黄灯光从上方落下。矮人酒保低头擦拭木酒杯，蒸汽从杯口升起。",
         "performance": "缓慢抬头，视线穿过吧台看向门口",
         "dialogue": "开店三十年，我见过有人拿着屠龙剑来买酒。"},
        {"no": "1-2", "scene": 1, "duration": 8, "state": "video", "chars": ["c1", "c2"],
         "props": [], "sets": ["t1"],
         "visual": "近景，缓慢推镜，85mm，浅景深。门口雨帘被推开，冷光切进暖调画面。",
         "performance": "酒保手上的动作停住，眼神一变",
         "dialogue": "但今天这位，是抱着一把法杖进来的。"},
        {"no": "1-3", "scene": 1, "duration": 8, "state": "image", "chars": ["c2"],
         "props": [], "sets": ["t1"],
         "visual": "中景，侧逆光，35mm，红袍法师推门而入，雨水从斗篷边缘滴落，手里攥着一把法杖。",
         "performance": "抖了抖斗篷，法杖重重顿地",
         "dialogue": "老板，来一杯能压住火气的。"},
        {"no": "1-4", "scene": 1, "duration": 10, "state": "empty", "chars": [],
         "props": [], "sets": ["t1"],
         "visual": "空镜，固定，24mm，酒馆全景。吊灯微晃，木桶与长桌排列，角落里壁炉火光跳动。",
         "performance": "", "dialogue": ""},
        {"no": "2-1", "scene": 2, "duration": 8, "state": "empty", "chars": ["c3", "c4"],
         "props": [], "sets": ["t1"],
         "visual": "中景，双人对峙构图，50mm。牧师坐在长桌一侧，战士靠在吧台边，两人视线交汇。",
         "performance": "牧师手指扣紧杯沿，指节发白", "dialogue": "你倒是说句话啊。"},
        {"no": "2-2", "scene": 2, "duration": 5, "state": "empty", "chars": ["c5"],
         "props": [], "sets": ["t1"],
         "visual": "特写，手持轻晃，100mm。兜帽下的盗贼露出一只眼，手指在桌下快速收起一枚硬币。",
         "performance": "压低帽檐，装作什么都没发生", "dialogue": "（低声）钱先记我账上。"},
        {"no": "2-3", "scene": 2, "duration": 10, "state": "empty", "chars": ["c2", "c1"],
         "props": ["p1"], "sets": ["t1"],
         "visual": "中景，低角度仰拍，35mm。法师将法杖重重顿在吧台上，酒液晃出杯沿。",
         "performance": "酒保后退半步，抬手挡住飞溅的酒", "dialogue": "别吵，这杯我请。"},
        {"no": "2-4", "scene": 2, "duration": 10, "state": "empty", "chars": [],
         "props": [], "sets": ["t1"],
         "visual": "空镜，缓慢横移，24mm。雨水顺着窗玻璃流下，窗外霓虹招牌在雨幕里晕成一片暖橙色。",
         "performance": "", "dialogue": ""},
        {"no": "3-1", "scene": 3, "duration": 15, "state": "empty", "chars": ["c4"],
         "props": [], "sets": ["t1"],
         "visual": "中景，环绕运镜，50mm。战士转身抽出背后的重剑，铁甲摩擦发出低沉声响。",
         "performance": "憋了整场的脸终于松动，喉咙动了一下", "dialogue": "……法杖，roll 不 roll？"},
        {"no": "3-2", "scene": 3, "duration": 15, "state": "empty",
         "chars": ["c1", "c2", "c3", "c4", "c5"], "props": ["p2"], "sets": ["t1"],
         "visual": "全景，固定，24mm，五人围绕长桌列坐，吊灯在正中投下锥形光影，桌面摊开一张地图。",
         "performance": "众人同时转头看向法杖，画面定格",
         "dialogue": "全片最后一句，说完直接切黑。"},
    ],
    "draft": "",
}


def seed_demo():
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    marker = os.path.join(PROJECTS_DIR, ".seeded")
    if os.path.isfile(marker):
        return
    write_project(SEED_ID, SEED_PROJECT)
    open(marker, "w").close()


# ---------------- HTTP ----------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(p.query)
        if p.path in ("/", "/index.html"):
            # 只保留一个入口：统一进 preview.html
            self.send_response(302)
            self.send_header("Location", "/preview.html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if p.path in ("/preview", "/preview.html"):
            with open(os.path.join(ROOT, "preview.html"), "r", encoding="utf-8") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if p.path == "/api/projects":
            return self._send(200, {"projects": list_projects()})
        if p.path == "/api/project":
            data = read_project((qs.get("id") or [""])[0])
            if not data:
                return self._send(404, {"error": "项目不存在"})
            return self._send(200, data)
        if p.path == "/api/library":
            pid = (qs.get("id") or [""])[0]
            if not pid_ok(pid):
                return self._send(400, {"error": "缺少或非法的项目 id"})
            return self._send(200, {"files": library_files(pid)})
        if p.path == "/api/status":
            return self._send(200, {"comfy": comfy_online()})
        if p.path == "/api/job":
            jid = (qs.get("id") or [""])[0]
            job = _jobs.get(jid)
            if not job:
                return self._send(404, {"error": "job not found"})
            return self._send(200, job)
        if p.path == "/api/file":
            fn = (qs.get("filename") or [""])[0]
            sub = (qs.get("subfolder") or [""])[0]
            full = os.path.normpath(os.path.join(OUTPUT_DIR, sub, fn))
            if not full.startswith(os.path.normpath(OUTPUT_DIR)) or not os.path.isfile(full):
                return self._send(404, {"error": "file not found"})
            ext = os.path.splitext(full)[1].lower()
            ctype = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".webp": "image/webp", ".mp4": "video/mp4", ".webm": "video/webm",
                     ".gif": "image/gif", ".wav": "audio/wav", ".mp3": "audio/mpeg",
                     ".flac": "audio/flac"}.get(ext, "application/octet-stream")
            # 视频/音频要支持 Range，否则浏览器进度条无法拖动
            size = os.path.getsize(full)
            rng = (self.headers.get("Range") or "").strip()
            m = re.match(r"bytes=(\d*)-(\d*)", rng) if rng else None
            if m and size > 0:
                start = int(m.group(1) or 0)
                end = int(m.group(2)) if m.group(2) else size - 1
                end = min(end, size - 1)
                if start > end:
                    return self._send(416, {"error": "bad range"})
                self.send_response(206)
                self.send_header("Content-Type", ctype)
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
                self.send_header("Content-Length", str(end - start + 1))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                with open(full, "rb") as f:
                    f.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = f.read(min(262144, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(full, "rb") as f:
                while True:
                    chunk = f.read(262144)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            return
        if p.path == "/api/spec":
            # 官方提示词范式（唯一事实来源），供前端做生成前自检
            sp = os.path.join(ROOT, "docs", "h3_prompt_spec.json")
            if not os.path.isfile(sp):
                return self._send(404, {"error": "spec not found"})
            with open(sp, encoding="utf-8") as f:
                return self._send(200, json.load(f))
        if p.path == "/api/frame":
            # 取视频首帧做缩略图，缓存到 .frames
            fn = (qs.get("filename") or [""])[0]
            sub = (qs.get("subfolder") or [""])[0]
            t = (qs.get("t") or ["0"])[0]
            full = os.path.normpath(os.path.join(OUTPUT_DIR, sub, fn))
            if not full.startswith(os.path.normpath(OUTPUT_DIR)) or not os.path.isfile(full):
                return self._send(404, {"error": "file not found"})
            cdir = os.path.join(ROOT, ".frames")
            os.makedirs(cdir, exist_ok=True)
            key = hashlib.md5((fn + "|" + sub + "|" + t).encode("utf-8")).hexdigest()[:20]
            thumb = os.path.join(cdir, key + ".jpg")
            if not os.path.isfile(thumb) or os.path.getmtime(thumb) < os.path.getmtime(full):
                subprocess.run(["ffmpeg", "-y", "-ss", t, "-i", full,
                                "-frames:v", "1", "-vf", "scale=320:-2", thumb],
                               capture_output=True, timeout=90)
            if not os.path.isfile(thumb):
                return self._send(500, {"error": "frame failed"})
            data = open(thumb, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(data)
            return
        return self._send(404, {"error": "not found"})

    def _body_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return None

    def do_DELETE(self):
        p = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(p.query)
        if p.path == "/api/project":
            pid = (qs.get("id") or [""])[0]
            path = project_file(pid)
            if os.path.isfile(path):
                os.remove(path)
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "项目不存在"})
        return self._send(404, {"error": "not found"})

    def do_PUT(self):
        p = urllib.parse.urlparse(self.path)
        if p.path != "/api/project":
            return self._send(404, {"error": "not found"})
        data = self._body_json()
        if not isinstance(data, dict):
            return self._send(400, {"error": "bad json"})
        pid = data.get("id") or ""
        if not pid_ok(pid):
            return self._send(400, {"error": "缺少或非法的项目 id"})
        cur = read_project(pid)
        if not cur:
            return self._send(404, {"error": "项目不存在"})
        # 版本校验：防止旧标签页把已更新的数据回写覆盖
        # 不带 rev 的保存一律拒绝（旧页面不发 rev，直接放行就等于允许覆盖）
        if "rev" not in data:
            return self._send(409, {"error": "stale", "rev": int(cur.get("rev") or 0)})
        try:
            crev = int(data.get("rev") or 0)
        except Exception:
            crev = 0
        srv_rev = int(cur.get("rev") or 0)
        if crev != srv_rev:
            return self._send(409, {"error": "stale", "rev": srv_rev})
        data["rev"] = int(cur.get("rev") or 0) + 1
        saved = write_project(pid, data)
        return self._send(200, {"ok": True, "id": pid, "updated": saved["updated"], "rev": data["rev"]})

    def do_POST(self):
        p = urllib.parse.urlparse(self.path)
        if p.path == "/api/project":
            data = self._body_json()
            if not isinstance(data, dict):
                return self._send(400, {"error": "bad json"})
            title = (data.get("title") or "").strip() or "未命名项目"
            try:
                episode = max(1, int(data.get("episode") or 1))
            except Exception:
                episode = 1
            pid = "p%d" % int(time.time() * 1000)
            while read_project(pid):
                pid += "x"
            write_project(pid, blank_project(title, episode))
            return self._send(200, {"id": pid, "title": title, "episode": episode})
        if p.path == "/api/cancel":
            data = self._body_json() or {}
            job = _jobs.get(data.get("id") or "")
            if not job:
                return self._send(404, {"error": "job not found"})
            if job.get("state") != "running":
                return self._send(200, {"ok": True, "state": job.get("state")})
            job["cancelled"] = True
            job["stage"] = "取消中"
            pid = job.get("prompt_id")
            if pid:
                threading.Thread(target=comfy_cancel, args=(pid,), daemon=True).start()
            return self._send(200, {"ok": True, "state": "cancelling"})
        if p.path == "/api/film":
            # 成片拼接：同参数直接 -c copy，参数不一致先逐镜归一化再拼
            data = self._body_json() or {}
            clips = data.get("clips") if isinstance(data.get("clips"), list) else []
            pid = re.sub(r"[^0-9A-Za-z_-]", "", str(data.get("project") or "")) or "film"
            if not clips:
                return self._send(400, {"error": "没有可拼接的镜头"})
            files = []
            for c in clips:
                c = c if isinstance(c, dict) else {}
                fn = str(c.get("filename") or "").strip()
                sub = str(c.get("subfolder") or "").strip()
                if not fn:
                    continue
                full = os.path.normpath(os.path.join(OUTPUT_DIR, sub, fn))
                if not full.startswith(os.path.normpath(OUTPUT_DIR)) or not os.path.isfile(full):
                    return self._send(404, {"error": "找不到镜头文件：%s" % fn})
                files.append(full)
            if not files:
                return self._send(400, {"error": "没有可拼接的镜头"})
            probes = [_ffprobe(f) for f in files]
            if any(x is None for x in probes):
                return self._send(500, {"error": "ffprobe 读取失败，请确认 ffmpeg 在 PATH 里"})
            base = probes[0]
            out_dir = os.path.join(OUTPUT_DIR, "studio", pid)
            os.makedirs(out_dir, exist_ok=True)
            stamp = time.strftime("%Y%m%d-%H%M%S")
            out = os.path.join(out_dir, "film_%s.mp4" % stamp)
            lst = os.path.join(out_dir, "_concat_%s.txt" % stamp)
            tmp = os.path.join(out_dir, "_norm_%s" % stamp)
            uniform = all(x["w"] == base["w"] and x["h"] == base["h"] and
                          abs(x["fps"] - base["fps"]) < 0.01 and
                          x["vc"] == base["vc"] and x["ac"] == base["ac"] for x in probes)
            ok = _film_concat(files, lst, out, True) if uniform else False
            if not ok:
                os.makedirs(tmp, exist_ok=True)
                norm = []
                vf = ("scale=%d:%d:force_original_aspect_ratio=decrease,"
                      "pad=%d:%d:(ow-iw)/2:(oh-ih)/2,fps=%s") % (
                          base["w"], base["h"], base["w"], base["h"], base["fps"])
                for i, src in enumerate(files):
                    dst = os.path.join(tmp, "n%02d.mp4" % i)
                    cmd = ["ffmpeg", "-y", "-i", src]
                    if probes[i]["ac"]:
                        cmd += ["-map", "0:v:0", "-map", "0:a:0"]
                    else:
                        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                                "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
                    cmd += ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", dst]
                    r = subprocess.run(cmd, capture_output=True, timeout=900)
                    if r.returncode != 0 or not os.path.isfile(dst):
                        shutil.rmtree(tmp, ignore_errors=True)
                        try:
                            os.remove(lst)
                        except Exception:
                            pass
                        return self._send(500, {"error": "镜头归一化失败：%s" % os.path.basename(src)})
                    norm.append(dst)
                ok = _film_concat(norm, lst, out, True)
                shutil.rmtree(tmp, ignore_errors=True)
            try:
                os.remove(lst)
            except Exception:
                pass
            if not ok:
                return self._send(500, {"error": "拼接失败"})
            dur = round(sum(x["dur"] for x in probes), 2)
            return self._send(200, {"filename": os.path.basename(out),
                                    "subfolder": "studio/" + pid, "duration": dur,
                                    "clips": len(files),
                                    "mode": "copy" if uniform else "reencode"})
        if p.path == "/api/tailframe":
            # 续接用：抽上一镜视频的最后一帧，返回 dataURL
            data = self._body_json() or {}
            fn = str(data.get("filename") or "").strip()
            sub = str(data.get("subfolder") or "").strip()
            full = os.path.normpath(os.path.join(OUTPUT_DIR, sub, fn))
            if not fn or not full.startswith(os.path.normpath(OUTPUT_DIR)) or not os.path.isfile(full):
                return self._send(404, {"error": "找不到视频文件"})
            tmpdir = tempfile.mkdtemp(prefix="tail_")
            png = os.path.join(tmpdir, "tail.png")
            try:
                r = subprocess.run(["ffmpeg", "-y", "-sseof", "-0.25", "-i", full,
                                    "-frames:v", "1", "-update", "1", png],
                                   capture_output=True, timeout=120)
                if r.returncode != 0 or not os.path.isfile(png):
                    return self._send(500, {"error": "抽尾帧失败"})
                with open(png, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
            return self._send(200, {"dataurl": "data:image/png;base64," + b64})
        if p.path == "/api/silence":
            # 无台词镜头：替换成空白音轨，保证静音但播放器仍显示正常音轨
            data = self._body_json() or {}
            fn = (data.get("filename") or "").strip()
            sub = (data.get("subfolder") or "").strip()
            src = os.path.normpath(os.path.join(OUTPUT_DIR, sub, fn))
            if not src.startswith(os.path.normpath(OUTPUT_DIR)) or not os.path.isfile(src):
                return self._send(404, {"error": "file not found"})
            stem, ext = os.path.splitext(src)
            out = stem + "_silent.mp4"
            subprocess.run(["ffmpeg", "-y", "-i", src,
                            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                            "-shortest", "-c:v", "copy", "-c:a", "aac", "-b:a", "64k", out],
                           capture_output=True, timeout=300)
            if not os.path.isfile(out):
                return self._send(500, {"error": "silence failed"})
            return self._send(200, {"filename": os.path.basename(out), "subfolder": sub})
        if p.path == "/api/mute":
            # 去掉音轨：无台词镜头做确定性静音
            data = self._body_json() or {}
            fn = (data.get("filename") or "").strip()
            sub = (data.get("subfolder") or "").strip()
            src = os.path.normpath(os.path.join(OUTPUT_DIR, sub, fn))
            if not src.startswith(os.path.normpath(OUTPUT_DIR)) or not os.path.isfile(src):
                return self._send(404, {"error": "file not found"})
            stem, ext = os.path.splitext(src)
            out = stem + "_mute.mp4"
            subprocess.run(["ffmpeg", "-y", "-i", src, "-an", "-c:v", "copy", out],
                           capture_output=True, timeout=300)
            if not os.path.isfile(out):
                return self._send(500, {"error": "mute failed"})
            return self._send(200, {"filename": os.path.basename(out), "subfolder": sub})
        if p.path != "/api/generate":
            return self._send(404, {"error": "not found"})
        n = int(self.headers.get("Content-Length") or 0)
        try:
            data = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return self._send(400, {"error": "bad json"})
        mode = data.get("mode")
        kind = data.get("kind")
        text = (data.get("prompt") or "").strip()
        desc = (data.get("desc") or "").strip()
        if kind in ("char", "set", "prop"):
            name = (data.get("name") or "").strip()
            role = (data.get("role") or "").strip()
            if kind == "char":
                core = desc or (name + ("，" + role if role else ""))
            else:
                core = desc or name
            if not core:
                return self._send(400, {"error": "主体描述不能为空"})
            style, world = project_look(data.get("project"))
            if (data.get("style") or "").strip():
                style = data.get("style").strip()
            if kind == "char":
                text = compose_asset_prompt("char", core, style)
                mode = "image"
            elif kind == "set":
                text = compose_asset_prompt("set", core, style, world)
                mode = "image_set"
            else:
                text = compose_asset_prompt("prop", core, style, fill_defaults=not desc)
                mode = "image_prop"
        if mode not in CFG:
            return self._send(400, {"error": "未知模式"})
        if not text:
            return self._send(400, {"error": "提示词不能为空"})
        jid = "%d-%d" % (int(time.time() * 1000), random.randint(100, 999))
        try:
            sec = int(data.get("seconds") or 5)
        except Exception:
            sec = 5
        if sec not in (5, 8, 10, 15):
            sec = 5
        job = {"id": jid, "state": "running", "stage": "排队", "mode": mode,
               "created": time.time(), "files": [], "english": "", "error": "",
               "cancelled": False,
               "seconds": sec if mode != "image" else 0}
        _jobs[jid] = job
        images = data.get("images")
        if not isinstance(images, list):
            images = []
        threading.Thread(target=run_job, args=(jid, mode, text, data.get("image"), sec,
                                               data.get("width"), data.get("height"),
                                               data.get("project"), data.get("kind"), images),
                         daemon=True).start()
        return self._send(200, {"id": jid})


def _ffprobe(path):
    """读视频参数：分辨率 / 帧率 / 编码 / 时长。读不到返回 None。"""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json",
                            "-show_streams", "-show_format", path],
                           capture_output=True, timeout=120)
        if r.returncode != 0:
            return None
        d = json.loads(r.stdout.decode("utf-8", "replace"))
    except Exception:
        return None
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), None)
    a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not v:
        return None
    try:
        num, den = (v.get("avg_frame_rate") or "24/1").split("/")
        fps = float(num) / float(den or 1)
    except Exception:
        fps = 24.0
    return {"w": int(v.get("width") or 0), "h": int(v.get("height") or 0),
            "fps": round(fps, 3), "vc": v.get("codec_name"), "ac": (a or {}).get("codec_name"),
            "dur": float(d.get("format", {}).get("duration") or 0)}


def _film_concat(paths, lst, target, copy_audio=True):
    """用 concat demuxer 合片；copy=True 直接拷贝流（同参数），否则重编码。"""
    with open(lst, "w", encoding="utf-8") as f:
        for q in paths:
            f.write("file '" + q.replace("\\", "/").replace("'", "'\\''") + "'\n")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst]
    if copy_audio:
        cmd += ["-c", "copy", "-movflags", "+faststart", target]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100", target]
    r = subprocess.run(cmd, capture_output=True, timeout=1800)
    return r.returncode == 0 and os.path.isfile(target)


def _sync_project_aliases():
    """在 output/studio 下给每个项目目录建一个中文名 junction，方便在资源管理器里认。"""
    if os.name != "nt" or not os.path.isdir(PROJECTS_DIR):
        return
    bad = '\\/:*?"<>|'
    for fn in os.listdir(PROJECTS_DIR):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(PROJECTS_DIR, fn), encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            continue
        pid = str(d.get("id") or os.path.splitext(fn)[0])
        title = str(d.get("title") or d.get("name") or "")
        name = "".join("-" if ch in bad else ch for ch in title).strip().strip(".")[:40].strip()
        target = os.path.join(OUTPUT_DIR, "studio", pid)
        alias = os.path.join(OUTPUT_DIR, "studio", name) if name else ""
        if not name or os.path.isdir(alias) or not os.path.isdir(target):
            continue
        try:
            subprocess.run(["cmd", "/c", "mklink", "/J", alias, target],
                           capture_output=True, timeout=30)
        except Exception:
            pass


def main():
    try:
        _sync_project_aliases()
    except Exception:
        pass
    os.makedirs(INPUT_DIR, exist_ok=True)
    seed_demo()
    print("AI Studio 已启动： http://127.0.0.1:%d" % PORT)
    print("分镜工作台： http://127.0.0.1:%d/preview.html" % PORT)
    print("ComfyUI 状态：", "在线" if comfy_online() else "未检测到（请先启动）")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
