"""MaterialCodeExtractor 口径测试。

覆盖：离线运行、中文紧邻编码、三段式编码、去重与顺序、日期/电话反例、
四分区恒在、通讯物资归区、NLP 单次加载与替身、EMG-UNK 独立编号、
extract_with_context 键保留与顺序。
"""
import logging
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.core.material_extractor import (
    CATEGORY_NAMES,
    MATERIAL_CATALOG,
    MaterialCodeExtractor,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_extractor():
    return MaterialCodeExtractor()


# ---------- 离线 / spacy 缺失 ----------

def test_import_and_extract_without_spacy(monkeypatch):
    # 环境本身没装 spacy；再确保 sys.modules 里没有残留
    monkeypatch.delitem(sys.modules, "spacy", raising=False)
    extractor = make_extractor()
    result = extractor.extract_material_codes("本次调拨物资编码EMG-1234已入库")
    assert "EMG-1234" in result["extracted_codes"]
    assert extractor.nlp_available is False


def test_model_load_failure_logs_warning_and_loads_once(monkeypatch, caplog):
    fake_spacy = types.ModuleType("spacy")
    load_calls = {"n": 0}

    def failing_load(name):
        load_calls["n"] += 1
        raise OSError("model not found (offline)")

    fake_spacy.load = failing_load
    monkeypatch.setitem(sys.modules, "spacy", fake_spacy)

    extractor = make_extractor()
    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            extractor.extract_material_codes("EMG-1234 已入库")
    assert load_calls["n"] == 1  # 同一实例只尝试一次
    assert extractor.nlp_available is False
    assert any("正则" in rec.message for rec in caplog.records)


def test_missing_spacy_package_logs_warning(monkeypatch, caplog):
    monkeypatch.delitem(sys.modules, "spacy", raising=False)
    extractor = make_extractor()
    with caplog.at_level(logging.WARNING):
        extractor.extract_material_codes("EMG-1234")
    assert extractor.nlp_available is False
    assert any("spacy" in rec.message for rec in caplog.records)


# ---------- 编码抽取口径 ----------

def test_code_adjacent_to_chinese_no_spaces():
    result = make_extractor().extract_material_codes("本次调拨物资编码EMG-1234已入库")
    assert result["extracted_codes"] == ["EMG-1234"]


def test_three_segment_codes_extracted_without_chinese_name():
    text = "重点核对 EMG-MED-001、EMG-LIF-004 和 EMG-RES-002 的库存。"
    result = make_extractor().extract_material_codes(text)
    assert result["extracted_codes"] == ["EMG-MED-001", "EMG-LIF-004", "EMG-RES-002"]


def test_too_few_digits_rejected():
    result = make_extractor().extract_material_codes("编号 EMG-123 这批先放一边")
    assert result["extracted_codes"] == []


def test_date_and_phone_rejected():
    result = make_extractor().extract_material_codes(
        "会议时间 20260301 ，联系人电话 13800138000"
    )
    assert result["extracted_codes"] == []


def test_mixed_codes_dedup_first_occurrence_order():
    text = "EMG-1234 与 MAT-2024 与 RES-4567 与 123456 与 EMG-1234"
    result = make_extractor().extract_material_codes(text)
    assert result["extracted_codes"] == ["EMG-1234", "MAT-2024", "RES-4567"]


def test_legacy_code_reported_once():
    result = make_extractor().extract_material_codes("旧台账编码 AB-998877 已核对")
    assert result["extracted_codes"] == ["AB-998877"]


def test_all_unique_codes_merge_rule():
    text = "医用口罩和MAT-2024，再补 EMG-MED-001"
    result = make_extractor().extract_material_codes(text)
    # extracted_codes 文本首现序在前，matched_materials 目录序在后，按序去重
    assert result["extracted_codes"] == ["MAT-2024", "EMG-MED-001"]
    assert result["all_unique_codes"] == ["MAT-2024", "EMG-MED-001"]


def test_all_unique_codes_stable_across_runs_and_hash_seeds(tmp_path):
    text = "EMG-1234 与 MAT-2024 与 RES-4567，医用口罩、对讲机、帐篷、卫星电话"
    expected = make_extractor().extract_material_codes(text)["all_unique_codes"]
    assert make_extractor().extract_material_codes(text)["all_unique_codes"] == expected

    script = tmp_path / "dump_codes.py"
    script.write_text(
        "from backend.core.material_extractor import MaterialCodeExtractor\n"
        f"text = {text!r}\n"
        "print(MaterialCodeExtractor().extract_material_codes(text)['all_unique_codes'])\n",
        encoding="utf-8",
    )
    outputs = []
    for seed in ("0", "1", "42"):
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=REPO_ROOT,
            env={"PYTHONHASHSEED": seed, "PYTHONPATH": str(REPO_ROOT), "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=True,
        )
        outputs.append(proc.stdout.strip())
    assert outputs[0] == outputs[1] == outputs[2]
    assert outputs[0] == repr(expected)


# ---------- 分区口径 ----------

def test_four_category_keys_always_present():
    # 负例锚点：若实现去掉「通讯物资」或只输出有命中的分区，本条必须变红
    result = make_extractor().extract_material_codes("今天天气不错，没有提到任何物资")
    assert set(CATEGORY_NAMES) <= set(result["material_categories"].keys())
    for name in CATEGORY_NAMES:
        assert result["material_categories"][name] == []


def test_communication_materials_in_own_category():
    text = "对讲机、卫星电话、应急广播、备用电池划入 D 区恒温防潮区"
    result = make_extractor().extract_material_codes(text)
    comm_names = [m["name"] for m in result["material_categories"]["通讯物资"]]
    assert comm_names == ["对讲机", "卫星电话", "应急广播", "备用电池"]
    rescue_names = [m["name"] for m in result["material_categories"]["救援物资"]]
    assert "对讲机" not in rescue_names and "卫星电话" not in rescue_names


def test_existing_codes_unchanged_and_one_category_per_material():
    text = "对讲机、卫星电话、手电筒、充电宝都要盘点"
    result = make_extractor().extract_material_codes(text)
    by_name = {m["name"]: m for m in result["matched_materials"]}
    assert by_name["对讲机"]["code"] == "EMG-RES-007"  # 目录已有编码不改
    assert by_name["卫星电话"]["code"] == "EMG-RES-008"
    assert by_name["对讲机"]["category"] == "通讯物资"
    assert by_name["手电筒"]["category"] == "生活物资"  # 手电筒留在生活物资
    assert by_name["充电宝"]["category"] == "生活物资"
    for mat in result["matched_materials"]:
        # 一条物资只属于一个分区
        cats = [c for c, items in MATERIAL_CATALOG.items()
                if any(n == mat["name"] for n, _ in items)]
        assert len(cats) == 1


def test_matched_materials_key_shape():
    result = make_extractor().extract_material_codes("医用口罩")
    assert result["matched_materials"] == [
        {"name": "医用口罩", "code": "EMG-MED-001", "category": "医疗物资"}
    ]


# ---------- NLP 替身 ----------

def _install_fake_spacy(monkeypatch, ents):
    fake_spacy = types.ModuleType("spacy")
    state = {"load_calls": 0}

    def load(name):
        state["load_calls"] += 1

        def nlp(text):
            return SimpleNamespace(
                ents=[SimpleNamespace(text=t, label_=l) for t, l in ents]
            )

        return nlp

    fake_spacy.load = load
    monkeypatch.setitem(sys.modules, "spacy", fake_spacy)
    return state


def test_nlp_fallback_entries_and_independent_unk_numbering(monkeypatch):
    _install_fake_spacy(monkeypatch, [("应急通讯车", "PRODUCT"), ("折叠担架", "PRODUCT")])
    extractor = make_extractor()
    # 文本里先命中 3 条目录物资，NLP 实体编号仍从 EMG-UNK-001 起
    result = extractor.extract_material_codes("医用口罩、防护服、护目镜，还有应急通讯车和折叠担架")
    nlp_items = [m for m in result["matched_materials"] if m.get("from_nlp")]
    assert [m["code"] for m in nlp_items] == ["EMG-UNK-001", "EMG-UNK-002"]
    assert all(m["category"] == "待分类" for m in nlp_items)
    assert extractor.nlp_available is True


def test_nlp_model_loaded_once_for_200_segments(monkeypatch):
    state = _install_fake_spacy(monkeypatch, [])
    extractor = make_extractor()
    segments = [{"start": float(i), "end": float(i) + 1.0, "text": f"第{i}段 EMG-1234"}
                for i in range(200)]
    extractor.extract_with_context(segments)
    assert state["load_calls"] == 1


# ---------- extract_with_context ----------

def test_extract_with_context_preserves_keys_and_order():
    extractor = make_extractor()
    segments = [
        {"start": 0.0, "end": 1.5, "text": "先领医用口罩", "role": "张三"},
        {"start": 1.5, "end": 3.0, "text": "EMG-1234 已入库", "role": "李四"},
        {"start": 3.0, "end": 4.0, "text": "散会"},
    ]
    enriched = extractor.extract_with_context(segments)
    assert [s["text"] for s in enriched] == [s["text"] for s in segments]
    for seg_in, seg_out in zip(segments, enriched):
        for key, value in seg_in.items():
            assert seg_out[key] == value
        assert set(seg_out) == set(seg_in) | {"extracted_materials"}
    assert enriched[0]["extracted_materials"]["matched_materials"][0]["name"] == "医用口罩"
    assert enriched[1]["extracted_materials"]["extracted_codes"] == ["EMG-1234"]
    assert enriched[2]["extracted_materials"]["extracted_codes"] == []
