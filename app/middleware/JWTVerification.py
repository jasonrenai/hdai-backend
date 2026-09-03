from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import jwt, JWTError
import os
from typing import Dict, Any

def jwt_validator(
    auth: HTTPAuthorizationCredentials = Security(HTTPBearer()),
) -> Dict[str, Any]:
    secret_key = os.getenv("JWT_SECRET")
    token = auth.credentials
    try:
        payload = jwt.decode(token, secret_key, algorithms=["HS256"])
        return payload

    except JWTError as e:
        print(e)
        raise HTTPException(status_code=401, detail="Invalid or expired token.")
