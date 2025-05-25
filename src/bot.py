import logging
import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, filters, ContextTypes
)
from telegram.constants import ParseMode
from .video_processor import VideoProcessor
from .file_handler import FileHandler
from .config import Config

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class TelegramVideoBot:
    def __init__(self):
        self.config = Config()
        self.video_processor = VideoProcessor()
        self.file_handler = FileHandler()
        
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send a message when the command /start is issued."""
        welcome_message = """
🎬 **Video Resolution Converter Bot**

Send me a video and I'll convert it to multiple resolutions!

**Supported resolutions:**
• 144p (256x144) - Ultra Low
• 240p (426x240) - Low  
• 360p (640x360) - Medium
• 480p (854x480) - Standard
• 720p (1280x720) - HD

**Limits:**
• Max file size: 50MB
• Supported formats: MP4, AVI, MOV, MKV

Just send me your video to get started! 🚀
        """
        
        await update.message.reply_text(
            welcome_message, 
            parse_mode=ParseMode.MARKDOWN
        )

    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send help information."""
        help_text = """
**Available Commands:**
/start - Start the bot
/help - Show this help message
/status - Check bot status

**How to use:**
1. Send a video file (max 50MB)
2. Choose which resolutions you want
3. Wait for processing (may take a few minutes)
4. Download your converted videos

**Tips:**
• Smaller input videos process faster
• Original quality affects output quality
• Processing time depends on video length and complexity
        """
        
        await update.message.reply_text(
            help_text,
            parse_mode=ParseMode.MARKDOWN
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show bot status."""
        status_message = """
🟢 **Bot Status: Online**

**System Info:**
• FFmpeg: Available
• Storage: Temporary processing
• Queue: Processing videos sequentially

Ready to process your videos! 📹
        """
        
        await update.message.reply_text(
            status_message,
            parse_mode=ParseMode.MARKDOWN
        )

    async def handle_video(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming video files."""
        try:
            if not update.message.video and not update.message.document:
                await update.message.reply_text(
                    "❌ Please send a video file!"
                )
                return

            # Get video info
            file_obj = update.message.video or update.message.document
            file_size = file_obj.file_size
            file_name = getattr(file_obj, 'file_name', 'video')

            # Check file size (50MB limit for Telegram bots)
            max_size = 50 * 1024 * 1024  # 50MB
            if file_size > max_size:
                await update.message.reply_text(
                    f"❌ **File too large!**\n\n"
                    f"Your file: {file_size / (1024*1024):.1f}MB\n"
                    f"Maximum allowed: 50MB\n\n"
                    f"Please compress your video or use a shorter clip.",
                    parse_mode=ParseMode.MARKDOWN
                )
                return

            # Show resolution selection keyboard
            keyboard = [
                [
                    InlineKeyboardButton("144p", callback_data="res_144p"),
                    InlineKeyboardButton("240p", callback_data="res_240p"),
                    InlineKeyboardButton("360p", callback_data="res_360p")
                ],
                [
                    InlineKeyboardButton("480p", callback_data="res_480p"),
                    InlineKeyboardButton("720p", callback_data="res_720p")
                ],
                [InlineKeyboardButton("🎯 All Resolutions", callback_data="res_all")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            # Store video info in context
            context.user_data['video_file_id'] = file_obj.file_id
            context.user_data['video_name'] = file_name
            context.user_data['video_size'] = file_size

            await update.message.reply_text(
                f"📹 **Video Received!**\n\n"
                f"**File:** {file_name}\n"
                f"**Size:** {file_size / (1024*1024):.1f}MB\n\n"
                f"Choose which resolution(s) you want:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=reply_markup
            )

        except Exception as e:
            logger.error(f"Error handling video: {e}")
            await update.message.reply_text(
                "❌ Error processing your video. Please try again."
            )

    async def handle_resolution_choice(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle resolution selection."""
        query = update.callback_query
        await query.answer()

        try:
            # Get stored video info
            video_file_id = context.user_data.get('video_file_id')
            video_name = context.user_data.get('video_name', 'video')
            
            if not video_file_id:
                await query.edit_message_text("❌ Video data not found. Please send the video again.")
                return

            # Parse resolution choice
            choice = query.data.replace('res_', '')
            
            if choice == 'all':
                resolutions = ['144p', '240p', '360p', '480p', '720p']
                choice_text = "All Resolutions"
            else:
                resolutions = [choice]
                choice_text = choice

            # Update message to show processing
            await query.edit_message_text(
                f"🔄 **Processing Video**\n\n"
                f"**File:** {video_name}\n"
                f"**Converting to:** {choice_text}\n\n"
                f"This may take a few minutes... Please wait ⏳",
                parse_mode=ParseMode.MARKDOWN
            )

            # Process video
            success = await self.process_video(
                update.effective_chat.id,
                context.bot,
                video_file_id,
                resolutions,
                video_name
            )

            if success:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="✅ **Processing Complete!**\n\nAll videos have been sent above.",
                    parse_mode=ParseMode.MARKDOWN
                )
            else:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="❌ **Processing Failed**\n\nThere was an error converting your video. Please try again with a different file.",
                    parse_mode=ParseMode.MARKDOWN
                )

        except Exception as e:
            logger.error(f"Error in resolution choice: {e}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Error processing your request. Please try again."
            )

    async def process_video(self, chat_id, bot, file_id, resolutions, filename):
        """Process video and send results."""
        try:
            # Download video
            progress_msg = await bot.send_message(
                chat_id=chat_id,
                text="📥 Downloading video..."
            )

            input_path = await self.file_handler.download_video(bot, file_id)
            
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=progress_msg.message_id,
                text="🔄 Converting video..."
            )

            # Process each resolution
            for i, resolution in enumerate(resolutions):
                try:
                    # Update progress
                    await bot.edit_message_text(
                        chat_id=chat_id,
                        message_id=progress_msg.message_id,
                        text=f"🔄 Converting to {resolution}... ({i+1}/{len(resolutions)})"
                    )

                    output_path = await self.video_processor.convert_video(
                        input_path, resolution
                    )

                    if output_path and os.path.exists(output_path):
                        # Send converted video
                        with open(output_path, 'rb') as video_file:
                            await bot.send_video(
                                chat_id=chat_id,
                                video=video_file,
                                caption=f"🎬 **{resolution} Version**\n\nOriginal: {filename}",
                                parse_mode=ParseMode.MARKDOWN
                            )
                        
                        # Clean up output file
                        os.unlink(output_path)
                    else:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=f"❌ Failed to convert to {resolution}"
                        )

                except Exception as e:
                    logger.error(f"Error converting to {resolution}: {e}")
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"❌ Error converting to {resolution}"
                    )

            # Clean up input file
            if os.path.exists(input_path):
                os.unlink(input_path)

            # Delete progress message
            await bot.delete_message(chat_id=chat_id, message_id=progress_msg.message_id)
            
            return True

        except Exception as e:
            logger.error(f"Error in process_video: {e}")
            return False

    def run(self):
        """Start the bot."""
        application = Application.builder().token(self.config.bot_token).build()

        # Add handlers
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.help_command))
        application.add_handler(CommandHandler("status", self.status))
        application.add_handler(MessageHandler(
            filters.VIDEO | filters.Document.VIDEO, 
            self.handle_video
        ))
        application.add_handler(CallbackQueryHandler(
            self.handle_resolution_choice,
            pattern="^res_"
        ))

        # Start the bot
        logger.info("Starting Telegram Video Bot...")
        application.run_polling(drop_pending_updates=True)

def main():
    bot = TelegramVideoBot()
    bot.run()

if __name__ == '__main__':
    main()
