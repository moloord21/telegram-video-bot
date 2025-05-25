import logging
import os
import asyncio
import subprocess
import tempfile
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes
from telegram.constants import ParseMode
from telethon import TelegramClient

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = int(os.getenv('API_ID', '0'))
API_HASH = os.getenv('API_HASH', '')
PORT = int(os.getenv('PORT', 8080))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is required!")

# Use dummy values if API credentials not provided (fallback to regular bot)
USE_CLIENT_API = API_ID and API_HASH

# Global variables
client = None
processing_users = set()

# Resolution configurations
RESOLUTIONS = {
    '144p': {'size': '256x144', 'bitrate': '200k', 'crf': '28'},
    '240p': {'size': '426x240', 'bitrate': '400k', 'crf': '26'},
    '360p': {'size': '640x360', 'bitrate': '800k', 'crf': '24'},
    '480p': {'size': '854x480', 'bitrate': '1500k', 'crf': '22'},
    '720p': {'size': '1280x720', 'bitrate': '2500k', 'crf': '20'}
}

class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            mode = "Large File Mode" if USE_CLIENT_API else "Standard Mode"
            self.wfile.write(f'Video Bot Running - {mode}'.encode())
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        return

def start_health_server():
    try:
        server = HTTPServer(('0.0.0.0', PORT), HealthCheckHandler)
        logger.info(f"✅ Health server started on port {PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"❌ Health server error: {e}")

async def download_large_file(message):
    """Download large file using Telethon client."""
    if not USE_CLIENT_API or not client:
        raise ValueError("Large file support not available")
    
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as temp_file:
            temp_path = temp_file.name

        await client.download_media(message, file=temp_path)
        logger.info(f"✅ Downloaded large file: {temp_path}")
        return temp_path

    except Exception as e:
        logger.error(f"❌ Error downloading large file: {e}")
        raise

async def convert_video(input_path, resolution):
    """Convert video to specified resolution."""
    try:
        if resolution not in RESOLUTIONS:
            return None

        config = RESOLUTIONS[resolution]
        
        with tempfile.NamedTemporaryFile(suffix=f'_{resolution}.mp4', delete=False) as output_file:
            output_path = output_file.name

        cmd = [
            'ffmpeg', '-i', input_path,
            '-vf', f"scale={config['size']}:flags=lanczos",
            '-c:v', 'libx264', '-preset', 'fast',
            '-crf', config['crf'],
            '-maxrate', config['bitrate'],
            '-bufsize', str(int(config['bitrate'].rstrip('k')) * 2) + 'k',
            '-c:a', 'aac', '-b:a', '128k',
            '-movflags', '+faststart',
            '-y', output_path
        ]

        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)

        if process.returncode == 0:
            logger.info(f"✅ Converted to {resolution}")
            return output_path
        else:
            logger.error(f"❌ FFmpeg error: {stderr.decode()[:200]}")
            if os.path.exists(output_path):
                os.unlink(output_path)
            return None

    except Exception as e:
        logger.error(f"❌ Conversion error: {e}")
        if 'output_path' in locals() and os.path.exists(output_path):
            os.unlink(output_path)
        return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command."""
    mode_info = "🚀 **Large File Support**" if USE_CLIENT_API else "⚠️ **Standard Mode**"
    max_size = "2GB" if USE_CLIENT_API else "50MB"
    
    welcome_text = f"""
🎬 **Video Converter Bot**
{mode_info}

**Features:**
• Process videos up to {max_size}
• Multiple resolution output
• Fast cloud processing

**Supported resolutions:**
• 144p - Ultra compact
• 240p - Low quality
• 360p - Standard quality
• 480p - Good quality
• 720p - HD quality

**How to use:**
1. Send your video file
2. Choose target resolution(s)
3. Get converted videos back

Just send your video to start! 📹

