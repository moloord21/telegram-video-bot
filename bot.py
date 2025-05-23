import os
import logging
import subprocess
import tempfile
import time
import signal
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# إعداد التسجيل
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# تعريف خيارات الدقة
RESOLUTIONS = {
    '144p': (256, 144),
    '240p': (426, 240),
    '360p': (640, 360),
    '480p': (854, 480),
    '720p': (1280, 720)
}

# تحديد الحد الأقصى لوقت التشغيل (285 دقيقة = 4.75 ساعات)
MAX_RUNTIME = 285 * 60  

def setup_shutdown_handler():
    start_time = time.time()
    
    def timeout_handler(signum, frame):
        logger.info("تم الوصول إلى الحد الأقصى لوقت التشغيل، جاري إيقاف البوت...")
        raise SystemExit(0)
    
    def check_timeout():
        if time.time() - start_time > MAX_RUNTIME:
            logger.info("تم الوصول إلى الحد الأقصى لوقت التشغيل، جاري إيقاف البوت...")
            raise SystemExit(0)
    
    signal.signal(signal.SIGALRM, timeout_handler)
    return check_timeout

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """إرسال رسالة عند إصدار الأمر /start."""
    await update.message.reply_text(
        'مرحبا! أرسل لي فيديو، وسأقوم بتحويله إلى دقات مختلفة: 144p، 240p، 360p، 480p، و720p.'
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """إرسال رسالة عند إصدار الأمر /help."""
    await update.message.reply_text('فقط أرسل لي أي ملف فيديو، وسأقوم بمعالجته لك!')

async def process_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """معالجة الفيديو بدقات مختلفة وإرساله مرة أخرى."""
    # التحقق مما إذا كانت الرسالة تحتوي على فيديو
    if not update.message.video and not update.message.document:
        await update.message.reply_text("الرجاء إرسال ملف فيديو.")
        return

    await update.message.reply_text("لقد استلمت فيديو الخاص بك! جاري معالجته بدقات مختلفة...")

    # الحصول على الملف
    if update.message.video:
        file = await update.message.video.get_file()
    else:
        file = await update.message.document.get_file()

    # تنزيل الفيديو
    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_input:
        await file.download_to_drive(temp_input.name)
        input_path = temp_input.name
    
    output_files = []
    
    try:
        # معالجة كل دقة على حدة
        for res_name, dimensions in RESOLUTIONS.items():
            width, height = dimensions
            output_path = f"{input_path}_{res_name}.mp4"
            
            # استخدام FFmpeg لتحويل الفيديو
            cmd = [
                'ffmpeg', '-i', input_path, 
                '-vf', f'scale={width}:{height}', 
                '-c:v', 'libx264', '-crf', '23',
                '-c:a', 'aac', '-b:a', '128k',
                output_path
            ]
            
            process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            stdout, stderr = process.communicate()
            
            if process.returncode != 0:
                logger.error(f"خطأ في معالجة الدقة {res_name}: {stderr.decode()}")
                await update.message.reply_text(f"فشل في معالجة الدقة {res_name}. الرجاء المحاولة لاحقًا.")
                continue
                
            output_files.append((res_name, output_path))
        
        # إرسال كل فيديو تمت معالجته
        for res_name, output_path in output_files:
            await update.message.reply_document(
                document=open(output_path, 'rb'),
                caption=f"فيديو الخاص بك بدقة {res_name}"
            )
            
    except Exception as e:
        logger.error(f"خطأ: {e}")
        await update.message.reply_text("حدث خطأ أثناء معالجة الفيديو الخاص بك. الرجاء المحاولة لاحقًا.")
        
    finally:
        # تنظيف الملفات المؤقتة
        os.unlink(input_path)
        for _, output_path in output_files:
            if os.path.exists(output_path):
                os.unlink(output_path)

def main() -> None:
    """بدء تشغيل البوت."""
    # الحصول على التوكن من متغير البيئة
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("لم يتم توفير توكن")
        return
    
    # إعداد التحقق من المهلة
    check_timeout = setup_shutdown_handler()
    
    # إنشاء التطبيق
    application = ApplicationBuilder().token(token).build()

    # إضافة المعالجات
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.VIDEO | filters.Document.VIDEO, process_video))
    
    # تعريف دالة مرجعية للتحقق بشكل دوري من وقت التشغيل
    async def check_runtime_callback(context: ContextTypes.DEFAULT_TYPE):
        check_timeout()
    
    # إضافة مهمة للتحقق من وقت التشغيل كل دقيقة
    application.job_queue.run_repeating(check_runtime_callback, interval=60)

    # تشغيل البوت
    application.run_polling()

if __name__ == '__main__':
    main()
