from datetime import datetime
from bson import ObjectId
from app.models.Scraper import ScraperModel
from app.schemas.Scraper import ScraperCreateSchema, ScraperUpdateSchema


class ScraperService:
    def __init__(self):
        self.scraper_model = ScraperModel()

    async def create(self, user_id: str, data: ScraperCreateSchema) -> dict:
        try:
            payload = data.model_dump()
            payload["userId"] = user_id
            payload["createdAt"] = datetime.utcnow()
            scraper_id = await self.scraper_model.create(payload)
            created = await self.scraper_model.get_by_id(scraper_id, user_id)
            return {"success": True, "data": created}
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}

    async def get_by_id(self, scraper_id: str, user_id: str) -> dict:
        try:
            scraper = await self.scraper_model.get_by_id(scraper_id, user_id)
            if not scraper:
                return {"success": False, "data": None, "error": "Scraper not found"}
            return {"success": True, "data": scraper}
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}

    async def get_list(self, user_id: str, skip: int = 0, limit: int = 100) -> dict:
        try:
            items = await self.scraper_model.get_list(user_id, skip=skip, limit=limit)
            total = await self.scraper_model.count(user_id)
            return {
                "success": True,
                "data": {"scrapers": items, "total": total},
            }
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}

    async def update(self, scraper_id: str, user_id: str, data: ScraperUpdateSchema) -> dict:
        try:
            existing = await self.scraper_model.get_by_id(scraper_id, user_id)
            if not existing:
                return {"success": False, "data": None, "error": "Scraper not found"}
            update_dict = data.model_dump(exclude_unset=True)
            if not update_dict:
                return {"success": False, "data": None, "error": "No data provided for update"}
            updated = await self.scraper_model.update(scraper_id, user_id, update_dict)
            if not updated:
                return {"success": False, "data": None, "error": "Failed to update scraper"}
            updated_doc = await self.scraper_model.get_by_id(scraper_id, user_id)
            return {"success": True, "data": updated_doc}
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}

    async def delete(self, scraper_id: str, user_id: str) -> dict:
        try:
            existing = await self.scraper_model.get_by_id(scraper_id, user_id)
            if not existing:
                return {"success": False, "data": None, "error": "Scraper not found"}
            await self.scraper_model.delete(scraper_id, user_id)
            return {"success": True, "data": "Scraper deleted successfully"}
        except Exception as e:
            return {"success": False, "data": None, "error": str(e)}