"""Telegram message formatting and chunking utilities."""

from __future__ import annotations

import html
from typing import Any, Dict, List
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from job_radar.models import Job, MatchResult, Preferences, UserProfile

TELEGRAM_MAX_MESSAGE_LENGTH = 4096


def split_message(text: str, max_length: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> List[str]:
    """Split long messages into chunks within Telegram's character limits."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    lines = text.split("\n")
    current_chunk = ""

    for line in lines:
        if len(current_chunk) + len(line) + 1 > max_length:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            # If a single line itself is longer than max_length, split by characters
            while len(line) > max_length:
                chunks.append(line[:max_length])
                line = line[max_length:]
        current_chunk += line + "\n"

    if current_chunk.strip():
        chunks.append(current_chunk.strip())

    return chunks


def format_job_message(match: MatchResult) -> str:
    """
    Format job message according to specified template:
    Company - Role (Location)
    Match: 87%
    Why: <2 lines>
    Missing: <skills>
    Apply: <link>
    """
    job = match.job
    company = html.escape(job.company)
    role = html.escape(job.title)
    location = html.escape(job.location)
    why = html.escape(match.why_matches)
    missing = html.escape(", ".join(match.missing_skills)) if match.missing_skills else "None"
    apply_url = job.url

    msg = (
        f"<b>{company} - {role}</b> ({location})\n\n"
        f"<b>Match:</b> {match.score}%\n"
        f"<b>Why:</b> {why}\n"
        f"<b>Missing:</b> {missing}\n"
        f"<b>Apply:</b> <a href=\"{apply_url}\">Direct Link</a>"
    )
    return msg


def get_job_action_keyboard(job_id: str) -> InlineKeyboardMarkup:
    """Inline action buttons for a job: [Applied] [Saved] [Skip]."""
    keyboard = [
        [
            InlineKeyboardButton("Applied ✅", callback_data=f"act:applied:{job_id}"),
            InlineKeyboardButton("Saved 💾", callback_data=f"act:saved:{job_id}"),
            InlineKeyboardButton("Skip ⏩", callback_data=f"act:skipped:{job_id}"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def format_profile_summary(profile: UserProfile) -> str:
    """Format readable candidate profile summary."""
    skills_str = html.escape(", ".join(profile.skills[:15])) or "None extracted"
    roles_str = html.escape(", ".join(profile.preferred_roles)) or "General"
    edu_str = "\n• ".join(html.escape(e) for e in profile.education[:2]) if profile.education else "Not specified"
    proj_str = "\n• ".join(html.escape(p) for p in profile.projects[:3]) if profile.projects else "Not specified"

    text = (
        "📄 <b>Your Parsed Profile Summary</b>\n\n"
        f"🎯 <b>Preferred Roles:</b> {roles_str}\n"
        f"⚡ <b>Experience Level:</b> {html.escape(profile.experience_level.capitalize())} ({profile.years_of_experience} yrs)\n"
        f"🛠 <b>Key Skills:</b> {skills_str}\n\n"
        f"🎓 <b>Education:</b>\n• {edu_str}\n\n"
        f"🚀 <b>Highlighted Projects:</b>\n• {proj_str}\n\n"
        "<i>To update your profile, simply send me an updated resume PDF, DOCX, or TXT file anytime!</i>"
    )
    return text


def format_preferences_message(prefs: Preferences) -> str:
    """Format current preferences and status."""
    roles = ", ".join(prefs.roles) or "None"
    skills = ", ".join(prefs.skills) or "None"
    locations = ", ".join(prefs.locations) or "None"
    status_str = "⏸ Paused" if prefs.is_paused else "▶️ Active"

    text = (
        "⚙️ <b>Your Job Preferences</b>\n\n"
        f"📌 <b>Target Roles:</b> {roles}\n"
        f"📍 <b>Locations:</b> {locations}\n"
        f"🛠 <b>Focus Skills:</b> {skills}\n"
        f"📈 <b>Experience Level:</b> {prefs.experience_level}\n"
        f"🎯 <b>Min Match Score:</b> {prefs.min_score}%\n"
        f"📬 <b>Jobs per Digest:</b> {prefs.jobs_per_digest}\n"
        f"⏰ <b>Daily Digest Time:</b> {prefs.digest_time}\n"
        f"🔔 <b>Status:</b> {status_str}\n\n"
        "<i>Use the buttons below to adjust your preferences:</i>"
    )
    return text


def get_preferences_keyboard(prefs: Preferences) -> InlineKeyboardMarkup:
    """Inline keyboard for modifying preferences."""
    pause_label = "▶️ Resume Daily Digest" if prefs.is_paused else "⏸ Pause Daily Digest"
    pause_data = "pref:resume" if prefs.is_paused else "pref:pause"

    keyboard = [
        [
            InlineKeyboardButton("🎯 Edit Roles", callback_data="pref:edit_roles"),
            InlineKeyboardButton("📍 Edit Locations", callback_data="pref:edit_locations"),
        ],
        [
            InlineKeyboardButton("🛠 Edit Skills", callback_data="pref:edit_skills"),
            InlineKeyboardButton("📈 Experience Level", callback_data="pref:cycle_exp"),
        ],
        [
            InlineKeyboardButton("🔢 Min Score (+/-)", callback_data="pref:cycle_score"),
            InlineKeyboardButton("📬 Jobs Count", callback_data="pref:cycle_count"),
        ],
        [
            InlineKeyboardButton("⏰ Digest Time", callback_data="pref:cycle_time"),
            InlineKeyboardButton(pause_label, callback_data=pause_data),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def format_stats_message(stats: Dict[str, int]) -> str:
    """Format user application statistics."""
    total = stats.get("total", 0)
    applied = stats.get("applied", 0)
    saved = stats.get("saved", 0)
    skipped = stats.get("skipped", 0)
    sent = stats.get("sent", 0)

    return (
        "📊 <b>Your JobRadar Activity & Statistics</b>\n\n"
        f"📨 <b>Total Jobs Delivered:</b> {total}\n"
        f"✅ <b>Applied:</b> {applied}\n"
        f"💾 <b>Saved:</b> {saved}\n"
        f"⏩ <b>Skipped:</b> {skipped}\n"
        f"⏳ <b>Pending Review:</b> {sent}\n\n"
        "<i>Keep tracking your applications to optimize your daily recommendations!</i>"
    )


def format_interview_prep(job_title: str, company: str, prep_data: Dict[str, Any]) -> str:
    """Format structured interview preparation guide into clean readable text."""
    tech_qs = "\n".join(f"• {q}" for q in prep_data.get("technical_questions", [])) or "None generated"
    dsa = "\n".join(f"• {d}" for d in prep_data.get("dsa_topics", [])) or "Standard algorithm fundamentals"
    resume_qs = "\n".join(f"• {r}" for r in prep_data.get("resume_based_questions", [])) or "General project walkthrough"
    company_pts = "\n".join(f"• {c}" for c in prep_data.get("company_specific_points", [])) or "Review product scale"

    return (
        f"🎯 <b>Interview Preparation Guide</b>\n"
        f"💼 <b>{company} - {job_title}</b>\n\n"
        f"🧠 <b>Key Technical Questions:</b>\n{tech_qs}\n\n"
        f"💻 <b>Relevant DSA & Problem Patterns:</b>\n{dsa}\n\n"
        f"📄 <b>Resume-Based Questions:</b>\n{resume_qs}\n\n"
        f"🏢 <b>Company & Domain Focus:</b>\n{company_pts}"
    )
