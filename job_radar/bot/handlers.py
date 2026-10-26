"""Telegram bot command, message, document, and callback handlers with interactive onboarding."""

from __future__ import annotations

import logging
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

from job_radar.config import settings
from job_radar.store import store
from job_radar.models import Preferences, Job, UserProfile
from job_radar.profile.resume_parser import (
    validate_resume_file,
    extract_text_from_bytes,
    parse_resume_to_profile,
)
from job_radar.jobs_pipeline import fetch_all_jobs, process_and_deliver_jobs_for_user
from job_radar.matching.interview_prep import generate_interview_prep
from job_radar.bot.formatting import (
    format_job_message,
    format_profile_summary,
    format_preferences_message,
    get_preferences_keyboard,
    format_stats_message,
    format_interview_prep,
    get_job_action_keyboard,
)

logger = logging.getLogger(__name__)

# State tracking for text inputs (roles, locations, skills, onboarding)
USER_INPUT_STATE = {}


def check_access(user_id: int) -> bool:
    """Validate user against ALLOWED_CHAT_IDS if configured."""
    allowed = settings.allowed_chat_ids
    if not allowed:
        return True
    return user_id in allowed


def get_exp_label(exp_type: str) -> str:
    labels = {
        "internship": "🎓 College Student (Seeking Internship)",
        "fresher": "🚀 Fresher / Graduate (0-1 YOE)",
        "mid": "💼 Experienced (1-3 YOE)",
        "senior": "👑 Senior / Lead (4+ YOE)",
    }
    return labels.get(exp_type, exp_type.capitalize())


