# NOTICE

## Model attribution and licensing

**English** | [简体中文](#简体中文)

This repository contains **no model weights**, only code that calls the ComfyUI HTTP API and workflow JSON files.

The following models are required at runtime. Download them from their official pages and comply with their respective licenses:

| Model | Official source | Key license terms |
|---|---|---|
| FastH3 OmniRef (MiniMax H3 distilled) | `FastVideo/FastH3-Omni-Comfy` | MiniMax H3 community license; excluded regions: EU / UK / South Korea / US; commercial UI must prominently credit "MiniMax H3" |
| Qwen-Image 2.1 Turbo | `Comfy-Org/Qwen-Image-2.1` | `qwen-research` license, **research and evaluation only**, commercial use requires separate authorization |
| Text encoders / VAE components | Same as above | Follow the corresponding model license |

This project is not affiliated with MiniMax or Alibaba Cloud Qwen. It is **not an official product**.

## Other dependencies

- **ComfyUI**: GPL-3.0, not distributed here; install it yourself.
- **FFmpeg**: GPL / LGPL, runtime dependency only, not distributed here.
- **MyMemory translation API**: when offline or out of quota, the code falls back to the original text and never blocks generation.

## Disclaimer

This repository grants no commercial rights to any model or generated content. Users are responsible for confirming that their region, usage and model licenses are compatible, and for their own compliance.

---

# 简体中文

## 模型归属与许可

本仓库**不包含任何模型权重**，仅包含调用 ComfyUI HTTP 接口的代码与工作流 JSON。

运行时需要使用以下模型，请自行从官方页面下载并遵守各自的许可协议：

| 模型 | 官方来源 | 许可要点 |
|---|---|---|
| FastH3 OmniRef（MiniMax H3 蒸馏版） | `FastVideo/FastH3-Omni-Comfy` | MiniMax H3 社区许可；适用地域排除 EU / UK / 韩国 / 美国；商用 UI 须显著标注「MiniMax H3」 |
| Qwen-Image 2.1 Turbo | `Comfy-Org/Qwen-Image-2.1` | `qwen-research` 许可，**仅限研究与评估用途**，商用需另行获得授权 |
| 文本编码器 / VAE 组件 | 同上两处 | 随对应模型许可 |

本项目与 MiniMax、阿里云通义千问均无隶属关系，**非官方产品**。

## 其他依赖

- **ComfyUI**：GPL-3.0，本仓库不分发，请自行安装。
- **FFmpeg**：GPL / LGPL，仅作为运行时外部依赖，本仓库不分发。
- **MyMemory 翻译接口**：代码在断网或接口额度耗尽时自动回退原文，不阻断生成。

## 免责

本仓库不授予任何模型或生成内容的商业权利。使用者应自行确认所在地域、用途与模型许可的一致性，并自行承担合规责任。
