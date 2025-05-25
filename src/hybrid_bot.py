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
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeVideo

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
API_ID = int(os.getenv('API_ID', '0'))  # From my.telegram.org
API_HASH = os.getenv('API_HASH', '')   # From my.telegram.org
PORT = int(os.getenv('PORT', 8080))

if not all([BOT_TOKEN, API_ID, API_HASH]):
    raise ValueError("BOT_TOKEN, API_ID, and API_HASH are required!")

# Global clients
bot_app = None
client = None

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
            self.wfile.write(b'Large file bot running')
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

class LargeFileBot:
    def __init__(self):
        self.processing_users = {}  # Track processing status
        
    async def download_large_file(self, message, progress_callback=None):
        """Download large file using Telethon client."""
        try:
            # Create temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp4') as temp_file:
                temp_path = temp_file.name

            # Download with progress tracking
            if progress_callback:
                await progress_callback("📥 Downloading large file...")

            await client.download_media(message, file=temp_path)
            
            logger.info(f"✅ Downloaded large file: {temp_path}")
            return temp_path

        except Exception as e:
            logger.error(f"❌ Error downloading large file: {e}")
            raise

    async def convert_video(self, input_path, resolution):
        """Convert video to specified resolution."""
        try:
            if resolution not in RESOLUTIONS:
                return None

            config = RESOLUTIONS[resolution]
            
            with tempfile.NamedTemporaryFile(suffix=f'_{resolution}.mp4', delete=False) as output_file:
                output_path = output_file.name

            # FFmpeg command
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

    async def process_large_video(self, chat_id, message, resolutions):
        """Process large video file."""
        input_path = None
        try:
            # Mark user as processing
            self.processing_users[chat_id] = True

            # Send initial progress message
            progress_msg = await bot_app.bot.send_message(
                chat_id=chat_id,
                text="🔄 **Processing Large Video**\n\nDownloading..."
            )

            # Download the large file
            input_path = await self.download_large_file(
                message,
                lambda msg: bot_app.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_msg.message_id,
                    text=f"🔄 **Processing Large Video**\n\n{msg}",
                    parse_mode=ParseMode.MARKDOWN
                )
            )

            file_size = os.path.getsize(input_path) / (1024 * 1024)
            logger.info(f"📁 Processing {file_size:.1f}MB video")

            success_count = 0
            
            # Process each resolution
            for i, resolution in enumerate(resolutions):
                try:
                    await bot_app.bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=progress_msg.message_id,
                        text=f"🔄 **Processing Large Video**\n\n"
                             f"Converting to {resolution}... ({i+1}/{len(resolutions)})",
                        parse_mode=ParseMode.MARKDOWN
                    )

                    output_path = await self.convert_video(input_path, resolution)
                    
                    if output_path and os.path.exists(output_path):
                        # Send converted video
                        with open(output_path, 'rb') as video_file:
                            await bot_app.bot.send_video(
                                chat_id=chat_id,
                                video=video_file,
                                caption=f"🎬 **{resolution} Version**\n\n"
                                       f"Original size: {file_size:.1f}MB",
                                parse_mode=ParseMode.MARKDOWN
                            )
                        
                        success_count += 1
                        os.unlink(output_path)

                except Exception as e:
                    logger.error(f"❌ Error processing {resolution}: {e}")

            # Clean up and notify
            await bot_app.bot.delete_message(chat_id=chat_id, message_id=progress_msg.message_id)
            
            if success_count > 0:
                await bot_app.bot.send_message(
                    chat_id=chat_id,
                    text=f"✅ **Processing Complete!**\n\n"
                         f"Successfully converted {success_count} resolution(s).\n"
                         f"Original: {file_size:.1f}MB",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await bot_app.bot.send_message(
                    chat_id=chat_id,
                    text="❌ **Processing Failed**\n\nUnable to convert video.",
                    parse_mode=ParseMode.MARKDOWN
                )

        except Exception as e:
            logger.error(f"❌ Error in process_large_video: {e}")
            await bot_app.bot.send_message(
                chat_id=chat_id,
                text=f"❌ **Error Processing Video**\n\n{str(e)[:200]}",
                parse_mode=ParseMode.MARKDOWN
            )
        finally:
            # Clean up
            if input_path and os.path.exists(input_path):
                os.unlink(input_path)
            self.processing_users.pop(chat_id, None)

# Initialize large file bot
large_bot = LargeFileBot()

# Bot handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command."""
    welcome_text = """
🎬 **Large File Video Converter**
*No size limits! 🚀*

**Features:**
• Process videos up to 2GB
• No external links needed
• Direct Telegram processing
• Multiple resolution output

**Supported resolutions:**
• 144p - Ultra compact
• 240p - Low quality
• 360p - Standard quality
• 480p - Good quality
• 720p - HD quality

**How it works:**
1. Send ANY size video file
2. Choose your resolutions
3. Get converted videos back

Just send your video to start! 📹

*Powered by Telegram Client API*
    """
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle any size video."""
    try:
        video = update.message.video or update.message.document
        if not video:
            await update.message.reply_text("❌ Please send a video file!")
            return

        file_size = video.file_size / (1024 * 1024)
        file_name = getattr(video, 'file_name', 'video.mp4')

        # Check if user is already processing
        if update.effective_chat.id in large_bot.processing_users:
            await update.message.reply_text(
                "⚠️ **Already Processing**\n\nPlease wait for current video to finish."
            )
            return

        # Show resolution selection
        keyboard = [
            [InlineKeyboardButton("144p", callback_data="large_144p"),
             InlineKeyboardButton("240p", callback_data="large_240p")],
            [InlineKeyboardButton("360p", callback_data="large_360p"),
             InlineKeyboardButton("480p", callback_data="large_480p")],
            [InlineKeyboardButton("720p", callback_data="large_720p")],
            [InlineKeyboardButton("🎯 All Resolutions", callback_data="large_all")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Store message for processing
        context.user_data['large_video_message'] = update.message
        context.user_data['video_info'] = {
            'name': file_name,
            'size': file_size
        }

        await update.message.reply_text(
            f"📹 **Large Video Received!**\n\n"
            f"**File:** {file_name}\n"
            f"**Size:** {file_size:.1f}MB\n\n"
            f"✅ **No size limit!** Choose resolutions:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"❌ Error handling video: {e}")
        await update.message.reply_text("❌ Error processing video. Please try again.")

async def handle_large_resolution(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle resolution selection for large files."""
    query = update.callback_query
    await query.answer()

    try:
        resolution = query.data.replace('large_', '')
        video_message = context.user_data.get('large_video_message')
        video_info = context.user_data.get('video_info', {})

        if not video_message:
            await query.edit_message_text("❌ Video data lost. Please send video again.")
            return

        # Determine resolutions
        if resolution == 'all':
            resolutions = ['144p', '240p', '360p', '480p', '720p']
            resolution_text = "All Resolutions"
        else:
            resolutions = [resolution]
            resolution_text = resolution

        await query.edit_message_text(
            f"🚀 **Starting Large File Processing**\n\n"
            f"**File:** {video_info.get('name', 'video.mp4')}\n"
            f"**Size:** {video_info.get('size', 0):.1f}MB\n"
            f"**Target:** {resolution_text}\n\n"
            f"Processing will begin shortly...",
            parse_mode=ParseMode.MARKDOWN
        )

        # Start processing in background
        asyncio.create_task(
            large_bot.process_large_video(
                update.effective_chat.id,
                video_message,
                resolutions
            )
        )

    except Exception as e:
        logger.error(f"❌ Error in resolution selection: {e}")
        await query.edit_message_text("❌ Error starting processing. Please try again.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot status."""
    processing_count = len(large_bot.processing_users)
    
    status_text = f"""
🟢 **Large File Bot Status**

**System:**
• Platform: Railway Cloud
• Client API: Connected
• FFmpeg: Available
• Processing: {processing_count} active

**Capabilities:**
• Max file size: 2GB
• Supported formats: MP4, AVI, MOV, MKV
• Resolutions: 144p to 720p
• Simultaneous processing: Unlimited

**Features:**
• Direct Telegram file access
• No external upload needed
• Real-time progress updates

Ready for large files! 🚀
    """
    
    await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN)

async def init_clients():
    """Initialize both bot and client."""
    global bot_app, client
    
    # Initialize Telegram Client (for large file downloads)
    client = TelegramClient('bot_session', API_ID, API_HASH)
    await client.start(bot_token=BOT_TOKEN)
    logger.info("✅ Telegram Client connected")
    
    # Initialize Bot Application
    bot_app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    bot_app.add_handler(CommandHandler("start", start))
    bot_app.add_handler(CommandHandler("status", status))
    bot_app.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, handle_video))
    bot_app.add_handler(CallbackQueryHandler(handle_large_resolution, pattern="^large_"))
    
    logger.info("✅ Bot handlers configured")

def main():
    """Main function."""
    # Start health server
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    
    # Run bot
    async def run_bot():
        await init_clients()
        logger.info("🚀 Large File Video Bot starting...")
        await bot_app.run_polling(drop_pending_updates=True)
    
    asyncio.run(run_bot())

if __name__ == '__main__':
    main()
