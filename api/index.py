import os
import requests
import hmac
import hashlib
from flask import Flask, Response, request, jsonify, render_template_string, session, redirect, url_for
from datetime import datetime, timedelta
import time
import threading
import logging
from functools import wraps

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask application
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'your-secret-key-here')  # For sessions

# Bot configuration
TOKEN = os.getenv('TOKEN')
if not TOKEN:
    raise ValueError("Bot token is not set in environment variables! Set 'TOKEN' in Vercel settings.")
CHANNEL_USERNAME = '@cdntelegraph'  # Channel username
BASE_API_URL = f"https://api.telegram.org/bot{TOKEN}"
ADMIN_IDS = [6099917788]  # Replace with your admin user IDs
MAX_FILE_SIZE_MB = 4000  # Maximum file size in MB
RATE_LIMIT = 3  # Files per minute per user
BOT_USERNAME = "IP_AdressBot"  # Your bot's username

# User data and file storage (in memory for simplicity; use a database in production)
uploaded_files = {}
user_activity = {}
users = {}

# Helper functions
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

def create_inline_keyboard(buttons, columns=2):
    keyboard = []
    row = []
    for i, button in enumerate(buttons, 1):
        row.append(button)
        if i % columns == 0:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return {"inline_keyboard": keyboard}

def create_reply_keyboard(buttons, resize=True, one_time=False):
    keyboard = []
    row = []
    for button in buttons:
        row.append({"text": button})
    keyboard.append(row)
    return {
        "keyboard": keyboard,
        "resize_keyboard": resize,
        "one_time_keyboard": one_time,
        "selective": True
    }

def send_message(chat_id, text, reply_markup=None, disable_web_page_preview=True):
    try:
        url = f"{BASE_API_URL}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": disable_web_page_preview
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Error sending message: {e}")
        return None

def edit_message_text(chat_id, message_id, text, reply_markup=None):
    try:
        url = f"{BASE_API_URL}/editMessageText"
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML"
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Error editing message: {e}")
        return None

def send_file_to_channel(file_id, file_type, caption=None, chat_id=CHANNEL_USERNAME):
    methods = {
        "document": ("sendDocument", "document"),
        "photo": ("sendPhoto", "photo"),
        "video": ("sendVideo", "video"),
        "audio": ("sendAudio", "audio"),
        "voice": ("sendVoice", "voice")
    }
    
    if file_type not in methods:
        return None

    method, payload_key = methods[file_type]
    url = f"{BASE_API_URL}/{method}"
    payload = {"chat_id": chat_id, payload_key: file_id}
    if caption:
        payload["caption"] = caption
        payload["parse_mode"] = "HTML"
    response = requests.post(url, json=payload, timeout=30)
    response.raise_for_status()
    return response.json()

def delete_message(chat_id, message_id):
    url = f"{BASE_API_URL}/deleteMessage"
    payload = {"chat_id": chat_id, "message_id": message_id}
    response = requests.post(url, json=payload, timeout=30)
    return response.status_code == 200

def get_user_info(user_id):
    url = f"{BASE_API_URL}/getChat"
    payload = {"chat_id": user_id}
    response = requests.post(url, json=payload, timeout=30)
    if response.status_code == 200:
        return response.json().get("result", {})
    return {}

def send_typing_action(chat_id):
    url = f"{BASE_API_URL}/sendChatAction"
    payload = {"chat_id": chat_id, "action": "typing"}
    requests.post(url, json=payload, timeout=30)

def create_file_info_message(file_data, channel_url):
    file_type_emoji = {
        "document": "📄",
        "photo": "🖼️",
        "video": "🎬",
        "audio": "🎵",
        "voice": "🎤"
    }.get(file_data["file_type"], "📁")
    
    user_info = get_user_info(file_data["user_id"])
    username = user_info.get("username", "Unknown")
    first_name = user_info.get("first_name", "User")
    
    upload_time = datetime.fromtimestamp(file_data["timestamp"]).strftime('%Y-%m-%d %H:%M:%S')
    
    return f"""
{file_type_emoji} <b>File Successfully Uploaded!</b>

👤 <b>Uploaded by:</b> {first_name} (@{username})
📅 <b>Upload time:</b> {upload_time}
📏 <b>File size:</b> {file_data.get('file_size', 'N/A')} MB

🔗 <b>Channel URL:</b> <a href="{channel_url}">Click here to view</a>

<i>You can delete this file using the button below.</i>
"""

def check_rate_limit(user_id):
    now = time.time()
    if user_id not in user_activity:
        user_activity[user_id] = []
    
    user_activity[user_id] = [t for t in user_activity[user_id] if now - t < 60]
    
    if len(user_activity[user_id]) >= RATE_LIMIT:
        return False
    
    user_activity[user_id].append(now)
    return True

