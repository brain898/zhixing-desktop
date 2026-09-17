from typing import Optional, List, Any
from pydantic import BaseModel, Field

class LoginRequest(BaseModel):
    username: str
    password: str

class UserResponse(BaseModel):
    id: str
    organization_id: str
    organization_name: str
    username: str
    display_name: str
    role: str
    account_status: str

class LoginResponse(BaseModel):
    token: str
    user: UserResponse

class KnowledgeOverviewResponse(BaseModel):
    document_count: int
    knowledge_count: int
    pending_count: int
    category_counts: dict
    is_empty: bool

class SystemStatusResponse(BaseModel):
    status: str
    version: str
    environment: str
    database_connected: bool
    storage_ready: bool

class ErrorResponse(BaseModel):
    detail: str
    code: Optional[str] = None
