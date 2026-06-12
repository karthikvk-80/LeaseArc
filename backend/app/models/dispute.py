from pydantic import BaseModel
from typing import Optional, List


class Dispute(BaseModel):
    disputeId: str
    leaseId: str
    statementId: Optional[str] = None
    disputeType: str
    status: str
    claimedAmount: float
    recoveredAmount: float
    createdAt: str
    resolvedAt: Optional[str] = None
    locationName: str
    lastActivity: str


class NegotiationItem(BaseModel):
    disputeId: str
    leaseId: str
    locationName: str
    type: str
    status: str
    claimedAmount: float
    recoveredAmount: float
    lastActivity: str


class DisputeLetter(BaseModel):
    letterId: str
    disputeId: str
    content: str
    tone: str
    clausesCited: List[str]
    totalDisputed: float


class ThreadEvent(BaseModel):
    eventType: str
    content: str
    sender: str
    role: str
    timestamp: str
    commId: Optional[str] = None


class RiskFlag(BaseModel):
    attributeKey: str
    attributeName: str
    riskType: str
    severity: str
    explanation: str
    counterLanguage: str
    marketBenchmark: str


class StatusUpdateRequest(BaseModel):
    status: str
    resolvedAmount: Optional[float] = None
    notes: Optional[str] = None
