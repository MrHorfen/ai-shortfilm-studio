# AI Shortfilm Studio (Local)

**English** | [简体中文](#简体中文)

> 12 GB VRAM. Script in, film out. A local AI short-film workstation for consumer GPUs.

A local short-film pipeline that runs on a consumer graphics card: **character / scene / prop reference images → storyboard → shots with Chinese dialogue → one-click film assembly**. Everything runs on your own machine; nothing is uploaded.

The backend is a single standard-library `http.server` module (1,056 lines). The frontend is one self-contained `preview.html` (3,624 lines, no external CDN).

This repository ships the **deployment plan and workstation only**. It contains no model weights and no demo content — bring your own models, drive it with your own agent.

---

## Measured limits (12 GB VRAM, RTX 5070 Ti Laptop / 32 GB RAM)

| Item | Result |
|---|---|
| Max shot length | 15 s / 362 frames, ~11 GB VRAM, nearly flat as duration grows |
| OmniRef high-quality ceiling | 10 s @ 1344×768 stable; 15 s must drop to 960×544 or VRAM starts thrashing |
| Recommended launch flag | `--reserve-vram 1` (with dynamic VRAM on, `--lowvram` is ineffective) |
| Reference images | up to 9 |
| Chinese dialogue | keep under ~30 characters per line, always wrapped in `<d>[Chinese] line</d>` |
| Silent shots | explicitly forbid spoken dialogue in the prompt, or the model may invent speech |

Runtime scales linearly with frame count, but **VRAM stays almost constant** — so always optimise duration first, resolution second.

---

## Quick start

### 1. Install ComfyUI

Install ComfyUI yourself (GPL-3.0) and place this project **next to** its directory:

```
your-folder\
├─ ComfyUI\                ← installed by you
└─ ai-shortfilm-studio\    ← this project
```

Otherwise, point to it with an environment variable:

```bat
set AI_COMFY_DIR=D:\ComfyUI
```

### 2. Download the models (7 files, ~58.6 GB)

Download them from the official pages and place them under `ComfyUI\models`:

| Target path | Model |
|---|---|
| `diffusion_models\fasth3_omniref_8step_pruned_int8_convrot.safetensors` | FastH3 OmniRef |
| `text_encoders\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | H3 text encoder |
| `vae\minimax_h3_video_vae_int8_convrot.safetensors` | H3 video VAE |
| `vae\minimax_h3_audio_vae_fp32.safetensors` | H3 audio VAE |
| `diffusion_models\qwen_image_2.1_turbo_int8_convrot.safetensors` | Qwen-Image 2.1 Turbo |
| `text_encoders\qwen3vl_8b_int8_convrot.safetensors` | Qwen text encoder |
| `vae\qwen_image_2.1_vae_bf16.safetensors` | Qwen image VAE |

> This repository provides **no weights and no mirror links**. See [NOTICE.md](NOTICE.md) for licensing — Qwen-Image 2.1 is restricted to research and evaluation only.

### 3. Launch

Double-click `start_studio.bat`. It starts ComfyUI, waits for readiness, launches the workstation and opens your browser.

First cold start takes 1–8 minutes while models load from disk. That is expected.

> Do not open `preview.html` directly — every API call fails under the `file://` protocol.

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `AI_COMFY_DIR` | `..\ComfyUI` | ComfyUI installation directory |
| `AI_COMFY_URL` | `http://127.0.0.1:8188` | ComfyUI endpoint (may point to a remote instance) |
| `AI_COMFY_PORT` | `8188` | ComfyUI port used by the launcher |
| `AI_STUDIO_PORT` | `8899` | Workstation port |

---

## Agent integration

The workstation is agent-agnostic. Anything that can send HTTP can drive it:

```bash
# 1. Health check (also reports whether ComfyUI is online)
curl http://127.0.0.1:8899/api/status

# 2. Submit a generation job
curl -X POST http://127.0.0.1:8899/api/generate \
  -H "Content-Type: application/json" \
  -d '{"mode":"audio_video","prompt":"...","seconds":5,"width":1344,"height":768}'

# 3. Poll the job
curl "http://127.0.0.1:8899/api/job?id=<job_id>"

# 4. Fetch the file
curl "http://127.0.0.1:8899/api/file?path=<relative_path>"
```

Poll every 2–3 seconds for minute-scale jobs. Batch generation must run **serially** — parallel jobs will exhaust VRAM.

Prompt format: [docs/h3_prompt_spec.md](docs/h3_prompt_spec.md) (with a machine-checkable `h3_prompt_spec.json`). This is the project's only source of truth; do not follow other formats circulating online.

---

## Layout

```
ai-shortfilm-studio/
├─ server.py              backend: HTTP service + ComfyUI scheduling + film assembly
├─ preview.html           frontend: single-file workstation
├─ start_studio.bat       launcher
├─ workflows/             5 ComfyUI workflow JSON files
├─ docs/                  H3 prompt spec (md + json)
├─ skills/                Trae skill: six-section storyboard writing
└─ projects/              generated at runtime, not tracked
```

---

## License

- Code: **AGPL-3.0** (see [LICENSE](LICENSE))
- Docs: **CC BY-NC 4.0**
- Model weights: not in this repository; each model follows its own license (see [NOTICE.md](NOTICE.md))

This project runs on distilled models released under the MiniMax H3 community license. It is **not an official MiniMax product**. The license excludes use in the EU, UK, South Korea and the United States.

## Feedback

Only **reproducible** reports are accepted (include hardware model, VRAM, model version and steps to reproduce). This repository does not promise a response to every issue.

---

# 简体中文

> 12G 显存，剧本进，成片出。家用显卡上的本地 AI 短片工作台。

一个跑在消费级显卡上的本地短片流水线：**角色 / 场景 / 道具设定图 → 分镜 → 带中文台词的镜头 → 一键拼接成片**。全部在本机运行，素材不上传。

后端只有标准库 `http.server`（1056 行），前端是单文件 `preview.html`（3624 行，无外部 CDN）。

本仓库只提供**部署方案与工作台**：不含模型权重，不含演示素材。模型由你自己下载，内容由你自己的 Agent 驱动生成。

---

## 实测边界（12G 显存，RTX 5070 Ti Laptop / 32G RAM）

| 项 | 结论 |
|---|---|
| 最长镜头 | 15 秒 / 362 帧，显存占用约 11 GB，几乎不随时长增长 |
| OmniRef 高画质上限 | 10 秒 @ 1344×768 稳定；15 秒需降到 960×544，否则会触发显存换页 |
| 推荐启动参数 | `--reserve-vram 1`（开启动态 VRAM 时 `--lowvram` 会失效） |
| 参考图数量 | 最多 9 张 |
| 中文台词 | 单条建议 ≤ 30 字，必须用 `<d>[Chinese] 台词</d>` 包裹 |
| 空镜 | 必须在提示词里显式禁止人声对白，否则模型会自行生成语音 |

耗时随帧数线性增长，但**显存开销几乎恒定**——所以优化顺序永远是「先缩时长，再降分辨率」。

---

## 快速开始

### 1. 准备 ComfyUI

自行安装 ComfyUI（GPL-3.0），并把本项目目录放在 ComfyUI 的**同级**目录：

```
你的目录\
├─ ComfyUI\                ← 自行安装
└─ ai-shortfilm-studio\    ← 本项目
```

如果不是同级，用环境变量指定：

```bat
set AI_COMFY_DIR=D:\ComfyUI
```

### 2. 下载模型（共 7 个，约 58.6 GB）

从官方页面下载后，放入 `ComfyUI\models` 对应子目录：

| 目标路径 | 模型 |
|---|---|
| `diffusion_models\fasth3_omniref_8step_pruned_int8_convrot.safetensors` | FastH3 OmniRef |
| `text_encoders\qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors` | H3 文本编码器 |
| `vae\minimax_h3_video_vae_int8_convrot.safetensors` | H3 视频 VAE |
| `vae\minimax_h3_audio_vae_fp32.safetensors` | H3 音频 VAE |
| `diffusion_models\qwen_image_2.1_turbo_int8_convrot.safetensors` | Qwen-Image 2.1 Turbo |
| `text_encoders\qwen3vl_8b_int8_convrot.safetensors` | Qwen 文本编码器 |
| `vae\qwen_image_2.1_vae_bf16.safetensors` | Qwen 图像 VAE |

> 本仓库**不提供权重、不提供转存链接**。模型许可见 [NOTICE.md](NOTICE.md)，其中 Qwen-Image 2.1 仅限研究与评估用途。

### 3. 启动

双击 `start_studio.bat`。脚本会自动拉起 ComfyUI、等待就绪、启动工作台并打开浏览器。

首次冷启动约 1–8 分钟（模型从硬盘加载），属正常现象。

> 不要直接双击 `preview.html`——`file://` 协议下所有接口都会失败。

### 环境变量

| 变量 | 默认 | 作用 |
|---|---|---|
| `AI_COMFY_DIR` | `..\ComfyUI` | ComfyUI 安装目录 |
| `AI_COMFY_URL` | `http://127.0.0.1:8188` | ComfyUI 接口地址（可指向远程实例） |
| `AI_COMFY_PORT` | `8188` | 启动脚本使用的 ComfyUI 端口 |
| `AI_STUDIO_PORT` | `8899` | 工作台端口 |

---

## Agent 接入

工作台不依赖任何特定 Agent，任何能发 HTTP 的客户端都能驱动：

```bash
# 1. 健康检查（同时返回 ComfyUI 是否在线）
curl http://127.0.0.1:8899/api/status

# 2. 提交生成任务
curl -X POST http://127.0.0.1:8899/api/generate \
  -H "Content-Type: application/json" \
  -d '{"mode":"audio_video","prompt":"...","seconds":5,"width":1344,"height":768}'

# 3. 轮询任务
curl "http://127.0.0.1:8899/api/job?id=<job_id>"

# 4. 取文件
curl "http://127.0.0.1:8899/api/file?path=<相对路径>"
```

分钟级任务建议 2–3 秒轮询一次。批量出片必须**串行**，并行会显存溢出。

提示词规范见 [docs/h3_prompt_spec.md](docs/h3_prompt_spec.md)（含机器可校验版 `h3_prompt_spec.json`）。这是本项目唯一的标准来源，不按其它网传格式编写。

---

## 目录

```
ai-shortfilm-studio/
├─ server.py              后端：HTTP 服务 + ComfyUI 调度 + 成片拼接
├─ preview.html           前端：单文件工作台
├─ start_studio.bat       启动脚本
├─ workflows/             ComfyUI 工作流 JSON（5 个）
├─ docs/                  H3 提示词规范（md + json）
├─ skills/                Trae 技能：六段式分镜写作
└─ projects/              运行时生成，不入库
```

---

## 许可

- 代码：**AGPL-3.0**（见 [LICENSE](LICENSE)）
- 文档：**CC BY-NC 4.0**
- 模型权重：不在本仓库内，遵循各自许可（见 [NOTICE.md](NOTICE.md)）

本项目基于 MiniMax H3 社区许可的蒸馏模型运行，**非 MiniMax 官方产品**。适用地域排除 EU / UK / 韩国 / 美国。

## 反馈

只接受**可复现**的实测反馈（附硬件型号、显存、模型版本、复现步骤）。本仓库不承诺按 issue 逐一响应。
