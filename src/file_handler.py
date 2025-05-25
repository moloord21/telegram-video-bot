import os
import tempfile
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class FileHandler:
    def __init__(self):
        self.temp_dir = tempfile.gettempdir()

    async def download_video(self, bot, file_id: str) -> Optional[str]:
        """Download video from Telegram."""
        try:
            # Get file info
            file = await bot.get_file(file_id)
            
            # Create temporary file
            fd, temp_path = tempfile.mkstemp(suffix='.mp4', dir=self.temp_dir)
            os.close(fd)  # Close file descriptor

            # Download file
            await file.download_to_drive(temp_path)
            
            logger.info(f"Downloaded video to {temp_path}")
            return temp_path

        except Exception as e:
            logger.error(f"Error downloading video: {e}")
            if 'temp_path' in locals() and os.path.exists(temp_path):
                os.unlink(temp_path)
            return None

    def cleanup_file(self, file_path: str):
        """Clean up temporary file."""
        try:
            if os.path.exists(file_path):
                os.unlink(file_path)
                logger.info(f"Cleaned up {file_path}")
        except Exception as e:
            logger.error(f"Error cleaning up {file_path}: {e}")

    def get_file_size_mb(self, file_path: str) -> float:
        """Get file size in MB."""
        try:
            size_bytes = os.path.getsize(file_path)
            return size_bytes / (1024 * 1024)
        except Exception:
            return 0.0
