from pydantic import BaseModel
from typing import Optional, List, Any


class UploadJobResponse(BaseModel):
    jobId: str
    fileName: str
    fileSizeMb: float
    pageCount: int
    status: str
    estimatedSeconds: int


class JobStatusResponse(BaseModel):
    jobId: str
    status: str
    progressPercent: int
    errorMessage: Optional[str] = None
    leaseId: Optional[str] = None


class LeaseAttribute(BaseModel):
    attribute_id: str
    lease_id: str
    category: str
    attribute_key: str
    attribute_name: str
    data_type: str
    extracted_value: Optional[str] = None
    user_edited_value: Optional[str] = None
    confidence_score: int
    confidence_level: str
    is_key_field: bool
    source_page_number: int
    source_text_snippet: str
    source_highlight_coordinates: dict
    is_verified: bool
    verified_by: Optional[str] = None
    verified_at: Optional[str] = None


class LeaseExtractedResponse(BaseModel):
    leaseId: str
    fileName: str
    pageCount: int
    extractedAt: str
    overallConfidence: int
    totalAttributes: int
    extractedCount: int
    lowConfidenceCount: int
    notFoundCount: int
    attributes: List[LeaseAttribute]


class LeaseListItem(BaseModel):
    lease_id: str
    location_id: str
    portfolio_id: str
    store_name: str
    store_code: str
    landlord_name: str
    lease_type: str
    commencement_date: str
    expiry_date: str
    status: str
    days_to_expiry: int
    monthly_rent: float
    currency: str


class LeaseStats(BaseModel):
    total: int
    expiringSoon: int
    holdover: int
    active: int


class LeasesListResponse(BaseModel):
    leases: List[dict]
    total: int
    page: int
    pageSize: int
    stats: LeaseStats


class BulkUploadResponse(BaseModel):
    batchId: str
    files: List[dict]


class AttributeUpdateRequest(BaseModel):
    userEditedValue: Optional[str] = None
    isVerified: Optional[bool] = None
    verifiedBy: Optional[str] = None
