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

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Bot configuration
BOT_TOKEN = os.getenv('BOT_TOKEN')
PORT = int(os.getenv('PORT', 8080))

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required!")

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
            self.wfile.write(b'Bot is running')
        else:
            self.send_response(404)
            self.end_headers()
    
    def log_message(self, format, *args):
        return  # Suppress default logging

def start_health_server():
    """Start health check server for Railway."""
    try:
        server = HTTPServer(('0.0.0.0', PORT), HealthCheckHandler)
        logger.info(f"Health check server started on port {PORT}")
        server.serve_forever()
    except Exception as e:
        logger.error(f"Health server error: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler."""
    welcome_text = """
🎬 **Telegram Video Converter Bot**

Send me a video and I'll convert it to your preferred resolution!

**Available resolutions:**
• 144p (256x144) - Ultra compact
• 240p (426x240) - Low quality
• 360p (640x360) - Standard quality
• 480p (854x480) - Good quality
• 720p (1280x720) - HD quality

**Limits:**
• Maximum file size: 50MB
• Supported formats: MP4, AVI, MOV, MKV

Send your video to get started! 🚀

*Powered by Railway Cloud ☁️*
    """
    await update.message.reply_text(welcome_text, parse_mode=ParseMode.MARKDOWN)

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming videos."""
    try:
        # Get video file
        video = update.message.video or update.message.document
        if not video:
            await update.message.reply_text("❌ Please send a video file!")
            return

        # Check file size (50MB limit)
        max_size = 50 * 1024 * 1024
        if video.file_size > max_size:
            await update.message.reply_text(
                f"❌ **File too large!**\n\n"
                f"Your file: {video.file_size/(1024*1024):.1f}MB\n"
                f"Maximum: 50MB\n\n"
                f"Please compress your video or use a shorter clip.",
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Create resolution selection keyboard
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
        context.user_data['video_file_id'] = video.file_id
        context.user_data['video_size'] = video.file_size
        file_name = getattr(video, 'file_name', 'video.mp4')
        context.user_data['video_name'] = file_name

        await update.message.reply_text(
            f"📹 **Video Received!**\n\n"
            f"**File:** {file_name}\n"
            f"**Size:** {video.file_size/(1024*1024):.1f}MB\n\n"
            f"Choose which resolution(s) you want:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=reply_markup
        )

    except Exception as e:
        logger.error(f"Error handling video: {e}")
        await update.message.reply_text("❌ Error processing video. Please try again.")

async def handle_resolution(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle resolution selection."""
    query = update.callback_query
    await query.answer()

    try:
        resolution = query.data.replace('res_', '')
        video_file_id = context.user_data.get('video_file_id')
        video_name = context.user_data.get('video_name', 'video.mp4')

        if not video_file_id:
            await query.edit_message_text("❌ Video not found. Please send the video again.")
            return

        # Determine resolutions to process
        if resolution == 'all':
            resolutions = ['144p', '240p', '360p', '480p', '720p']
            resolution_text = "All Resolutions"
        else:
            resolutions = [resolution]
            resolution_text = resolution

        # Update message to show processing
        await query.edit_message_text(
            f"🔄 **Processing Video**\n\n"
            f"**File:** {video_name}\n"
            f"**Converting to:** {resolution_text}\n\n"
            f"This may take a few minutes... Please wait ⏳",
            parse_mode=ParseMode.MARKDOWN
        )

        # Process video
        success_count = await process_video_resolutions(
            update.effective_chat.id,
            context.bot,
            video_file_id,
            resolutions,
            video_name
        )

        if success_count > 0:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ **Processing Complete!**\n\n"
                     f"Successfully converted {success_count} resolution(s).",
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ **Processing Failed**\n\n"
                     "Unable to convert video. Please try with a different file.",
                parse_mode=ParseMode.MARKDOWN
            )

    except Exception as e:
        logger.error(f"Error in resolution selection: {e}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Error processing your request. Please try again."
        )