# Webhook and routes
@app.route('/setwebhook', methods=['GET', 'POST'])
def set_webhook():
    vercel_url = os.getenv('VERCEL_URL', 'https://uploadfiletgbot.vercel.app')
    webhook_url = f"{BASE_API_URL}/setWebhook?url={vercel_url}/webhook&allowed_updates=%5B%22message%22,%22callback_query%22%5D"
    response = requests.get(webhook_url, timeout=30)
    if response.status_code == 200:
        return "Webhook successfully set", 200
    return f"Error setting webhook: {response.text}", response.status_code

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if not update:
        return jsonify({"status": "no data"}), 400

    if "callback_query" in update:
        handle_callback_query(update["callback_query"])
    elif "message" in update:
        handle_message(update["message"])

    return jsonify({"status": "processed"}), 200

def handle_callback_query(callback):
    chat_id = callback["message"]["chat"]["id"]
    message_id = callback["message"]["message_id"]
    user_id = callback["from"]["id"]
    callback_data = callback["data"]

    if callback_data.startswith("delete_"):
        channel_message_id = int(callback_data.split("_")[1])
        handle_delete(chat_id, message_id, user_id, channel_message_id)
    elif callback_data in ["help", "upload_instructions", "main_menu", "admin_panel", "admin_stats", "admin_list", "admin_users", "admin_restart", "privacy"]:
        handle_menu_action(chat_id, message_id, user_id, callback_data)

def handle_message(message):
    chat_id = message["chat"]["id"]
    user_id = message["from"]["id"]

    if "text" in message:
        handle_text_command(chat_id, user_id, message["text"])
    elif any(key in message for key in ["document", "photo", "video", "audio", "voice"]):
        handle_file_upload(chat_id, user_id, message)

def handle_text_command(chat_id, user_id, text):
    send_typing_action(chat_id)
    if text == "/start":
        show_main_menu(chat_id, user_id)
    elif text == "/help":
        show_help(chat_id)
    elif text == "/upload":
        show_upload_instructions(chat_id)
    elif text == "/stats" and user_id in ADMIN_IDS:
        show_stats(chat_id)
    elif text == "/list" and user_id in ADMIN_IDS:
        list_files(chat_id, user_id)
    elif text == "/privacy":
        show_privacy_policy(chat_id)
    elif text == "/restart" and user_id in ADMIN_IDS:
        uploaded_files.clear()
        send_message(chat_id, "🔄 <b>Bot has been restarted.</b>\n\nAll cached data has been cleared.")
    else:
        send_message(chat_id, "❓ <b>Unknown Command</b>\n\nType /help to see available commands.")

def handle_file_upload(chat_id, user_id, message):
    if not check_rate_limit(user_id):
        send_message(chat_id, "⚠️ <b>Rate Limit Exceeded</b>\n\nPlease wait a minute before uploading more files.")
        return

    file_id, file_type, caption, file_size = extract_file_info(message)
    if file_size > MAX_FILE_SIZE_MB:
        send_message(chat_id, f"⚠️ <b>File Too Large</b>\n\nMaximum file size is {MAX_FILE_SIZE_MB} MB. Your file is {file_size:.2f} MB.")
        return

    send_typing_action(chat_id)
    result = send_file_to_channel(file_id, file_type, caption)
    if result and result.get("ok"):
        channel_message_id = result["result"]["message_id"]
        channel_url = f"https://t.me/{CHANNEL_USERNAME[1:]}/{channel_message_id}"

        uploaded_files[channel_message_id] = {
            "file_id": file_id,
            "file_type": file_type,
            "user_id": user_id,
            "timestamp": message["date"],
            "caption": caption,
            "file_size": round(file_size, 2)
        }

        file_info = create_file_info_message(uploaded_files[channel_message_id], channel_url)
        buttons = [
            {"text": "🗑️ Delete File", "callback_data": f"delete_{channel_message_id}"},
            {"text": "🔗 Copy Link", "url": channel_url},
            {"text": "📤 Upload Another", "callback_data": "upload_instructions"},
            {"text": "🏠 Main Menu", "callback_data": "main_menu"}
        ]
        reply_markup = create_inline_keyboard(buttons)
        send_message(chat_id, file_info, reply_markup)
    else:
        send_message(chat_id, "❌ <b>Upload Failed</b>\n\nSorry, I couldn't upload your file. Please try again.")

def extract_file_info(message):
    if "document" in message:
        return message["document"]["file_id"], "document", message.get("caption"), message["document"].get("file_size", 0) / (1024 * 1024)
    elif "photo" in message:
        return message["photo"][-1]["file_id"], "photo", message.get("caption"), message["photo"][-1].get("file_size", 0) / (1024 * 1024)
    elif "video" in message:
        return message["video"]["file_id"], "video", message.get("caption"), message["video"].get("file_size", 0) / (1024 * 1024)
    elif "audio" in message:
        return message["audio"]["file_id"], "audio", message.get("caption"), message["audio"].get("file_size", 0) / (1024 * 1024)
    elif "voice" in message:
        return message["voice"]["file_id"], "voice", message.get("caption"), message["voice"].get("file_size", 0) / (1024 * 1024)
    return None, None, None, 0

