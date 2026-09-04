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


class MongoDB:
    """Async MongoDB client using Motor for better performance"""
    client: AsyncIOMotorClient = None

    @classmethod
    def connect(cls, uri: str):
        """Connect to MongoDB using Motor async client.

        Atlas / TLS connections need the certifi CA bundle, but a plain local
        ``mongodb://`` URI speaks plaintext, so forcing ``tlsCAFile`` (which
        implies ``tls=True``) would break local development. Only pass the CA
        bundle when the connection actually uses TLS.
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
    async def async_connection_status(cls):
        """Alias for connection_status for backward compatibility"""
        return await cls.connection_status()