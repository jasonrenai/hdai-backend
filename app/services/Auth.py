import logging
from typing import Optional

from pydantic import EmailStr, TypeAdapter, ValidationError

from app.schemas.User import UserSchema, UserType, default_user_subscription
from app.models.User import UserModel
from app.models.Otp import OTPModel
from app.helpers.Utilities import Utils
from datetime import datetime, timedelta
from fastapi import HTTPException, UploadFile
from app.helpers.AzureStorage import AzureBlobUploader
import random 
from bson import ObjectId
from bson.errors import InvalidId
from app.email.enums import EmailEventType
from app.email.signup_emails import try_send_signup_emails
from app.email.welcome_account import try_send_welcome_email_on_account_created


class AuthService:
    
    def __init__(self):
        self.user_model = UserModel()
        self.otp_model= OTPModel()
        self.uploader = AzureBlobUploader()
            
    async def get_user(self, email, password):
        """
        Authenticate user and return JWT token
        """
        try:
            # Get user by email
            user = await self.user_model.get_user({"email": email})
            if not user:
                return {
                    "success": False,
                    "data": None,
                    "error": "User does not exist"
                }

            # Verify password
            password_match = Utils.verify_password(password, user.password)
            if not password_match:
                return {
                    "success": False,
                    "data": None,
                    "error": "Invalid email or password"
                }

            # Create user response data
            user_dict = user.dict()
            user_dict.pop("password", None)  # Remove password from response
            
            # Create JWT token
            token = Utils.create_jwt_token(user_dict)
            
            return {
                "success": True,
                "data": {
                    "token": token
                }
            }
        except ValidationError as e:
            error_details = e.errors()
            return {
                "success": False,
                "data": None,
                "error": f"Validation error: {error_details}"
            }
        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": str(e)
            }
    
    async def signup(self, user_data) -> dict:
        """
        Handle the signup process with enhanced validation and response.
        - Checks if user exists
        - Validates and sanitizes input data
        - Hashes password
        - Creates user record
        - Returns user data with JWT token
        """
        try:
            # Check if user exists
            existing_user = await self.user_model.get_user({"email": user_data.email})
            if existing_user:
                return {
                    "success": False,
                    "data": None,
                    "error": "User Already Exists."
                }

            # Prepare user data
            user_data_dict = user_data.dict()
            
            # Hash password
            hashed_password = Utils.hash_password(user_data.password)
            user_data_dict["password"] = hashed_password
            
            # Set timestamps
            current_time = datetime.utcnow()
            user_data_dict["createdOn"] = current_time
            user_data_dict["updatedOn"] = current_time
            user_data_dict.setdefault("subscription", default_user_subscription())
            user_data_dict["emailVerified"] = False
            
            # Create user
            user_id = await self.user_model.create_user(user_data_dict)

            from app.models.NotificationSettings import NotificationSettingsModel

            await NotificationSettingsModel().create_for_user_if_missing(str(user_id))

            try_send_signup_emails(
                full_name=user_data_dict.get("fullName", ""),
                account_email=user_data_dict.get("email"),
                user_id=str(user_id),
            )

            # JWT must use `id` (same as login), not `_id` — APIs read jwt_payload["id"]
            created_user = await self.user_model.get_user({"_id": ObjectId(user_id)})
            if not created_user:
                return {
                    "success": False,
                    "data": None,
                    "error": "User created but could not be loaded for token.",
                }
            user_dict = created_user.dict()
            user_dict.pop("password", None)
            token = Utils.create_jwt_token(user_dict)
            
            return {
                "success": True,
                "data": {
                    "token": token,
                    "emailVerified": False,
                }
            }
        
        except ValidationError as e:
            return {
                "success": False,
                "data": None,
                "error": f"Validation error: {str(e)}"
            }
        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": str(e)
            }

    async def verify_email(self, user_id: str) -> dict:
        """Mark a user's email as verified (public endpoint; userId from signup verification link)."""
        try:
            try:
                oid = ObjectId((user_id or "").strip())
            except InvalidId:
                return {"success": False, "data": None, "error": "Invalid user id."}

            user = await self.user_model.get_user({"_id": oid})
            if not user:
                return {"success": False, "data": None, "error": "User not found."}

            if user.emailVerified:
                return {
                    "success": True,
                    "data": {"message": "Email already verified.", "emailVerified": True},
                }

            await self.user_model.update_user(
                str(oid),
                {"emailVerified": True, "updatedOn": datetime.utcnow()},
            )
            return {
                "success": True,
                "data": {"message": "Email verified successfully.", "emailVerified": True},
            }
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}

    async def create_speaker_user(
        self,
        email: str,
        full_name: str,
        plain_password: str,
        admin_id: Optional[str] = None,
    ) -> dict:
        """
        Insert a users row for a new speaker. Caller must ensure the email is not already taken.
        Email should already be normalized (e.g. validated with EmailStr).
        """
        try:
            normalized_email = TypeAdapter(EmailStr).validate_python((email or "").strip())
        except ValidationError as e:
            return {"success": False, "error": f"Invalid email: {e}"}

        fn = (full_name or "").strip()
        if len(fn) < 2 or len(fn) > 50:
            return {"success": False, "error": "Full name must be between 2 and 50 characters."}

        if len(plain_password) < 8:
            return {"success": False, "error": "Password must be at least 8 characters."}

        hashed_password = Utils.hash_password(plain_password)
        now = datetime.utcnow()
        user_data_dict = {
            "email": normalized_email,
            "password": hashed_password,
            "fullName": fn,
            "userType": UserType.USER,
            "subscription": default_user_subscription(),
            "createdOn": now,
            "updatedOn": now,
        }
        if admin_id:
            user_data_dict["adminId"] = admin_id

        try:
            user_id = await self.user_model.create_user(user_data_dict)
            try_send_welcome_email_on_account_created(
                user_display_name=fn,
                account_email=str(normalized_email),
            )
            return {
                "success": True,
                "user_id": str(user_id),
                "email": normalized_email,
            }
        except PydanticValidationError as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def send_otp_email(self, email: str):
        """
        Send OTP email for password reset with enhanced validation
        """
        try:
            # Validate user exists
            user = await self.user_model.get_user_by_email(email)
            if not user:
                return {"success": False, "data": None, "error": "User not found."}

            to_email = (user.email or email or "").strip()
            otp = random.randint(100000, 999999)
            await self.otp_model.save_otp(to_email, otp)

            user_name = (user.fullName if getattr(user, "fullName", None) else "").strip()

            from app.dependencies import get_email_service

            sent = get_email_service().send_event_email(
                event_type=EmailEventType.PASSWORD_RESET,
                to_email=to_email,
                template_model={
                    "user_name": user_name or "there",
                    "otp": str(otp),
                },
            )
            if not sent:
                reason = (
                    get_email_service().last_send_error or "Failed to send reset email."
                )
                return {
                    "success": False,
                    "data": None,
                    "error": reason,
                }

            return {
                "success": True,
                "data": "Password reset instructions sent to your email",
                "meta": {
                    "email": email,
                    "expires_in": "10 minutes"
                }
            }
        except ValueError as ve:
            return {"success": False, "data": None, "error": str(ve)}
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}

    async def verify_otp_reset_password(self, email: str, input_otp: str, new_password: str):
        """
        Verify OTP and update user's password with enhanced validation
        """
        try:
            # Validate user exists
            user = await self.user_model.get_user_by_email(email)
            if not user:
                return {"success": False, "data": None, "error": "User not found."}

            lookup_email = (user.email or email or "").strip()
            otp_record = await self.otp_model.get_otp(lookup_email)
            if not otp_record and lookup_email != (email or "").strip():
                otp_record = await self.otp_model.get_otp((email or "").strip())
            if not otp_record:
                return {
                    "success": False,
                    "data": None,
                    "error": "OTP not found."
                }

            # Validate OTP expiry
            stored_otp = otp_record["otp"]
            created_at = otp_record["createdAt"]
            if datetime.utcnow() - created_at > timedelta(minutes=10):
                # Delete expired OTP
                await self.otp_model.delete_otp(lookup_email)
                await self.otp_model.delete_otp((email or "").strip())
                return {
                    "success": False,
                    "data": None,
                    "error": "OTP Expired. Request a new OTP."
                }

            # Validate OTP
            if str(stored_otp) != input_otp:
                return {
                    "success": False,
                    "data": None,
                    "error": "Invalid OTP."
                }

            # Update password
            new_hashed_password = Utils.hash_password(new_password)
            await self.user_model.update_password(lookup_email, new_hashed_password)

            # Delete used OTP
            await self.otp_model.delete_otp(lookup_email)
            await self.otp_model.delete_otp((email or "").strip())

            # Generate new token for automatic login
            user_dict = user.dict()
            user_dict["password"] = new_hashed_password
            token = Utils.create_jwt_token(user_dict)

            return {
                "success": True,
                "data": {
                    "message": "Password Updated Successfully"
                }
            }
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}
    
    def upload_profile_picture(self, file: UploadFile):
        try:
            file_content = file.file.read()  
            file_name = file.filename
            return self.uploader.upload_profile_picture(file_content, file_name)
        except Exception as e:
            raise Exception(f"Error uploading profile picture: {str(e)}")
        
    async def get_all_users(self, page: int = 1, limit: int = 10):
        try:
            import asyncio
            filters = {}
            number_to_skip = (page - 1) * limit
            
            # Run queries in parallel for better performance
            total, users = await asyncio.gather(
                self.user_model.get_documents_count(filters),
                self.user_model.get_users(filters, number_to_skip, limit)
            )
            total_pages = (total + limit - 1) // limit
            
            # Remove password from user dicts
            users_data = []
            for user in users:
                user_dict = user.dict()
                user_dict.pop('password', None)
                users_data.append(user_dict)
            return {
                "success": True,
                "data": {
                    "users": users_data,
                    "pagination": {
                        "totalPages": total_pages,
                        "currentPage": page,
                        "limit": limit
                }
            }
            }
        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": str(e)
            }
        
    async def delete_user(self, user_id: str):
        try:
            deleted = await self.user_model.delete_user(user_id)
            if deleted:
                return {"success": True, "data": "User deleted successfully."}
            else:
                return {"success": False, "data": None, "error": "User not found or already deleted."}
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}
        
    async def update_user(self, user_id: str, update_data: dict) -> dict:
        """
        Update user information by user_id.
        """
        try:
            if not update_data:
                return {"success": False, "data": None, "error": "No data provided for update."}
            updated = await self.user_model.update_user(user_id, update_data)
            if not updated:
                return {"success": True, "data": "No new changes in data."}
            return {"success": True, "data": "User info updated successfully."}
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}

    async def get_user_by_id(self, user_id: str, admin_id: str) -> dict:
        """
        Get a specific user by user_id.
        Verifies the user was created by the requesting admin.
        """
        try:
            # Get user by ID
            user = await self.user_model.get_user({"_id": ObjectId(user_id)})
            if not user:
                return {
                    "success": False,
                    "data": None,
                    "error": "User not found"
                }
            
            # Verify the user was created by this admin
            if user.adminId != admin_id:
                return {
                    "success": False,
                    "data": None,
                    "error": "You don't have permission to view this user"
                }
            
            # Remove password from response
            user_dict = user.dict()
            user_dict.pop('password', None)
            
            return {
                "success": True,
                "data": {"user": user_dict}
            }
        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": str(e)
            }

    async def update_user_profile(self, user_id: str, update_data: dict) -> dict:
        """
        Update a user profile.
        Can be called by both admin and user.
        User can update their own profile, admin can update any user.
        """
        try:
            if not update_data:
                return {"success": False, "data": None, "error": "No data provided for update."}
            
            # Get user by ID
            user = await self.user_model.get_user({"_id": ObjectId(user_id)})
            if not user:
                return {
                    "success": False,
                    "data": None,
                    "error": "User not found"
                }
            
            # Don't allow updating certain protected fields
            protected_fields = ["password", "adminId", "createdOn", "_id"]
            for field in protected_fields:
                update_data.pop(field, None)

            if "userType" in update_data and update_data["userType"] is not None:
                update_data["userType"] = (
                    update_data["userType"].value
                    if hasattr(update_data["userType"], "value")
                    else update_data["userType"]
                )
            
            if not update_data:
                return {"success": False, "data": None, "error": "No valid fields to update"}
            
            # Add updatedOn timestamp
            update_data["updatedOn"] = datetime.utcnow()
            
            # Update user
            updated = await self.user_model.update_user(user_id, update_data)
            if not updated:
                return {"success": True, "data": "No new changes in data."}
            
            # Get updated user
            updated_user = await self.user_model.get_user({"_id": ObjectId(user_id)})
            user_dict = updated_user.dict()
            user_dict.pop('password', None)
            
            return {
                "success": True,
                "data": {
                    "user": user_dict,
                    "message": "User updated successfully"
                }
            }
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}

    async def get_users_by_admin(self, admin_id: str, page: int = 1, limit: int = 10) -> dict:
        """
        Get all users created by a specific admin with pagination.
        """
        try:
            import asyncio
            filters = {"adminId": admin_id}
            number_to_skip = (page - 1) * limit
            
            # Run queries in parallel for better performance
            total, users = await asyncio.gather(
                self.user_model.get_documents_count(filters),
                self.user_model.get_users(filters, number_to_skip, limit)
            )
            total_pages = (total + limit - 1) // limit
            
            # Remove password from user dicts
            users_data = []
            for user in users:
                user_dict = user.dict()
                user_dict.pop('password', None)
                users_data.append(user_dict)
                
            return {
                "success": True,
                "data": {
                    "users": users_data,
                    "pagination": {
                        "total": total,
                        "totalPages": total_pages,
                        "currentPage": page,
                        "limit": limit
                    }
                }
            }
        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": str(e)
            }

    async def create_user_by_admin(self, user_data, admin_id: str) -> dict:
        """
        Create a new user by admin with admin ID tracking.
        - Checks if user exists
        - Validates and sanitizes input data
        - Hashes password
        - Sets userType to USER
        - Saves admin ID who created the user
        - Creates user record
        """
        try:
            # Check if user exists
            existing_user = await self.user_model.get_user({"email": user_data.email})
            if existing_user:
                return {
                    "success": False,
                    "data": None,
                    "error": "User with this email already exists."
                }

            # Prepare user data (do not persist speaker_profile_ids on the user document)
            user_data_dict = user_data.model_dump(exclude={"speaker_profile_ids"})
            
            # Hash password
            hashed_password = Utils.hash_password(user_data.password)
            user_data_dict["password"] = hashed_password
            
            # Set user type to USER (cannot create admin via this endpoint)
            user_data_dict["userType"] = "user"
            
            # Set the admin ID who created this user
            user_data_dict["adminId"] = admin_id
            
            # Set timestamps
            current_time = datetime.utcnow()
            user_data_dict["createdOn"] = current_time
            user_data_dict["updatedOn"] = current_time
            user_data_dict.setdefault("subscription", default_user_subscription())
            
            # Create user
            user_id = await self.user_model.create_user(user_data_dict)

            from app.models.NotificationSettings import NotificationSettingsModel

            await NotificationSettingsModel().create_for_user_if_missing(str(user_id))

            try_send_welcome_email_on_account_created(
                user_display_name=user_data_dict.get("fullName", ""),
                account_email=user_data_dict.get("email"),
            )
            
            # Prepare response data
            response_data = {
                "_id": str(user_id),
                "email": user_data_dict["email"],
                "fullName": user_data_dict["fullName"],
                "phone": user_data_dict.get("phone"),
                "userType": user_data_dict["userType"],
                "adminId": admin_id,
                "createdOn": current_time
            }
            data_out = {
                "user": response_data,
                "message": "User created successfully",
            }
            profile_ids = user_data.speaker_profile_ids
            if profile_ids:
                from app.models.SpeakerProfile import SpeakerProfileModel

                link = await SpeakerProfileModel().assign_profiles_to_user(
                    profile_ids, str(user_id)
                )
                data_out["speakerProfilesLinked"] = link

            return {
                "success": True,
                "data": data_out,
            }
        
        except ValidationError as e:
            return {
                "success": False,
                "data": None,
                "error": f"Validation error: {str(e)}"
            }
        except Exception as e:
            return {
                "success": False,
                "data": None,
                "error": str(e)
            }
        