import os
import subprocess
import tempfile
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class VideoProcessor:
    def __init__(self):
        self.resolutions = {
            '144p': {'size': '256x144', 'bitrate': '200k'},
            '240p': {'size': '426x240', 'bitrate': '400k'},
            '360p': {'size': '640x360', 'bitrate': '800k'},
            '480p': {'size': '854x480', 'bitrate': '1500k'},
            '720p': {'size': '1280x720', 'bitrate': '2500k'}
        }

    async def convert_video(self, input_path: str, resolution: str) -> Optional[str]:
        """Convert video to specified resolution."""
        try:
            if resolution not in self.resolutions:
                logger.error(f"Unsupported resolution: {resolution}")
                return None

            res_config = self.resolutions[resolution]
            
            # Create output file
            output_fd, output_path = tempfile.mkstemp(suffix=f'_{resolution}.mp4')
            os.close(output_fd)  # Close the file descriptor, we just need the path

            # FFmpeg command with optimizations for speed and quality
            cmd = [
                'ffmpeg',
                '-i', input_path,
                '-vf', f"scale={res_config['size']}:flags=lanczos",
                '-c:v', 'libx264',
                '-preset', 'medium',  # Balance between speed and compression
                '-crf', '23',  # Good quality setting
                '-maxrate', res_config['bitrate'],
                '-bufsize', str(int(res_config['bitrate'].rstrip('k')) * 2) + 'k',
                '-c:a', 'aac',
                '-b:a', '128k',
                '-ac', '2',  # Stereo audio
                '-movflags', '+faststart',  # Optimize for web playback
                '-y',  # Overwrite output file
                output_path
            ]

            logger.info(f"Converting to {resolution}...")
            
            # Run FFmpeg
            process = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )

            if process.returncode == 0:
                logger.info(f"Successfully converted to {resolution}")
                return output_path
            else:
                logger.error(f"FFmpeg error for {resolution}: {process.stderr}")
                if os.path.exists(output_path):
                    os.unlink(output_path)
                return None

        except subprocess.TimeoutExpired:
            logger.error(f"Timeout converting to {resolution}")
            if os.path.exists(output_path):
                os.unlink(output_path)
            return None
        except Exception as e:
            logger.error(f"Error converting to {resolution}: {e}")
            if 'output_path' in locals() and os.path.exists(output_path):
                os.unlink(output_path)
            return None

    def get_video_info(self, video_path: str) -> dict:
        """Get video information using ffprobe."""
        try:
            cmd = [
                'ffprobe',
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                video_path
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                import json
                return json.loads(result.stdout)
            else:
                logger.error(f"ffprobe error: {result.stderr}")
                return {}
        except Exception as e:
            logger.error(f"Error getting video info: {e}")
            return {}
