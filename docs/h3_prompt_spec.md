# MiniMax H3 提示词标准范式（Ref2VA / 全参考模式）

> 本文件是唯一事实来源（single source of truth）。工作台 `preview.html` 的 `r2vPrompt()` 与
> 技能 `h3-omniref-storyboard` 都必须按本文件生成提示词。
> **禁止自行发明 section、标签或句式。**
>
> 依据：
> - MiniMax H3 Prompt Guide（https://minimaxh3.co/prompt-guide）
> - MiniMax H3 Prompt Guide: Official Format & Step-by-Step（https://minimax-h3-ai.com/blog/minimax-h3-prompt-guide/）
> - 本地 ComfyUI 节点说明 `comfyui_embedded_docs/docs/MiniMaxH3ReferenceToVideo`
> - 更新：2026-10-29

---

## 1. 结构：只有六个 section

顺序固定，不增不减：

```
subject_definitions:
summary:
retention_analysis:
detailed_description:
overall_soundscape:
non_diegetic_music:
```

**禁止**出现 `dialogue:`、`input_rules:`、`language_control:` 等自定义 section。

## 2. 语言

- 六个 section 的**正文用英文**。
- 只有三类内容保留原语言：
  1. `<d>…</d>` 内的精确台词；
  2. 画面中必须逐字出现的可见文字（英文双引号包裹）；
  3. 参考资产的中文名（作为专有名词引用即可）。
- 中文口语化长句会被音频分支当成朗读文本，导致“把描述念出来”。

## 3. 台词写法（最容易错的地方）

```
牧师 (S1), 低沉·慢速·短促, says in standard Mandarin: <d>[Chinese] 你倒是说句话啊。</d>
```

硬规则：

1. **说话人身份、语气、动作全部放在 `<d>` 外**；
2. `<d>` 内**只放语言标签 + 逐字台词**：`<d>[Chinese] 台词</d>`；
3. 说话人用稳定 ID `(S1)`、`(S2)`，按发声顺序编号，同一人后续复用同一 ID；
4. 旁白写成 `An off-screen narrator (S1) narrates in standard Mandarin: <d>[Chinese] …</d>`，并保持画面角色嘴部闭合；
5. 一句台词只出现一次，不得在 `detailed_description` 里重复原文；
6. 跨镜头连续台词用 `<scenetrans>`，被结尾截断用 `<cutoff>`；
7. 无台词镜头写英文禁读句：`No spoken dialogue in this shot; characters stay silent, never read the descriptive text aloud.` / 空镜：`Empty shot with no characters and no human voice; never read the descriptive text aloud.`

## 4. 各 section 写法

### subject_definitions
参考资产定义，图片在 Subject 定义里引用，编号一一对应：

```
<Subject 1> is the 场景 "酒馆大堂", shown in <Picture 1>: 酒馆大堂.
```

- `<Picture N>`：仅在图片作为首帧/关键帧/分镜图使用时单独成行；只定义人物/场景/风格时，写在 Subject 行内。
- `<Video N>`：整段视频被剪辑、续写、或提供运镜节奏时使用。
- `<Audio N>`：音频被完整/部分复用，或作为音色、口音、音乐风格参考时使用。

### summary
一句话：任务类型 + 目标视频。任务类型必须取自固定词表：

`keyframe completion` / `reference generation` / `video editing` / `video continuation` / `audio reuse` / `audio reference`

```
[reference generation] Single 8-second shot: 中景，双人对峙构图，50mm。...
```

### retention_analysis
逐条写标签与实际保留关系，句式固定：

```
<Subject 1> (appears in [Shot 1]): fully_preserved - keep the 场景's appearance, colors and materials unchanged.
```

固定词表：`fully_preserved` / `partially_preserved` / `attribute_transfer` / `weak_reference` /
`fully_copy` / `partially_copy` / `reference`（音频）/ `weak_reference (audio)`。

- 说话人 ID `(S1)` **不写**进 retention_analysis。

### detailed_description
- `[Shot 1]` 开头；全参考模式下，先写 1–2 句整体风格，再进入镜头内容。
- 按播放顺序写：景别与构图 → 主体外观/位置 → 环境与光线 → 可见动作与状态变化 → 运镜 → 同步音效 → 引出下一镜的事件。
- 参考标签在“真正生效的位置”插入；同一标签后续复用不再重定义。
- 台词按第 3 节规则嵌入。
- **台词必须带时间锚点**（本机实测补充，非官方语法）：`At about X.X s, 说话人 (S1), 语气, says in standard Mandarin: <d>…</d>`。
  - 手填：分镜台词行首写 `[2.5s]`（也接受 `[2.5]` / `[2.5秒]`），写进提示词就是该秒数。
  - 未填：第一条落在 1.0 s，其余按 `(时长-1.6)/条数` 均匀递增，上限 `时长-0.3`。
  - 依据：没有锚点时模型会把所有台词堆到镜头最后几秒，前段大量空场。
- 全参考提示词英文正文 350–500 词为参考区间（不是硬指标）；台词多的镜头优先保证台词时间线完整。
- 不用 `【画面】【表演】【台词】` 这类中文标签。

### overall_soundscape
只写环境音与物理音效；**不重复台词内容**。需要全静音时写 `N/A`。

### non_diegetic_music
只写观众能听到的配乐（乐器、节奏、与参考音频的关系）；无配乐写 `N/A`。

## 5. 运镜词表（写运镜用固定词，不用自造词）

| 意图 | 词 |
|---|---|
| 变焦 | Zoom In / Zoom Out |
| 前后移动 | Push In / Pull Out |
| 左右平移 | Truck Left / Truck Right |
| 升降 | Pedestal Up / Pedestal Down |
| 原地摇 | Pan Left / Pan Right / Tilt Up / Tilt Down |
| 环绕 | Arc Shot |
| 跟随 | Tracking Shot |
| 主观 | POV |
| 滚转 | Roll Clockwise / Roll Counterclockwise |
| 抖动 | Shake Slightly / Shake Strongly |
| 静止 | Static Shot |

幅度用 `with small amplitude` / `with large amplitude`，速度用 `at slow speed` / `at fast speed`。

## 6. 反面清单（出现任意一条即判不合格）

1. 出现 `dialogue:` / `input_rules:` / `language_control:` 等非官方 section；
2. 六个 section 顺序错乱或缺失；
3. 台词没有 `<d>` 包裹，或用「」引号代替 `<d>`；
4. 台词里混入说话人、语气、动作描述；
5. 同一句台词在 `detailed_description` 里重复出现；
6. `overall_soundscape` 复述台词；
7. `non_diegetic_music` 用 `no music` 而不是 `N/A`；
8. retention_analysis 里出现 `(S1)`；
9. 中文描述被当成朗读文本（缺禁读句 / 缺 `<d>`）；
10. 自行发明长句规则、额外 section 或标签。

## 7. 本机已知偏差（记录在案，不得当成官方写法）

- 工作台的镜头描述字段是中文，`detailed_description` 正文仍含中文段落（本地无翻译模型）。
  这是**已知偏差**，不是可选写法；有英文正文时优先用英文。
- 帧数取整、像素档、15 秒限制属于本机 ComfyUI 工作流约束，**不是**提示词范式的一部分。
