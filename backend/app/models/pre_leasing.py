from pydantic import BaseModel
from typing import Optional


class ProspectCreate(BaseModel):
    locationName: str
    address: Optional[str] = None
    city: str
    country: str
    landlordId: Optional[str] = None
    landlordName: Optional[str] = None
    brokerName: Optional[str] = None
    sizeSqft: Optional[float] = None
    useType: Optional[str] = "Retail"
    targetOpenDate: Optional[str] = None
    notes: Optional[str] = None


class ProspectPatch(BaseModel):
    locationName: Optional[str] = None
    stage: Optional[str] = None
    loi_action: Optional[str] = None
    notes: Optional[str] = None
    targetOpenDate: Optional[str] = None


class QuoteCreate(BaseModel):
    quotedBy: str
    landlordId: Optional[str] = None
    baseRentMonthly: float
    currency: str = "USD"
    rentFreeMonths: Optional[int] = 0
    leaseTermYears: Optional[float] = 5
    fitOutContribution: Optional[float] = None
    camEstimatedMonthly: Optional[float] = None
    escalationPct: Optional[float] = None
    keyTerms: Optional[str] = None
    notes: Optional[str] = None
    source: Optional[str] = "manual"
    rawEmailText: Optional[str] = None
    brokerContactEmail: Optional[str] = None
    brokerContactPhone: Optional[str] = None
    landlordName: Optional[str] = None
    address: Optional[str] = None
    sizeSqft: Optional[float] = None


class QuotePatch(BaseModel):
    quotedBy: Optional[str] = None
    baseRentMonthly: Optional[float] = None
    currency: Optional[str] = None
    rentFreeMonths: Optional[int] = None
    leaseTermYears: Optional[float] = None
    fitOutContribution: Optional[float] = None
    camEstimatedMonthly: Optional[float] = None
    escalationPct: Optional[float] = None
    keyTerms: Optional[str] = None
    notes: Optional[str] = None
    isShortlisted: Optional[bool] = None


class LOICreate(BaseModel):
    source: str  # pasted | uploaded | ai_drafted
    rawText: Optional[str] = None
    fileName: Optional[str] = None


class ClausePatch(BaseModel):
    humanStatus: Optional[str] = None
    humanNote: Optional[str] = None


class ExtractQuoteRequest(BaseModel):
    emailText: str
    prospectId: Optional[str] = None
    apiKey: Optional[str] = None


class AnalyzeLOIRequest(BaseModel):
    loiId: str
    apiKey: Optional[str] = None


class DraftLOIRequest(BaseModel):
    apiKey: Optional[str] = None


class DraftCounterRequest(BaseModel):
    apiKey: Optional[str] = None


class ApproveCounterRequest(BaseModel):
    emailText: str


class FinalizeRequest(BaseModel):
    quoteId: str
    loiAction: str  # request_loi | create_loi | share_loi_form


class SignLOIRequest(BaseModel):
    signerName: str
    password: str
    documentRef: Optional[str] = None


# ── Location Intelligence ──────────────────────────────────────────────────────

class ResearchLocationRequest(BaseModel):
    apiKey: Optional[str] = None


class LocationAnalysisRequest(BaseModel):
    question: str
    provider: Optional[str] = None


class LocationAnalysisEmailDraftRequest(BaseModel):
    provider: Optional[str] = None


class ApproveLocationAnalysisEmailRequest(BaseModel):
    emailText: str


class LocationIntelPatch(BaseModel):
    status: Optional[str] = None
    footfall_score: Optional[float] = None
    competition_score: Optional[float] = None
    transit_score: Optional[float] = None
    attractions_score: Optional[float] = None
    overall_score: Optional[float] = None
    narrative: Optional[str] = None


# ── Lease Verification ─────────────────────────────────────────────────────────

class LeaseDocCreate(BaseModel):
    fileName: Optional[str] = None
    rawText: Optional[str] = None


class AnalyzeLeaseRequest(BaseModel):
    apiKey: Optional[str] = None


class MismatchPatch(BaseModel):
    resolved: Optional[bool] = None
    disputeEmailDraft: Optional[str] = None
    disputeEmailApproved: Optional[bool] = None


class DraftDisputeRequest(BaseModel):
    mismatchId: str
    apiKey: Optional[str] = None


# ── Due Diligence ──────────────────────────────────────────────────────────────

class DDReportCreate(BaseModel):
    extractedLandlordName: Optional[str] = None
    extractedPropertyAddress: Optional[str] = None
    extractedSurveyNumber: Optional[str] = None


class DDReportReview(BaseModel):
    humanStatus: str   # approved | clarification_requested | rejected
    humanNote: Optional[str] = None


class SignLeaseRequest(BaseModel):
    signerName: str
    password: str
    documentRef: Optional[str] = None
    role: Optional[str] = "RE Director"


class LOIActionRequest(BaseModel):
    action: str  # request_loi | create_loi
    confirmSend: Optional[bool] = False
