#!/bin/bash

# Update package list
sudo apt-get update

# Install FFmpeg
sudo apt-get install -y ffmpeg

# Install Python dependencies
pip install -r requirements.txt

# Create necessary directories
mkdir -p logs

echo "🎬 Telegram Video Bot development environment is ready!"
echo "📝 Don't forget to set your BOT_TOKEN in the environment variables"
