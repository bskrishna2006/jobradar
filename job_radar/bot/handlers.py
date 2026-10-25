"""Telegram bot command, message, document, and callback handlers."""

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
    filters,
)

from job_radar.config import settings
from job_radar.store import store
from job_radar.models import Preferences, Job
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

# State tracking for text inputs (roles, locations, skills)
USER_INPUT_STATE = {}


def check_access(user_id: int) -> bool:
    """Validate user against ALLOWED_CHAT_IDS if configured."""
    allowed = settings.allowed_chat_ids
    if not allowed:
        return True
    return user_id in allowed


# ==================== Command Handlers ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    user = update.effective_user
    if not user:
        return

    logger.info(f"Received /start from user {user.id} ({user.first_name})")

    if not check_access(user.id):
        await update.message.reply_text("⛔ Sorry, access to this JobRadar bot instance is restricted.")
        return

    try:
        store.upsert_user(user.id, username=user.username or "", first_name=user.first_name or "")
        # Ensure default preferences exist
        store.get_preferences(user.id)
    except Exception as e:
        logger.warning(f"Could not persist user to MongoDB in /start: {e}")

    welcome_text = (
        f"👋 <b>Welcome to JobRadar, {user.first_name}!</b>\n\n"
        "I am your personal AI job-finding assistant. Every day, I scan fresh openings from "
        "<b>freehire.me</b>, <b>Greenhouse</b>, <b>Lever</b>, and <b>Ashby</b>, match them to your skills, "
        "and send you the best direct application links.\n\n"
        "🛡️ <i>Note: I only discover and recommend high-match jobs. I NEVER auto-apply or fill forms.</i>\n\n"
        "🚀 <b>To get started:</b>\n"
        "Please send me your resume as a <b>PDF, DOCX, or TXT</b> file (max 5 MB). "
        "I will extract your skills and build your matching profile!"
    )
    await update.message.reply_text(welcome_text, parse_mode="HTML")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    if not update.effective_user or not check_access(update.effective_user.id):
        return

    help_text = (
        "🤖 <b>JobRadar Commands</b>\n\n"
        "/start - Welcome & resume upload prompt\n"
        "/profile - View your parsed profile & extracted skills\n"
        "/preferences - View & edit roles, locations, score & digest settings\n"
        "/jobs - Scan and deliver new matching jobs right now\n"
        "/applied - View your applied jobs list\n"
        "/stats - Application metrics (sent, saved, applied, skipped)\n"
        "/prep &lt;company&gt; - AI interview prep (technical Qs, DSA, resume questions)\n"
        "/pause - Pause your daily job digest\n"
        "/resume - Resume your daily job digest\n"
        "/deleteme - Permanently purge your profile and all stored data\n"
        "/help - Show this guide\n\n"
        "📎 <i>To update your resume, simply upload a new document file at any time!</i>"
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

    # Check rate limit
    if not store.check_and_increment_rate_limit(user.id, "jobs"):
        await update.message.reply_text(
            "⏳ You have reached your daily limit for /jobs on-demand searches. "
            "Your automatic daily digest will still arrive as scheduled!"
        )
        return

    status_msg = await update.message.reply_text("🔍 Scanning job boards & matching against your resume...")

    try:
        jobs = await fetch_all_jobs()
        await process_and_deliver_jobs_for_user(user.id, jobs, bot=context.bot, force=True)
    except Exception as e:
        logger.error(f"Error during /jobs for user {user.id}: {e}")
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
    await update.message.reply_text("⏸ Daily job digests paused. Use /resume when you're ready to restart!")


async def resume_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /resume command."""
    user = update.effective_user
    if not user or not check_access(user.id):
        return

    prefs = store.get_preferences(user.id)
    prefs.is_paused = False
    store.save_preferences(prefs)
    await update.message.reply_text("▶️ Daily job digests resumed! You will receive your daily recommendations.")


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
        # Check user's recently saved/applied jobs
        recent_records = store.get_applied_jobs(user.id, limit=5)
        if recent_records:
            comps = ", ".join(r.get("company_name", "") for r in recent_records if r.get("company_name"))
            await update.message.reply_text(
                f"💡 Usage: <code>/prep &lt;company_name&gt;</code>\n"
                f"Example: <code>/prep {recent_records[0].get('company_name', 'Stripe')}</code>\n"
                f"Recent companies: {comps}",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                "💡 Usage: <code>/prep &lt;company_name&gt;</code>\nExample: <code>/prep Stripe</code>",
                parse_mode="HTML",
            )
        return

    status_msg = await update.message.reply_text(f"🧠 Generating interview prep for <b>{query}</b>...", parse_mode="HTML")

    # Look up job in user_jobs or seen_jobs
    job_record = store.db.seen_jobs.find_one({"company": {"$regex": f"^{query}", "$options": "i"}})
    if not job_record:
        job_record = store.db.seen_jobs.find_one({"title": {"$regex": query, "$options": "i"}})

    if job_record:
        job = Job(
            id=job_record.get("job_id", f"query:{query}"),
            source=job_record.get("source", "freehire"),
            title=job_record.get("title", f"{query} Engineer"),
            company=job_record.get("company", query),
            location=job_record.get("location", "Remote"),
            url=job_record.get("url", ""),
            description=job_record.get("description", f"Role at {query}"),
        )
    else:
        # Synthesize role context for query
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


# ==================== Document (Resume) Handler ====================

async def document_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle resume file uploads (PDF, DOCX, TXT)."""
    user = update.effective_user
    doc = update.message.document
    if not user or not doc:
        return

    if not check_access(user.id):
        await update.message.reply_text("⛔ Restricted access.")
        return

    # Check rate limit
    if not store.check_and_increment_rate_limit(user.id, "resume"):
        await update.message.reply_text("⏳ You have reached your daily resume upload limit (5/day).")
        return

    filename = doc.file_name or "resume.pdf"
    file_size = doc.file_size or 0

    try:
        validate_resume_file(filename, file_size)
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
        return

    status_msg = await update.message.reply_text("📥 Downloading & analyzing your resume with AI...")

    try:
        # Download file bytes via Telegram getFile API
        tg_file = await doc.get_file()
        file_bytes = await tg_file.download_as_bytearray()

        # Extract text (never logged)
        extracted_text = extract_text_from_bytes(bytes(file_bytes), filename)

        # Parse into structured profile
        profile = await parse_resume_to_profile(user.id, extracted_text)

        # Store profile (replaces old profile if user uploads a new one)
        store.save_profile(profile)

        summary_text = format_profile_summary(profile)
        keyboard = [
            [
                InlineKeyboardButton("Looks good! ✅", callback_data="profile_ok"),
                InlineKeyboardButton("Upload again 🔄", callback_data="profile_reupload"),
            ]
        ]

        try:
            await status_msg.delete()
        except Exception:
            pass

        await update.message.reply_text(
            f"🎉 <b>Resume analyzed successfully!</b>\n\n{summary_text}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    except Exception as e:
        logger.error(f"Resume parsing error for user {user.id}: {e}")
        await update.message.reply_text(
            f"❌ Could not process resume: {e}\n"
            "Please make sure the file is a clean text-based PDF, DOCX, or TXT file."
        )


# ==================== Callback Query Handler ====================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle inline button clicks."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()
    data = query.data
    user_id = query.from_user.id

    # 1. Job action buttons: act:applied:{job_id}, act:saved:{job_id}, act:skipped:{job_id}
    if data.startswith("act:"):
        _, action, job_id = data.split(":", 2)
        store.update_job_status(user_id, job_id, action)
        action_emojis = {"applied": "✅ Applied", "saved": "💾 Saved", "skipped": "⏩ Skipped"}
        toast = f"Marked as {action_emojis.get(action, action)}!"
        await query.answer(toast, show_alert=False)

    # 2. Preference modifications
    elif data.startswith("pref:"):
        action = data.split(":", 1)[1]
        prefs = store.get_preferences(user_id)

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
            exp_cycle = ["entry-level", "junior", "mid-level", "senior"]
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

        # Update message
        updated_prefs = store.get_preferences(user_id)
        text = format_preferences_message(updated_prefs)
        keyboard = get_preferences_keyboard(updated_prefs)
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)

    # 3. Profile confirmation
    elif data == "profile_ok":
        await query.answer("Profile confirmed! Ready to find jobs.", show_alert=True)
        await query.message.reply_text(
            "🚀 <b>You're all set!</b>\n"
            "Use /jobs to find matches now, or sit back and wait for your morning digest.",
            parse_mode="HTML",
        )
    elif data == "profile_reupload":
        await query.message.reply_text("📎 Send your new resume file (PDF, DOCX, TXT) whenever you're ready!")

    # 4. Account deletion
    elif data.startswith("confirm_delete:"):
        target_uid = int(data.split(":")[1])
        if target_uid == user_id:
            store.delete_user_data(user_id)
            await query.edit_message_text("🗑️ All your profile, preferences, and job tracking data have been permanently deleted. Goodbye!")
    elif data == "cancel_delete":
        await query.edit_message_text("✅ Deletion cancelled. Your profile is safe.")


# ==================== Text Message Handler ====================

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages when editing preferences."""
    user = update.effective_user
    if not user or not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    user_id = user.id

    if user_id in USER_INPUT_STATE:
        state = USER_INPUT_STATE.pop(user_id)
        items = [x.strip() for x in text.split(",") if x.strip()]
        prefs = store.get_preferences(user_id)

        if state == "edit_roles":
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


from telegram.ext import TypeHandler


async def log_all_updates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log every incoming update for easy debugging."""
    if update.effective_user:
        msg = update.effective_message
        text_preview = msg.text if (msg and msg.text) else (msg.document.file_name if (msg and msg.document) else "[Other]")
        logger.info(f"📩 Telegram event from {update.effective_user.id} ({update.effective_user.first_name}): {text_preview}")


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

    # Documents (PDF, DOCX, TXT)
    application.add_handler(MessageHandler(filters.Document.ALL, document_handler))

    # Inline button callbacks
    application.add_handler(CallbackQueryHandler(callback_handler))

    # Text messages (preference edits)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))
