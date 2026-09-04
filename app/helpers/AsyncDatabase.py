"""
Async MongoDB helper using Motor for improved performance.
Motor is the async driver for MongoDB in Python.
"""
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ConnectionFailure
import os
import certifi
from dotenv import load_dotenv

load_dotenv()


def _uri_uses_tls(uri: str) -> bool:
    """Whether a MongoDB URI should negotiate TLS.

    ``mongodb+srv://`` (Atlas) enables TLS by default; a plain ``mongodb://``
    URI only uses TLS when explicitly requested via ``tls=true``/``ssl=true``.
    """
    if not uri:
        return False
    lowered = uri.lower()
    if lowered.startswith("mongodb+srv://"):
        return "tls=false" not in lowered and "ssl=false" not in lowered
    return "tls=true" in lowered or "ssl=true" in lowered


class AsyncMongoDB:
    """Async MongoDB connection manager using Motor"""
    client: AsyncIOMotorClient = None

    @classmethod
    def connect(cls, uri: str):
        """Establish async MongoDB connection.

        Only pass the certifi CA bundle for TLS/Atlas connections; a plain
        local ``mongodb://`` URI speaks plaintext and must not force TLS.
        """
        kwargs = {}
        if _uri_uses_tls(uri):
            kwargs["tlsCAFile"] = certifi.where()
        cls.client = AsyncIOMotorClient(uri, **kwargs)

    @classmethod
    def get_database(cls, db_name: str):
        """Get async database instance"""
        return cls.client[db_name]
    
    @classmethod
    async def connection_status(cls):
        """Check async connection status"""
        try:
            await cls.client.admin.command('ping')
            return {"status": "connected", "db": os.getenv('DB_NAME')}
        except ConnectionFailure as e:
            return {"status": "disconnected", "db": os.getenv('DB_NAME')}
    
    @classmethod
    async def close(cls):
        """Close async MongoDB connection"""
        if cls.client:
            cls.client.close()

