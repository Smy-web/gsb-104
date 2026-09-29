import logging
import re
from typing import Any, Dict, List

from ..config import settings

logger = logging.getLogger(__name__)

try:
    import spacy
except ImportError:
    spacy = None


class MaterialCodeExtractor:
    """从中文会议转写文本中抽取物资编码与物资实体。

    离线可用：spacy 缺失或中文模型加载失败时退回纯正则抽取，
    同一个实例只尝试加载一次模型，可用 `nlp_available` 查询状态。
    """

    # 编码边界：前后不允许紧邻 ASCII 字母 / 数字 / 半角连字符；
    # 汉字、空白、标点都视为合法边界。纯数字串不算编码。
    CODE_PATTERN = re.compile(
        r"(?<![A-Za-z0-9-])"
        r"(?:[A-Z]{2,4}-[A-Z]{2,4}-\d{3,8}"   # 三段式，如 EMG-MED-001
        r"|[A-Z]{2,4}-\d{4,8})"               # 两段式，如 EMG-1234 / AB-998877
        r"(?![A-Za-z0-9-])"
    )

    NLP_ENTITY_LABELS = ("PRODUCT", "ORG", "GPE")

    # 仓库固定四个分区；目录里已有物资的编码保持不变（下游按编码对账）。
    MATERIAL_CATALOG: Dict[str, List[tuple]] = {
        "医疗物资": [
            ("医用口罩", "EMG-MED-001"),
            ("N95口罩", "EMG-MED-002"),
            ("防护服", "EMG-MED-003"),
            ("护目镜", "EMG-MED-004"),
            ("急救包", "EMG-MED-005"),
            ("呼吸机", "EMG-MED-006"),
            ("消毒液", "EMG-MED-007"),
            ("绷带", "EMG-MED-008"),
            ("药品", "EMG-MED-009"),
        ],
        "生活物资": [
            ("方便面", "EMG-LIF-001"),
            ("压缩饼干", "EMG-LIF-002"),
            ("饮用水", "EMG-LIF-003"),
            ("帐篷", "EMG-LIF-004"),
            ("毛毯", "EMG-LIF-005"),
            ("棉被", "EMG-LIF-006"),
            ("手电筒", "EMG-LIF-007"),
            ("充电宝", "EMG-LIF-008"),
        ],
        "救援物资": [
            ("救生艇", "EMG-RES-001"),
            ("救生衣", "EMG-RES-002"),
            ("绳索", "EMG-RES-003"),
            ("切割机", "EMG-RES-004"),
            ("破拆工具", "EMG-RES-005"),
            ("发电设备", "EMG-RES-006"),
        ],
        "通讯物资": [
            ("对讲机", "EMG-RES-007"),      # 既有编码，不改动
            ("卫星电话", "EMG-RES-008"),    # 既有编码，不改动
            ("应急广播", "EMG-COM-001"),    # 新增物资，通讯大类启用 COM 段
            ("备用电池", "EMG-COM-002"),
        ],
    }

    ZONE_NAMES: List[str] = list(MATERIAL_CATALOG.keys())

    def __init__(self):
        self.nlp = None
        self.model_name = settings.SPACY_MODEL
        self._nlp_load_attempted = False

    @property
    def nlp_available(self) -> bool:
        """NLP 模型是否可用（False 时走纯正则抽取）。"""
        return self.nlp is not None

    def _load_model(self):
        # 同一个实例只尝试加载一次，避免每处理一段就重试。
        if self._nlp_load_attempted:
            return
        self._nlp_load_attempted = True
        if spacy is None:
            logger.warning(
                "spacy 未安装，物资抽取退回纯正则模式（模型 %s 未加载）。",
                self.model_name,
            )
            return
        try:
            self.nlp = spacy.load(self.model_name)
        except Exception as exc:
            logger.warning(
                "spaCy 模型 %s 加载失败，物资抽取退回纯正则模式：%s",
                self.model_name,
                exc,
            )
            self.nlp = None

    def extract_material_codes(self, text: str) -> Dict[str, Any]:
        self._load_model()

        # 按文本中首次出现的顺序抽取并去重。
        extracted_codes = []
        seen_codes = set()
        for match in self.CODE_PATTERN.finditer(text):
            code = match.group(0)
            if code not in seen_codes:
                seen_codes.add(code)
                extracted_codes.append(code)

        matched_materials = []
        for category in self.ZONE_NAMES:
            for mat_name, mat_code in self.MATERIAL_CATALOG[category]:
                if mat_name in text:
                    matched_materials.append({
                        "name": mat_name,
                        "code": mat_code,
                        "category": category,
                    })

        if self.nlp is not None:
            matched_materials.extend(self._extract_nlp_entities(text, matched_materials))

        # 合并顺序固定：先 extracted_codes（文本首次出现序），
        # 再按 matched_materials 的顺序补上目录 / NLP 编码，去重。
        all_unique_codes = list(extracted_codes)
        seen_all = set(extracted_codes)
        for mat in matched_materials:
            if mat["code"] not in seen_all:
                seen_all.add(mat["code"])
                all_unique_codes.append(mat["code"])

        return {
            "extracted_codes": extracted_codes,
            "matched_materials": matched_materials,
            "all_unique_codes": all_unique_codes,
            "material_categories": self._categorize_materials(matched_materials),
        }

    def _extract_nlp_entities(self, text: str, matched_materials: List[Dict]) -> List[Dict]:
        """NLP 兜底：目录未命中的实体编为 EMG-UNK-xxx。

        编号独立：每次调用内从 001 起按实体出现顺序编排，
        与目录命中条数无关。
        """
        known_names = {m["name"] for m in matched_materials}
        entities = []
        seen_texts = set()
        doc = self.nlp(text)
        for ent in doc.ents:
            if ent.label_ not in self.NLP_ENTITY_LABELS:
                continue
            ent_text = ent.text.strip()
            if len(ent_text) <= 1:
                continue
            if ent_text in seen_texts or ent_text in known_names:
                continue
            if self.CODE_PATTERN.fullmatch(ent_text):
                continue
            seen_texts.add(ent_text)
            entities.append({
                "name": ent_text,
                "code": f"EMG-UNK-{len(entities) + 1:03d}",
                "category": "待分类",
                "from_nlp": True,
            })
        return entities

    def _categorize_materials(self, materials: List[Dict]) -> Dict[str, List[Dict]]:
        # 四个分区键恒在，没命中的分区给空列表；
        # 目录外类别（如 NLP 的「待分类」）作为额外键追加。
        categories: Dict[str, List[Dict]] = {zone: [] for zone in self.ZONE_NAMES}
        for mat in materials:
            cat = mat.get("category", "其他")
            categories.setdefault(cat, []).append(mat)
        return categories

    def extract_with_context(self, segments: List[Dict]) -> List[Dict]:
        enriched_segments = []
        for seg in segments:
            text = seg.get("text", "")
            extracted = self.extract_material_codes(text)
            enriched_segments.append({
                **seg,
                "extracted_materials": extracted,
            })
        return enriched_segments
