import logging
import sqlite3
import os
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "8822264854:AAG8isj4DVBIzSx-YpmwD6o3Z6CNUqDTKOQ")

# Conversation States
# Profile/Settings Setup
GENDER, AGE, PREF_GENDER, PREF_AGE = range(4)
# Reporting
REPORT_REASON = 4
# Settings Updates
SETTING_CHOICE, SET_GENDER, SET_AGE, SET_PREF_GENDER, SET_PREF_AGE = range(5, 10)

# --- Database Logic ---
def init_db():
    try:
        conn = sqlite3.connect("bot_database.db")
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                gender TEXT,
                age INTEGER,
                pref_gender TEXT,
                pref_age_min INTEGER,
                pref_age_max INTEGER,
                status TEXT DEFAULT 'idle', -- 'idle', 'searching', 'chatting'
                partner_id INTEGER DEFAULT NULL,
                last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id INTEGER,
                reported_id INTEGER,
                reason TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        logger.error(f"Database initialization error: {e}")

def get_db_connection():
    return sqlite3.connect("bot_database.db")

def update_user(user_id, username=None, **kwargs):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Insert if not exists
        if username:
            cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
        
        # Update specified fields and timestamp
        for key, value in kwargs.items():
            cursor.execute(f"UPDATE users SET {key} = ?, last_active = CURRENT_TIMESTAMP WHERE user_id = ?", (value, user_id))
        
        # Always update last_active on any operation
        cursor.execute("UPDATE users SET last_active = CURRENT_TIMESTAMP WHERE user_id = ?", (user_id,))
        
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        logger.error(f"Error updating user {user_id}: {e}")

