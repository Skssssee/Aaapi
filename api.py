
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
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    return {
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "code": result.returncode
    }

@app.get("/")
def root():
    return {"message": "YouTube API - Use /audio, /video, or /info endpoints"}

@app.get("/audio")
def get_audio(url: str = Query(...)):
    """Get direct audio stream URL"""
    video_id = get_video_id(url)
    if not video_id:
        raise HTTPException(400, "Invalid YouTube URL")
    
    # Try multiple format selectors
    format_selectors = [
        "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio",
        "bestaudio/best",
        "140/251/250"  # Specific format IDs
    ]
    
    for fmt in format_selectors:
        cmd = [
            "yt-dlp",
            "-f", fmt,
            "-g",
            "--no-warnings",
            f"https://youtube.com/watch?v={video_id}"
        ]
        
        result = run_cmd(cmd)
        
        if result["code"] == 0 and result["stdout"]:
            audio_url = result["stdout"].split('\n')[0]
            if audio_url and "googlevideo.com" in audio_url:
                return {
                    "success": True,
                    "audio_url": audio_url,
                    "direct_stream": True,
                    "format_used": fmt
                }
    
    # If all fail, get info and show available formats
    cmd_info = [
        "yt-dlp",
        "--dump-json",
        "--no-warnings",
        f"https://youtube.com/watch?v={video_id}"
    ]
    
    result_info = run_cmd(cmd_info)
    if result_info["code"] == 0:
        try:
            data = json.loads(result_info["stdout"])
            audio_formats = []
            for f in data.get("formats", []):
                if f.get("acodec") != "none" and f.get("vcodec") == "none":
                    audio_formats.append({
                        "format_id": f.get("format_id"),
                        "ext": f.get("ext"),
                        "bitrate": f.get("abr"),
                        "url": f.get("url")[:100] + "..." if f.get("url") else None
                    })
            
            raise HTTPException(400, {
                "error": "No direct audio stream found",
                "available_formats": audio_formats[:5]
            })
        except:
            pass
    
    raise HTTPException(500, "Failed to get audio stream")

@app.get("/video")
def get_video(url: str = Query(...), quality: str = "best"):
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
    elif quality == "360p":
        fmt = "best[height<=360]"
    else:
        fmt = "best"
    
    cmd = [
        "yt-dlp",
        "-f", fmt,
        "-g",
        "--no-warnings",
        f"https://youtube.com/watch?v={video_id}"
    ]
    
    result = run_cmd(cmd)
    
    if result["code"] != 0 or not result["stdout"]:
        raise HTTPException(500, result["stderr"] or "No video stream found")
    
    video_url = result["stdout"].split('\n')[0]
    
    return {
        "success": True,
        "video_url": video_url,
        "quality": quality,
        "direct_stream": True
    }

@app.get("/info")
def get_info(url: str = Query(...)):
    """Get video information and available formats"""
    video_id = get_video_id(url)
    if not video_id:
        raise HTTPException(400, "Invalid YouTube URL")
    
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--no-warnings",
        f"https://youtube.com/watch?v={video_id}"
    ]
    
    result = run_cmd(cmd)
    
    if result["code"] != 0:
        raise HTTPException(500, result["stderr"])
    
    try:
        data = json.loads(result["stdout"])
        
        # Get best direct URLs
        cmd_audio = ["yt-dlp", "-f", "bestaudio", "-g", f"https://youtube.com/watch?v={video_id}"]
        cmd_video = ["yt-dlp", "-f", "best[height<=720]", "-g", f"https://youtube.com/watch?v={video_id}"]
        
        audio_result = run_cmd(cmd_audio)
        video_result = run_cmd(cmd_video)
        
        audio_url = audio_result["stdout"].split('\n')[0] if audio_result["code"] == 0 else None
        video_url = video_result["stdout"].split('\n')[0] if video_result["code"] == 0 else None
        
        # Format data
        formats = []
        for f in data.get("formats", []):
            if f.get("url"):
                formats.append({
                    "id": f.get("format_id"),
                    "ext": f.get("ext"),
                    "resolution": f"{f.get('width', '?')}x{f.get('height', '?')}",
                    "vcodec": f.get("vcodec"),
                    "acodec": f.get("acodec"),
                    "filesize_mb": round(f.get("filesize", 0) / 1048576, 2) if f.get("filesize") else None,
                    "note": f.get("format_note", "")
                })
        
        return {
            "title": data.get("title"),
            "duration": data.get("duration"),
            "uploader": data.get("uploader"),
            "view_count": data.get("view_count"),
            "thumbnail": data.get("thumbnail"),
            "direct_urls": {
                "audio": audio_url,
                "video_720p": video_url
            },
            "available_formats": formats[:10]  # First 10 formats
        }
    
    except json.JSONDecodeError:
        raise HTTPException(500, "Failed to parse video info")

@app.get("/download")
def download_audio(url: str = Query(...)):
    """Simple download endpoint - returns direct URL"""
    video_id = get_video_id(url)
    if not video_id:
        raise HTTPException(400, "Invalid YouTube URL")
    
    # Try to get bestaudio
    cmd = [
        "yt-dlp",
        "-f", "bestaudio",
        "-g",
        "--no-warnings",
        f"https://youtube.com/watch?v={video_id}"
    ]
    
    result = run_cmd(cmd)
    
    if result["code"] == 0 and result["stdout"]:
        audio_url = result["stdout"].split('\n')[0]
        return {
            "download_url": audio_url,
            "message": "Use this URL directly with wget/curl or in media players",
            "example": f"wget -O audio.mp3 '{audio_url}'"
        }
    
    # Fallback to best format
    cmd2 = [
        "yt-dlp",
        "-f", "best",
        "-g",
        "--no-warnings",
        f"https://youtube.com/watch?v={video_id}"
    ]
    
    result2 = run_cmd(cmd2)
    
    if result2["code"] == 0 and result2["stdout"]:
        video_url = result2["stdout"].split('\n')[0]
        return {
            "download_url": video_url,
            "message": "Video URL (contains both audio and video)",
            "example": f"wget -O video.mp4 '{video_url}'"
        }
    
    raise HTTPException(500, "Could not get download URL")
