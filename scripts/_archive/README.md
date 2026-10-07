# scripts/_archive — 已停止使用的探针

**2026-10-07 功耗线与语种线过程探针，已停止使用。**

保留作为 `REPORT.md` 第 17.6 节（十个测量错误）与第 19 节（素材描述错误）
的**证据源**。如果将来有人质疑某个结论——比如"`+0.11W` 真的是采样窗口错位吗"
或者"`clip222` 真的是日语为主吗"——可以翻出当时跑的原始脚本对照。

**不要直接运行它们，也不要为它们补测试。**

## 归档清单

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