def show_main_menu(chat_id, user_id=None, message_id=None):
    welcome_message = """
    🌟 <b>Welcome to File Uploader Bot!</b> 🌟

    I can upload your files to our channel and provide you with a shareable link.

    <b>Main Features:</b>
    • Upload documents, photos, videos, and audio files
    • Get direct links to your uploaded files
    • Delete your files anytime
    • Simple and intuitive interface

    Use the buttons below to get started or type /help for more information.
    """
    buttons = [
        {"text": "📤 Upload File", "callback_data": "upload_instructions"},
        {"text": "ℹ️ Help", "callback_data": "help"},
        {"text": "🔒 Privacy Policy", "callback_data": "privacy"}
    ]
    if user_id and user_id in ADMIN_IDS:
        buttons.append({"text": "🛠️ Admin Panel", "callback_data": "admin_panel"})
    
    reply_markup = create_inline_keyboard(buttons, columns=2)
    
    if message_id:
        edit_message_text(chat_id, message_id, welcome_message, reply_markup)
    else:
        send_message(chat_id, welcome_message, reply_markup)

def show_help(chat_id, message_id=None):
    help_text = """
    📚 <b>File Uploader Bot Help</b>

    <b>Available commands:</b>
    /start - Start the bot and get instructions
    /help - Show this help message
    /upload - Learn how to upload files
    /privacy - View our privacy policy

    <b>How to use:</b>
    1. Send me a file (document, photo, video, or audio)
    2. I'll automatically upload it to the channel
    3. You'll get a shareable link
    4. You can delete it anytime with the delete button

    <b>Features:</b>
    • Fast and secure file uploading
    • Direct links to your files
    • Delete functionality for your files
    • Support for various file types
    • Rate limiting (max {RATE_LIMIT} files per minute)
    • File size limit ({MAX_FILE_SIZE_MB} MB max)
    """.format(RATE_LIMIT=RATE_LIMIT, MAX_FILE_SIZE_MB=MAX_FILE_SIZE_MB)
    buttons = [
        {"text": "📤 How to Upload", "callback_data": "upload_instructions"},
        {"text": "🔒 Privacy Policy", "callback_data": "privacy"},
        {"text": "🔙 Main Menu", "callback_data": "main_menu"}
    ]
    reply_markup = create_inline_keyboard(buttons)
    
    if message_id:
        edit_message_text(chat_id, message_id, help_text, reply_markup)
    else:
        send_message(chat_id, help_text, reply_markup)

def show_upload_instructions(chat_id, message_id=None):
    instructions = """
    📤 <b>How to Upload Files</b>

    1. <b>Simple Upload:</b>
       • Just send me any file (document, photo, video, or audio)
       • I'll automatically upload it to the channel

    2. <b>With Caption:</b>
       • Send a file with a caption
       • The caption will be included with your file

    3. <b>Supported Formats:</b>
       • Documents (PDF, Word, Excel, etc.)
       • Photos (JPG, PNG, etc.)
       • Videos (MP4, etc.)
       • Audio files (MP3, etc.)

    <b>Limitations:</b>
    • Max file size: {MAX_FILE_SIZE_MB} MB
    • Max uploads: {RATE_LIMIT} per minute

    <i>Note: Large files may take longer to process.</i>
    """.format(MAX_FILE_SIZE_MB=MAX_FILE_SIZE_MB, RATE_LIMIT=RATE_LIMIT)
    buttons = [
        {"text": "🔙 Main Menu", "callback_data": "main_menu"},
        {"text": "ℹ️ General Help", "callback_data": "help"}
    ]
    reply_markup = create_inline_keyboard(buttons)
    
    if message_id:
        edit_message_text(chat_id, message_id, instructions, reply_markup)
    else:
        send_message(chat_id, instructions, reply_markup)

def show_privacy_policy(chat_id, message_id=None):
    privacy_text = """
    🔒 <b>Privacy Policy</b>

    We are committed to protecting your privacy. Here's how we handle your data:

    1. <b>Data Collection:</b> We only collect the data necessary for file uploading and management, such as your Telegram ID, username, and file metadata.

    2. <b>Data Usage:</b> Your data is used solely to provide our services, including uploading files and managing your uploads. We do not share your data with third parties unless required by law.

    3. <b>Data Storage:</b> Files and user data are stored temporarily and can be deleted at your request or automatically after a set period.

    4. <b>Your Rights:</b> You can request deletion of your data or files at any time by contacting us or using the delete button.

    5. <b>Contact Us:</b> For privacy concerns, contact our admin at @MAXWARORG.

    By using this bot, you agree to this privacy policy.
    """
    buttons = [
        {"text": "🔙 Main Menu", "callback_data": "main_menu"}
    ]
    reply_markup = create_inline_keyboard(buttons)
    if message_id:
        edit_message_text(chat_id, message_id, privacy_text, reply_markup)
    else:
        send_message(chat_id, privacy_text, reply_markup)

def show_admin_panel(chat_id, message_id=None):
    admin_text = """
    🛠️ <b>Admin Panel</b>

    <b>Available Commands:</b>
    /stats - Show bot statistics
    /list - List all uploaded files
    /restart - Clear all cached data

    <b>Quick Actions:</b>
    """
    buttons = [
        {"text": "📊 View Stats", "callback_data": "admin_stats"},
        {"text": "📜 List Files", "callback_data": "admin_list"},
        {"text": "🔙 Main Menu", "callback_data": "main_menu"}
    ]
    reply_markup = create_inline_keyboard(buttons, columns=2)
    
    if message_id:
        edit_message_text(chat_id, message_id, admin_text, reply_markup)
    else:
        send_message(chat_id, admin_text, reply_markup)

