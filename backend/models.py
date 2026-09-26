from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime


class TranscriptionSegment(BaseModel):
    start: float
    end: float
    text: str
    role: Optional[str] = None


class MaterialItem(BaseModel):
    name: str
    code: str
    category: str
    quantity: Optional[str] = None


class WarehouseZone(BaseModel):
    zone_name: str
    materials: List[str]
    adjustment: str


class ReplenishmentItem(BaseModel):
    material_name: str
    code: str
    quantity: str
    deadline: str


class ActionItem(BaseModel):
    task: str
    responsible: str
    deadline: str
    priority: str


class MeetingSummary(BaseModel):
    meeting_summary: str
    layout_adjustment: Dict[str, Any]
    replenishment_plan: Dict[str, Any]
    logistics_suggestions: Dict[str, Any]
    action_items: List[ActionItem]
    risk_assessment: Dict[str, Any]


class MeetingProcessRequest(BaseModel):
    audio_file_path: Optional[str] = None
    manual_transcript: Optional[str] = None


class MeetingProcessResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict[str, Any]] = None


class EmailSendRequest(BaseModel):
    to_emails: List[str]
    meeting_minutes: str
    summary: Dict[str, Any]


class WarehouseLayoutData(BaseModel):
    zones: List[Dict[str, Any]]
    shelves: List[Dict[str, Any]]
    emergency_kits: List[Dict[str, Any]]