def get_step1_experience_keyboard() -> InlineKeyboardMarkup:
    """Question 1: Experience & Student status keyboard."""
    keyboard = [
        [InlineKeyboardButton("🎓 College Student (Seeking Internship)", callback_data="onb:status:internship")],
        [InlineKeyboardButton("🚀 Fresher / Graduate (0-1 YOE)", callback_data="onb:status:fresher")],
        [InlineKeyboardButton("💼 Working Professional (1-3 YOE)", callback_data="onb:status:mid")],
        [InlineKeyboardButton("👑 Senior / Lead (4+ YOE)", callback_data="onb:status:senior")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_step2_roles_keyboard() -> InlineKeyboardMarkup:
    """Question 2: Target roles keyboard."""
    keyboard = [
        [InlineKeyboardButton("🤖 ML & AI Engineer", callback_data="onb:role:ml_ai")],
        [InlineKeyboardButton("📊 Data Scientist / Analyst", callback_data="onb:role:data")],
        [InlineKeyboardButton("⚙️ Python Backend Developer", callback_data="onb:role:backend")],
        [InlineKeyboardButton("🌐 Full Stack / Software Engineer", callback_data="onb:role:fullstack")],
        [InlineKeyboardButton("✏️ Type Custom Role(s)", callback_data="onb:role:custom")],
    ]
    return InlineKeyboardMarkup(keyboard)


def get_step3_location_keyboard() -> InlineKeyboardMarkup:
    """Question 3: Preferred locations keyboard."""
    keyboard = [
        [InlineKeyboardButton("🌍 100% Remote (Worldwide)", callback_data="onb:loc:remote")],
        [InlineKeyboardButton("🇮🇳 India Tech Hubs (Bengaluru, Hyd, etc.)", callback_data="onb:loc:india")],
        [InlineKeyboardButton("🤝 Remote + India", callback_data="onb:loc:remote_india")],
        [InlineKeyboardButton("✏️ Type Custom Location", callback_data="onb:loc:custom")],
    ]
    return InlineKeyboardMarkup(keyboard)


# ==================== Command Handlers ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command with interactive onboarding or dashboard."""
    user = update.effective_user
    if not user:
        return

    logger.info(f"Received /start from user {user.id} ({user.first_name})")

    if not check_access(user.id):
        await update.message.reply_text("⛔ Sorry, access to this JobRadar bot instance is restricted.")
        return

    try:
        store.upsert_user(user.id, username=user.username or "", first_name=user.first_name or "")
        prefs = store.get_preferences(user.id)
    except Exception as e:
        logger.warning(f"Could not initialize user store in /start: {e}")
        prefs = Preferences(user_id=user.id)

    profile = store.get_profile(user.id)

    # If user has already completed onboarding and has a profile: show active dashboard
    if prefs.onboarding_completed and profile:
        exp_title = get_exp_label(prefs.experience_type)
        roles_str = ", ".join(prefs.roles) or "General"
        locs_str = ", ".join(prefs.locations) or "Remote, India"

        text = (
            f"👋 <b>Welcome back to JobRadar, {user.first_name}!</b>\n\n"
            f"✅ <b>Status:</b> {exp_title}\n"
            f"🎯 <b>Target Roles:</b> {roles_str}\n"
            f"📍 <b>Preferred Locations:</b> {locs_str}\n"
            f"⏰ <b>Scheduled Alerts:</b> Every 2 hours\n\n"
            "What would you like to do?"
        )
        keyboard = [
            [
                InlineKeyboardButton("🔍 Find Matching Jobs Now 🚀", callback_data="action:find_jobs_now"),
            ],
            [
                InlineKeyboardButton("📄 View Profile", callback_data="action:view_profile"),
                InlineKeyboardButton("⚙️ Preferences", callback_data="action:view_prefs"),
            ],
            [
                InlineKeyboardButton("🔄 Retake Questionnaire", callback_data="onb:restart"),
            ],
        ]
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    # Start interactive onboarding
    welcome_text = (
        f"👋 <b>Welcome to JobRadar, {user.first_name}!</b>\n\n"
        "I am your personal AI job-finding assistant. I scan verified openings across "
        "<b>freehire.me</b>, <b>Greenhouse</b>, <b>Lever</b>, and <b>Ashby</b>, match them to your skills, "
        "and send direct opportunities every 2 hours.\n\n"
        "🛡️ <i>Note: I only discover and recommend direct openings. I NEVER auto-apply or spam.</i>\n\n"
        "🔒 <i>By continuing, you confirm you are 18+ years of age and agree to our /privacy policy.</i>\n\n"
        "Let's set up your profile in 3 quick questions!\n\n"
        "<b>Step 1 of 3: What is your current experience status?</b>"
    )
    await update.message.reply_text(
        welcome_text,
        parse_mode="HTML",
        reply_markup=get_step1_experience_keyboard(),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    if not update.effective_user or not check_access(update.effective_user.id):
        return

    help_text = (
        "🤖 <b>JobRadar Commands & Guide</b>\n\n"
        "/start - Welcome, interactive questionnaire & setup\n"
        "/jobs - Scan and deliver new matching jobs right now\n"
        "/profile - View your parsed profile & extracted skills\n"
        "/preferences - View & edit roles, locations, score & digest settings\n"
        "/applied - View your applied jobs tracker\n"
        "/stats - Application metrics (sent, saved, applied, skipped)\n"
        "/prep &lt;company&gt; - AI interview prep (technical Qs, DSA, resume questions)\n"
        "/pause - Pause automated 2-hour job alerts\n"
        "/resume - Resume automated 2-hour job alerts\n"
        "/deleteme - Permanently purge your profile and all stored data\n"
        "/privacy - View our DPDP Act data protection & privacy policy\n"
        "/help - Show this guide\n\n"
        "📎 <i>To update your resume, simply upload a PDF, DOCX, or TXT file at any time!</i>"
    )
    await update.message.reply_text(help_text, parse_mode="HTML")


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /profile command."""
    user = update.effective_user
    if not user or not check_access(user.id):
        return

    profile = store.get_profile(user.id)
    if not profile:
        await update.message.reply_text(
            "⚠️ You haven't uploaded a resume yet!\nPlease send your resume as a PDF, DOCX, or TXT file."
        )
        return

    text = format_profile_summary(profile)
    await update.message.reply_text(text, parse_mode="HTML")


async def preferences_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /preferences command."""
    user = update.effective_user
    if not user or not check_access(user.id):
        return

    prefs = store.get_preferences(user.id)
    text = format_preferences_message(prefs)
    keyboard = get_preferences_keyboard(prefs)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def jobs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /jobs command: run matching on demand."""
    user = update.effective_user
    if not user or not check_access(user.id):
        return

    status_msg = await update.message.reply_text("🔍 Scanning job boards & matching against your profile...")

    try:
        jobs = await fetch_all_jobs()
        await process_and_deliver_jobs_for_user(user.id, jobs, bot=context.bot, force=True)
    except Exception as e:
        logger.error(f"Error during /jobs for user {user.id}: {e}", exc_info=True)
        await update.message.reply_text(f"❌ An error occurred while finding jobs: {e}")
    finally:
        try:
            await status_msg.delete()
        except Exception:
            pass


async def applied_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /applied command."""
    user = update.effective_user
    if not user or not check_access(user.id):
        return

    applied_list = store.get_applied_jobs(user.id, limit=15)
    if not applied_list:
        await update.message.reply_text(
            "📝 You haven't marked any jobs as 'Applied' yet.\n"
            "When I send jobs, tap the <b>Applied ✅</b> button to track them here!",
            parse_mode="HTML",
        )
        return

    lines = ["📋 <b>Your Applied Jobs Tracker</b>\n"]
    for idx, item in enumerate(applied_list, 1):
        title = item.get("job_title") or "Role"
        company = item.get("company_name") or "Company"
        url = item.get("apply_url") or "#"
        score = item.get("match_score", 0)
        lines.append(f"{idx}. <b>{company}</b> - {title} ({score}% match)\n   🔗 <a href=\"{url}\">Apply Link</a>")

    lines.append("\n<i>Tip: Type /prep &lt;company&gt; to generate tailored interview questions!</i>")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML", disable_web_page_preview=True)


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /stats command."""
    user = update.effective_user
    if not user or not check_access(user.id):
        return

    stats = store.get_user_stats(user.id)
    text = format_stats_message(stats)
    await update.message.reply_text(text, parse_mode="HTML")


async def pause_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /pause command."""
    user = update.effective_user
    if not user or not check_access(user.id):
        return

    prefs = store.get_preferences(user.id)
    prefs.is_paused = True
    store.save_preferences(prefs)
    await update.message.reply_text("⏸ Automated job alerts paused. Use /resume when you're ready to restart!")


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /resume command."""
    user = update.effective_user
    if not user or not check_access(user.id):
        return

    prefs = store.get_preferences(user.id)
    prefs.is_paused = False
    store.save_preferences(prefs)
    await update.message.reply_text("▶️ Automated job alerts resumed! You will receive updates every 2 hours.")


async def prep_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /prep <company or job>."""
    user = update.effective_user
    if not user or not check_access(user.id):
        return

    profile = store.get_profile(user.id)
    if not profile:
        await update.message.reply_text("⚠️ Please upload your resume first with /start before generating interview prep.")
        return

    query = " ".join(context.args).strip() if context.args else ""
    if not query:
        recent_records = store.get_applied_jobs(user.id, limit=5)
        if recent_records:
            comps = ", ".join(r.get("company_name", "") for r in recent_records if r.get("company_name"))
            await update.message.reply_text(
                f"💡 Usage: <code>/prep &lt;company_name&gt;</code>\n"
                f"Example: <code>/prep {recent_records[0].get('company_name', 'Google')}</code>\n"
                f"Recent companies: {comps}",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                "💡 Usage: <code>/prep &lt;company_name&gt;</code>\nExample: <code>/prep Google</code>",
                parse_mode="HTML",
            )
        return

    status_msg = await update.message.reply_text(f"🧠 Generating interview prep for <b>{query}</b>...", parse_mode="HTML")

    job = Job(
        id=f"query:{query}",
        source="custom",
        title=f"{query} Engineering Role",
        company=query,
        location="Remote",
        url="",
        description=f"Software engineering and machine learning position at {query}.",
    )

    prep_data = await generate_interview_prep(job, profile)
    prep_msg = format_interview_prep(job.title, job.company, prep_data)

    try:
        await status_msg.delete()
    except Exception:
        pass

    await update.message.reply_text(prep_msg, parse_mode="HTML")


async def deleteme_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /deleteme command with confirmation button."""
    user = update.effective_user
    if not user or not check_access(user.id):
        return

    keyboard = [
        [
            InlineKeyboardButton("🚨 Yes, Delete All My Data", callback_data=f"confirm_delete:{user.id}"),
            InlineKeyboardButton("Cancel", callback_data="cancel_delete"),
        ]
    ]
    await update.message.reply_text(
        "⚠️ <b>Are you sure you want to delete your profile?</b>\n\n"
        "This will permanently purge:\n"
        "• Your uploaded resume profile & skills\n"
        "• Your customized preferences\n"
        "• Your application history and saved jobs\n\n"
        "This action cannot be undone.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def privacy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /privacy command with itemized DPDP Act 2023 compliance notice."""
    if not update.effective_user or not check_access(update.effective_user.id):
        return

    privacy_text = (
        "🛡️ <b>JobRadar Privacy & Data Protection Notice</b>\n"
        "<i>Compliant with the Digital Personal Data Protection Act, 2023 (DPDP Act, India)</i>\n\n"
        "<b>1. Data Fiduciary & Purpose:</b>\n"
        "• JobRadar processes your preferences and profile strictly to match and recommend verified job opportunities.\n"
        "• We NEVER sell, share, or monetize your personal data.\n\n"
        "<b>2. What Data We Process:</b>\n"
        "• <b>Account:</b> Telegram user ID, username, and first name.\n"
        "• <b>Profile:</b> Extracted technical skills, experience level, target roles, preferred locations, and project summaries.\n"
        "• <b>Resume Files:</b> When you upload a resume, it is parsed in-memory. <b>Raw resume documents are NEVER stored on disk or in the database.</b>\n\n"
        "<b>3. Third-Party Sub-Processors:</b>\n"
        "• Google Gemini API (structured skill parsing and semantic fit scoring).\n"
        "• Job APIs: Freehire.me, Greenhouse, Lever, and Ashby (read-only job discovery).\n\n"
        "<b>4. Your Rights as a Data Principal:</b>\n"
        "• <b>Right to Access:</b> View your profile anytime with /profile and /preferences.\n"
        "• <b>Right to Erasure:</b> Permanently delete all your records anytime with /deleteme.\n"
        "• <b>Right to Withdraw Consent:</b> Pause automated alerts anytime with /pause.\n\n"
        "<b>5. Age Assurance:</b>\n"
        "• This service is intended exclusively for individuals aged 18 and above.\n\n"
        "<b>6. Grievance Redressal Officer:</b>\n"
        "• Designated Grievance Officer: Krishna (JobRadar Administrator)\n"
        "• Inquiries / Grievances: Contact via Telegram bot administrator.\n"
        "• Resolution SLA: Grievances acknowledged within 48 hours and resolved within 30 days."
    )
    await update.message.reply_text(privacy_text, parse_mode="HTML")


# ==================== Document (Resume) Handler ====================

async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle resume file uploads (PDF, DOCX, TXT) and combine with questionnaire."""
    user = update.effective_user
    doc = update.message.document
    if not user or not doc:
        return

    if not check_access(user.id):
        await update.message.reply_text("⛔ Restricted access.")
        return

    filename = doc.file_name or "resume.pdf"
    file_size = doc.file_size or 0

    try:
        validate_resume_file(filename, file_size)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return

    status_msg = await update.message.reply_text("📥 Analyzing your resume and integrating with your preferences...")

    try:
        tg_file = await doc.get_file()
        file_bytes = await tg_file.download_as_bytearray()

        extracted_text = extract_text_from_bytes(bytes(file_bytes), filename)
        profile = await parse_resume_to_profile(user.id, extracted_text)

        # Merge with onboarding questionnaire preferences
        prefs = store.get_preferences(user.id)
        if prefs.experience_type == "internship":
            profile.experience_level = "internship"
            profile.years_of_experience = 0.0
        elif prefs.experience_type == "fresher":
            profile.experience_level = "entry-level"
        elif prefs.experience_type == "mid":
            profile.experience_level = "mid-level"
        elif prefs.experience_type == "senior":
            profile.experience_level = "senior"

        # Combine preferred roles
        profile.preferred_roles = list(dict.fromkeys((prefs.roles or []) + profile.preferred_roles))

        # Mark onboarding as complete
        prefs.onboarding_completed = True
        store.save_preferences(prefs)
        store.save_profile(profile)

        summary_text = format_profile_summary(profile)
        exp_title = get_exp_label(prefs.experience_type)
        roles_str = ", ".join(prefs.roles) or "General"
        locs_str = ", ".join(prefs.locations) or "Remote, India"

        ready_message = (
            "🎉 <b>Profile & Preferences Setup Complete!</b>\n\n"
            f"🎓 <b>Target Status:</b> {exp_title}\n"
            f"🎯 <b>Target Roles:</b> {roles_str}\n"
            f"📍 <b>Locations:</b> {locs_str}\n"
            f"⏰ <b>Alert Frequency:</b> Every 2 hours\n\n"
            f"{summary_text}\n\n"
            "🚀 <b>You are all set!</b> Tap below to run your first match scan now, "
            "or wait for your scheduled 2-hour updates."
        )

        keyboard = [
            [
                InlineKeyboardButton("🚀 Find Matching Jobs Now", callback_data="action:find_jobs_now"),
            ],
            [
                InlineKeyboardButton("⚙️ Edit Preferences", callback_data="action:view_prefs"),
                InlineKeyboardButton("🔄 Upload New Resume", callback_data="profile_reupload"),
            ],
        ]

        try:
            await status_msg.delete()
        except Exception:
            pass

        await update.message.reply_text(
            ready_message,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"Resume parsing error for user {user.id}: {e}", exc_info=True)
        await update.message.reply_text(
            f"❌ Could not process resume: {e}\n"
            "Please make sure the file is a clean text-based PDF, DOCX, or TXT file."
        )


# ==================== Callback Query Handler ====================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button clicks for onboarding, job actions, and preferences."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()
    data = query.data
    user_id = query.from_user.id
    prefs = store.get_preferences(user_id)

    # 1. Job action buttons: act:applied:{job_id}, act:saved:{job_id}, act:skipped:{job_id}
    if data.startswith("act:"):
        _, action, job_id = data.split(":", 2)
        store.update_job_status(user_id, job_id, action)
        action_emojis = {"applied": "✅ Applied", "saved": "💾 Saved", "skipped": "⏩ Skipped"}
        toast = f"Marked as {action_emojis.get(action, action)}!"
        await query.answer(toast, show_alert=False)

    # 2. Onboarding: Step 1 (Status & Experience Level)
    elif data.startswith("onb:status:"):
        status_key = data.split(":", 2)[2]
        prefs.experience_type = status_key
        if status_key == "internship":
            prefs.years_of_experience = 0.0
            prefs.experience_level = "internship"
        elif status_key == "fresher":
            prefs.years_of_experience = 0.5
            prefs.experience_level = "entry-level"
        elif status_key == "mid":
            prefs.years_of_experience = 2.0
            prefs.experience_level = "mid-level"
        elif status_key == "senior":
            prefs.years_of_experience = 5.0
            prefs.experience_level = "senior"

        store.save_preferences(prefs)
        exp_label = get_exp_label(status_key)

        step2_text = (
            f"✅ <b>Status saved:</b> {exp_label}\n\n"
            "<b>Step 2 of 3: What job roles are you looking for?</b>\n"
            "Select your primary track or choose 'Custom' to specify your own:"
        )
        await query.edit_message_text(
            step2_text,
            parse_mode="HTML",
            reply_markup=get_step2_roles_keyboard(),
        )

    # 3. Onboarding: Step 2 (Roles)
    elif data.startswith("onb:role:"):
        role_key = data.split(":", 2)[2]
        if role_key == "custom":
            USER_INPUT_STATE[user_id] = "onb_custom_role"
            await query.message.reply_text(
                "✏️ Please type your desired target roles as a comma-separated list:\n"
                "<i>Example: Machine Learning Engineer, NLP Engineer, Python Developer</i>",
                parse_mode="HTML",
            )
            return

        role_presets = {
            "ml_ai": ["Machine Learning Engineer", "AI Engineer", "MLOps Engineer"],
            "data": ["Data Scientist", "Data Analyst", "Business Intelligence"],
            "backend": ["Python Backend Engineer", "Software Engineer", "Backend Developer"],
            "fullstack": ["Full Stack Engineer", "Software Engineer", "Frontend Developer"],
        }
        chosen_roles = role_presets.get(role_key, ["Software Engineer"])
        prefs.roles = chosen_roles
        store.save_preferences(prefs)

        step3_text = (
            f"✅ <b>Target roles saved:</b> {', '.join(chosen_roles)}\n\n"
            "<b>Step 3 of 3: Where would you like to work?</b>"
        )
        await query.edit_message_text(
            step3_text,
            parse_mode="HTML",
            reply_markup=get_step3_location_keyboard(),
        )

    # 4. Onboarding: Step 3 (Locations)
    elif data.startswith("onb:loc:"):
        loc_key = data.split(":", 2)[2]
        if loc_key == "custom":
            USER_INPUT_STATE[user_id] = "onb_custom_loc"
            await query.message.reply_text(
                "✏️ Please type your desired job locations as a comma-separated list:\n"
                "<i>Example: Remote, Bengaluru, Hyderabad, Singapore</i>",
                parse_mode="HTML",
            )
            return

        loc_presets = {
            "remote": ["Remote"],
            "india": ["India", "Bengaluru", "Hyderabad", "Pune", "Mumbai", "Noida", "Gurgaon"],
            "remote_india": ["Remote", "India", "Bengaluru", "Hyderabad", "Pune"],
        }
        chosen_locs = loc_presets.get(loc_key, ["Remote", "India"])
        prefs.locations = chosen_locs
        store.save_preferences(prefs)

        exp_label = get_exp_label(prefs.experience_type)
        roles_label = ", ".join(prefs.roles)

        step4_text = (
            f"✅ <b>Location preferences saved:</b> {', '.join(chosen_locs)}\n\n"
            "🎉 <b>Questionnaire Complete!</b>\n\n"
            "📄 <b>Final Step: Upload Your Resume</b>\n"
            "Please send your resume file here as a <b>PDF, DOCX, or TXT</b> document.\n\n"
            f"💡 <i>JobRadar will merge your answers ({exp_label}, {roles_label}) with your resume "
            "skills, education, and projects to match you with top job openings!</i>"
        )
        await query.edit_message_text(step4_text, parse_mode="HTML")

    # 5. Onboarding: Restart
    elif data == "onb:restart":
        welcome_text = (
            "🔄 <b>Restarting Profile Setup</b>\n\n"
            "<b>Step 1 of 3: What is your current experience status?</b>"
        )
        await query.edit_message_text(
            welcome_text,
            parse_mode="HTML",
            reply_markup=get_step1_experience_keyboard(),
        )

    # 6. Action buttons: Find jobs now, View Profile, View Preferences
    elif data == "action:find_jobs_now":
        status_msg = await query.message.reply_text("🔍 Scanning job boards & matching against your profile...")
        try:
            jobs = await fetch_all_jobs()
            await process_and_deliver_jobs_for_user(user_id, jobs, bot=context.bot, force=True)
        except Exception as e:
            logger.error(f"Error in find_jobs_now for {user_id}: {e}", exc_info=True)
            await query.message.reply_text(f"❌ Error finding jobs: {e}")
        finally:
            try:
                await status_msg.delete()
            except Exception:
                pass

    elif data == "action:view_profile":
        profile = store.get_profile(user_id)
        if profile:
            await query.message.reply_text(format_profile_summary(profile), parse_mode="HTML")
        else:
            await query.message.reply_text("⚠️ No resume profile uploaded yet. Please send your resume file!")

    elif data == "action:view_prefs":
        updated_prefs = store.get_preferences(user_id)
        await query.message.reply_text(
            format_preferences_message(updated_prefs),
            parse_mode="HTML",
            reply_markup=get_preferences_keyboard(updated_prefs),
        )

    # 7. Preference settings modifications
    elif data.startswith("pref:"):
        action = data.split(":", 1)[1]

        if action == "pause":
            prefs.is_paused = True
            store.save_preferences(prefs)
        elif action == "resume":
            prefs.is_paused = False
            store.save_preferences(prefs)
        elif action == "cycle_score":
            score_cycle = [50, 60, 70, 80]
            curr_idx = score_cycle.index(prefs.min_score) if prefs.min_score in score_cycle else 1
            prefs.min_score = score_cycle[(curr_idx + 1) % len(score_cycle)]
            store.save_preferences(prefs)
        elif action == "cycle_count":
            count_cycle = [5, 10, 15, 20]
            curr_idx = count_cycle.index(prefs.jobs_per_digest) if prefs.jobs_per_digest in count_cycle else 1
            prefs.jobs_per_digest = count_cycle[(curr_idx + 1) % len(count_cycle)]
            store.save_preferences(prefs)
        elif action == "cycle_exp":
            exp_cycle = ["internship", "entry-level", "mid-level", "senior"]
            curr_idx = exp_cycle.index(prefs.experience_level) if prefs.experience_level in exp_cycle else 0
            prefs.experience_level = exp_cycle[(curr_idx + 1) % len(exp_cycle)]
            store.save_preferences(prefs)
        elif action == "cycle_time":
            time_cycle = ["08:00 IST", "09:00 IST", "10:00 IST", "18:00 IST"]
            curr_idx = time_cycle.index(prefs.digest_time) if prefs.digest_time in time_cycle else 1
            prefs.digest_time = time_cycle[(curr_idx + 1) % len(time_cycle)]
            store.save_preferences(prefs)
        elif action in ("edit_roles", "edit_locations", "edit_skills"):
            USER_INPUT_STATE[user_id] = action
            prompt_name = action.replace("edit_", "").capitalize()
            await query.message.reply_text(
                f"✏️ Please reply with your desired <b>{prompt_name}</b> as a comma-separated list.\n"
                f"Example: <code>ML Engineer, Data Scientist, AI Engineer</code>",
                parse_mode="HTML",
            )
            return

        updated_prefs = store.get_preferences(user_id)
        text = format_preferences_message(updated_prefs)
        keyboard = get_preferences_keyboard(updated_prefs)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)

    elif data == "profile_reupload":
        await query.message.reply_text("📎 Send your new resume file (PDF, DOCX, TXT) whenever you're ready!")

    # 8. Account deletion
    elif data.startswith("confirm_delete:"):
        target_uid = int(data.split(":")[1])
        if target_uid == user_id:
            store.delete_user_data(user_id)
            await query.edit_message_text("🗑️ All your profile, preferences, and job tracking data have been permanently deleted. Goodbye!")
    elif data == "cancel_delete":
        await query.edit_message_text("✅ Deletion cancelled. Your profile is safe.")


# ==================== Text Message Handler ====================

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle text inputs during onboarding or preference editing."""
    user = update.effective_user
    if not user or not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    user_id = user.id

    if user_id in USER_INPUT_STATE:
        state = USER_INPUT_STATE.pop(user_id)
        items = [x.strip() for x in text.split(",") if x.strip()]
        prefs = store.get_preferences(user_id)

        # Onboarding custom inputs
        if state == "onb_custom_role":
            prefs.roles = items
            store.save_preferences(prefs)
            step3_text = (
                f"✅ <b>Target roles saved:</b> {', '.join(items)}\n\n"
                "<b>Step 3 of 3: Where would you like to work?</b>"
            )
            await update.message.reply_text(
                step3_text,
                parse_mode="HTML",
                reply_markup=get_step3_location_keyboard(),
            )
            return

        elif state == "onb_custom_loc":
            prefs.locations = items
            store.save_preferences(prefs)
            exp_label = get_exp_label(prefs.experience_type)
            roles_label = ", ".join(prefs.roles)

            step4_text = (
                f"✅ <b>Location preferences saved:</b> {', '.join(items)}\n\n"
                "🎉 <b>Questionnaire Complete!</b>\n\n"
                "📄 <b>Final Step: Upload Your Resume</b>\n"
                "Please send your resume file here as a <b>PDF, DOCX, or TXT</b> document.\n\n"
                f"💡 <i>JobRadar will merge your answers ({exp_label}, {roles_label}) with your resume "
                "skills, education, and projects to match you with top job openings!</i>"
            )
            await update.message.reply_text(step4_text, parse_mode="HTML")
            return

        # General preference edits
        elif state == "edit_roles":
            prefs.roles = items
            store.save_preferences(prefs)
            await update.message.reply_text(f"✅ Updated target roles: <b>{', '.join(items)}</b>", parse_mode="HTML")
        elif state == "edit_locations":
            prefs.locations = items
            store.save_preferences(prefs)
            await update.message.reply_text(f"✅ Updated locations: <b>{', '.join(items)}</b>", parse_mode="HTML")
        elif state == "edit_skills":
            prefs.skills = items
            store.save_preferences(prefs)
            await update.message.reply_text(f"✅ Updated focus skills: <b>{', '.join(items)}</b>", parse_mode="HTML")

        # Show updated preferences
        updated_prefs = store.get_preferences(user_id)
        await update.message.reply_text(
            format_preferences_message(updated_prefs),
            parse_mode="HTML",
            reply_markup=get_preferences_keyboard(updated_prefs),
        )


async def log_all_updates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log incoming updates for diagnostics."""
    if update.effective_user:
        msg = update.effective_message
        preview = msg.text if (msg and msg.text) else (msg.document.file_name if (msg and msg.document) else "[Other]")
        logger.info(f"📩 Telegram event from {update.effective_user.id} ({update.effective_user.first_name}): {preview}")


def register_handlers(application: Application) -> None:
    """Attach all command, document, callback, and message handlers to application."""
    application.add_handler(TypeHandler(Update, log_all_updates), group=-1)

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("profile", profile_command))
    application.add_handler(CommandHandler("preferences", preferences_command))
    application.add_handler(CommandHandler("jobs", jobs_command))
    application.add_handler(CommandHandler("applied", applied_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("pause", pause_command))
    application.add_handler(CommandHandler("resume", resume_command))
    application.add_handler(CommandHandler("prep", prep_command))
    application.add_handler(CommandHandler("deleteme", deleteme_command))
    application.add_handler(CommandHandler("privacy", privacy_command))

    # Documents (PDF, DOCX, TXT)
    application.add_handler(MessageHandler(filters.Document.ALL, document_handler))

    # Inline button callbacks
    application.add_handler(CallbackQueryHandler(callback_handler))

    # Text messages (custom inputs & preference edits)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