def show_stats(chat_id):
    total_files = len(uploaded_files)
    active_users = len({v['user_id'] for v in uploaded_files.values()})
    total_size = sum(v.get('file_size', 0) for v in uploaded_files.values())
    
    stats_message = f"""
    📊 <b>Bot Statistics</b>

    • Total files uploaded: {total_files}
    • Active users: {active_users}
    • Total storage used: {total_size:.2f} MB
    • Rate limit: {RATE_LIMIT} files per minute
    • Max file size: {MAX_FILE_SIZE_MB} MB

    <b>System Status:</b>
    The bot is functioning normally.
    """
    buttons = [
        {"text": "🛠️ Admin Panel", "callback_data": "admin_panel"},
        {"text": "🔙 Main Menu", "callback_data": "main_menu"}
    ]
    reply_markup = create_inline_keyboard(buttons)
    send_message(chat_id, stats_message, reply_markup)

def list_files(chat_id, user_id):
    if user_id not in ADMIN_IDS:
        send_message(chat_id, "⛔ <b>Permission Denied</b>\n\nOnly admins can use this command.")
        return
    
    if not uploaded_files:
        send_message(chat_id, "ℹ️ <b>No files uploaded yet.</b>")
        return
    
    message = "📜 <b>Recently Uploaded Files</b>\n\n"
    for i, (msg_id, file_data) in enumerate(list(uploaded_files.items())[-10:], 1):
        user_info = get_user_info(file_data["user_id"])
        username = user_info.get("username", "Unknown")
        file_type = file_data["file_type"].capitalize()
        timestamp = datetime.fromtimestamp(file_data["timestamp"]).strftime('%Y-%m-%d %H:%M')
        
        message += f"{i}. <b>{file_type}</b> by @{username}\n"
        message += f"   📅 {timestamp} | 📏 {file_data.get('file_size', 'N/A')} MB\n"
        message += f"   🔗 <a href='https://t.me/{CHANNEL_USERNAME[1:]}/{msg_id}'>View File</a>\n\n"
    
    if len(uploaded_files) > 10:
        message += f"<i>Showing last 10 of {len(uploaded_files)} files</i>"
    
    buttons = [
        {"text": "🛠️ Admin Panel", "callback_data": "admin_panel"},
        {"text": "🔙 Main Menu", "callback_data": "main_menu"}
    ]
    reply_markup = create_inline_keyboard(buttons)
    send_message(chat_id, message, reply_markup)

def handle_delete(chat_id, message_id, user_id, channel_message_id):
    if channel_message_id in uploaded_files:
        file_data = uploaded_files[channel_message_id]
        if user_id in ADMIN_IDS or file_data["user_id"] == user_id:
            if delete_message(CHANNEL_USERNAME, channel_message_id):
                del uploaded_files[channel_message_id]
                edit_message_text(chat_id, message_id, "✅ <b>File successfully deleted!</b>", reply_markup=None)
            else:
                edit_message_text(chat_id, message_id, "❌ <b>Failed to delete the file.</b>\n\nPlease try again.", reply_markup=create_inline_keyboard([{"text": "Try Again", "callback_data": f"delete_{channel_message_id}"}]))
        else:
            edit_message_text(chat_id, message_id, "⛔ <b>Permission Denied</b>\n\nOnly the uploader or admins can delete this file.", reply_markup=None)
    else:
        edit_message_text(chat_id, message_id, "⚠️ <b>File not found</b>\n\nThis file may have already been deleted.", reply_markup=None)

def handle_menu_action(chat_id, message_id, user_id, action):
    if action == "help":
        show_help(chat_id, message_id)
    elif action == "upload_instructions":
        show_upload_instructions(chat_id, message_id)
    elif action == "main_menu":
        show_main_menu(chat_id, message_id, user_id)
    elif action == "privacy":
        show_privacy_policy(chat_id, message_id)
    elif action == "admin_panel" and user_id in ADMIN_IDS:
        show_admin_panel(chat_id, message_id)
    elif action == "admin_stats" and user_id in ADMIN_IDS:
        show_stats(chat_id)
    elif action == "admin_list" and user_id in ADMIN_IDS:
        list_files(chat_id, user_id)

# Background task
def clean_activity_data():
    while True:
        now = time.time()
        for user_id in list(user_activity.keys()):
            user_activity[user_id] = [t for t in user_activity[user_id] if now - t < 120]
            if not user_activity[user_id]:
                del user_activity[user_id]
        time.sleep(3600)

cleaner_thread = threading.Thread(target=clean_activity_data, daemon=True)
cleaner_thread.start()

# Web Routes
@app.route('/', methods=['GET'])
def home():
    return render_template_string(HOME_HTML, bot_username=BOT_USERNAME, privacy_policy_url='/privacy')

