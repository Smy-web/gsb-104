"""MaterialCodeExtractor 的口径钉板测试。

spacy 在内网环境不存在，凡涉及 NLP 的用例一律用假模块替身
（monkeypatch 掉抽取模块里的 spacy 引用），并统计模型加载次数。
"""
import json
import logging
import os
import subprocess
import sys

import pytest

from backend.core import material_extractor
from backend.core.material_extractor import MaterialCodeExtractor


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DIRTY_TEXT = (
    "本次调拨物资编码EMG-1234已入库，"
    "EMG-1234 与 MAT-2024 与 RES-4567 与 123456 都已核对，"
    "旧台账编码 AB-998877 继续沿用，"
    "系统编码 EMG-MED-001、EMG-LIF-004、EMG-RES-002 直接出库，"
    "会议时间 20260301 ，联系人电话 13800138000，"
    "位数不够的 EMG-123 不算数。"
)


# ---------- 假 spacy 替身 ----------

class FakeEnt:
    def __init__(self, text, label):
        self.text = text
        self.label_ = label


class FakeDoc:
    def __init__(self, ents):
        self.ents = ents


class FakeSpacy:
    """统计 load 调用次数的假 spacy 模块。"""

    def __init__(self, ents=None, fail_with=None):
        self.load_count = 0
        self._ents = ents or []
        self._fail_with = fail_with

    def load(self, model_name):
        self.load_count += 1
        if self._fail_with is not None:
            raise self._fail_with

        def nlp(text):
            return FakeDoc(self._ents)

        return nlp


@pytest.fixture
def extractor():
    return MaterialCodeExtractor()


# ---------- 症状 1：离线可 import、可运行 ----------

def test_module_importable_and_runs_without_spacy(extractor):
    # 当前环境没有 spacy，模块本身能 import 就是第一层保证；
    # 抽取必须仍然出结果。
    result = extractor.extract_material_codes("本次调拨物资编码EMG-1234已入库")
    assert "EMG-1234" in result["extracted_codes"]
    assert extractor.nlp_available is False


def test_spacy_none_falls_back_and_warns(monkeypatch, caplog):
    monkeypatch.setattr(material_extractor, "spacy", None)
    ext = MaterialCodeExtractor()
    with caplog.at_level(logging.WARNING, logger="backend.core.material_extractor"):
        result = ext.extract_material_codes("本次调拨物资编码EMG-1234已入库")
    assert "EMG-1234" in result["extracted_codes"]
    assert ext.nlp_available is False
    assert any("正则" in rec.message for rec in caplog.records)


def test_model_load_failure_falls_back_and_warns(monkeypatch, caplog):
    fake = FakeSpacy(fail_with=OSError("can't find model 'zh_core_web_sm'"))
    monkeypatch.setattr(material_extractor, "spacy", fake)
    ext = MaterialCodeExtractor()
    with caplog.at_level(logging.WARNING, logger="backend.core.material_extractor"):
        result = ext.extract_material_codes("本次调拨物资编码EMG-1234已入库")
    assert "EMG-1234" in result["extracted_codes"]
    assert ext.nlp_available is False
    assert fake.load_count == 1
    assert any("zh_core_web_sm" in rec.message for rec in caplog.records)


def test_model_loaded_at_most_once_per_instance(monkeypatch):
    fake = FakeSpacy()
    monkeypatch.setattr(material_extractor, "spacy", fake)
    ext = MaterialCodeExtractor()
    segments = [{"start": float(i), "end": float(i) + 1.0, "text": f"第{i}段 EMG-1234"}
                for i in range(200)]
    ext.extract_with_context(segments)
    ext.extract_material_codes("再来一次 EMG-1234")
    assert fake.load_count == 1


def test_model_load_failure_not_retried(monkeypatch):
    fake = FakeSpacy(fail_with=OSError("no model"))
    monkeypatch.setattr(material_extractor, "spacy", fake)
    ext = MaterialCodeExtractor()
    for _ in range(5):
        ext.extract_material_codes("EMG-1234")
    assert fake.load_count == 1


def test_nlp_available_true_when_model_loads(monkeypatch):
    fake = FakeSpacy()
    monkeypatch.setattr(material_extractor, "spacy", fake)
    ext = MaterialCodeExtractor()
    assert ext.nlp_available is False  # 尚未加载
    ext.extract_material_codes("随便一段")
    assert ext.nlp_available is True


# ---------- 症状 2/3/5：中文紧邻、三段式、旧台账编码 ----------

def test_code_adjacent_to_chinese_extracted(extractor):
    result = extractor.extract_material_codes("本次调拨物资编码EMG-1234已入库")
    assert result["extracted_codes"] == ["EMG-1234"]


def test_three_segment_codes_extracted_directly(extractor):
    result = extractor.extract_material_codes(
        "出库单号EMG-MED-001和EMG-LIF-004,还有EMG-RES-002。"
    )
    assert result["extracted_codes"] == ["EMG-MED-001", "EMG-LIF-004", "EMG-RES-002"]


