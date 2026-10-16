# ARCHIVE.md — 项目定稿与归档记录

> 本文件记录本项目收尾时的最终定稿状态、技术选型结论、已知缺陷、未测项、语言注入机制差异以及未来迁移与打包指南。

---

## 序言

这个项目从“让风扇不转”开始，历经全链路深度实测与架构重构，最终沉淀为一个兼顾**电影弱音增强、大模型自回归抗噪、极速流式同传、标点分层单调锁定**的高性能工业级桌面实时双语字幕系统。

- **ASR 双模热切换**：`Qwen3-ASR 0.6B`（野生视频/电影弱音自回归强推断，8099 端口）⟷ `Nemotron 3.5 ASR 0.6B`（会议/播客极速流式 RNN-T 62ms 步进）。
- **翻译引擎**：`Index-Translate 2B`（Q4_K_M / Q5_K_M，口语/影视专项，8080 端口）+ `Hy-MT2 1.8B StreamRevise-v4`（流式 LoRA 备选）。
- **调度系统**：`-np 2` 双槽位无锁并行 + 过时草稿主动抢占调度（Active Draft Preemption，排队延迟从 136ms 直降为 0ms）。
- **锁定状态机**：`MonotonicPrefixFreezer` 标点分层单调锁定（Tier 1 终态归档进历史抽屉，Tier 2 逗/顿/分/冒增量固化防抖，语法缩写与小数穿透保护）。
- **显示与交互**：PyQt6 置顶抗抖动高对比度字幕窗 + 360 级贴边折叠阻尼悬浮球（`qt_pet_widget.py`）+ Chrome 仿原生极简弹窗与单一真实源（SSOT）中央状态分发。
- **采集链路**：`WASAPI Loopback` 系统音频回环 + 前置数字 AGC 弱音增强（`AudioEnhancer`，微弱人声平滑放大 6~16dB，软压限防破音）。
- **交付形态**：独立桌面客户端 [`D:\TranslationDesktop\TranslationDesktop.exe`](file:///D:/TranslationDesktop/TranslationDesktop.exe)（不依赖浏览器与 Python 环境，双击即用）。

---

## 一、定稿状态与架构矩阵

### 1. 路线矩阵总览

| 路线标识 | 组成 | 状态与定位 | 输出与锁定模式 | 核心特点与实测依据 |
|---|---|---|---|---|
| **`qwen3-hy`** | **Qwen3-ASR (0.6B, 8099) + Index-Translate 2B (8080)** | **影视出货主力** | **单调前缀锁定流式** (Tier 1 归档 / Tier 2 固化) | 适合电影大片、嘈杂背景、微弱对白；ASR 耗时 p50 180ms，Index-Translate TTFT 43ms；45ms 极速上屏且字词稳固不抖动 |
| **`nv-hy`** | **Nemotron 3.5 ASR (0.6B, 本地) + 混元翻译 (1.8B, 8080)** | **极速会议主力** | **极速流式** (160ms 步长 + 前缀锁定) | 极速全双工同传；ASR step 延迟 62.8ms，RTF 0.392；静音单调 blank 发射；低显存低能耗 |
| **`whisper-hy`** | faster-whisper (large-v3-turbo) + 混元翻译 (1.8B) | **通用回退路线** | **流式输出** (750ms 节奏) | 识别成熟、语种广泛；ASR p50 ~820ms，需 750ms 节奏避免 overrun，作为备用兜底 |
| **`gemma-e2e`** | Gemma4 E2B Audio-Native（端到端直出） | **实验归档 / 停用** | 流式输出 | 仅干净旁白可用；在复杂背景音乐与电影对白下噪声边界崩溃（REPORT §25） |

### 2. 核心模块选型定稿

- **音频采集**：`WASAPI Loopback` 系统音频回环（`AUDIO_SOURCE=loopback`）+ 进程级环回捕获 DLL。内置数字 AGC 自适应增益，摆脱浏览器 TabCapture 黑屏与系统音量绑定限制。
- **字幕渲染**：PyQt6 原生高刷置顶窗口。完全透明悬浮（“只有字”）与标准黑框双模，自适应平滑换行、边缘拉伸调节、历史字幕抽屉（滚轮直达）。具备差异化静音隐藏（“只有字”超时隐去全窗；“黑框”保留黑底框仅清字，杜绝闪烁）。
- **桌面宠物球**：360 悬浮球级交互。阻尼磁吸平滑贴边（`QPropertyAnimation`）、静止 2 秒自动向屏幕外收缩半身只留小耳朵、鼠标悬停滑出恢复、一键完全隐藏与托盘随时召回。
- **状态同步总线**：单一真实源（Single Source of Truth）。插件弹窗、桌面黑框、悬浮球、系统托盘四端状态由中央权威总管统一分发，彻底根治状态打架与连点多次问题。

---

## 二、权重身份确认与历史数据锚定

### 1. 当前权重身份与物理路径
所有生产模型权重全部物理隔离存储于 **`D:\TranslationDesktop\models\`**：

- **ASR 识别**：
  * Qwen3-ASR：`D:\TranslationDesktop\models\Qwen-ASR-0.6B\Qwen3-ASR-0.6B-Q8_0.gguf` + `mmproj-Qwen3-ASR-0.6B-Q8_0.gguf`
  * Nemotron 3.5 ASR：`D:\TranslationDesktop\models\NV3.5-ASR-0.6B\nemotron-3.5-asr-streaming-0.6b.q8_0.gguf`
  * Faster-Whisper：`D:\TranslationDesktop\models\faster-whisper-large-v3-turbo\`
  * VAD：`D:\TranslationDesktop\models\silero_vad.onnx`
- **翻译后端**：
  * Index-Translate：`D:\TranslationDesktop\models\Index-Translate\Index-Translate-2B.Q4_K_M.gguf`（推荐）与 `Q5_K_M.gguf`
  * Hy-MT2：`D:\TranslationDesktop\models\hy-mt2-1.8b\Hy-MT2-1.8B-StreamRevise-v4-Q4_K_M.gguf`

### 2. 测量数据基准说明
- `REPORT.md` 早期历史基准（§47–§54）在原版 `Hy-MT2-1.8B-Q4_K_M` 测得；
- 中期测试在 `StreamRevise-v4` 测得；
- 当前定稿推荐基于 `Index-Translate 2B (Q4_K_M)` 配合 `-np 2` 双槽位并发。历史绝对数值与当前运行态不可直接并列对比。

---

## 三、已知缺陷与踩坑归档（教训台账）

1. **子进程解耦入口遗漏导致 `caption.paint = 0`**
   - **表现**：后台 ASR/MT 正常产出数据，但字幕窗一次未画，评测 `caption.paint = 0`。
   - **根因**：`server/qt_caption_window.py` 解耦为 facade 时未暴露 `if __name__ == '__main__': main()`，子进程瞬时退出导致 8766 端口无人监听；且 Qt 重绘层缺失 `perf.emit("caption", "paint", ...)` 埋点。
   - **治理**：补全模块入口与 `perf.emit` 埋点，纳入启动强制健康自检。

2. **启动脚本硬编码与静默降级（Silent Fallback）**
   - **表现**：选择 `qwen3-hy` 链路，但因查找路径不一致，`start.ps1` 悄悄降级为 `whisper-hy`，产生虚假报告。
   - **历史证据追溯**：检索历史 `start.log`（`2026-10-13 11:44:01` 与 `11:59:14`）明确记录：`Qwen3-ASR 权重缺失 ... 识别会卡住，改用 whisper`；证实历史 120 份无元数据标明的 `qwen3-hy` 跑测记录全被静默降级为 whisper，确证假阳性不可信。
   - **治理**：统一锁定 `D:\TranslationDesktop\models\`，实行 **Fail-Fast** 铁律：权重缺失立即抛错退出，坚决禁止隐式降级。

3. **细粒度短句评估在粗暴 VAD 截断下的指标错位（0/15 假象）**
   - **表现**：25s 片段原版 SRT 有 15 句台词，VAD 仅切 2 大段，`eval_recall.py` 采用整体二元 Dice 系数（阈值 0.5），长段稀释短句导致 Dice 得分只有 0.15，判定为 0 召回。
   - **治理**：结合 `MonotonicPrefixFreezer` 的标点断句结果进行微粒度句子对齐。

4. **日语识别系统性写成汉字并误判为中文（发生率 21.7%）**
   - **表现**：Qwen3-ASR 0.6B 小模型在日语混合音频中有约 21.7% 概率直接转录为汉字并标为 Chinese。
   - **归因**：上游小模型先验分布问题。通过锁定语言（`language=ja`）或接入 `AudioEnhancer` 提升辨识度。

---

## 四、未测项（待验证清单）

1. **`ds2_*` 10 个极端电影场景（dense / quiet / action / silent）在 Qwen3 + Index-Translate 链路下的全量回归指标**；
2. **切片档位寻优**：Qwen3-ASR 在 500ms / 750ms / 1000ms 切片下的幻觉与重复率对比；
3. **真实长片体感回看**：使用《生活大爆炸》S12E22（22.mp4）与《奇异博士2》（mp4+srt）验证长时间看电影的稳定性和发热。

---

## 五、语言注入机制差异（技术归档）

```
Whisper (CT2 / faster-whisper)
  - 接口参数：transcribe(language="zh")
  - 编码规范：ISO 639-1 简码（en, ja, zh, ko 等）

Qwen3-ASR (llama.cpp)
  - 接口参数：language="Japanese"（英文全名）
  - 模板形式：user\n<audio_placeholder>\nassistant\nlanguage {full_name}<asr_text>
  - 提示词构造：锁定语种注入 "Transcribe the {full_name} audio."，自动检测注入 "Transcribe the audio."
  - 解析保护：parse_qwen3_asr 正则剥离语言元数据与特殊 token，防止将语言标记打印上屏
```

---

## 六、Hooks 与环境变量清单

| 环境变量 | 可选值 | 默认值 | 作用说明 |
|---|---|---|---|
| `TRANSLATION_CHAIN` | `qwen3-hy` / `nv-hy` / `whisper-hy` | `qwen3-hy` | 声明启动链路 |
| `TRANSLATION_ASR_BACKEND` | `qwen3` / `nemotron` / `whisper` | `qwen3` | ASR 识别引擎后端 |
| `TRANSLATION_MT_BACKEND` | `index-translate` / `hy-mt2` / `gemma4` | `index-translate` | 翻译模型后端 |
| `TRANSLATION_AUDIO_SOURCE` | `loopback` / `socket` | `loopback` | 音频采集方式 |
| `TRANSLATION_SAMPLE_WAV` | 文件路径 | `samples\blog_3min.wav` | 回放评测指定音频 |
| `TRANSLATION_CAPTION_BACKEND` | `qt` / `tk` | `qt` | 字幕窗渲染引擎 |
| `TRANSLATION_MODELS_DIR` | 绝对路径 | `D:\TranslationDesktop\models` | 模型权重根目录 |

---

## 七、独立桌面客户端打包与发布

- **打包构建脚本**：[`scripts/build_desktop_exe.ps1`](file:///c:/Users/20693/Desktop/translation/scripts/build_desktop_exe.ps1)
- **交付目标路径**：[`D:\TranslationDesktop\TranslationDesktop.exe`](file:///D:/TranslationDesktop/TranslationDesktop.exe)
- **运行依赖**：已内置 PyQt6、CUDA 12 运行时、WASAPI 回环捕获 DLL 与资源图标；自动联动 D 盘已安装的模型权重与 `llama-server.exe`。