@app.route('/privacy', methods=['GET'])
def privacy_policy():
    return render_template_string(PRIVACY_HTML)

@app.route('/admin', methods=['GET'])
@login_required
def admin_panel():
    user_id = session.get('user_id')
    if user_id not in ADMIN_IDS:
        return "Access denied", 403
    return render_template_string(ADMIN_HTML, uploaded_files=uploaded_files, CHANNEL_USERNAME=CHANNEL_USERNAME)

@app.route('/delete_file/<int:msg_id>', methods=['POST'])
@login_required
def delete_file(msg_id):
    user_id = session.get('user_id')
    if user_id not in ADMIN_IDS:
        return jsonify({"status": "error", "message": "Access denied"}), 403
    if msg_id in uploaded_files and delete_message(CHANNEL_USERNAME, msg_id):
        del uploaded_files[msg_id]
        return jsonify({"status": "success", "message": "File deleted"}), 200
    return jsonify({"status": "error", "message": "File not found or deletion failed"}), 404

# HTML Templates as Strings
HOME_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Telegram File Uploader Bot</title>
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root { --primary-color: #4361ee; --secondary-color: #3f37c9; --accent-color: #4895ef; --dark-color: #2b2d42; --light-color: #f8f9fa; --success-color: #4cc9f0; --danger-color: #f72585; --warning-color: #f8961e; }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Poppins', sans-serif; line-height: 1.6; color: var(--dark-color); background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); min-height: 100vh; padding: 2rem; }
        .container { max-width: 1200px; margin: 0 auto; padding: 2rem; background-color: white; border-radius: 15px; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1); position: relative; overflow: hidden; }
        .container::before { content: ''; position: absolute; top: 0; left: 0; width: 100%; height: 10px; background: linear-gradient(90deg, var(--primary-color), var(--accent-color)); }
        header { text-align: center; margin-bottom: 3rem; }
        h1 { font-size: 2.5rem; color: var(--primary-color); margin-bottom: 1rem; font-weight: 700; }
        .subtitle { font-size: 1.2rem; color: var(--dark-color); opacity: 0.8; margin-bottom: 2rem; }
        .status-card, .feature-card { background-color: white; border-radius: 10px; padding: 2rem; margin-bottom: 2rem; box-shadow: 0 5px 15px rgba(0, 0, 0, 0.05); transition: transform 0.3s, box-shadow 0.3s; }
        .status-card:hover, .feature-card:hover { transform: translateY(-5px); box-shadow: 0 10px 25px rgba(0, 0, 0, 0.1); }
        .status-title { font-size: 1.5rem; color: var(--dark-color); margin-bottom: 1rem; display: flex; align-items: center; }
        .status-title::before { content: '✓'; width: 30px; height: 30px; background-color: var(--success-color); color: white; border-radius: 50%; text-align: center; line-height: 30px; margin-right: 10px; font-size: 1rem; }
        .features { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 2rem; margin-bottom: 3rem; }
        .feature-icon { font-size: 2.5rem; color: var(--primary-color); margin-bottom: 1rem; }
        .feature-title { font-size: 1.3rem; color: var(--dark-color); margin-bottom: 0.5rem; font-weight: 600; }
        .btn { display: inline-block; padding: 0.8rem 1.5rem; background: linear-gradient(135deg, var(--primary-color), var(--secondary-color)); color: white; text-decoration: none; border-radius: 50px; font-weight: 500; transition: all 0.3s; border: none; cursor: pointer; box-shadow: 0 5px 15px rgba(67, 97, 238, 0.3); margin: 0.5rem; }
        .btn:hover { transform: translateY(-3px); box-shadow: 0 8px 20px rgba(67, 97, 238, 0.4); color: white; }
        .btn-outline { background: transparent; border: 2px solid var(--primary-color); color: var(--primary-color); box-shadow: none; }
        .btn-outline:hover { background: linear-gradient(135deg, var(--primary-color), var(--secondary-color)); color: white; }
        .btn-group { display: flex; flex-wrap: wrap; justify-content: center; margin-top: 2rem; }
        footer { text-align: center; margin-top: 3rem; color: var(--dark-color); opacity: 0.7; font-size: 0.9rem; }
        @media (max-width: 768px) { .container { padding: 1.5rem; } h1 { font-size: 2rem; } .features { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Telegram File Uploader Bot</h1>
            <p class="subtitle">Easily upload and share files through Telegram</p>
        </header>
        
        <div class="status-card">
            <h2 class="status-title">Bot Status: Running</h2>
            <p>This is the webhook endpoint for the Telegram File Uploader Bot. The bot is currently online and ready to process your requests.</p>
        </div>
        
        <div class="features">
            <div class="feature-card">
                <div class="feature-icon">📤</div>
                <h3 class="feature-title">File Upload</h3>
                <p>Upload documents, photos, videos, and audio files directly to your Telegram channel with ease.</p>
            </div>
            
            <div class="feature-card">
                <div class="feature-icon">🔗</div>
                <h3 class="feature-title">Shareable Links</h3>
                <p>Get direct links to your uploaded files that you can share with anyone.</p>
            </div>
            
            <div class="feature-card">
                <div class="feature-icon">🛡️</div>
                <h3 class="feature-title">Secure & Private</h3>
                <p>Your files are securely stored and can be deleted anytime, with a strict privacy policy.</p>
            </div>
        </div>
        
        <div class="btn-group">
            <a href="https://t.me/{{ bot_username }}" class="btn">Start the Bot</a>
            <a href="/setwebhook" class="btn btn-outline">Set Webhook</a>
        </div>
        
        <footer>
            <p>© 2025 Telegram File Uploader Bot. All rights reserved. <a href="{{ privacy_policy_url }}">Privacy Policy</a></p>
        </footer>
    </div>
</body>
</html>
"""

PRIVACY_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Privacy Policy - Telegram File Uploader Bot</title>
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <style>
        :root {
            --primary-color: #1a73e8; /* Professional blue */
            --secondary-color: #f8faf9; /* Very light gray background */
            --text-color: #202124; /* Dark text for readability */
            --accent-color: #34a853; /* Green accent for trust */
            --error-color: #ea4335; /* Red for warnings */
            --success-color: #34a853; /* Green for positive actions */
            --border-color: #e0e0e0; /* Light border */
            --shadow-color: rgba(0, 0, 0, 0.1); /* Subtle shadow */
            --highlight-color: #fbbc05; /* Yellow for emphasis */
        }

        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Poppins', sans-serif;
            line-height: 1.6;
            color: var(--text-color);
            background: linear-gradient(135deg, #e3f2fd 0%, #f5f7fa 100%);
            padding: 2rem 0;
            overflow-x: hidden;
        }

        .container {
            max-width: 1000px;
            margin: 0 auto;
            background: white;
            padding: 4rem 5rem;
            border-radius: 25px;
            box-shadow: 0 15px 40px var(--shadow-color);
            border: 2px solid var(--border-color);
            position: relative;
            animation: fadeIn 1s ease-in;
        }

        @keyframes fadeIn {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }

        header {
            text-align: center;
            margin-bottom: 3rem;
            position: relative;
        }

        header::after {
            content: '';
            position: absolute;
            bottom: -15px;
            left: 50%;
            transform: translateX(-50%);
            width: 50px;
            height: 3px;
            background: var(--primary-color);
            border-radius: 2px;
        }

        h1 {
            color: var(--primary-color);
            font-size: 3rem;
            font-weight: 700;
            margin-bottom: 1.5rem;
            letter-spacing: -0.5px;
            text-shadow: 0 2px 5px rgba(0, 0, 0, 0.1);
        }

        h2 {
            color: var(--primary-color);
            font-size: 1.8rem;
            font-weight: 600;
            margin: 2.5rem 0 1.5rem;
            border-bottom: 3px solid var(--border-color);
            padding-bottom: 0.7rem;
            transition: transform 0.3s ease;
        }

        h2:hover {
            transform: translateX(10px);
        }

        p {
            margin-bottom: 1.5rem;
            color: #333;
            font-size: 1.1rem;
            line-height: 1.8;
            opacity: 0.95;
            transition: opacity 0.3s ease;
        }

        p:hover {
            opacity: 1;
        }

        a {
            color: var(--primary-color);
            text-decoration: none;
            transition: color 0.3s ease, text-decoration 0.3s ease;
            position: relative;
        }

        a::after {
            content: '';
            position: absolute;
            width: 0;
            height: 2px;
            bottom: -4px;
            left: 0;
            background: var(--accent-color);
            transition: width 0.3s ease;
        }

        a:hover::after {
            width: 100%;
            text-decoration: underline;
        }

        .policy-section {
            background: var(--secondary-color);
            padding: 2rem;
            border-radius: 15px;
            margin-bottom: 2.5rem;
            border-left: 5px solid var(--primary-color);
            box-shadow: 0 5px 15px var(--shadow-color);
            animation: slideUp 0.8s ease-out;
        }

        @keyframes slideUp {
            from { opacity: 0; transform: translateY(20px); }
            to { opacity: 1; transform: translateY(0); }
        }

        .icon {
            margin-right: 10px;
            color: var(--primary-color);
        }

        .key-points {
            background: white;
            border: 1px solid var(--border-color);
            border-radius: 15px;
            padding: 1.5rem;
            margin: 2rem 0;
            box-shadow: 0 5px 15px var(--shadow-color);
        }

        .key-points table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 1rem;
        }

        .key-points th, .key-points td {
            padding: 1rem;
            text-align: left;
            border-bottom: 1px solid var(--border-color);
        }

        .key-points th {
            background: var(--primary-color);
            color: white;
            font-weight: 600;
        }

        .key-points td {
            background: var(--secondary-color);
        }

        .back-btn {
            display: inline-block;
            padding: 1rem 2rem;
            background: linear-gradient(45deg, var(--primary-color), var(--accent-color));
            color: white;
            border-radius: 50px;
            text-decoration: none;
            font-weight: 600;
            transition: all 0.4s ease;
            box-shadow: 0 8px 25px var(--shadow-color);
            margin-top: 2.5rem;
            position: relative;
            overflow: hidden;
        }

        .back-btn:hover {
            transform: translateY(-3px);
            box-shadow: 0 12px 35px var(--shadow-color);
            background: linear-gradient(45deg, var(--accent-color), var(--primary-color));
        }

        .back-btn::before {
            content: '';
            position: absolute;
            top: 50%;
            left: 50%;
            width: 0;
            height: 0;
            background: rgba(255, 255, 255, 0.2);
            border-radius: 50%;
            transform: translate(-50%, -50%);
            transition: width 0.6s, height 0.6s;
        }

        .back-btn:hover::before {
            width: 300px;
            height: 300px;
        }

        @media (max-width: 768px) {
            .container {
                padding: 2.5rem 2rem;
                margin: 0 1rem;
            }

            h1 {
                font-size: 2.2rem;
            }

            h2 {
                font-size: 1.4rem;
            }

            p {
                font-size: 1rem;
            }

            .policy-section, .key-points {
                padding: 1.5rem;
            }

            .back-btn {
                padding: 0.8rem 1.5rem;
                font-size: 0.9rem;
            }

            .key-points table {
                font-size: 0.9rem;
            }
        }

        @media (max-width: 480px) {
            .container {
                padding: 2rem 1.5rem;
            }

            h1 {
                font-size: 1.8rem;
            }

            h2 {
                font-size: 1.2rem;
            }

            .policy-section {
                padding: 1rem;
            }

            .key-points {
                padding: 1rem;
            }

            .back-btn {
                padding: 0.7rem 1.2rem;
            }
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Privacy Policy</h1>
            <p>Ensuring your privacy and data security is our top priority. Read on to understand how we protect your information.</p>
        </header>

        <div class="policy-section">
            <p><i class="fas fa-lock icon"></i> At Telegram File Uploader Bot, we are dedicated to safeguarding your privacy. This comprehensive Privacy Policy outlines how we collect, use, store, and protect your personal data when you interact with our bot and website, ensuring transparency and trust.</p>
        </div>

        <h2>1. Data We Collect</h2>
        <p><i class="fas fa-database icon"></i> We only collect essential information to provide our services effectively:</p>
        <ul style="list-style-type: none; padding-left: 1rem; margin-bottom: 1.5rem;">
            <li><i class="fas fa-check-circle" style="color: var(--success-color); margin-right: 10px;"></i> Telegram ID and Username</li>
            <li><i class="fas fa-check-circle" style="color: var(--success-color); margin-right: 10px;"></i> File Metadata (type, size, upload time)</li>
        </ul>

        <h2>2. How We Use Your Data</h2>
        <p><i class="fas fa-cog icon"></i> Your data is used solely for the following purposes:</p>
        <ul style="list-style-type: none; padding-left: 1rem; margin-bottom: 1.5rem;">
            <li><i class="fas fa-upload" style="color: var(--accent-color); margin-right: 10px;"></i> Facilitating file uploads to our Telegram channel</li>
            <li><i class="fas fa-link" style="color: var(--accent-color); margin-right: 10px;"></i> Generating and sharing direct links to your files</li>
            <li><i class="fas fa-user-shield" style="color: var(--accent-color); margin-right: 10px;"></i> Managing your interactions and ensuring service functionality</li>
        </ul>
        <p>We never share your data with third parties except as required by law or with your explicit consent.</p>

        <h2>3. Data Storage and Retention</h2>
        <p><i class="fas fa-server icon"></i> Your data is stored securely and temporarily:</p>
        <ul style="list-style-type: none; padding-left: 1rem; margin-bottom: 1.5rem;">
            <li><i class="fas fa-clock" style="color: var(--highlight-color); margin-right: 10px;"></i> Files and metadata are retained for up to 30 days or until you request deletion</li>
            <li><i class="fas fa-trash" style="color: var(--error-color); margin-right: 10px;"></i> You can delete your files anytime via the bot or by contacting us</li>
        </ul>

        <div class="key-points">
            <h3>Key Privacy Points at a Glance</h3>
            <table>
                <thead>
                    <tr>
                        <th>Aspect</th>
                        <th>Details</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td>Data Collection</td>
                        <td>Minimal and necessary for service functionality</td>
                    </tr>
                    <tr>
                        <td>Data Sharing</td>
                        <td>Never shared unless legally required</td>
                    </tr>
                    <tr>
                        <td>Retention Period</td>
                        <td>Up to 30 days or until deletion</td>
                    </tr>
                    <tr>
                        <td>Your Rights</td>
                        <td>Access, correct, or delete your data anytime</td>
                    </tr>
                    <tr>
                        <td>Security Measures</td>
                        <td>Advanced encryption and secure storage</td>
                    </tr>
                </tbody>
            </table>
        </div>

        <h2>4. Your Rights</h2>
        <p><i class="fas fa-user-check icon"></i> You have full control over your data:</p>
        <ul style="list-style-type: none; padding-left: 1rem; margin-bottom: 1.5rem;">
            <li><i class="fas fa-eye" style="color: var(--success-color); margin-right: 10px;"></i> Right to access your stored data</li>
            <li><i class="fas fa-edit" style="color: var(--success-color); margin-right: 10px;"></i> Right to correct inaccuracies</li>
            <li><i class="fas fa-trash-alt" style="color: var(--success-color); margin-right: 10px;"></i> Right to request deletion at any time</li>
        </ul>
        <p>For assistance, contact our admin at <a href="https://t.me/MAXWARORG">@MAXWARORG</a>.</p>

        <h2>5. Security Measures</h2>
        <p><i class="fas fa-shield-alt icon"></i> We prioritize your data security with:</p>
        <ul style="list-style-type: none; padding-left: 1rem; margin-bottom: 1.5rem;">
            <li><i class="fas fa-lock" style="color: var(--success-color); margin-right: 10px;"></i> End-to-end encryption for data transmission</li>
            <li><i class="fas fa-server" style="color: var(--success-color); margin-right: 10px;"></i> Secure server infrastructure</li>
            <li><i class="fas fa-user-secret" style="color: var(--success-color); margin-right: 10px;"></i> Regular security audits and updates</li>
        </ul>
        <p>While we strive for maximum security, no system is entirely immune to all risks.</p>

        <h2>6. Third-Party Services</h2>
        <p><i class="fas fa-handshake icon"></i> Our services rely on Telegram’s API and infrastructure. Their <a href="https://telegram.org/privacy" target="_blank">Privacy Policy</a> also applies to data processed through their platform. We ensure compliance with their standards and ours.</p>

        <h2>7. Policy Updates</h2>
        <p><i class="fas fa-refresh icon"></i> We may update this policy to reflect changes in our practices or legal requirements. Any updates will be posted here, and we recommend reviewing this page periodically. You’ll be notified of significant changes via the bot or website.</p>

        <h2>8. Contact Us</h2>
        <p><i class="fas fa-envelope icon"></i> If you have questions, concerns, or need assistance regarding your privacy, please reach out:</p>
        <ul style="list-style-type: none; padding-left: 1rem; margin-bottom: 1.5rem;">
            <li><i class="fas fa-telegram" style="color: var(--highlight-color); margin-right: 10px;"></i> Telegram: <a href="https://t.me/MAXWARORG">@MAXWARORG</a></li>
            <li><i class="fas fa-globe" style="color: var(--highlight-color); margin-right: 10px;"></i> Website: <a href="/">Telegram File Uploader Bot</a></li>
            <li><i class="fas fa-phone-alt" style="color: var(--highlight-color); margin-right: 10px;"></i> Email: hojievmakhmud@gmail.com</li>
        </ul>

        <a href="/" class="back-btn">Return to Home</a>
    </div>

    <script>
        document.addEventListener('DOMContentLoaded', () => {
            const headers = document.querySelectorAll('h2');
            headers.forEach(header => {
                header.addEventListener('mouseover', () => {
                    header.style.color = var(--accent-color);
                });
                header.addEventListener('mouseout', () => {
                    header.style.color = var(--primary-color);
                });
            });
        });
    </script>
</body>
</html>
"""

ADMIN_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Admin Panel</title>
    <link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        body { background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); font-family: 'Poppins', sans-serif; padding: 2rem; }
        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 2rem; border-radius: 15px; box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1); }
        h1 { color: #4361ee; margin-bottom: 1rem; }
        .file-list { margin-top: 2rem; }
        .file-item { border-bottom: 1px solid #eee; padding: 1rem 0; }
        .btn { display: inline-block; padding: 0.8rem 1.5rem; background: #4361ee; color: white; border-radius: 50px; text-decoration: none; margin: 0.5rem; }
        .btn:hover { background: #3f37c9; }
    </style>
</head>
<body>
    <div class="container">
        <h1>Admin Panel</h1>
        <p>Manage uploaded files, users, and bot settings.</p>

        <div class="file-list">
            <h2>Uploaded Files</h2>
            {% for msg_id, file_data in uploaded_files.items() %}
                <div class="file-item">
                    <p><strong>File Type:</strong> {{ file_data.file_type|capitalize }}</p>
                    <p><strong>Uploaded By:</strong> User ID {{ file_data.user_id }}</p>
                    <p><strong>Size:</strong> {{ file_data.file_size }} MB</p>
                    <p><strong>Uploaded At:</strong> {{ datetime.fromtimestamp(file_data.timestamp).strftime('%Y-%m-%d %H:%M:%S') }}</p>
                    <a href="https://t.me/{{ CHANNEL_USERNAME[1:] }}/{{ msg_id }}" class="btn">View File</a>
                    <a href="#" class="btn" onclick="deleteFile({{ msg_id }})">Delete</a>
                </div>
            {% endfor %}
        </div>

        <a href="/" class="btn">Back to Home</a>
    </div>

    <script>
        function deleteFile(msg_id) {
            if (confirm("Are you sure you want to delete this file?")) {
                fetch('/delete_file/' + msg_id, { method: 'POST' })
                    .then(response => response.json())
                    .then(data => {
                        if (data.status === 'success') {
                            alert('File deleted successfully!');
                            location.reload();
                        } else {
                            alert('Failed to delete file.');
                        }
                    });
            }
        }
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
