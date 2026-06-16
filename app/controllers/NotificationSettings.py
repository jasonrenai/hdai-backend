from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError

from app.dependencies import get_notification_settings_service
from app.helpers.Utilities import Utils
from app.middleware.JWTVerification import jwt_validator
from app.schemas.NotificationSettings import NotificationSettingsUpdateSchema
from app.schemas.ServerResponse import ServerResponse

router = APIRouter(prefix="/api/v1/notification-settings", tags=["Notification Settings"])


def _user_id_from_jwt(payload: dict) -> str:
    for key in ("id", "user_id", "_id"):
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"data": None, "error": "Missing user id in token", "success": False},
    )


@router.get("", response_model=ServerResponse)
async def get_notification_settings(
    jwt_payload: dict = Depends(jwt_validator),
    service=Depends(get_notification_settings_service),
):
    try:
        user_id = _user_id_from_jwt(jwt_payload)
        result = await service.get_or_create_for_user(user_id)
        return Utils.create_response(result["data"], result["success"], result.get("error", ""))
    except HTTPException:
        raise
    except ValidationError as ve:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"data": None, "error": str(ve), "success": False},
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"data": None, "error": "Internal server error", "success": False},
        )


@router.put("", response_model=ServerResponse)
async def update_notification_settings(
    body: NotificationSettingsUpdateSchema,
    jwt_payload: dict = Depends(jwt_validator),
    service=Depends(get_notification_settings_service),
):
    try:
        user_id = _user_id_from_jwt(jwt_payload)
        updates = body.non_empty_updates()
        result = await service.update_for_user(user_id, updates)
        if not result["success"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"data": None, "error": result.get("error"), "success": False},
            )
        return Utils.create_response(result["data"], result["success"], result.get("error", ""))
    except HTTPException:
        raise
    except ValidationError as ve:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"data": None, "error": str(ve), "success": False},
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"data": None, "error": "Internal server error", "success": False},
        )
