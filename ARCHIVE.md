# ARCHIVE.md — 项目定稿与归档记录

> 本文件记录本项目收尾时的最终定稿状态、技术选型结论、已知缺陷、未测项、语言注入机制差异以及未来迁移与打包指南。

---

## 序言

这个项目从“让风扇不转”开始，最后落在一个能用的管线、三条路线、和一套完整的记录上。

主力路线是 **qwen3-hy + 块状输出（按标点分块）**。
Whisper 保留为回退，Gemma 音频直出已确定在这台机器上到不了，Gemma 文本后端挂在开关后面等换卡。

已知缺陷两条：日语识别系统性写成汉字（21.7%），识别层偶有重复。两条都在上游，应用层已到顶。

未测四项：StreamRevise-v4、e_3min 上的 A/B、2.55.0 出货 build、Gemma 文本后端在大卡上的倍数。

这一周试了六条路，砍掉五条半，剩下一条能用的。
不是白玩，是把设备能走的和不能走的分清楚了。

---

## 一、定稿状态与架构矩阵

### 1. 三条路线总览

| 路线标识 | 组成 | 状态与定位 | 输出模式 | 核心特点与实测依据 |
|---|---|---|---|---|
| **`qwen3-hy`** | **Qwen3-ASR (0.6B) + 混元翻译 (1.8B)** | **主力路线** | **块状输出**（按标点分块） | ASR 耗时 p50 180ms（较 Whisper 快 4.6 倍），显存与算力占用低；密集体感结合标点分块输出，字词不跳动 |
| **`whisper-hy`** | faster-whisper (large-v3-turbo) + 混元翻译 (1.8B) | **回退路线** | **流式输出** | 识别准确度成熟、语言覆盖广，但 ASR p50 ~820ms，需 750ms 节奏避免 overrun |
| **`gemma-e2e`** | Gemma4 E2B Audio-Native（端到端直出） | **停用 / 实验备选** | 流式输出 | 干净旁白上可用；在背景配乐/电影对白等复杂音频下识别崩溃（噪声边界问题，REPORT §25） |

### 2. 核心模块选型定稿

- **音频采集**：`WASAPI Loopback` 系统音频回环（`AUDIO_SOURCE=loopback`）。绕过 Chrome `TabCapture` 全屏黑屏、DRM 限制及多源混叠问题，内置动态增益自适应。
- **字幕渲染**：Windows 原生置顶 Tkinter 窗口。支持完全透明悬浮（“只有字”模式）与标准黑框模式，内建文本外发光/描边高对比度渲染、自适应换行与抗抖动防跳变。具备差异化静音自动隐去机制（“只有字”模式下超时自动撤回窗体隐去；黑框模式下保留黑底框仅清除文字，彻底消除反复隐藏/弹出的闪烁感）。
- **交互控制**：Chrome 扩展前端极简化，默认锁定主力路线，支持字号实时缩放、多色调色板一键预设、改口节奏平滑调节及两路状态自动同步。

---

## 二、权重身份确认与历史数据锚定

### 1. 当前权重身份
- 端口 `8080` 上实际加载运行的翻译权重为：
  `models/hy-mt2-1.8b/Hy-MT2-1.8B-StreamRevise-v4-Q4_K_M.gguf`
- 目录 `models/hy-mt2-1.8b/` 中目前仅保留此一份微调权重。

### 2. 测量数据基准说明
- **重要说明**：`REPORT.md` 中所有历史基准数据（如 §47–§54 中的延迟与首字耗时），均在原版 `Hy-MT2-1.8B-Q4_K_M.gguf` 基线权重上测得。
- `StreamRevise-v4` 为字幕场景流式微调版（LoRA 合并权重），在当前设备上直接作为主力运行，但尚未单独进行全量回归跑分。历史绝对数值与当前运行态不可直接并列对比。

---

## 三、已知缺陷

1. **日语识别系统性写成汉字并误判为中文（发生率 21.7%）**
   - **表现**：在日语或日英混合素材中，Qwen3-ASR 有约 21.7% 的概率将日语语音直接转录为汉字，并将其源语言标记判定为 `Chinese`。
   - **归因**：上游 Qwen3-ASR 0.6B 小模型的跨语言先验分布问题，应用层无法在上游输出前拦截纠正。

2. **识别层偶发短语重复**
   - **表现**：在部分含较长停顿或背景杂音的音频片段末尾，识别层偶发连续重复输出相同词句。
   - **处理**：应用层已实现 `AUDIO_REPEAT_PENALTY` 与 `collapse_caption_repeats` 重复折叠过滤，但上游 Decoder 的生成幻觉无法彻底在源头消除。

---

## 四、未测项（待验证清单）