def test_multiple_codes_deduped(extractor):
    result = extractor.extract_material_codes(
        "EMG-1234 与 MAT-2024 与 RES-4567 与 123456"
    )
    assert result["extracted_codes"] == ["EMG-1234", "MAT-2024", "RES-4567"]


def test_legacy_code_single_entry(extractor):
    result = extractor.extract_material_codes("旧台账编码 AB-998877 继续沿用")
    assert result["extracted_codes"] == ["AB-998877"]
    assert "998877" not in result["all_unique_codes"]
    assert result["all_unique_codes"].count("AB-998877") == 1


def test_extracted_codes_first_occurrence_order(extractor):
    result = extractor.extract_material_codes(
        "RES-4567 先出库，EMG-1234 随后，RES-4567 再确认一遍"
    )
    assert result["extracted_codes"] == ["RES-4567", "EMG-1234"]


# ---------- 症状 4/反例：位数不够、日期、电话 ----------

def test_short_digit_code_rejected(extractor):
    result = extractor.extract_material_codes("位数不够的 EMG-123 不算编码")
    assert "EMG-123" not in result["extracted_codes"]
    assert result["extracted_codes"] == []


def test_date_and_phone_not_codes(extractor):
    result = extractor.extract_material_codes(
        "会议时间 20260301 ，联系人电话 13800138000"
    )
    assert result["extracted_codes"] == []
    assert "20260301" not in result["all_unique_codes"]
    assert "13800138000" not in result["all_unique_codes"]


def test_dirty_text_full_pipeline(extractor):
    result = extractor.extract_material_codes(DIRTY_TEXT)
    assert result["extracted_codes"] == [
        "EMG-1234", "MAT-2024", "RES-4567",
        "AB-998877", "EMG-MED-001", "EMG-LIF-004", "EMG-RES-002",
    ]
    for junk in ("123456", "20260301", "13800138000", "998877", "EMG-123"):
        assert junk not in result["all_unique_codes"]


# ---------- 症状 6：all_unique_codes 确定性 ----------

def test_all_unique_codes_stable_within_process(extractor):
    first = extractor.extract_material_codes(DIRTY_TEXT)["all_unique_codes"]
    for _ in range(20):
        assert extractor.extract_material_codes(DIRTY_TEXT)["all_unique_codes"] == first


def test_all_unique_codes_merge_order(extractor):
    # extracted_codes（文本首次出现序）在前，目录命中编码按目录顺序追加。
    result = extractor.extract_material_codes("医用口罩已按 EMG-LIF-004 登记")
    assert result["all_unique_codes"] == ["EMG-LIF-004", "EMG-MED-001"]


