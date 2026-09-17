import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from fastapi import HTTPException, Security, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from database import get_db

security = HTTPBearer(auto_error=False)

def hash_password(password: str, salt: str = "zhixing_salt_2026") -> str:
    return hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()

def verify_password(plain_password: str, hashed_password: str, salt: str = "zhixing_salt_2026") -> bool:
    return hash_password(plain_password, salt) == hashed_password

def create_session(user_id: str, organization_id: str, duration_days: int = 7) -> str:
    token = secrets.token_hex(32)
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(days=duration_days)).isoformat()
    created_at = now.isoformat()
    
    with get_db() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, organization_id, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (token, user_id, organization_id, expires_at, created_at)
        )
    return token

def destroy_session(token: str) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        return cur.rowcount > 0

def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Security(security)) -> Dict[str, Any]:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证令牌，请先登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = credentials.credentials
    now_iso = datetime.now(timezone.utc).isoformat()
    
    with get_db() as conn:
        session = conn.execute(
            "SELECT user_id, organization_id, expires_at FROM sessions WHERE token = ?",
            (token,)
        ).fetchone()
        
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="登录状态不存在或已退出，请重新登录",
                headers={"WWW-Authenticate": "Bearer"},
            )
            
        if session["expires_at"] < now_iso:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="登录已过期，请重新登录",
                headers={"WWW-Authenticate": "Bearer"},
            )
            
        # 实时查询用户最新状态与角色，杜绝沿用旧权限 (AC36)
        user = conn.execute(
            """
            SELECT u.id, u.organization_id, u.username, u.display_name, u.role, u.account_status, o.name as organization_name
            FROM users u
            JOIN organizations o ON u.organization_id = o.id
            WHERE u.id = ?
            """,
            (session["user_id"],)
        ).fetchone()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户账号不存在",
            )
            
        if user["account_status"] != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="账号已被禁用，拒绝访问受保护资源",
            )
            
        return {
            "id": user["id"],
            "organization_id": user["organization_id"],
            "organization_name": user["organization_name"],
            "username": user["username"],
            "display_name": user["display_name"],
            "role": user["role"],
            "account_status": user["account_status"],
            "session_token": token
        }

def require_admin(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    if user["role"] != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="当前账号无权访问此页面（需要管理员权限）",
        )
    return user
