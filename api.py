
from fastapi import FastAPI, Query, HTTPException
import subprocess
import json
import re

app = FastAPI()

def run_cmd(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return result.stdout.strip(), result.stderr.strip(), result.returncode

@app.get("/")
def home():
    return {"api": "YouTube Stream", "endpoints": ["/audio", "/video", "/info"]}

@app.get("/audio")
def audio(url: str = Query(...)):
    """Get audio stream URL"""
    # Extract video ID
    video_id = None
    patterns = [
        r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'youtu\.be/([a-zA-Z0-9_-]{11})'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            video_id = match.group(1)
            break
    
    if not video_id:
        raise HTTPException(400, "Invalid YouTube URL")
    
    # Try multiple format selectors
    formats = [
        "bestaudio",
        "140/251/250",  # m4a 128kbps / webm 160kbps
        "best"
    ]
    
    for fmt in formats:
        cmd = [
            "python", "-m", "yt_dlp",
            "--remote-components", "ejs:github",  # ADD THIS!
            "-f", fmt,
            "-g",
            "--no-warnings",
            f"https://youtube.com/watch?v={video_id}"
        ]
        
        stdout, stderr, code = run_cmd(cmd)
        
        if code == 0 and stdout:
            audio_url = stdout.split('\n')[0]
            if audio_url:
                return {
                    "success": True,
                    "audio_url": audio_url,
                    "video_id": video_id,
                    "format": fmt
                }
    
    raise HTTPException(500, "Failed to get audio stream")

@app.get("/video")
def video(url: str = Query(...), quality: str = "720p"):
    """Get video stream URL"""
    # Extract video ID
    video_id = None
    patterns = [
        r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'youtu\.be/([a-zA-Z0-9_-]{11})'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            video_id = match.group(1)
            break
    
    if not video_id:
        raise HTTPException(400, "Invalid YouTube URL")
    
    if quality == "best":
        fmt = "best"
    elif quality == "720p":
        fmt = "22/18"  # 720p mp4 / 360p mp4
    elif quality == "480p":
        fmt = "135/134"  # 480p / 360p
    else:
        fmt = "best"
    
    cmd = [
        "python", "-m", "yt_dlp",
        "--remote-components", "ejs:github",  # ADD THIS!
        "-f", fmt,
        "-g",
        "--no-warnings",
        f"https://youtube.com/watch?v={video_id}"
    ]
    
    stdout, stderr, code = run_cmd(cmd)
    
    if code != 0:
        raise HTTPException(500, stderr[:200])
    
    if not stdout:
        raise HTTPException(404, "No video stream found")
    
    video_url = stdout.split('\n')[0]
    
    return {
        "success": True,
        "video_url": video_url,
        "quality": quality,
        "video_id": video_id
    }

@app.get("/info")
def info(url: str = Query(...)):
    """Get video info"""
    # Extract video ID
    video_id = None
    patterns = [
        r'youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
        r'youtu\.be/([a-zA-Z0-9_-]{11})'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            video_id = match.group(1)
            break
    
    if not video_id:
        raise HTTPException(400, "Invalid YouTube URL")
    
    cmd = [
        "python", "-m", "yt_dlp",
        "--remote-components", "ejs:github",  # ADD THIS!
        "--dump-json",
        "--no-warnings",
        f"https://youtube.com/watch?v={video_id}"
    ]
    
    stdout, stderr, code = run_cmd(cmd)
    
    if code != 0:
        raise HTTPException(500, stderr[:200])
    
    try:
        data = json.loads(stdout)
        return {
            "title": data.get("title"),
            "duration": data.get("duration"),
            "channel": data.get("uploader"),
            "thumbnail": data.get("thumbnail")
        }
    except:
        raise HTTPException(500, "Failed to parse info")

@app.get("/test")
def test():
    """Test if yt-dlp works"""
    cmd = ["python", "-m", "yt_dlp", "--version"]
    stdout, stderr, code = run_cmd(cmd)
    
    if code == 0:
        return {"status": "ok", "version": stdout}
    else:
        return {"status": "error", "error": stderr}
