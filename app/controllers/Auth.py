from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from app.middleware.JWTVerification import jwt_validator
from app.schemas.User import ResetPassword, UpdateUserSchema
from app.schemas.ServerResponse import ServerResponse
from app.schemas.User import GetUserSchema, UserSchema, CreateUserSchema, ForgotPasswordRequest, AdminUpdateUserSchema, AdminCreateUserSchema
from app.helpers.Utilities import Utils
from app.dependencies import get_auth_service
from app.helpers.auth_roles import is_admin_role

router = APIRouter(prefix="/api/v1/auth", tags=["Auth"])
    
@router.post("/signup", response_model=ServerResponse, status_code=201)
async def signup(user_data: CreateUserSchema, auth_service = Depends(get_auth_service)):
    try:
        data = await auth_service.signup(user_data)
        # if not data["success"]:
        #     status_code = 409 if "Already Exists" in data.get("error", "") else 400
        #     raise HTTPException(
        #         status_code=status_code,
        #         detail={"data": None, "error": data.get("error"), "success": False}
        #     )
        return Utils.create_response(data["data"],data["success"],data.get("error", ""))
    except Exception as e:
        raise HTTPException(status_code=400, detail={"data": None, "error":str(e),"success": False})

    
@router.post("/signin", response_model=ServerResponse)
async def signin_user(body: GetUserSchema, service = Depends(get_auth_service)):
    try:
        data = await service.get_user(body.email, body.password)
        return Utils.create_response(data["data"],data["success"],data.get("error", "") )
    except Exception as e:
        return JSONResponse(status_code=400, content={"data":None, "error":str(e), "success":False}) 
    
@router.post("/forgot-password", response_model=ServerResponse)
async def forgot_password(body: ForgotPasswordRequest, service = Depends(get_auth_service)):
    try:
        data = await service.send_otp_email(body.email)
        return Utils.create_response(data["data"],data["success"],data.get("error", ""))
    except Exception as e:
        raise HTTPException(status_code=400, detail={"data": None, "error":str(e),"success": False})


@router.post("/reset-password", response_model=ServerResponse)
async def reset_password(password_data: ResetPassword, service = Depends(get_auth_service)):
    try:
        data = await service.verify_otp_reset_password(password_data.email, password_data.otp, password_data.new_password)
        if not data["success"]:
            status_code = 400
            if data.get("error") == "OTP not found.":
                status_code = 404
            elif data.get("error") == "OTP Expired. Request a new OTP.":
                status_code = 410
            elif data.get("error") == "Invalid OTP.":
                status_code = 401
                
            raise HTTPException(
                status_code=status_code,
                detail={"data": None, "error": data.get("error"), "success": False}
            )
        return Utils.create_response(data["data"],data["success"],data.get("error", ""))
    except Exception as e:
        raise HTTPException(status_code=400, detail={"data": None, "error":str(e),"success": False})


@router.get("/users/get-all-users", response_model=ServerResponse)
async def get_all_users(
    page: int=1,
    limit: int=10,
    service = Depends(get_auth_service),
    jwt_payload: dict = Depends(jwt_validator)
    
):
    try:
        data = await service.get_all_users(page=page, limit=limit)
        return Utils.create_response(data["data"], data["success"], data.get("error", ""))
    except Exception as e:
        raise HTTPException(status_code=400, detail={"data": None, "error": str(e), "success": False})

@router.get("/admin/users", response_model=ServerResponse)
async def get_users_by_admin(
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(10, ge=1, le=100, description="Items per page"),
    service = Depends(get_auth_service),
    jwt_payload: dict = Depends(jwt_validator)
):
    try:
        # Extract admin user ID from JWT payload
        admin_id = jwt_payload.get("user_id") or jwt_payload.get("id") or str(jwt_payload.get("_id"))
        if not admin_id:
            raise HTTPException(
                status_code=400,
                detail={"data": None, "error": "Admin ID not found in JWT token", "success": False}
            )
        
        # Verify the user making request is an admin
        admin_user_type = jwt_payload.get("userType")
        if not is_admin_role(admin_user_type):
            raise HTTPException(
                status_code=403,
                detail={"data": None, "error": "Only admins can access this resource", "success": False}
            )
        
        data = await service.get_users_by_admin(admin_id, page, limit)
        if not data["success"]:
            raise HTTPException(
                status_code=400,
                detail={"data": None, "error": data.get("error"), "success": False}
            )
        return Utils.create_response(data["data"], data["success"], data.get("error", ""))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail={"data": None, "error": str(e), "success": False})