def test_all_unique_codes_stable_across_processes_and_hashseeds(tmp_path):
    transcript = tmp_path / "transcript.txt"
    transcript.write_text(DIRTY_TEXT, encoding="utf-8")
    script = (
        "import json, sys;"
        "from backend.core.material_extractor import MaterialCodeExtractor;"
        "text = open(sys.argv[1], encoding='utf-8').read();"
        "print(json.dumps(MaterialCodeExtractor().extract_material_codes(text)"
        "['all_unique_codes'], ensure_ascii=False))"
    )
    outputs = []
    for seed in ("0", "1", "42"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        proc = subprocess.run(
            [sys.executable, "-c", script, str(transcript)],
            capture_output=True, text=True, cwd=REPO_ROOT, env=env, check=True,
        )
        outputs.append(proc.stdout.strip())
    assert outputs[0] == outputs[1] == outputs[2]
    assert json.loads(outputs[0])  # 非空


# ---------- 症状 7：四分区恒在、通讯物资独立 ----------

def test_material_categories_four_zones_always_present(extractor):
    # 负例钉板：若实现删掉「通讯物资」分区、或只输出有命中的分区，
    # 本测试必须变红。
    for text in ("", "今天开会没谈物资", "医用口罩到货"):
        categories = extractor.extract_material_codes(text)["material_categories"]
        for zone in ("医疗物资", "生活物资", "救援物资", "通讯物资"):
            assert zone in categories, f"{text!r} 缺少分区 {zone}"
    empty = extractor.extract_material_codes("今天开会没谈物资")["material_categories"]
    assert empty["通讯物资"] == []


def test_communication_materials_in_own_zone(extractor):
    result = extractor.extract_material_codes(
        "对讲机、卫星电话、应急广播和备用电池移到 D 区恒温防潮区"
    )
    comm = result["material_categories"]["通讯物资"]
    assert {m["name"] for m in comm} == {"对讲机", "卫星电话", "应急广播", "备用电池"}
    assert result["material_categories"]["救援物资"] == []


def test_daily_supplies_stay_in_life_zone(extractor):
    result = extractor.extract_material_codes("手电筒和充电宝留在 B 区")
    life = result["material_categories"]["生活物资"]
    assert {m["name"] for m in life} == {"手电筒", "充电宝"}


def test_material_belongs_to_exactly_one_zone(extractor):
    result = extractor.extract_material_codes(
        "对讲机、卫星电话、手电筒、充电宝、救生衣都盘了一遍"
    )
    owners = {}
    for zone, items in result["material_categories"].items():
        for item in items:
            assert item["name"] not in owners, f"{item['name']} 出现在多个分区"
            owners[item["name"]] = zone
    assert owners["对讲机"] == "通讯物资"
    assert owners["卫星电话"] == "通讯物资"
    assert owners["手电筒"] == "生活物资"


def test_existing_catalog_codes_unchanged(extractor):
    result = extractor.extract_material_codes(
        "医用口罩、对讲机、卫星电话、救生衣、手电筒、帐篷"
    )
    by_name = {m["name"]: m for m in result["matched_materials"]}
    assert by_name["医用口罩"]["code"] == "EMG-MED-001"
    assert by_name["对讲机"]["code"] == "EMG-RES-007"
    assert by_name["卫星电话"]["code"] == "EMG-RES-008"
    assert by_name["救生衣"]["code"] == "EMG-RES-002"
    assert by_name["手电筒"]["code"] == "EMG-LIF-007"
    assert by_name["帐篷"]["code"] == "EMG-LIF-004"


def test_new_communication_materials_get_com_codes(extractor):
    result = extractor.extract_material_codes("应急广播和备用电池到货")
    by_name = {m["name"]: m for m in result["matched_materials"]}
    assert by_name["应急广播"]["code"] == "EMG-COM-001"
    assert by_name["备用电池"]["code"] == "EMG-COM-002"
    assert by_name["应急广播"]["category"] == "通讯物资"


# ---------- 数据形状契约 ----------

def test_matched_materials_catalog_entries_have_exact_keys(extractor):
    result = extractor.extract_material_codes("医用口罩和防护服")
    for item in result["matched_materials"]:
        assert set(item.keys()) == {"name", "code", "category"}


def test_result_has_four_top_level_keys(extractor):
    result = extractor.extract_material_codes("EMG-1234")
    assert {"extracted_codes", "matched_materials",
            "all_unique_codes", "material_categories"} <= set(result.keys())


def test_extract_with_context_preserves_keys_and_order(extractor):
    segments = [
        {"start": 0.0, "end": 1.5, "text": "第一段 EMG-1234", "role": "主持人"},
        {"start": 1.5, "end": 3.0, "text": "第二段没有编码", "role": "仓管"},
        {"start": 3.0, "end": 4.0, "text": "第三段医用口罩", "role": "采购"},
    ]
    enriched = extractor.extract_with_context(segments)
    assert [s["text"] for s in enriched] == [s["text"] for s in segments]
    for original, new in zip(segments, enriched):
        for key, value in original.items():
            assert new[key] == value
        assert "extracted_materials" in new
    assert enriched[0]["extracted_materials"]["extracted_codes"] == ["EMG-1234"]
    assert enriched[2]["extracted_materials"]["matched_materials"][0]["name"] == "医用口罩"


# ---------- 症状 8：NLP 兜底与 EMG-UNK 独立编号 ----------

def test_nlp_entities_get_independent_unk_numbering(monkeypatch):
    fake = FakeSpacy(ents=[FakeEnt("应急指挥车", "PRODUCT"), FakeEnt("移动照明灯", "PRODUCT")])
    monkeypatch.setattr(material_extractor, "spacy", fake)
    ext = MaterialCodeExtractor()
    # 文本里先命中 3 条目录物资，第一个 NLP 实体仍然从 EMG-UNK-001 起编。
    result = ext.extract_material_codes("医用口罩、防护服、护目镜已入库，应急指挥车待命")
    nlp_items = [m for m in result["matched_materials"] if m.get("from_nlp")]
    assert [m["code"] for m in nlp_items] == ["EMG-UNK-001", "EMG-UNK-002"]
    assert all(m["category"] == "待分类" for m in nlp_items)
    assert all(set(m.keys()) == {"name", "code", "category", "from_nlp"}
               for m in nlp_items)


def test_nlp_entities_deduped_and_skip_known(monkeypatch):
    fake = FakeSpacy(ents=[
        FakeEnt("医用口罩", "PRODUCT"),      # 目录已命中，跳过
        FakeEnt("应急指挥车", "PRODUCT"),
        FakeEnt("应急指挥车", "PRODUCT"),    # 重复实体，去重
        FakeEnt("x", "PRODUCT"),             # 长度不足，跳过
        FakeEnt("某单位", "PERSON"),         # 标签不在白名单，跳过
    ])
    monkeypatch.setattr(material_extractor, "spacy", fake)
    ext = MaterialCodeExtractor()
    result = ext.extract_material_codes("医用口罩到货，应急指挥车待命")
    nlp_items = [m for m in result["matched_materials"] if m.get("from_nlp")]
    assert [m["name"] for m in nlp_items] == ["应急指挥车"]


def test_nlp_pending_category_appears_as_extra_key(monkeypatch):
    fake = FakeSpacy(ents=[FakeEnt("应急指挥车", "PRODUCT")])
    monkeypatch.setattr(material_extractor, "spacy", fake)
    ext = MaterialCodeExtractor()
    categories = ext.extract_material_codes("应急指挥车待命")["material_categories"]
    for zone in ("医疗物资", "生活物资", "救援物资", "通讯物资"):
        assert zone in categories
    assert [m["name"] for m in categories["待分类"]] == ["应急指挥车"]
