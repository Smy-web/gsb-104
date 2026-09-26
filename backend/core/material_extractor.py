import re
from typing import Dict, List, Any
import spacy
from ..config import settings


class MaterialCodeExtractor:
    def __init__(self):
        self.nlp = None
        self.model_name = settings.SPACY_MODEL

    def _load_model(self):
        if self.nlp is None:
            try:
                self.nlp = spacy.load(self.model_name)
            except:
                self.nlp = None

    def extract_material_codes(self, text: str) -> Dict[str, Any]:
        self._load_model()
        
        code_patterns = [
            r'\b[A-Z]{2,4}-?\d{4,8}\b',
            r'\b\d{6,12}\b',
            r'\b[A-Z]{1,3}\d{5,10}\b',
            r'\bEMG-\d{4,6}\b',
            r'\bMAT-\d{4,6}\b',
            r'\bRES-\d{4,6}\b',
        ]
        
        found_codes = []
        for pattern in code_patterns:
            matches = re.findall(pattern, text)
            found_codes.extend(matches)
        
        material_categories = {
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
                ("对讲机", "EMG-RES-007"),
                ("卫星电话", "EMG-RES-008"),
            ],
            "其他": []
        }
        
        matched_materials = []
        for category, materials in material_categories.items():
            for mat_name, mat_code in materials:
                if mat_name in text:
                    matched_materials.append({
                        "name": mat_name,
                        "code": mat_code,
                        "category": category
                    })
        
        if self.nlp:
            doc = self.nlp(text)
            for ent in doc.ents:
                if ent.label_ in ["PRODUCT", "ORG", "GPE"]:
                    if len(ent.text) > 1:
                        matched_materials.append({
                            "name": ent.text,
                            "code": f"EMG-UNK-{len(matched_materials) + 1:03d}",
                            "category": "待分类",
                            "from_nlp": True
                        })
        
        all_codes = list(set(found_codes + [m["code"] for m in matched_materials]))
        
        return {
            "extracted_codes": found_codes,
            "matched_materials": matched_materials,
            "all_unique_codes": all_codes,
            "material_categories": self._categorize_materials(matched_materials)
        }

    def _categorize_materials(self, materials: List[Dict]) -> Dict[str, List[Dict]]:
        categories = {}
        for mat in materials:
            cat = mat.get("category", "其他")
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(mat)
        return categories

    def extract_with_context(self, segments: List[Dict]) -> List[Dict]:
        enriched_segments = []
        for seg in segments:
            text = seg.get("text", "")
            extracted = self.extract_material_codes(text)
            enriched_segments.append({
                **seg,
                "extracted_materials": extracted
            })
        return enriched_segments