@router.post("/admin/create-user", response_model=ServerResponse, status_code=201)
async def create_user_by_admin(
    user_data: AdminCreateUserSchema,
    service = Depends(get_auth_service),
    jwt_payload: dict = Depends(jwt_validator)
):
    try:
        # Extract admin user ID from JWT payload
        admin_id = jwt_payload.get("user_id") or jwt_payload.get("id") or str(jwt_payload.get("_id"))
        if not admin_id:
            raise HTTPException(
                status_code=400,
                detail={"data": None, "error": "Admin ID not found in JWT token", "success": False}
            )
        
        # Verify the user making request is an admin
        admin_user_type = jwt_payload.get("userType")
        if not is_admin_role(admin_user_type):
            raise HTTPException(
                status_code=403,
                detail={"data": None, "error": "Only admins can create users", "success": False}
            )
        
        data = await service.create_user_by_admin(user_data, admin_id)
        if not data["success"]:
            status_code = 409 if "already exists" in data.get("error", "").lower() else 400
            raise HTTPException(
                status_code=status_code,
                detail={"data": None, "error": data.get("error"), "success": False}
            )
        return Utils.create_response(data["data"], data["success"], data.get("error", ""))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail={"data": None, "error": str(e), "success": False})

@router.get("/admin/users/{user_id}", response_model=ServerResponse)
async def get_user_by_id(
    user_id: str,
    service = Depends(get_auth_service),
    jwt_payload: dict = Depends(jwt_validator)
):
    try:
        # Extract admin user ID from JWT payload
        admin_id = jwt_payload.get("user_id") or jwt_payload.get("id") or str(jwt_payload.get("_id"))
        if not admin_id:
            raise HTTPException(
                status_code=400,
                detail={"data": None, "error": "Admin ID not found in JWT token", "success": False}
            )
        
        # Verify the user making request is an admin
        admin_user_type = jwt_payload.get("userType")
        if not is_admin_role(admin_user_type):
            raise HTTPException(
                status_code=403,
                detail={"data": None, "error": "Only admins can access this resource", "success": False}
            )
        
        data = await service.get_user_by_id(user_id, admin_id)
        if not data["success"]:
            status_code = 404 if data.get("error") == "User not found" else 403 if "permission" in data.get("error", "").lower() else 400
            raise HTTPException(
                status_code=status_code,
                detail={"data": None, "error": data.get("error"), "success": False}
            )
        return Utils.create_response(data["data"], data["success"], data.get("error", ""))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail={"data": None, "error": str(e), "success": False})

@router.put("/users/{user_id}", response_model=ServerResponse)
async def update_user_profile(
    user_id: str,
    body: UpdateUserSchema,
    service = Depends(get_auth_service),
    jwt_payload: dict = Depends(jwt_validator)
):
    try:
        # No authorization check - both admin and user can update profiles
        # User can update their own profile, admin can update any user
        
        update_data = body.model_dump(exclude_unset=True, mode="json")
        data = await service.update_user_profile(user_id, update_data)
        if not data["success"]:
            status_code = 404 if data.get("error") == "User not found" else 400
            raise HTTPException(
                status_code=status_code,
                detail={"data": None, "error": data.get("error"), "success": False}
            )
        return Utils.create_response(data["data"], data["success"], data.get("error", ""))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail={"data": None, "error": str(e), "success": False})


@router.delete("/users/delete-user/{user_id}", response_model=ServerResponse)
async def delete_user(user_id: str, service = Depends(get_auth_service),    jwt_payload: dict = Depends(jwt_validator)):
    try:
        data = await service.delete_user(user_id)
        return Utils.create_response(data["data"],data["success"],data.get("error", ""))
    except Exception as e:
        raise HTTPException(status_code=400, detail={"data": None, "error":str(e),"success": False})

# @router.put("/users/update-user/{user_id}", response_model=ServerResponse, )
# async def update_user(user_id: str,body: AdminUpdateUserSchema,service = Depends(get_auth_service),jwt_payload: dict = Depends(jwt_validator)):
#     try:
#         update_data = body.dict(exclude_unset=True)
#         data = await service.update_user(user_id, update_data)
#         return Utils.create_response(data["data"],data["success"],data.get("error", ""))
    # except Exception as e:
    #     raise HTTPException(status_code=400, detail={"data": None, "error":str(e),"success": False})