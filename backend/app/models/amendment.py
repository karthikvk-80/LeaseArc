from pydantic import BaseModel
from typing import Optional, List


class AmendmentListItem(BaseModel):
    amendmentId: str
    leaseId: str
    versionNumber: int
    executionDate: str
    changedCount: int
    riskScore: int
    riskLevel: str
    status: str


class AttributeDiff(BaseModel):
    key: str
    attributeName: str
    previousValue: Optional[str] = None
    newValue: Optional[str] = None
    changedBy: str
    executionDate: str


class DiffResponse(BaseModel):
    changedAttributes: List[AttributeDiff]


class EffectiveTerm(BaseModel):
    key: str
    attributeName: str
    currentValue: Optional[str] = None
    introducedByVersion: str
    dataType: str
    category: str


class EffectiveTermsResponse(BaseModel):
    attributes: List[EffectiveTerm]


class AuditEvent(BaseModel):
    eventType: str
    userId: str
    userName: str
    timestamp: str
    description: str


class AuditTrailResponse(BaseModel):
    events: List[AuditEvent]


class RiskFlag(BaseModel):
    attributeKey: str
    attributeName: str
    riskType: str
    severity: str
    explanation: str
    counterLanguage: str
    marketBenchmark: str


class RiskResponse(BaseModel):
    riskScore: int
    riskLevel: str
    flaggedClauses: List[RiskFlag]