*Running on Railway Cloud*
    """
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle video files."""
    try:
        video = update.message.video or update.message.document
        if not video:
            await update.message.reply_text("❌ Please send a video file!")
            return

        user_id = update.effective_chat.id
        if user_id in processing_users:
            await update.message.reply_text(
                "⚠️ **Already Processing**\n\nPlease wait for current video to finish."
            )
            return

        file_size = video.file_size / (1024 * 1024)
        file_name = getattr(video, 'file_name', 'video.mp4')

        # Check file size limits
        telegram_limit = 50
        if file_size > telegram_limit and not USE_CLIENT_API:
            await update.message.reply_text(
                f"❌ **File too large for standard mode**\n\n"
                f"Your file: {file_size:.1f}MB\n"
                f"Limit: {telegram_limit}MB\n\n"
                f"To enable large file support, the bot needs API credentials.\n"
                f"Contact the bot owner for large file processing.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Show resolution selection
        keyboard = [
            [InlineKeyboardButton("144p", callback_data="res_144p"),
             InlineKeyboardButton("240p", callback_data="res_240p")],
            [InlineKeyboardButton("360p", callback_data="res_360p"),
             InlineKeyboardButton("480p", callback_data="res_480p")],
            [InlineKeyboardButton("720p", callback_data="res_720p")],
            [InlineKeyboardButton("🎯 All Resolutions", callback_data="res_all")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Store video data
        context.user_data['video_message'] = update.message
        context.user_data['video_info'] = {
            'name': file_name,
            'size': file_size,
            'file_id': video.file_id,
            'is_large': file_size > telegram_limit
        }

        status_emoji = "🚀" if file_size > telegram_limit else "📹"
        mode_text = "Large File" if file_size > telegram_limit else "Standard"

        await update.message.reply_text(
            f"{status_emoji} **Video Received!**\n\n"
            f"**File:** {file_name}\n"
            f"**Size:** {file_size:.1f}MB\n"
            f"**Mode:** {mode_text}\n\n"
            f"Choose your target resolution(s):",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"❌ Error handling video: {e}")
        await update.message.reply_text("❌ Error processing video. Please try again.")

async def handle_resolution(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle resolution selection."""
    query = update.callback_query
    await query.answer()

    try:
        resolution = query.data.replace('res_', '')
        video_info = context.user_data.get('video_info', {})
        video_message = context.user_data.get('video_message')

        if not video_message:
            await query.edit_message_text("❌ Video data lost. Please send video again.")
            return

        user_id = update.effective_chat.id
        processing_users.add(user_id)

        # Determine resolutions
        if resolution == 'all':
            resolutions = ['144p', '240p', '360p', '480p', '720p']
            resolution_text = "All Resolutions"
        else:
            resolutions = [resolution]
            resolution_text = resolution

        await query.edit_message_text(
            f"🔄 **Processing Video**\n\n"
            f"**File:** {video_info.get('name', 'video.mp4')}\n"
            f"**Size:** {video_info.get('size', 0):.1f}MB\n"
            f"**Target:** {resolution_text}\n\n"
            f"Starting conversion...",
            parse_mode=ParseMode.MARKDOWN
        )

        # Process video
        success_count = await process_video(
            user_id, context.bot, video_message, video_info, resolutions
        )

        if success_count > 0:
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ **Processing Complete!**\n\n"
                     f"Successfully converted {success_count} resolution(s).\n\n"
                     f"Send another video or use /start for help!",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text="❌ **Processing Failed**\n\nUnable to convert video. Please try again.",
                parse_mode=ParseMode.MARKDOWN
            )

    except Exception as e:
        logger.error(f"❌ Error in resolution selection: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Error processing request. Please try again."
        )
    finally:
        processing_users.discard(update.effective_chat.id)

async def process_video(chat_id, bot, video_message, video_info, resolutions):
    """Process video with appropriate method."""
    input_path = None
    try:
        # Send progress message
        progress_msg = await bot.send_message(
            chat_id=chat_id,
            text="📥 Downloading video..."
        )

        # Download video
        if video_info.get('is_large', False) and USE_CLIENT_API:
            # Use client API for large files
            input_path = await download_large_file(video_message)
        else:
            # Use bot API for standard files
            file = await bot.get_file(video_info['file_id'])
            with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_file:
                await file.download_to_drive(temp_file.name)
                input_path = temp_file.name

        success_count = 0

        # Process each resolution
        for i, resolution in enumerate(resolutions):
            try:
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_msg.message_id,
                    text=f"🔄 Converting to {resolution}... ({i+1}/{len(resolutions)})"
                )

                output_path = await convert_video(input_path, resolution)
                
                if output_path and os.path.exists(output_path):
                    with open(output_path, 'rb') as video_file:
                        await bot.send_video(
                            chat_id=chat_id,
                            video=video_file,
                            caption=f"🎬 **{resolution} Version**\n\n"
                                   f"Original: {video_info.get('name', 'video.mp4')}",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    
                    success_count += 1
                    os.unlink(output_path)

            except Exception as e:
                logger.error(f"❌ Error processing {resolution}: {e}")

        await bot.delete_message(chat_id=chat_id, message_id=progress_msg.message_id)
        return success_count

    except Exception as e:
        logger.error(f"❌ Error in process_video: {e}")
        return 0
    finally:
        if input_path and os.path.exists(input_path):
            os.unlink(input_path)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot status."""
    mode = "Large File Mode (2GB)" if USE_CLIENT_API else "Standard Mode (50MB)"
    processing_count = len(processing_users)
    
    status_text = f"""
🟢 **Bot Status: Online**

**Mode:** {mode}
**Processing:** {processing_count} active
**Platform:** Railway Cloud
**FFmpeg:** Available

**Capabilities:**
• Supported formats: MP4, AVI, MOV, MKV
• Resolutions: 144p to 720p
• Real-time progress updates

Ready to process videos! 🚀
    """
    
    await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN)

def main():
    """Main function with fixed event loop handling."""
    global client
    
    # Start health server in thread
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    
    async def run_app():
        global client
        
        # Initialize client if credentials available
        if USE_CLIENT_API:
            try:
                client = TelegramClient('bot_session', API_ID, API_HASH)
                await client.start(bot_token=BOT_TOKEN)
                logger.info("✅ Telegram Client connected (Large file support enabled)")
            except Exception as e:
                logger.error(f"❌ Client initialization failed: {e}")
                logger.info("Falling back to standard mode")
                client = None
        else:
            logger.info("⚠️ API credentials not provided - Standard mode only")

        # Create bot application
        application = Application.builder().token(BOT_TOKEN).build()

        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("status", status))
        application.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))
        application.add_handler(CallbackQueryHandler(handle_resolution, pattern="^res_"))

        logger.info("🚀 Video Bot starting...")
        
        try:
            # Run bot
            await application.run_polling(drop_pending_updates=True)
        finally:
            if client:
                await client.disconnect()

    # Run the application
    asyncio.run(run_app())

if __name__ == '__main__':
    main()
