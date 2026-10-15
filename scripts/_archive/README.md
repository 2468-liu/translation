# scripts/_archive — 已停止使用的探针

**2026-10-07 功耗线与语种线过程探针，已停止使用。**
**2026-10-08 又归档一批：音频直出边界的取证探针（见下第二张表）。**

保留作为 `REPORT.md` 第 17.6 节（十个测量错误）、第 19 节（素材描述错误）
与第 25 节（音频直出边界）的**证据源**。如果将来有人质疑某个结论——
比如"`+0.11W` 真的是采样窗口错位吗"、"`clip222` 真的是日语为主吗"、
"音频直出真的只对干净旁白有效吗"——可以翻出当时跑的原始脚本对照。

**不要直接运行它们，也不要为它们补测试。**

## 归档清单（2026-10-08，音频直出边界）

| 脚本 | 当时的问题 | 结论 |
|---|---|---|
| `probe_audio_by_clip.py` | 音频直出为什么只在 blog_3min 上工作 | 素材分界：crest 18.5dB/有真静音 → 正确；12.9dB/全程有声 → 崩坏（REPORT 25.3） |
| `probe_audio_duration.py` | 片段太短是崩坏的原因吗 | **不是**：把 1.12s 语音补到 12s 仍错译；补到 4s 时正确译文与垃圾同时出现（REPORT 25.2） |
| `probe_audio_gain.py` | 响度（e_3min RMS 是 blog 两倍）是原因吗 | **不是**：增益 1→1/16，输出几乎逐字不变（REPORT 25.2） |
| `probe_audio_inputs.py` | 提示词/采样率能救吗 | **不能**：三种提示词、8k/16k/24k 都坏（REPORT 25.2） |
| `probe_clip_audio.py` | 用什么量把两份素材分开 | crest factor 与帧 RMS p90/p10；blog 有真静音（p10=0），另两份没有（REPORT 25.3） |
| `probe_timeline_alignment.py` | 时间轴文本和音频对得上吗 | **对得上**：whisper 逐句印证，排除了"文本对错音频"（REPORT 25.2） |

**归档理由**：结论已进 `REPORT.md` 第 25 节；它们各自只回答一个已关闭的问题，
且都依赖当时那台机器的素材。留在 `scripts/` 会让人以为它们是日常工具。

## 归档清单（2026-10-07，功耗线与语种线）

| 脚本 | 当时的问题 | 结论 |
|---|---|---|
| `probe_call_cost.py` | whisper.cpp 单次成本 | `total` 含 `load time`，口径错 |
| `probe_resident_cost.py` | 常驻边际成本 | 空输出伪装成速度（43ms/2030ms） |
| `probe_power_diff.py` | ASR 放 GPU 的功耗 | 采样器与请求循环窗口错位，得出反向的 +0.11W |
| `probe_instrument.py` | 仪器本身噪声 | 正对照通过，但 MDD 标签由参数生成而非数据 |
| `probe_lang_baseline.py` | 语种置信度分布 | 结论有效（置信度单峰） |
| `probe_boundary_error.py` | 边界处错语种代价 | 91% 垃圾——但成因混了真边界与 small 检测幻觉 |
| `probe_semantic_agreement.py` | 错语种译文语义是否接近 | 25% 一致 → "先上屏后覆盖"被证伪 |
| `probe_collapse_semantics.py` | 折叠函数的真实行为 | 推翻了"哈哈哈会变成哈"的推断（实际是 `'哈哈哈'`） |
| `probe_collapse_impact.py` | 折叠对存量译文的影响 | 82,522 条中改 140 条（0.17%） |
| `probe_offline_asr.py` | 离线分块语种/耗时 | 有效 |

## 它们为什么有价值

第 17.6 节列了十个测量错误，其中五个是"分子分母的窗口不一致"。
**光看结论无法判断一个错误是方法错还是系统错**——`+0.11W` 看起来完全合理。

这些脚本是那十个错误的具体形状。删掉它们，REPORT 里那节就只剩断言。

## 当前有效的脚本

```
scripts/build_timing.py        分片级判语种重建时间轴（校验失败拒写）
scripts/rag.py                 检索仓库笔记
scripts/audit_pauses.py        读 vad.end / vad.silence
scripts/manifest.py            臂身份唯一来源
scripts/smoke.ps1              一分钟冒烟
scripts/diff_defaults.py       两版本间的默认值差异表
```