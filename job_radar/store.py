"""MongoDB store and data access layer for JobRadar with graceful local fallback."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from pymongo import MongoClient, UpdateOne
from pymongo.errors import PyMongoError, ServerSelectionTimeoutError

from job_radar.config import settings
from job_radar.models import Job, Preferences, UserProfile, UserJobRecord

logger = logging.getLogger(__name__)

LOCAL_BACKUP_FILE = ".job_radar_local_store.json"


class LocalStoreFallback:
    """Lightweight local JSON store fallback when MongoDB Atlas is unreachable."""

    def __init__(self, filepath: str = LOCAL_BACKUP_FILE):
        self.filepath = filepath
        self._data: Dict[str, Any] = {
            "users": {},
            "profiles": {},
            "preferences": {},
            "seen_jobs": {},
            "user_jobs": {},
            "run_logs": [],
        }
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to load local store: {e}")

    def _save(self) -> None:
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self._data, f, default=str, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save local store: {e}")


class MongoStore:
    """Access layer for MongoDB Atlas free tier with automatic fallback."""

    def __init__(self, uri: Optional[str] = None, db_name: Optional[str] = None):
        self.uri = uri or settings.mongodb_uri
        self.db_name = db_name or settings.mongodb_db_name
        self._client: Optional[MongoClient] = None
        self._db = None
        self._fallback = LocalStoreFallback()
        self._using_fallback = False

    @property
    def db(self):
        if self._using_fallback:
            return None
        if self._db is None:
            try:
                self._client = MongoClient(
                    self.uri,
                    serverSelectionTimeoutMS=4000,
                    connectTimeoutMS=4000,
                )
                # Quick health check
                self._client.admin.command("ping")
                self._db = self._client[self.db_name]
                self._ensure_indexes()
                logger.info("✅ Connected to MongoDB Atlas successfully.")
            except Exception as e:
                logger.warning(
                    f"⚠️ MongoDB Atlas unreachable ({e}).\n"
                    "👉 Using local JSON storage fallback so the bot continues running without interruption!\n"
                    "👉 (To enable MongoDB Atlas, go to Atlas -> Network Access -> Add IP Address -> 0.0.0.0/0)"
                )
                self._using_fallback = True
                return None
        return self._db

    def _ensure_indexes(self) -> None:
        if self._db is None:
            return
        try:
            self._db.users.create_index("user_id", unique=True)
            self._db.profiles.create_index("user_id", unique=True)
            self._db.preferences.create_index("user_id", unique=True)
            self._db.seen_jobs.create_index("job_id", unique=True)
            self._db.seen_jobs.create_index("content_hash")
            self._db.user_jobs.create_index([("user_id", 1), ("job_id", 1)], unique=True)
        except Exception as e:
            logger.debug(f"Index creation note: {e}")

    # ==================== User & Rate Limiting ====================

    def upsert_user(self, user_id: int, username: str = "", first_name: str = "") -> None:
        now = datetime.now(timezone.utc)
        if self.db is not None:
            try:
                self.db.users.update_one(
                    {"user_id": user_id},
                    {
                        "$set": {
                            "username": username,
                            "first_name": first_name,
                            "is_active": True,
                            "last_active": now,
                        },
                        "$setOnInsert": {"created_at": now},
                    },
                    upsert=True,
                )
                return
            except Exception:
                self._using_fallback = True

        # Local fallback
        uid_str = str(user_id)
        u = self._fallback._data["users"].setdefault(uid_str, {"created_at": now.isoformat()})
        u.update({"username": username, "first_name": first_name, "is_active": True, "last_active": now.isoformat()})
        self._fallback._save()

    def set_user_active_status(self, user_id: int, is_active: bool) -> None:
        if self.db is not None:
            try:
                self.db.users.update_one({"user_id": user_id}, {"$set": {"is_active": is_active}})
                return
            except Exception:
                self._using_fallback = True

        uid_str = str(user_id)
        if uid_str in self._fallback._data["users"]:
            self._fallback._data["users"][uid_str]["is_active"] = is_active
            self._fallback._save()

    def get_active_users(self) -> List[int]:
        if self.db is not None:
            try:
                cursor = self.db.users.find({"is_active": True}, {"user_id": 1})
                return [doc["user_id"] for doc in cursor]
            except Exception:
                self._using_fallback = True

        return [
            int(uid)
            for uid, data in self._fallback._data["users"].items()
            if data.get("is_active", True)
        ]

    def check_and_increment_rate_limit(self, user_id: int, action: str) -> bool:
        """Rate limits disabled for personal use."""
        return True

    # ==================== User Profile ====================

    def save_profile(self, profile: UserProfile) -> None:
        doc = profile.model_dump()
        if self.db is not None:
            try:
                self.db.profiles.update_one(
                    {"user_id": profile.user_id},
                    {"$set": doc},
                    upsert=True,
                )
                return
            except Exception:
                self._using_fallback = True

        uid_str = str(profile.user_id)
        self._fallback._data["profiles"][uid_str] = doc
        self._fallback._save()

    def get_profile(self, user_id: int) -> Optional[UserProfile]:
        if self.db is not None:
            try:
                doc = self.db.profiles.find_one({"user_id": user_id})
                if doc:
                    doc.pop("_id", None)
                    return UserProfile(**doc)
                return None
            except Exception:
                self._using_fallback = True

        uid_str = str(user_id)
        data = self._fallback._data["profiles"].get(uid_str)
        if data:
            return UserProfile(**data)
        return None

    # ==================== User Preferences ====================

    def save_preferences(self, prefs: Preferences) -> None:
        doc = prefs.model_dump()
        if self.db is not None:
            try:
                self.db.preferences.update_one(
                    {"user_id": prefs.user_id},
                    {"$set": doc},
                    upsert=True,
                )
                return
            except Exception:
                self._using_fallback = True

        uid_str = str(prefs.user_id)
        self._fallback._data["preferences"][uid_str] = doc
        self._fallback._save()

    def get_preferences(self, user_id: int) -> Preferences:
        if self.db is not None:
            try:
                doc = self.db.preferences.find_one({"user_id": user_id})
                if doc:
                    doc.pop("_id", None)
                    return Preferences(**doc)
            except Exception:
                self._using_fallback = True

        uid_str = str(user_id)
        data = self._fallback._data["preferences"].get(uid_str)
        if data:
            return Preferences(**data)

        default_prefs = Preferences(user_id=user_id)
        self.save_preferences(default_prefs)
        return default_prefs

    # ==================== Seen Jobs Cache & User History ====================

    def filter_unseen_jobs_for_user(self, user_id: int, jobs: List[Job]) -> List[Job]:
        if not jobs:
            return []

        seen_ids: Set[str] = set()
        seen_hashes: Set[str] = set()

        if self.db is not None:
            try:
                seen_records = list(
                    self.db.user_jobs.find(
                        {"user_id": user_id},
                        {"job_id": 1, "content_hash": 1},
                    )
                )
                seen_ids = {r["job_id"] for r in seen_records if "job_id" in r}
                seen_hashes = {r.get("content_hash") for r in seen_records if r.get("content_hash")}
            except Exception:
                self._using_fallback = True

        if self._using_fallback:
            uid_str = str(user_id)
            user_jobs = self._fallback._data["user_jobs"].get(uid_str, {})
            seen_ids = set(user_jobs.keys())
            seen_hashes = {v.get("content_hash") for v in user_jobs.values() if v.get("content_hash")}

        return [j for j in jobs if j.id not in seen_ids and j.content_hash not in seen_hashes]

    def record_global_seen_jobs(self, jobs: List[Job]) -> None:
        if not jobs or settings.dry_run:
            return
        if self.db is not None:
            try:
                ops = []
                now = datetime.now(timezone.utc)
                for job in jobs:
                    ops.append(
                        UpdateOne(
                            {"job_id": job.id},
                            {
                                "$set": {
                                    "content_hash": job.content_hash,
                                    "source": job.source,
                                    "title": job.title,
                                    "company": job.company,
                                    "url": job.url,
                                    "location": job.location,
                                    "last_seen_at": now,
                                },
                                "$setOnInsert": {"first_seen_at": now, "posted_at": job.posted_at},
                            },
                            upsert=True,
                        )
                    )
                if ops:
                    self.db.seen_jobs.bulk_write(ops, ordered=False)
                return
            except Exception:
                self._using_fallback = True

        for j in jobs:
            self._fallback._data["seen_jobs"][j.id] = {
                "title": j.title,
                "company": j.company,
                "content_hash": j.content_hash,
                "url": j.url,
            }
        self._fallback._save()

    def record_job_sent_to_user(self, user_id: int, job: Job, match_score: int) -> None:
        if settings.dry_run:
            return
        now = datetime.now(timezone.utc)
        if self.db is not None:
            try:
                self.db.user_jobs.update_one(
                    {"user_id": user_id, "job_id": job.id},
                    {
                        "$set": {
                            "match_score": match_score,
                            "job_title": job.title,
                            "company_name": job.company,
                            "apply_url": job.url,
                            "content_hash": job.content_hash,
                            "updated_at": now,
                        },
                        "$setOnInsert": {"status": "sent", "sent_at": now},
                    },
                    upsert=True,
                )
                return
            except Exception:
                self._using_fallback = True

        uid_str = str(user_id)
        u_jobs = self._fallback._data["user_jobs"].setdefault(uid_str, {})
        u_jobs[job.id] = {
            "job_id": job.id,
            "job_title": job.title,
            "company_name": job.company,
            "apply_url": job.url,
            "content_hash": job.content_hash,
            "match_score": match_score,
            "status": "sent",
            "updated_at": now.isoformat(),
        }
        self._fallback._save()

    def update_job_status(self, user_id: int, job_id: str, status: str) -> bool:
        now = datetime.now(timezone.utc)
        if self.db is not None:
            try:
                res = self.db.user_jobs.update_one(
                    {"user_id": user_id, "job_id": job_id},
                    {"$set": {"status": status, "updated_at": now}},
                )
                return res.modified_count > 0 or res.matched_count > 0
            except Exception:
                self._using_fallback = True

        uid_str = str(user_id)
        u_jobs = self._fallback._data["user_jobs"].get(uid_str, {})
        if job_id in u_jobs:
            u_jobs[job_id]["status"] = status
            u_jobs[job_id]["updated_at"] = now.isoformat()
            self._fallback._save()
            return True
        return False

    def get_applied_jobs(self, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        if self.db is not None:
            try:
                cursor = (
                    self.db.user_jobs.find({"user_id": user_id, "status": "applied"})
                    .sort("updated_at", -1)
                    .limit(limit)
                )
                return list(cursor)
            except Exception:
                self._using_fallback = True

        uid_str = str(user_id)
        u_jobs = self._fallback._data["user_jobs"].get(uid_str, {})
        applied = [v for v in u_jobs.values() if v.get("status") == "applied"]
        return applied[:limit]

    def get_user_stats(self, user_id: int) -> Dict[str, int]:
        counts = {"sent": 0, "saved": 0, "applied": 0, "skipped": 0, "total": 0}
        if self.db is not None:
            try:
                pipeline = [
                    {"$match": {"user_id": user_id}},
                    {"$group": {"_id": "$status", "count": {"$sum": 1}}},
                ]
                for item in self.db.user_jobs.aggregate(pipeline):
                    status = item["_id"]
                    c = item["count"]
                    if status in counts:
                        counts[status] = c
                    counts["total"] += c
                return counts
            except Exception:
                self._using_fallback = True

        uid_str = str(user_id)
        u_jobs = self._fallback._data["user_jobs"].get(uid_str, {})
        for item in u_jobs.values():
            st = item.get("status", "sent")
            if st in counts:
                counts[st] += 1
            counts["total"] += 1
        return counts

    def delete_user_data(self, user_id: int) -> bool:
        if self.db is not None:
            try:
                self.db.users.delete_many({"user_id": user_id})
                self.db.profiles.delete_many({"user_id": user_id})
                self.db.preferences.delete_many({"user_id": user_id})
                self.db.user_jobs.delete_many({"user_id": user_id})
            except Exception:
                self._using_fallback = True

        uid_str = str(user_id)
        self._fallback._data["users"].pop(uid_str, None)
        self._fallback._data["profiles"].pop(uid_str, None)
        self._fallback._data["preferences"].pop(uid_str, None)
        self._fallback._data["user_jobs"].pop(uid_str, None)
        self._fallback._save()
        return True

    def log_run(self, run_id: str, summary: Dict[str, Any]) -> None:
        if settings.dry_run:
            return
        now = datetime.now(timezone.utc)
        if self.db is not None:
            try:
                self.db.run_logs.insert_one({"run_id": run_id, "timestamp": now, **summary})
                return
            except Exception:
                pass

        self._fallback._data["run_logs"].append({"run_id": run_id, "timestamp": now.isoformat(), **summary})
        self._fallback._save()


# Global store instance
store = MongoStore()