1. **`StreamRevise-v4` 独立指标验证**：当前直接作为主力权重上岗，未在基准音频集上建立独立的完整精度与延迟台账。
2. **`e_3min` 样本上的 Whisper vs Qwen3 A/B 盲测**：历史 A/B 均在 `lecture_mix` 与 `blog_3min` 上采集，未在 `e_3min` 上做过全量对比。
3. **llama.cpp `2.55.0` 出货构建**：本机已安装 2.55.0，但为保数据可比性，历史所有测试均严格钉在 `2.47.0` / `2.53.0`；2.55.0 尚未被测量覆盖。
4. **Gemma 文本翻译后端在大显存 GPU 上的吞吐倍数**：在 4GB 显存显卡上受限于 CPU/GPU 分层，实测约 50–75 tok/s（2.5–3 倍），在大容量显卡上的全层 GPU 卸载表现待测。

---

## 五、语言注入机制差异（技术归档）

Whisper 与 Qwen3-ASR 在语言控制机制上存在本质差异：

### 1. 两端注入机制对比

```
Whisper (CT2 / faster-whisper)
  - 接口参数：transcribe(language="zh")
  - 编码规范：ISO 639-1 简码（en, ja, zh, ko 等）
  - 注入机制：模型内部解码器初始 token 注入，属官方标准参数

Qwen3-ASR (llama.cpp)
  - 接口参数：language="Japanese"（必须使用全名，非 ISO 码）
  - 注入位置：assistant\n 之后
  - 模板形式：user\n<audio_placeholder>\nassistant\nlanguage {full_name}<asr_text>
```

### 2. 当前实现机制
- **Whisper 分支**：直接传递 ISO 简码给 `model.transcribe(language=code)`。
- **Qwen3-ASR 分支**：在 `server/asr.py` 中将 ISO 简码映射为英文全名（如 `ja` -> `Japanese`），当用户锁定特定语种时（非 `auto`），在 user 轮的 prompt 中注入 `Transcribe the {full_name} audio.`。
- **双模解析保护**：`parse_qwen3_asr` 同步兼容带 `<asr_text>` 的前缀结构与不带 `<asr_text>` 的直接输出，自动去除语言标记并提取真实转录文本，彻底避免将语言标签打印到屏幕或语音播报。

---

## 六、Hooks 与开关清单（换机/调试入口）

以下环境变量为管线唯一生效的控制开关，无需修改源码即可切换：

| 环境变量 | 可选值 | 默认值 | 作用说明 |
|---|---|---|---|
| `TRANSLATION_CHAIN_RUNNING` | `qwen3-hy` / `whisper-hy` / `gemma-e2e` | `qwen3-hy` | 声明当前运行链路 |
| `TRANSLATION_ASR_BACKEND` | `qwen3` / `whisper` | `qwen3` | 识别引擎后端切换 |
| `TRANSLATION_MT_BACKEND` | `hy-mt2` / `gemma4` | `hy-mt2` | 翻译模型后端切换 |
| `TRANSLATION_AUDIO_SOURCE` | `loopback` / `socket` | `loopback` | 音频采集源（WASAPI 系统音频 / WebSocket 注入） |
| `TRANSLATION_PARTIAL_INTERVAL_MS` | `250` ~ `1000` | `750` | 识别中间态改口节奏（毫秒） |
| `TRANSLATION_LLAMA_VERSION` | 如 `2.53.0`, `2.55.0` | `""` (浮动最新) | 显式钉死 llama-server 二进制版本 |
| `TRANSLATION_VAD_MIN_SPEECH_MS` | 整数（毫秒） | `250` | VAD 语音切片最短有效门限 |

---

## 七、剥离与打包分发指南

### 1. 四个交付边界确认

1. **产品范围**：
   - 服务端程序（`server/`）
   - 原生字幕渲染器（`server/caption_window.py`）
   - Chrome 控制插件（`extension/`）
   - 启动与桥接脚本（`scripts/start.ps1`, `scripts/native_host.py`）
2. **权重分发**：
   - 建议由用户通过 ModelScope 首次下载或自备，不随代码仓库打包（体积 > 2GB）。
3. **llama.cpp 二进制**：
   - 默认通过 `scripts/resolve_llama.ps1` 自动发现本机 LM Studio 安装目录；亦可打包独立 build 到 `tools/`。
4. **分发形态**：
   - 建议提供源码 + PowerShell 一键启动脚本（`scripts/start.ps1`），配合 Chrome 开发者模式加载 `extension/`。

### 2. 清理与打包排除规则

- **打包需排除**：
  - `tests/`（保留在源码仓库，分发物排除）
  - `logs/`（仅保留 `start.log` 骨架与 `run.*.meta`）
  - `tools/whisper.cpp` 编译产物
  - `__pycache__/` 及 `.pytest_cache/`
- **必须保留**：
  - `logs/run.*.meta`（历史测量数据锚点）
  - `models/silero_vad.onnx`
  - `server/`、`extension/`、`scripts/` 全部核心模块
