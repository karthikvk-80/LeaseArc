from pydantic import BaseModel
from typing import Optional, List


class CAMLineItem(BaseModel):
    lineItemId: str
    statementId: str
    leaseId: str
    expenseCategory: str
    landlordAmount: float
    allowableAmount: float
    variance: float
    isExcluded: bool
    flagReason: Optional[str] = None
    status: str
    confidence: int
    leaseClauseRef: str


class CAMStatement(BaseModel):
    statementId: str
    leaseId: str
    statementYear: int
    landlordTotal: float
    auditedTotal: float
    varianceAmount: float
    percentOvercharged: float
    status: str
    pdfUrl: str


class AuditSummary(BaseModel):
    landlordTotal: float
    allowableTotal: float
    varianceAmount: float
    percentOvercharged: float


class AuditResult(BaseModel):
    auditId: str
    statementId: str
    summary: AuditSummary
    lineItems: List[dict]


class PaymentLocation(BaseModel):
    leaseId: str
    locationName: str
    expected: float
    actual: float
    variance: float
    status: str


class PaymentDashboard(BaseModel):
    owedThisMonth: float
    paidThisMonth: float
    variance: float
    locations: List[PaymentLocation]


class AccrualMonth(BaseModel):
    month: str
    year: int
    estimatedCam: float


class YoYLocation(BaseModel):
    locationId: str
    locationName: str
    currentYear: float
    priorYear: float
    variance: float


class CFOSummary(BaseModel):
    totalExposure: float
    disputesRaised: int
    amountRecovered: float
    topLocations: List[dict]
    topLandlords: List[dict]
