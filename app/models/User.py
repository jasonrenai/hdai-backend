from typing import List, Optional
from app.helpers.Database import MongoDB
from bson import ObjectId
import os
from app.schemas.User import UserSchema
from datetime import datetime
from app.schemas.PyObjectId import PyObjectId
from dotenv import load_dotenv

load_dotenv()

class UserModel:
    def __init__(self, db_name=os.getenv('DB_NAME'), collection_name="users"):
        self.collection = MongoDB.get_database(db_name)[collection_name]

    async def get_user(self, filters: dict) -> Optional[UserSchema]:
        """
        Retrieve a single user matching the given filters.
        """
        document = await self.collection.find_one(filters)
        if document:
            return UserSchema(**document)
        return None

    
    async def get_documents_count(self, filters: dict) -> int:
        """
        Retrieve a count of documents matching the given filters.
        """
        total_count = await self.collection.count_documents(filters)
        if total_count:
            return total_count
        return 0
    
    async def update_many(self, filters: dict, update: dict) -> bool:
        """
        Update multiple documents matching the given filters.
        """
        await self.collection.update_many(filters, update)
        return True

    async def get_users(
        self,
        filters: dict = {},
        skip: int = 0,
        limit: int = 10,
        sort_by: dict | None = None,
    ) -> List[UserSchema]:
        """
        Retrieve a list of users matching the given filters with pagination.
        """
        cursor = self.collection.find(filters)
        if sort_by:
            cursor = cursor.sort(list(sort_by.items()))
        cursor = cursor.skip(skip).limit(limit)
        users = []
        async for doc in cursor:
            users.append(UserSchema(**doc))
        return users
    
    async def get_users_with_projection(self, filters: dict = {}, skip: int = 0, limit: int = 10, fields: List[str] = None) -> List[dict]:
        """
        Retrieve a list of users matching the given filters with pagination and projection.
        """
        if fields is None:
            projection = {}
        else:
            projection = {field: 1 for field in fields}

        cursor = self.collection.find(filters, projection).skip(skip).limit(limit)
        result = []
        async for doc in cursor:
            result.append(doc)
        return result

    async def get_all_user_ids_and_full_names(self) -> List[dict]:
        """
        Load only _id and fullName from the users collection; return [{"id": "...", "fullName": "..."}] sorted by fullName.
        """
        projection = {"_id": 1, "fullName": 1}
        cursor = self.collection.find({}, projection)
        docs = await cursor.to_list(length=None)
        out = [
            {"id": str(d["_id"]), "fullName": (d.get("fullName") or "").strip()}
            for d in docs
            if d and d.get("_id") is not None
        ]
        out.sort(key=lambda row: row["fullName"].casefold())
        return out

    async def create_user(self, data: dict) -> PyObjectId:
        """
        Create a new user document in the database.
        """
        data["createdOn"] = datetime.utcnow()
        user = UserSchema(**data)
        result = await self.collection.insert_one(user.dict(by_alias=True))
        return result.inserted_id

    async def update_user(self, user_id: str, updates: dict) -> bool:
        """
        Update an existing user by its ID.
        """
        filters = {"_id": ObjectId(user_id)}
        result = await self.collection.update_one(filters, {"$set": updates})
        return result.modified_count > 0

    async def push_knowledge_id(self, user_id: str, knowledge) -> bool:
        """
        Update an existing user by its ID.
        """
        filters = {"_id": ObjectId(user_id), "isDeleted": False}
        result = await self.collection.update_one(filters, {"$push": {"KnowledgeIds": knowledge}})
        return result.modified_count > 0

    async def soft_delete_user(self, user_id: str) -> bool:
        """
        Soft delete a user by marking it as deleted and setting DeletedOn.
        """
        result = await self.collection.update_one(
            {"_id": ObjectId(user_id), "IsDeleted": False},
            {"$set": {"IsDeleted": True, "DeletedOn": datetime.utcnow()}}
        )
        return result.modified_count > 0

    async def delete_user(self, user_id: str) -> bool:
        """
        Permanently delete a user document from the database.
        """
        result = await self.collection.delete_one({"_id": ObjectId(user_id)})
        return result.deleted_count > 0
    
    async def update_password(self, email: str, new_password: str) -> bool:
        """Update the password of a user based on email."""
        result = await self.collection.update_one(
            {"email": email},
            {"$set": {"password": new_password, "updatedOn": datetime.utcnow()}}
        )
        return result.modified_count > 0    