async def process_video_resolutions(chat_id, bot, file_id, resolutions, filename):
    """Process video for multiple resolutions."""
    success_count = 0
    
    try:
        # Download video
        progress_msg = await bot.send_message(
            chat_id=chat_id,
            text="📥 Downloading video..."
        )

        file = await bot.get_file(file_id)
        
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as input_file:
            await file.download_to_drive(input_file.name)
            input_path = input_file.name

        logger.info(f"Downloaded video: {input_path}")

        # Process each resolution
        for i, resolution in enumerate(resolutions):
            try:
                # Update progress
                await bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=progress_msg.message_id,
                    text=f"🔄 Converting to {resolution}... ({i+1}/{len(resolutions)})"
                )

                # Convert video
                output_path = await convert_video(input_path, resolution)
                
                if output_path and os.path.exists(output_path):
                    # Send converted video
                    with open(output_path, 'rb') as video_file:
                        await bot.send_video(
                            chat_id=chat_id,
                            video=video_file,
                            caption=f"🎬 **{resolution} Version**\n\nOriginal: {filename}",
                            parse_mode=ParseMode.MARKDOWN
                        )
                    
                    success_count += 1
                    logger.info(f"Successfully converted to {resolution}")
                    
                    # Clean up output file
                    os.unlink(output_path)
                else:
                    logger.error(f"Failed to convert to {resolution}")

            except Exception as e:
                logger.error(f"Error converting to {resolution}: {e}")

        # Clean up input file
        os.unlink(input_path)
        
        # Delete progress message
        await bot.delete_message(chat_id=chat_id, message_id=progress_msg.message_id)
        
        return success_count

    except Exception as e:
        logger.error(f"Error in process_video_resolutions: {e}")
        return 0

async def convert_video(input_path, resolution):
    """Convert video to specified resolution."""
    try:
        if resolution not in RESOLUTIONS:
            return None

        config = RESOLUTIONS[resolution]
        
        # Create output file
        with tempfile.NamedTemporaryFile(suffix=f'_{resolution}.mp4', delete=False) as output_file:
            output_path = output_file.name

        # FFmpeg command optimized for Railway
        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-vf', f"scale={config['size']}:flags=lanczos",
            '-c:v', 'libx264',
            '-preset', 'fast',  # Balance speed and quality
            '-crf', config['crf'],
            '-maxrate', config['bitrate'],
            '-bufsize', str(int(config['bitrate'].rstrip('k')) * 2) + 'k',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-ac', '2',
            '-movflags', '+faststart',
            '-y',  # Overwrite output
            output_path
        ]

        logger.info(f"Converting to {resolution} with command: {' '.join(cmd[:8])}...")

        # Run FFmpeg with timeout
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)

        if process.returncode == 0:
            logger.info(f"Successfully converted to {resolution}")
            return output_path
        else:
            logger.error(f"FFmpeg error for {resolution}: {stderr.decode()}")
            if os.path.exists(output_path):
                os.unlink(output_path)
            return None

    except asyncio.TimeoutError:
        logger.error(f"Timeout converting to {resolution}")
        if 'output_path' in locals() and os.path.exists(output_path):
            os.unlink(output_path)
        return None
    except Exception as e:
        logger.error(f"Error converting to {resolution}: {e}")
        if 'output_path' in locals() and os.path.exists(output_path):
            os.unlink(output_path)
        return None

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show bot status."""
    # Check FFmpeg
    try:
        process = await asyncio.create_subprocess_exec(
            'ffmpeg', '-version',
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        ffmpeg_status = "✅ Available" if process.returncode == 0 else "❌ Error"
    except:
        ffmpeg_status = "❌ Not found"

    status_text = f"""
🟢 **Bot Status: Online**

**System Information:**
• Platform: Railway Cloud
• FFmpeg: {ffmpeg_status}
• Storage: Temporary processing
• Processing: Sequential queue

**Limits:**
• Max file size: 50MB
• Timeout: 5 minutes per video
• Concurrent: 1 video per user

Ready to process your videos! 📹
    """
    
    await update.message.reply_text(status_text, parse_mode=ParseMode.MARKDOWN)

def main():
    """Main function to run the bot."""
    # Start health check server in a separate thread
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()
    
    # Create the Application
    application = Application.builder().token(BOT_TOKEN).build()

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(MessageHandler(
        filters.VIDEO | filters.Document.VIDEO, 
        handle_video
    ))
    application.add_handler(CallbackQueryHandler(
        handle_resolution,
        pattern="^res_"
    ))

    # Start the bot
    logger.info("🚀 Starting Telegram Video Bot on Railway...")
    logger.info(f"Health check server running on port {PORT}")
    
    # Run the bot
    application.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
