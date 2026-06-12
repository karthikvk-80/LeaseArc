from pydantic import BaseModel
from typing import Optional


class Communication(BaseModel):
    commId: str
    leaseId: str
    disputeId: Optional[str] = None
    type: str
    direction: str
    subject: str
    body: str
    sentBy: str
    sentAt: str
    linkedClause: Optional[str] = None


class CommunicationCreate(BaseModel):
    leaseId: str
    disputeId: Optional[str] = None
    type: str
    direction: str
    subject: str
    body: str
    sentBy: Optional[str] = "user-001"
    linkedClause: Optional[str] = None
