"""Integration unit tests for end-to-end user onboarding, deduplication across runs, and /deleteme."""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone
from job_radar.models import Job, Preferences, UserProfile, MatchResult
from job_radar.store import MongoStore
from job_radar.jobs_pipeline import process_and_deliver_jobs_for_user


class InMemoryMongoCollection:
    """Lightweight in-memory MongoDB collection emulator for fast unit testing."""

    def __init__(self):
        self.docs = []

    def create_index(self, *args, **kwargs):
        pass

    def update_one(self, filter_doc, update_doc, upsert=False):
        set_fields = update_doc.get("$set", {})
        set_on_insert = update_doc.get("$setOnInsert", {})

        for doc in self.docs:
            match = True
            for k, v in filter_doc.items():
                if doc.get(k) != v:
                    match = False
                    break
            if match:
                doc.update(set_fields)
                return MagicMock(matched_count=1, modified_count=1)

        if upsert:
            new_doc = {**filter_doc, **set_on_insert, **set_fields}
            self.docs.append(new_doc)
            return MagicMock(matched_count=0, modified_count=0, upserted_id="id")

        return MagicMock(matched_count=0, modified_count=0)

    def find_one(self, filter_doc, projection=None):
        for doc in self.docs:
            match = True
            for k, v in filter_doc.items():
                if doc.get(k) != v:
                    match = False
                    break
            if match:
                res = doc.copy()
                if projection:
                    return {k: res[k] for k in projection if k in res}
                return res
        return None

    def find(self, filter_doc=None, projection=None):
        filter_doc = filter_doc or {}
        matches = []
        for doc in self.docs:
            match = True
            for k, v in filter_doc.items():
                if doc.get(k) != v:
                    match = False
                    break
            if match:
                res = doc.copy()
                if projection:
                    res = {k: res[k] for k in projection if k in res}
                matches.append(res)
        return matches

    def delete_many(self, filter_doc):
        initial = len(self.docs)
        self.docs = [
            doc for doc in self.docs
            if not all(doc.get(k) == v for k, v in filter_doc.items())
        ]
        return MagicMock(deleted_count=initial - len(self.docs))

    def bulk_write(self, ops, ordered=False):
        for op in ops:
            self.update_one(op._filter, op._doc, upsert=True)

    def aggregate(self, pipeline):
        counts = {}
        for doc in self.docs:
            st = doc.get("status", "sent")
            counts[st] = counts.get(st, 0) + 1
        return [{"_id": k, "count": v} for k, v in counts.items()]


class InMemoryTestStore(MongoStore):
    """MongoStore backed by in-memory collections for testing."""

    def __init__(self):
        self.uri = "mock://"
        self.db_name = "test_radar"
        self._using_fallback = False
        from job_radar.store import LocalStoreFallback
        self._fallback = LocalStoreFallback(filepath=".test_store_mock.json")
        self._collections = {
            "users": InMemoryMongoCollection(),
            "profiles": InMemoryMongoCollection(),
            "preferences": InMemoryMongoCollection(),
            "seen_jobs": InMemoryMongoCollection(),
            "user_jobs": InMemoryMongoCollection(),
            "run_logs": InMemoryMongoCollection(),
        }

    @property
    def db(self):
        class DBWrapper:
            def __getitem__(wrap_self, item):
                return self._collections[item]
            def __getattr__(wrap_self, item):
                return self._collections[item]
        return DBWrapper()


@pytest.fixture
def mock_store():
    return InMemoryTestStore()


def test_user_onboarding_and_preferences(mock_store):
    uid = 424242
    mock_store.upsert_user(uid, username="krishna", first_name="Krishna")
    assert uid in mock_store.get_active_users()

    # Preferences created with defaults
    prefs = mock_store.get_preferences(uid)
    assert prefs.experience_level == "entry-level"
    assert "India" in prefs.locations

    # Save profile
    profile = UserProfile(
        user_id=uid,
        skills=["Python", "SQL", "Machine Learning"],
        experience_level="fresher",
        preferred_roles=["ML Engineer"],
        resume_summary_text="Fresher ML Engineer proficient in Python and SQL.",
    )
    mock_store.save_profile(profile)

    fetched_prof = mock_store.get_profile(uid)
    assert fetched_prof is not None
    assert fetched_prof.user_id == uid
    assert "Python" in fetched_prof.skills


def test_no_duplicate_jobs_across_runs(mock_store):
    uid = 555555
    mock_store.upsert_user(uid, username="tester")

    job_a = Job(
        id="freehire:job-A",
        source="freehire",
        title="Junior ML Engineer",
        company="Company A",
        location="Remote",
        url="https://example.com/job-a",
        description="Python ML job",
    )
    job_b = Job(
        id="greenhouse:job-B",
        source="greenhouse",
        title="Data Analyst",
        company="Company B",
        location="India",
        url="https://example.com/job-b",
        description="Data analyst role",
    )

    # Initial pool has both jobs
    unseen_run_1 = mock_store.filter_unseen_jobs_for_user(uid, [job_a, job_b])
    assert len(unseen_run_1) == 2

    # Simulate delivering job_a in run 1
    mock_store.record_job_sent_to_user(uid, job_a, match_score=85)

    # In run 2, user should ONLY see job_b, NEVER job_a again!
    unseen_run_2 = mock_store.filter_unseen_jobs_for_user(uid, [job_a, job_b])
    assert len(unseen_run_2) == 1
    assert unseen_run_2[0].id == "greenhouse:job-B"


def test_deleteme_completely_purges_user_data(mock_store):
    uid = 999999
    mock_store.upsert_user(uid, username="to_be_deleted")
    mock_store.save_profile(UserProfile(user_id=uid, skills=["Python"]))
    mock_store.save_preferences(Preferences(user_id=uid))

    job = Job(id="test:1", source="test", title="Role", company="Co", url="https://ex.com")
    mock_store.record_job_sent_to_user(uid, job, match_score=90)

    # Verify data exists
    assert mock_store.get_profile(uid) is not None
    assert uid in mock_store.get_active_users()

    # Call delete
    success = mock_store.delete_user_data(uid)
    assert success is True

    # Verify completely purged
    assert mock_store.get_profile(uid) is None
    assert uid not in mock_store.get_active_users()
    assert len(mock_store.db.user_jobs.find({"user_id": uid})) == 0
