"""
app/models/enums.py
-------------------
SQLAlchemy-compatible Python enums for the User Management module.
Import these into models.py via:
    from app.models.enums import PdfTypeEnum, DataTypeEnum, ConfidenceLevelEnum
"""

import enum


class PdfTypeEnum(str, enum.Enum):
    native = "native"
    scanned = "scanned"


class DataTypeEnum(str, enum.Enum):
    text = "text"
    number = "number"
    date = "date"
    percentage = "percentage"


class ConfidenceLevelEnum(str, enum.Enum):
    low = "low"
    high = "high"