def get_user(user_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = cursor.fetchone()
        conn.close()
        return user
    except sqlite3.Error as e:
        logger.error(f"Error fetching user {user_id}: {e}")
        return None

def find_partner(user_id):
    user = get_user(user_id)
    if not user:
        return None
    
    # Unpack user tuple
    u_id, u_name, u_gender, u_age, u_pref_gender, u_pref_age_min, u_pref_age_max, u_status, u_partner_id, u_last_active = user

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        query = '''
            SELECT user_id FROM users 
            WHERE status = 'searching' 
            AND user_id != ? 
        '''
        params = [u_id]
        
        if u_pref_gender != 'Any':
            query += ' AND gender = ?'
            params.append(u_pref_gender)
        
        query += ' AND (pref_gender = ? OR pref_gender = "Any")'
        params.append(u_gender)
        
        query += ' AND age BETWEEN ? AND ?'
        params.extend([u_pref_age_min, u_pref_age_max])
        
        query += ' AND ? BETWEEN pref_age_min AND pref_age_max'
        params.append(u_age)
        
        cursor.execute(query, params)
        partner = cursor.fetchone()
        conn.close()
        return partner[0] if partner else None
    except sqlite3.Error as e:
        logger.error(f"Error finding partner for {u_id}: {e}")
        return None

def pair_users(user1_id, user2_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET status = 'chatting', partner_id = ? WHERE user_id = ?", (user2_id, user1_id))
        cursor.execute("UPDATE users SET status = 'chatting', partner_id = ? WHERE user_id = ?", (user1_id, user2_id))
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        logger.error(f"Error pairing users {user1_id} and {user2_id}: {e}")

def end_chat(user_id):
    try:
        user = get_user(user_id)
        if not user:
            return None
        partner_id = user[8]
        
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET status = 'idle', partner_id = NULL WHERE user_id = ?", (user_id,))
        if partner_id:
            cursor.execute("UPDATE users SET status = 'idle', partner_id = NULL WHERE user_id = ?", (partner_id,))
        conn.commit()
        conn.close()
        return partner_id
    except sqlite3.Error as e:
        logger.error(f"Error ending chat for {user_id}: {e}")
        return None

def report_user(reporter_id, reported_id, reason):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO reports (reporter_id, reported_id, reason) VALUES (?, ?, ?)", (reporter_id, reported_id, reason))
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        logger.error(f"Error reporting user: {e}")

def delete_account(user_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        logger.error(f"Error deleting account {user_id}: {e}")

# --- Background Jobs ---
async def cleanup_stale_users(context: ContextTypes.DEFAULT_TYPE):
    """Reset users to 'idle' if they've been searching for more than 5 minutes without activity."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # SQLite datetime check: users who are 'searching' and last_active < 5 mins ago
        cursor.execute('''
            UPDATE users 
            SET status = 'idle' 
            WHERE status = 'searching' 
            AND last_active < datetime('now', '-5 minutes')
        ''')
        conn.commit()
        conn.close()
        logger.info("Cleanup job: Stale searching users reset to idle.")
    except sqlite3.Error as e:
        logger.error(f"Cleanup job error: {e}")

# --- Bot Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_data = get_user(user.id)
    
    if user_data:
        await update.message.reply_text(
            f"Welcome back, {user.first_name}! 👋\n\n"
            "You are already registered. Use /search to find a partner or /settings to update your profile."
        )
        return ConversationHandler.END

    await update.message.reply_text(
        f"Namaste {user.first_name}! 🙏\n\n"
        "Welcome to Anonymous Chat 3.0. Meet new people without revealing your identity.\n\n"
        "Let's set up your profile first."
    )
    
    reply_keyboard = [["Male", "Female", "Other"]]
    await update.message.reply_text(
        "Tell us your gender:",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return GENDER

async def gender_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['gender'] = update.message.text
    await update.message.reply_text("Now, enter your age (e.g., 21):", reply_markup=ReplyKeyboardRemove())
    return AGE

async def age_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    age_text = update.message.text
    if not age_text.isdigit():
        await update.message.reply_text("Please enter a number.")
        return AGE
    
    context.user_data['age'] = int(age_text)
    reply_keyboard = [["Male", "Female", "Any"]]
    await update.message.reply_text(
        "Who would you like to chat with?",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return PREF_GENDER

async def pref_gender_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['pref_gender'] = update.message.text
    await update.message.reply_text("Enter the minimum age of the person you want to chat with:", reply_markup=ReplyKeyboardRemove())
    return PREF_AGE

async def pref_age_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    age_text = update.message.text
    if not age_text.isdigit():
        await update.message.reply_text("Please enter a number.")
        return PREF_AGE
    
    pref_age_min = int(age_text)
    pref_age_max = pref_age_min + 10 
    
    user_id = update.effective_user.id
    update_user(
        user_id, update.effective_user.username, 
        gender=context.user_data['gender'],
        age=context.user_data['age'],
        pref_gender=context.user_data['pref_gender'],
        pref_age_min=pref_age_min,
        pref_age_max=pref_age_max
    )
    
    await update.message.reply_text(
        "Profile setup complete! 🥳\n\n"
        "/search - Find a partner\n"
        "/stop - End chat\n"
        "/settings - Change profile\n"
        "/terms - Privacy Policy\n"
        "/delete_me - Delete account"
    )
    return ConversationHandler.END

# --- Settings Conversation ---

async def settings_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    if not user:
        await update.message.reply_text("Please /start first.")
        return ConversationHandler.END
        
    reply_keyboard = [
        ["Gender", "Age"],
        ["Preferred Gender", "Preferred Age"],
        ["Cancel"]
    ]
    await update.message.reply_text(
        "What would you like to update?\n\n"
        f"Current Profile:\n- Gender: {user[2]}\n- Age: {user[3]}\n- Pref Gender: {user[4]}\n- Pref Age Min: {user[5]}",
        reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True),
    )
    return SETTING_CHOICE

async def setting_choice_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text
    if choice == "Cancel":
        await update.message.reply_text("Settings update cancelled.", reply_markup=ReplyKeyboardRemove())
        return ConversationHandler.END
    
    if choice == "Gender":
        reply_keyboard = [["Male", "Female", "Other"]]
        await update.message.reply_text("Select your gender:", reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True))
        return SET_GENDER
    elif choice == "Age":
        await update.message.reply_text("Enter your new age:", reply_markup=ReplyKeyboardRemove())
        return SET_AGE
    elif choice == "Preferred Gender":
        reply_keyboard = [["Male", "Female", "Any"]]
        await update.message.reply_text("Select preferred gender:", reply_markup=ReplyKeyboardMarkup(reply_keyboard, one_time_keyboard=True, resize_keyboard=True))
        return SET_PREF_GENDER
    elif choice == "Preferred Age":
        await update.message.reply_text("Enter new minimum age preference:", reply_markup=ReplyKeyboardRemove())
        return SET_PREF_AGE
    else:
        await update.message.reply_text("Invalid choice. Please select from the menu.")
        return SETTING_CHOICE

async def set_gender_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_user(update.effective_user.id, gender=update.message.text)
    await update.message.reply_text("Gender updated! ✅\nUse /settings again to change more or /search to start.")
    return ConversationHandler.END

async def set_age_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    age_text = update.message.text
    if not age_text.isdigit():
        await update.message.reply_text("Please enter a number.")
        return SET_AGE
    update_user(update.effective_user.id, age=int(age_text))
    await update.message.reply_text("Age updated! ✅\nUse /settings again to change more or /search to start.")
    return ConversationHandler.END

async def set_pref_gender_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_user(update.effective_user.id, pref_gender=update.message.text)
    await update.message.reply_text("Preferred gender updated! ✅\nUse /settings again to change more or /search to start.")
    return ConversationHandler.END

async def set_pref_age_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    age_text = update.message.text
    if not age_text.isdigit():
        await update.message.reply_text("Please enter a number.")
        return SET_PREF_AGE
    pref_age_min = int(age_text)
    update_user(update.effective_user.id, pref_age_min=pref_age_min, pref_age_max=pref_age_min+10)
    await update.message.reply_text("Preferred age range updated! ✅\nUse /settings again to change more or /search to start.")
    return ConversationHandler.END

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if not user:
        await update.message.reply_text("Please /start first.")
        return

    if user[7] == 'chatting':
        await update.message.reply_text("You are already in a chat! /stop to leave.")
        return

    await update.message.reply_text("Searching for a compatible partner... 🔍")
    
    partner_id = find_partner(user_id)
    if partner_id:
        pair_users(user_id, partner_id)
        await context.bot.send_message(user_id, "Partner found! Start chatting. 💬\n/stop to end.")
        await context.bot.send_message(partner_id, "Partner found! Start chatting. 💬\n/stop to end.")
    else:
        update_user(user_id, status='searching')
        await update.message.reply_text("No one matches your preferences right now. We'll notify you when someone joins!")

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    partner_id = end_chat(user_id)
    
    await update.message.reply_text("Chat ended. /search for someone new!")
    if partner_id:
        await context.bot.send_message(partner_id, "Your partner has ended the chat. 💔\n/search for a new partner!")

async def report_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if not user or user[8] is None:
        await update.message.reply_text("You are not in an active chat.")
        return ConversationHandler.END
    
    await update.message.reply_text("Please describe the reason for this report:")
    return REPORT_REASON

async def report_reason_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    if not user or user[8] is None:
        await update.message.reply_text("Chat ended. Report failed.")
        return ConversationHandler.END
        
    partner_id = user[8]
    reason = update.message.text
    report_user(user_id, partner_id, reason)
    
    await update.message.reply_text("Thank you. The report has been filed. The partner has been flagged.")
    return ConversationHandler.END

async def delete_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if user and user[7] == 'chatting':
        partner_id = end_chat(user_id)
        if partner_id:
            await context.bot.send_message(partner_id, "Your partner has deleted their account.")
            
    delete_account(user_id)
    await update.message.reply_text("Your data has been permanently deleted.")

async def terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📜 *Privacy & Terms 3.0*\n\n"
        "1. *Anonymity:* Your identity is hidden until you choose to share it.\n"
        "2. *DPDP Compliance:* We follow India's DPDP Act 2023. You have the right to access and delete your data.\n"
        "3. *Safety:* Hate speech and harassment are strictly forbidden. Use /report to flag users.\n"
        "4. *Erasure:* Use /delete_me to wipe all your data from our servers."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def relay_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user = get_user(user_id)
    
    if not user:
        await update.message.reply_text("Please /start first.")
        return

    if user[7] != 'chatting':
        if user[7] == 'searching':
            await update.message.reply_text("Still searching... please wait.")
        else:
            await update.message.reply_text("You aren't in a chat. /search to start!")
        return

    partner_id = user[8]
    
    try:
        if update.message.text:
            await context.bot.send_message(partner_id, update.message.text)
        if update.message.photo:
            await context.bot.send_photo(partner_id, update.message.photo[-1].file_id, caption=update.message.caption)
        if update.message.video:
            await context.bot.send_video(partner_id, update.message.video.file_id, caption=update.message.caption)
        if update.message.voice:
            await context.bot.send_voice(partner_id, update.message.voice.file_id, caption=update.message.caption)
        if update.message.sticker:
            await context.bot.send_sticker(partner_id, update.message.sticker.file_id)
    except Exception as e:
        logger.error(f"Relay error: {e}")
        await update.message.reply_text("Failed to send message. Partner might have left.")
        end_chat(user_id)

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # Background Job for cleaning stale users every 5 minutes
    app.job_queue.run_repeating(cleanup_stale_users, interval=300, first=10)

    # Profile Setup Conversation
    profile_conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, gender_choice)],
            AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, age_choice)],
            PREF_GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, pref_gender_choice)],
            PREF_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, pref_age_choice)],
        },
        fallbacks=[CommandHandler("start", start)],
    )

    # Settings Update Conversation
    settings_conv = ConversationHandler(
        entry_points=[CommandHandler("settings", settings_start)],
        states={
            SETTING_CHOICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, setting_choice_handler)],
            SET_GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_gender_handler)],
            SET_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_age_handler)],
            SET_PREF_GENDER: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_pref_gender_handler)],
            SET_PREF_AGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, set_pref_age_handler)],
        },
        fallbacks=[CommandHandler("settings", settings_start)],
    )

    # Reporting Conversation
    report_conv = ConversationHandler(
        entry_points=[CommandHandler("report", report_start)],
        states={
            REPORT_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, report_reason_handler)],
        },
        fallbacks=[CommandHandler("stop", stop)],
    )

    app.add_handler(profile_conv)
    app.add_handler(settings_conv)
    app.add_handler(report_conv)
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CommandHandler("stop", stop))
    app.add_handler(CommandHandler("delete_me", delete_me))
    app.add_handler(CommandHandler("terms", terms))
    
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, relay_message))

    print("Bot 3.0 is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
