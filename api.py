
from fastapi import FastAPI, Query, HTTPException
import subprocess
import json
import re
from typing import Optional

app = FastAPI()

def get_video_id(url: str) -> Optional[str]:
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'^([a-zA-Z0-9_-]{11})$'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None

def run_cmd(cmd):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "code": result.returncode
        }
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "code": 1}

@app.get("/")
def root():
    return {
        "status": "online",
        "endpoints": ["/audio", "/video", "/info", "/download"],
        "example": "/audio?url=https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    }

@app.get("/test")
def test():
    """Test if yt-dlp is working"""
    cmd = ["python", "-m", "yt_dlp", "--version"]
    result = run_cmd(cmd)
    
    if result["code"] == 0:
        return {
            "status": "ok",
            "ytdlp_version": result["stdout"],
            "message": "yt-dlp is working"
        }
    else:
        return {
            "status": "error",
            "error": result["stderr"],
            "message": "yt-dlp not working"
        }

@app.get("/audio")
def get_audio(url: str = Query(...)):
    """Get direct audio stream URL"""
    video_id = get_video_id(url)
    if not video_id:
        raise HTTPException(400, "Invalid YouTube URL")
    
    # Try multiple ways to call yt-dlp
    commands_to_try = [
        ["yt-dlp", "-f", "bestaudio", "-g", "--no-warnings", f"https://youtube.com/watch?v={video_id}"],
        ["python", "-m", "yt_dlp", "-f", "bestaudio", "-g", "--no-warnings", f"https://youtube.com/watch?v={video_id}"],
        ["/usr/local/bin/yt-dlp", "-f", "bestaudio", "-g", "--no-warnings", f"https://youtube.com/watch?v={video_id}"]
    ]
    
    for cmd in commands_to_try:
        result = run_cmd(cmd)
        if result["code"] == 0 and result["stdout"]:
            audio_url = result["stdout"].split('\n')[0]
            if audio_url:
                return {
                    "success": True,
                    "audio_url": audio_url,
                    "video_id": video_id,
                    "command_used": cmd[0]
                }
    
    # If all fail, show error
    raise HTTPException(500, f"Failed to get audio. Last error: {result['stderr'][:200]}")

@app.get("/video")
def get_video(url: str = Query(...), quality: str = "720p"):
    """Get direct video stream URL"""
    video_id = get_video_id(url)
    if not video_id:
        raise HTTPException(400, "Invalid YouTube URL")
    
    if quality == "best":
        fmt = "best"
    elif quality == "720p":
        fmt = "best[height<=720]"
    elif quality == "480p":
        fmt = "best[height<=480]"
    else:
        fmt = "best"
    
    cmd = ["python", "-m", "yt_dlp", "-f", fmt, "-g", "--no-warnings", f"https://youtube.com/watch?v={video_id}"]
    result = run_cmd(cmd)
    
    if result["code"] != 0:
        raise HTTPException(500, result["stderr"][:200])
    
    if not result["stdout"]:
        raise HTTPException(404, "No video stream found")
    
    video_url = result["stdout"].split('\n')[0]
    
    return {
        "success": True,
        "video_url": video_url,
        "quality": quality,
        "video_id": video_id
    }

@app.get("/info")
def get_info(url: str = Query(...)):
    """Get video info"""
    video_id = get_video_id(url)
    if not video_id:
        raise HTTPException(400, "Invalid YouTube URL")
    
    cmd = ["python", "-m", "yt_dlp", "--dump-json", "--no-warnings", f"https://youtube.com/watch?v={video_id}"]
    result = run_cmd(cmd)
    
    if result["code"] != 0:
        raise HTTPException(500, result["stderr"][:200])
    
    try:
        data = json.loads(result["stdout"])
        return {
            "title": data.get("title"),
            "duration": data.get("duration"),
            "channel": data.get("uploader"),
            "views": data.get("view_count"),
            "thumbnail": data.get("thumbnail")
        }
    except:
        raise HTTPException(500, "Failed to parse video info")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
