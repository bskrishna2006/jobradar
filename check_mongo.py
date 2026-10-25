"""Test MongoDB Atlas connection."""

from pymongo import MongoClient
from job_radar.config import settings

print(f"Connecting to MongoDB at {settings.mongodb_uri[:35]}...")
try:
    client = MongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=5000)
    db = client[settings.mongodb_db_name]
    # Ping
    client.admin.command('ping')
    print("✅ Successfully connected to MongoDB Atlas!")
    print("Database name:", db.name)
except Exception as e:
    print("❌ MongoDB Atlas connection error:", e)
