import logging
import re
from typing import Any, Dict, List

from ..config import settings

logger = logging.getLogger(__name__)

# 编码口径（细节见 README「自定口径」一节）：
# - 三段式：EMG-MED-001，2-4 位大写字母 + "-" + 2-4 位大写字母大类 + "-" + 3 位数字
# - 两段式：EMG-1234 / AB-998877，2-4 位大写字母 + "-" + 4-8 位数字
# - 纯数字串不算编码（日期、电话会被误伤）
# - 边界：编码两侧紧邻 ASCII 字母 / 数字 / 连字符时视为同一 token 的一部分，不匹配；
#   紧邻中文、空白、标点均算合法边界（\b 对中文无效，故用前后否定断言）
_CODE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9-])(?:[A-Z]{2,4}-[A-Z]{2,4}-\d{3}|[A-Z]{2,4}-\d{4,8})(?![A-Za-z0-9-])"
)

# 前端 3D 页面与补库报表的硬约定：四个分区键恒在
CATEGORY_NAMES = ("医疗物资", "生活物资", "救援物资", "通讯物资")

# 目录里已有物资的编码一律不动（下游按编码对账）；
# 对讲机、卫星电话从救援物资迁入通讯物资但保留原编码；
# 新增通讯物资按 EMG-COM-xxx 顺序编号。
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
        ("对讲机", "EMG-RES-007"),
        ("卫星电话", "EMG-RES-008"),
        ("应急广播", "EMG-COM-001"),
        ("备用电池", "EMG-COM-002"),
    ],
}


class MaterialCodeExtractor:
    def __init__(self):
        self._nlp = None
        self._nlp_load_attempted = False
        self.model_name = settings.SPACY_MODEL

    @property
    def nlp_available(self) -> bool:
        """NLP 是否可用。首次访问时触发一次加载尝试，结果缓存。"""
        self._load_model()
        return self._nlp is not None

    def _load_model(self):
        # 每个实例只尝试加载一次：spacy 未安装或中文模型加载失败，
        # 记一条 warning 后退回纯正则路径，之后不再重试。
        if self._nlp_load_attempted:
            return
        self._nlp_load_attempted = True
        try:
            import spacy
        except ImportError:
            logger.warning(
                "spacy 未安装，物资抽取退回纯正则模式（NLP 实体兜底不可用）"
            )
            return
        try:
            self._nlp = spacy.load(self.model_name)
        except Exception as exc:
            logger.warning(
                "spacy 中文模型 %r 加载失败（%s），物资抽取退回纯正则模式",
                self.model_name,
                exc,
            )
            self._nlp = None

    def extract_material_codes(self, text: str) -> Dict[str, Any]:
        self._load_model()

        # 单次扫描，按文本中首次出现的顺序去重
        found_codes: List[str] = []
        seen_codes = set()
        for match in _CODE_PATTERN.finditer(text):
            code = match.group(0)
            if code not in seen_codes:
                seen_codes.add(code)
                found_codes.append(code)

        matched_materials: List[Dict[str, Any]] = []
        for category, materials in MATERIAL_CATALOG.items():
            for mat_name, mat_code in materials:
                if mat_name in text:
                    matched_materials.append({
                        "name": mat_name,
                        "code": mat_code,
                        "category": category,
                    })

        if self._nlp is not None:
            doc = self._nlp(text)
            # EMG-UNK 编号独立计数：每次调用内从 001 起按实体出现顺序编号，
            # 与目录命中条数解耦（理由见 README）
            unk_seq = 0
            for ent in doc.ents:
                if ent.label_ in ["PRODUCT", "ORG", "GPE"] and len(ent.text) > 1:
                    unk_seq += 1
                    matched_materials.append({
                        "name": ent.text,
                        "code": f"EMG-UNK-{unk_seq:03d}",
                        "category": "待分类",
                        "from_nlp": True,
                    })

        # 合并规则：extracted_codes（文本首现序）在前，matched_materials 的
        # 编码（目录定义序）在后，按序去重。全程不用 set 迭代，跨进程稳定。
        all_unique_codes: List[str] = list(found_codes)
        seen_all = set(found_codes)
        for mat in matched_materials:
            code = mat["code"]
            if code not in seen_all:
                seen_all.add(code)
                all_unique_codes.append(code)

        return {
            "extracted_codes": found_codes,
            "matched_materials": matched_materials,
            "all_unique_codes": all_unique_codes,
            "material_categories": self._categorize_materials(matched_materials),
        }

    def _categorize_materials(self, materials: List[Dict]) -> Dict[str, List[Dict]]:
        # 四个分区键恒在，没命中就是空列表；NLP 兜底等非目录分区追加在后
        categories: Dict[str, List[Dict]] = {name: [] for name in CATEGORY_NAMES}
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
