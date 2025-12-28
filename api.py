import os
import json
import asyncio
import logging
from typing import Optional
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
import re
import tempfile
import aiofiles

app = FastAPI(title="YouTube Stream API", version="1.0")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------
# UTILS
# -------------------------
async def run_cmd(cmd):
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    return {
        "stdout": stdout.decode().strip(),
        "stderr": stderr.decode().strip(),
        "code": process.returncode
    }

def get_video_id(url):
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'^([a-zA-Z0-9_-]{11})$'
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise HTTPException(400, "Invalid URL")

# -------------------------
# ENDPOINTS
# -------------------------
@app.get("/")
async def home():
    return {
        "api": "YouTube Stream API",
        "endpoints": {
            "audio": "/audio?url=YOUTUBE_URL&quality=best|high|medium|low",
            "video": "/video?url=YOUTUBE_URL&quality=720p|480p|360p|best",
            "download": "/download?url=YOUTUBE_URL&type=audio|video&quality=best",
            "info": "/info?url=YOUTUBE_URL"
        }
    }

@app.get("/audio")
async def audio(
    url: str = Query(...),
    quality: str = Query("best")
):
    """Get direct audio stream URL"""
    video_id = get_video_id(url)
    
    quality_map = {
        "best": "bestaudio",
        "high": "bestaudio[abr>=128]",
        "medium": "bestaudio[abr>=96]",
        "low": "bestaudio[abr>=64]"
    }
    fmt = quality_map.get(quality, "bestaudio")
    
    cmd = [
        "yt-dlp",
        "-f", fmt,
        "-g",
        "--no-warnings",
        f"https://youtube.com/watch?v={video_id}"
    ]
    
    result = await run_cmd(cmd)
    
    if result["code"] != 0:
        error = result["stderr"]
        if "age" in error.lower():
            raise HTTPException(403, "Age-restricted video")
        raise HTTPException(500, error[:200])
    
    audio_url = result["stdout"].split('\n')[0]
    if not audio_url:
        raise HTTPException(404, "No audio found")
    
    return {
        "url": audio_url,
        "quality": quality,
        "direct_stream": True
    }

@app.get("/video")
async def video(
    url: str = Query(...),
    quality: str = Query("720p")
):
    """Get direct video stream URL"""
    video_id = get_video_id(url)
    
    if quality == "best":
        fmt = "best[height<=1080]"
    elif "p" in quality:
        height = quality.replace("p", "")
        fmt = f"best[height<={height}]"
    else:
        fmt = "best[height<=720]"
    
    cmd = [
        "yt-dlp",
        "-f", fmt,
        "-g",
        "--no-warnings",
        f"https://youtube.com/watch?v={video_id}"
    ]
    
    result = await run_cmd(cmd)
    
    if result["code"] != 0:
        error = result["stderr"]
        if "age" in error.lower():
            raise HTTPException(403, "Age-restricted video")
        raise HTTPException(500, error[:200])
    
    video_url = result["stdout"].split('\n')[0]
    if not video_url:
        raise HTTPException(404, "No video found")
    
    return {
        "url": video_url,
        "quality": quality,
        "direct_stream": True
    }

@app.get("/download")
async def download(
    url: str = Query(...),
    type: str = Query("audio"),
    quality: str = Query("best"),
    format: str = Query("mp3")
):
    """Download audio/video directly"""
    video_id = get_video_id(url)
    
    # Build yt-dlp command
    if type == "audio":
        quality_map = {
            "best": "bestaudio",
            "high": "bestaudio[abr>=128]",
            "medium": "bestaudio[abr>=96]",
            "low": "bestaudio[abr>=64]"
        }
        fmt = quality_map.get(quality, "bestaudio")
        ext = format if format in ["mp3", "m4a", "opus"] else "mp3"
        output_template = f"%(title)s.%(ext)s"
        
        cmd = [
            "yt-dlp",
            "-f", fmt,
            "--extract-audio",
            "--audio-format", ext,
            "--audio-quality", "0",  # best quality
            "-o", output_template,
            "--no-warnings",
            f"https://youtube.com/watch?v={video_id}"
        ]
    
    else:  # video
        if quality == "best":
            fmt = "best"
        elif "p" in quality:
            height = quality.replace("p", "")
            fmt = f"best[height<={height}]"
        else:
            fmt = "best[height<=720]"
        
        ext = format if format in ["mp4", "webm", "mkv"] else "mp4"
        output_template = f"%(title)s.%(ext)s"
        
        cmd = [
            "yt-dlp",
            "-f", fmt,
            "--recode-video", ext,
            "-o", output_template,
            "--no-warnings",
            f"https://youtube.com/watch?v={video_id}"
        ]
    
    # Create temp file
    with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as tmp:
        tmp_path = tmp.name
    
    try:
        # Download file
        download_cmd = cmd + ["-o", tmp_path]
        result = await run_cmd(download_cmd)
        
        if result["code"] != 0:
            raise HTTPException(500, f"Download failed: {result['stderr'][:200]}")
        
        # Get filename
        filename_cmd = [
            "yt-dlp",
            "--get-filename",
            "-o", "%(title)s",
            f"https://youtube.com/watch?v={video_id}"
        ]
        name_result = await run_cmd(filename_cmd)
        filename = f"{name_result['stdout'] or 'video'}.{ext}"
        
        # Stream file
        async def file_sender():
            async with aiofiles.open(tmp_path, "rb") as f:
                chunk = await f.read(65536)
                while chunk:
                    yield chunk
                    chunk = await f.read(65536)
            os.unlink(tmp_path)
        
        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"'
        }
        
        return StreamingResponse(
            file_sender(),
            media_type="application/octet-stream",
            headers=headers
        )
        
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise HTTPException(500, str(e))

@app.get("/info")
async def info(url: str = Query(...)):
    """Get video information"""
    video_id = get_video_id(url)
    
    cmd = [
        "yt-dlp",
        "--dump-json",
        "--no-warnings",
        f"https://youtube.com/watch?v={video_id}"
    ]
    
    result = await run_cmd(cmd)
    
    if result["code"] != 0:
        raise HTTPException(500, result["stderr"][:200])
    
    try:
        data = json.loads(result["stdout"])
        
        # Get formats
        formats = []
        for f in data.get("formats", []):
            if f.get("url"):
                formats.append({
                    "id": f.get("format_id"),
                    "ext": f.get("ext"),
                    "resolution": f"{f.get('width', '?')}x{f.get('height', '?')}",
                    "fps": f.get("fps"),
                    "vcodec": f.get("vcodec"),
                    "acodec": f.get("acodec"),
                    "size_mb": round(f.get("filesize", 0) / 1048576, 2) if f.get("filesize") else None,
                    "url": f.get("url")
                })
        
        return {
            "title": data.get("title"),
            "duration": data.get("duration"),
            "uploader": data.get("uploader"),
            "views": data.get("view_count"),
            "thumbnail": data.get("thumbnail"),
            "formats": formats[:20]  # Limit to 20 formats
        }
    
    except json.JSONDecodeError:
        raise HTTPException(500, "Failed to parse video info")
