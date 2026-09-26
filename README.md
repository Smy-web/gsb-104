# 应急物资储备库会议纪要系统 · 物资编码抽取子系统

## 这个仓库是什么

应急物资储备库开布局优化会，现场录音转写成中文文本之后，要有人把文本里提到的物资和物资编码抽出来，交给补库计划、汇总渲染和前端 3D 仓库页面。这里裁剪出来的就是「抽取」这一段：转写、说话人分离、摘要渲染、邮件发送都不在范围内，不要去找它们。

## 目录

- `backend/config.py` — 配置项，pydantic-settings，从环境变量和 `.env` 读。
- `backend/models.py` — 上下游交换的数据形状（pydantic 模型）。抽取模块本身不 import 它，留着是让你看清下游拿什么。
- `backend/core/material_extractor.py` — 本次要处理的模块。

`backend` 和 `backend/core` 都没有 `__init__.py`，按命名空间包用。验证命令必须从仓库根目录、用 `-m` 的形式跑，这样仓库根才会进 `sys.path`，`from backend.core.material_extractor import MaterialCodeExtractor` 才解析得到。

## 运行环境

应急局内网机器，离线。`~/venvs/gsb-warehouse` 里只有 `requirements-task.txt` 列出来的包。**spacy、whisper、torch、pyannote.audio 都没有装，也不允许装、不允许联网下载**；spacy 的中文模型 `zh_core_web_sm` 同样拿不到。模块里凡是用到 spacy 的路径，都必须能在这些包缺失的情况下正常工作。

## 数据形状

`extract_material_codes(text: str) -> dict`，下游按这四个键取数：

- `extracted_codes`: `List[str]` — 直接从文本里抽出来的编码字符串。
- `matched_materials`: `List[dict]` — 每项 `{"name", "code", "category"}`；走 NLP 实体识别兜出来的条目额外带 `"from_nlp": True`，编码形如 `EMG-UNK-001`，`category` 是 `待分类`。
- `all_unique_codes`: `List[str]` — `extracted_codes` 与 `matched_materials` 里编码的合集，去重。
- `material_categories`: `Dict[str, List[dict]]` — 按分区名把 `matched_materials` 分组。

`extract_with_context(segments: List[dict]) -> List[dict]`：输入形如 `[{"start": float, "end": float, "text": str, ...}]`，逐段调用 `extract_material_codes`，结果挂在该段的 `extracted_materials` 键上，其余键原样保留，输出顺序与输入一致。一份 200 段的纪要就是 200 次调用。

## 仓库分区口径（前端与补库报表的硬约定）

储备库固定四个分区，前端 3D 页面按这四个名字取数，少一个键就渲染失败：

| 分区 | 货架 | 物资 |
| --- | --- | --- |
| 医疗物资 | A 区 | 医用口罩、N95口罩、防护服、护目镜、急救包、呼吸机、消毒液、绷带、药品 |
| 生活物资 | B 区 | 方便面、压缩饼干、饮用水、帐篷、毛毯、棉被、手电筒、充电宝 |
| 救援物资 | C 区 | 救生艇、救生衣、绳索、切割机、破拆工具、发电设备 |
| 通讯物资 | D 区 | 对讲机、卫星电话、应急广播、备用电池 |

一条物资只属于一个分区。

## 编码口径

系统在用的物资编码是三段式，中间一段标大类：`EMG-MED-001`（医疗）、`EMG-LIF-004`（生活）、`EMG-RES-002`（救援）。会上也会念到旧台账里的编码，形如 `EMG-1234`、`MAT-2024`、`RES-4567`、`AB-998877`。纪要正文是语音转写出来的中文，编码前后经常直接挨着汉字，没有空格。

## 已定口径与自定口径

上面几节是系统已经定死的口径，改不了。除此之外还有若干处需要你自己拿主意（边界怎么定义、纯数字串算不算编码、NLP 不可用的状态怎么暴露等），定了就写进下面这一节，评审看这里。

### 本次做题人填写

（把你的取舍和理由写在这里。）
