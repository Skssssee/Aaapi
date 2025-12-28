import time
import json
import os
import asyncio
import subprocess
import logging
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import re
import psutil

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Environment variables
PORT = int(os.getenv("PORT", "8000"))
WORKERS = int(os.getenv("WORKERS", "2"))
YTDLP_PATH = os.getenv("YTDLP_PATH", "yt-dlp")
COOKIES_PATH = os.getenv("COOKIES_PATH", "cookies.txt")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "90"))
MAX_VIDEO_DURATION = int(os.getenv("MAX_VIDEO_DURATION", "7200"))  # 2 hours max

# Track startup time
START_TIME = time.time()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events"""
    # Startup
    logger.info(f"Starting YouTube Stream API on port {PORT}")
    logger.info(f"Using yt-dlp path: {YTDLP_PATH}")
    
    # Verify yt-dlp installation
    try:
        result = await run_command([YTDLP_PATH, "--version"], timeout=10)
        logger.info(f"yt-dlp version: {result['stdout'].strip()}")
    except Exception as e:
        logger.error(f"Failed to verify yt-dlp: {e}")
        # Don't exit, just log error
    
    yield
    
    # Shutdown
    logger.info("Shutting down YouTube Stream API")

app = FastAPI(
    title="YouTube Stream Extractor API",
    description="Extract YouTube video/audio streams in all qualities",
    version="2.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Response-Time"]
)

# -------------------------
# UTILITY FUNCTIONS
# -------------------------
async def run_command(cmd: List[str], timeout: int = 90) -> Dict[str, Any]:
    """Run subprocess command asynchronously with timeout"""
    logger.info(f"Running command: {' '.join(cmd[:3])}...")
    
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout
        )
        
        return {
            "stdout": stdout.decode('utf-8', errors='ignore').strip(),
            "stderr": stderr.decode('utf-8', errors='ignore').strip(),
            "returncode": process.returncode
        }
    
    except asyncio.TimeoutError:
        # Kill the process if it's still running
        if process and process.returncode is None:
            try:
                process.kill()
                await process.wait()
            except:
                pass
        raise HTTPException(status_code=408, detail="Request timeout")
    
    except Exception as e:
        logger.error(f"Command execution failed: {e}")
        raise HTTPException(status_code=500, detail=f"Command execution failed: {str(e)}")

def extract_video_id(url: str) -> Optional[str]:
    """Extract YouTube video ID from URL"""
    patterns = [
        r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/v/|youtube\.com/shorts/)([a-zA-Z0-9_-]{11})',
        r'^([a-zA-Z0-9_-]{11})$'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    raise HTTPException(status_code=400, detail="Invalid YouTube URL or Video ID")

def build_ytdlp_cmd(extra_args: List[str] = None) -> List[str]:
    """Build base yt-dlp command with common arguments"""
    cmd = [
        YTDLP_PATH,
        "--no-warnings",
        "--no-check-certificates",
        "--force-ipv4",
        "--geo-bypass",
        "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "--referer", "https://www.youtube.com/",
        "--dump-json"
    ]
    
    # Add cookies if file exists
    if os.path.exists(COOKIES_PATH):
        cmd.extend(["--cookies", COOKIES_PATH])
        logger.info("Using cookies file")
    
    # Add EJS component if available
    try:
        cmd.extend(["--load-components", "ejs"])
        logger.info("EJS component loaded")
    except:
        logger.warning("EJS component not available")
    
    if extra_args:
        cmd.extend(extra_args)
    
    return cmd

def get_system_stats() -> Dict[str, Any]:
    """Get system statistics"""
    try:
        cpu_percent = psutil.cpu_percent(interval=1)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        return {
            "cpu_percent": cpu_percent,
            "memory_percent": memory.percent,
            "memory_available_mb": round(memory.available / (1024 * 1024), 2),
            "disk_percent": disk.percent,
            "disk_free_gb": round(disk.free / (1024 ** 3), 2),
            "process_count": len(psutil.pids())
        }
    except Exception as e:
        logger.error(f"Failed to get system stats: {e}")
        return {"error": str(e)}

def uptime() -> Dict[str, Any]:
    """Calculate server uptime"""
    s = int(time.time() - START_TIME)
    days, s = divmod(s, 86400)
    hours, s = divmod(s, 3600)
    minutes, seconds = divmod(s, 60)
    
    return {
        "days": days,
        "hours": hours,
        "minutes": minutes,
        "seconds": seconds,
        "total_seconds": int(time.time() - START_TIME)
    }

# -------------------------
# MIDDLEWARE
# -------------------------
from fastapi import Request
import time as time_module

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Add X-Response-Time header to all responses"""
    start_time = time_module.time()
    response = await call_next(request)
    process_time = time_module.time() - start_time
    response.headers["X-Response-Time"] = f"{process_time:.3f}s"
    
    # Log request
    logger.info(f"{request.method} {request.url.path} - {response.status_code} - {process_time:.3f}s")
    
    return response

# -------------------------
# HEALTH & MONITORING ENDPOINTS
# -------------------------
@app.get("/")
async def root():
    """Root endpoint with API information"""
    return {
        "status": "running",
        "api": "YouTube Stream Extractor API",
        "version": "2.1.0",
        "uptime": uptime(),
        "documentation": {
            "swagger": "/docs",
            "redoc": "/redoc"
        },
        "endpoints": {
            "audio": "GET /audio?url={youtube_url}&quality=[best|high|medium|low]",
            "audio_qualities": "GET /audio/qualities?url={youtube_url}",
            "video_qualities": "GET /video/qualities?url={youtube_url}",
            "video_stream": "GET /video/stream?url={youtube_url}&quality=[best|720p|480p|...]",
            "video_info": "GET /video/info?url={youtube_url}",
            "health": "GET /health",
            "stats": "GET /stats"
        },
        "limits": {
            "max_video_duration_seconds": MAX_VIDEO_DURATION,
            "request_timeout_seconds": REQUEST_TIMEOUT
        }
    }

@app.get("/health")
async def health_check():
    """Comprehensive health check endpoint"""
    health_status = {
        "status": "healthy",
        "timestamp": time_module.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "uptime": uptime(),
        "system": get_system_stats()
    }
    
    # Check yt-dlp
    try:
        result = await run_command([YTDLP_PATH, "--version"], timeout=10)
        health_status["ytdlp"] = {
            "available": True,
            "version": result["stdout"].strip()[:50]
        }
    except Exception as e:
        health_status["ytdlp"] = {
            "available": False,
            "error": str(e)
        }
        health_status["status"] = "degraded"
    
    # Check FFmpeg
    try:
        result = await run_command(["ffmpeg", "-version"], timeout=10)
        health_status["ffmpeg"] = {
            "available": True,
            "version": result["stdout"].split('\n')[0][:100] if result["stdout"] else "unknown"
        }
    except Exception as e:
        health_status["ffmpeg"] = {
            "available": False,
            "error": str(e)
        }
    
    return health_status

@app.get("/stats")
async def system_stats():
    """Get detailed system statistics"""
    return {
        "status": "success",
        "timestamp": time_module.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "uptime": uptime(),
        "system": get_system_stats(),
        "environment": {
            "port": PORT,
            "workers": WORKERS,
            "max_video_duration": MAX_VIDEO_DURATION,
            "request_timeout": REQUEST_TIMEOUT,
            "cookies_file_exists": os.path.exists(COOKIES_PATH)
        }
    }

# -------------------------
# VIDEO INFO ENDPOINT
# -------------------------
@app.get("/video/info")
async def get_video_info(url: str = Query(...)):
    """Get video information without formats"""
    try:
        video_id = extract_video_id(url)
        
        cmd = build_ytdlp_cmd([
            "--no-playlist",
            "--skip-download",
            "--print", "%(title)s\n%(duration)s\n%(uploader)s\n%(view_count)s\n%(like_count)s\n%(upload_date)s\n%(thumbnail)s\n%(description)s",
            f"https://www.youtube.com/watch?v={video_id}"
        ])
        
        result = await run_command(cmd, timeout=30)
        
        if result["returncode"] != 0:
            raise HTTPException(status_code=500, detail=result["stderr"])
        
        lines = result["stdout"].split('\n')
        
        return {
            "status": "success",
            "video_id": video_id,
            "title": lines[0] if len(lines) > 0 else "",
            "duration": int(lines[1]) if len(lines) > 1 and lines[1].isdigit() else 0,
            "author": lines[2] if len(lines) > 2 else "",
            "view_count": int(lines[3]) if len(lines) > 3 and lines[3].isdigit() else 0,
            "like_count": int(lines[4]) if len(lines) > 4 and lines[4].isdigit() else 0,
            "upload_date": lines[5] if len(lines) > 5 else "",
            "thumbnail": lines[6] if len(lines) > 6 else "",
            "description": lines[7] if len(lines) > 7 else ""
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get video info: {e}")
        raise HTTPException(status_code=500, detail="Failed to get video information")

# -------------------------
# AUDIO ENDPOINTS (Optimized)
# -------------------------
@app.get("/audio")
async def get_audio_stream(
    url: str = Query(...),
    quality: str = Query("best", description="Audio quality: best, high, medium, low"),
    format_type: str = Query("webm", description="Preferred format: webm, m4a, best")
):
    """Get direct audio stream URL"""
    try:
        video_id = extract_video_id(url)
        
        # Quality and format mapping
        quality_map = {
            "best": "bestaudio",
            "high": "bestaudio[abr>=128]",
            "medium": "bestaudio[abr>=96]",
            "low": "bestaudio[abr>=64]"
        }
        
        format_selector = quality_map.get(quality.lower(), "bestaudio")
        
        if format_type.lower() != "best":
            format_selector = f"bestaudio[ext={format_type.lower()}]/{format_selector}"
        
        cmd = [
            YTDLP_PATH,
            "--no-warnings",
            "--no-check-certificates",
            "--force-ipv4",
            "--geo-bypass",
            "-f", format_selector,
            "-g",
            "--no-playlist",
            f"https://www.youtube.com/watch?v={video_id}"
        ]
        
        if os.path.exists(COOKIES_PATH):
            cmd.extend(["--cookies", COOKIES_PATH])
        
        result = await run_command(cmd, timeout=60)
        
        if result["returncode"] != 0:
            raise HTTPException(status_code=500, detail=result["stderr"])
        
        audio_url = result["stdout"].strip()
        if not audio_url:
            raise HTTPException(status_code=404, detail="No audio stream found")
        
        # Check if it's a direct stream URL
        is_direct = "googlevideo.com" in audio_url
        
        return {
            "status": "success",
            "video_id": video_id,
            "quality": quality,
            "format": format_type,
            "audio_url": audio_url,
            "direct_stream": is_direct,
            "notes": "Use this URL directly with media players that support YouTube streams"
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Audio extraction failed: {e}")
        raise HTTPException(status_code=500, detail="Audio extraction failed")

@app.get("/audio/qualities")
async def get_audio_qualities(url: str = Query(...)):
    """Get all available audio qualities"""
    try:
        video_id = extract_video_id(url)
        
        cmd = build_ytdlp_cmd([f"https://www.youtube.com/watch?v={video_id}"])
        result = await run_command(cmd, timeout=90)
        
        if result["returncode"] != 0:
            raise HTTPException(status_code=500, detail=result["stderr"])
        
        info = json.loads(result["stdout"])
        
        # Check video duration
        duration = info.get("duration", 0)
        if duration > MAX_VIDEO_DURATION:
            raise HTTPException(
                status_code=400, 
                detail=f"Video too long ({duration}s > {MAX_VIDEO_DURATION}s limit)"
            )
        
        audio_formats = []
        for fmt in info.get("formats", []):
            if fmt.get("vcodec") == "none" and fmt.get("acodec") != "none":
                audio_formats.append({
                    "format_id": fmt.get("format_id"),
                    "extension": fmt.get("ext"),
                    "codec": fmt.get("acodec"),
                    "bitrate_kbps": fmt.get("abr"),
                    "sample_rate": fmt.get("asr"),
                    "channels": fmt.get("audio_channels"),
                    "filesize_mb": round(fmt.get("filesize", 0) / (1024 * 1024), 2) if fmt.get("filesize") else None,
                    "url": fmt.get("url"),
                    "protocol": fmt.get("protocol"),
                    "is_direct": "googlevideo.com" in (fmt.get("url") or "")
                })
        
        audio_formats.sort(key=lambda x: x.get("bitrate_kbps") or 0, reverse=True)
        
        return {
            "status": "success",
            "video_id": video_id,
            "title": info.get("title"),
            "duration": duration,
            "total_audio_formats": len(audio_formats),
            "audio_formats": audio_formats
        }
    
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        raise HTTPException(status_code=500, detail="Failed to parse video information")
    except Exception as e:
        logger.error(f"Failed to get audio qualities: {e}")
        raise HTTPException(status_code=500, detail="Failed to get audio qualities")

# -------------------------
# VIDEO QUALITIES ENDPOINT (Optimized)
# -------------------------
@app.get("/video/qualities")
async def get_video_qualities(
    url: str = Query(...),
    include_hls: bool = Query(False, description="Include HLS formats"),
    max_results: int = Query(50, description="Maximum number of formats to return", ge=1, le=100)
):
    """Get all available video qualities"""
    try:
        video_id = extract_video_id(url)
        
        cmd = build_ytdlp_cmd([f"https://www.youtube.com/watch?v={video_id}"])
        result = await run_command(cmd, timeout=REQUEST_TIMEOUT)
        
        if result["returncode"] != 0:
            error_msg = result["stderr"]
            if "Private video" in error_msg:
                raise HTTPException(status_code=403, detail="Video is private")
            elif "Video unavailable" in error_msg:
                raise HTTPException(status_code=404, detail="Video not found")
            elif "Age restricted" in error_msg:
                raise HTTPException(status_code=403, detail="Age-restricted video (cookies required)")
            else:
                raise HTTPException(status_code=500, detail=error_msg[:200])
        
        info = json.loads(result["stdout"])
        
        # Check video duration
        duration = info.get("duration", 0)
        if duration > MAX_VIDEO_DURATION:
            raise HTTPException(
                status_code=400, 
                detail=f"Video too long ({duration}s > {MAX_VIDEO_DURATION}s limit)"
            )
        
        formats = []
        for fmt in info.get("formats", []):
            if not fmt.get("url"):
                continue
            
            protocol = fmt.get("protocol", "").lower()
            is_hls = "m3u8" in protocol
            
            if not include_hls and is_hls:
                continue
            
            format_info = {
                "format_id": fmt.get("format_id"),
                "resolution": f"{fmt.get('width', '?')}x{fmt.get('height', '?')}",
                "width": fmt.get("width"),
                "height": fmt.get("height"),
                "fps": fmt.get("fps"),
                "video_codec": fmt.get("vcodec"),
                "audio_codec": fmt.get("acodec"),
                "bitrate_kbps": fmt.get("tbr"),
                "filesize_mb": round(fmt.get("filesize", 0) / (1024 * 1024), 2) if fmt.get("filesize") else None,
                "extension": fmt.get("ext"),
                "protocol": protocol,
                "url": fmt.get("url"),
                "is_hls": is_hls,
                "is_dash": "dash" in protocol,
                "has_video": fmt.get("vcodec") != "none",
                "has_audio": fmt.get("acodec") != "none",
                "quality_label": fmt.get("quality") or fmt.get("format_note", ""),
                "is_direct": "googlevideo.com" in (fmt.get("url") or "")
            }
            
            formats.append(format_info)
        
        # Sort: video with audio > resolution > fps > bitrate
        formats.sort(
            key=lambda x: (
                1 if x["has_video"] and x["has_audio"] else 0,
                x["height"] or 0,
                x["width"] or 0,
                x["fps"] or 0,
                x["bitrate_kbps"] or 0
            ),
            reverse=True
        )
        
        # Limit results
        formats = formats[:max_results]
        
        return {
            "status": "success",
            "video_id": video_id,
            "title": info.get("title"),
            "author": info.get("uploader"),
            "duration": duration,
            "view_count": info.get("view_count"),
            "upload_date": info.get("upload_date"),
            "thumbnail": info.get("thumbnail"),
            "total_formats_found": len(info.get("formats", [])),
            "formats_returned": len(formats),
            "formats": formats
        }
    
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        raise HTTPException(status_code=500, detail="Failed to parse video information")
    except Exception as e:
        logger.error(f"Video qualities extraction failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to get video qualities")

# -------------------------
# DIRECT VIDEO STREAM ENDPOINT
# -------------------------
@app.get("/video/stream")
async def get_video_stream(
    url: str = Query(...),
    quality: str = Query("720p", description="Desired quality (e.g., 720p, 480p, best, worst)"),
    with_audio: bool = Query(True, description="Include audio in stream")
):
    """Get direct video stream URL for specific quality"""
    try:
        video_id = extract_video_id(url)
        
        # Build format selector
        if quality.lower() == "best":
            format_selector = "best"
        elif quality.lower() == "worst":
            format_selector = "worst"
        elif "p" in quality.lower():
            # Parse resolution like "720p"
            match = re.match(r'(\d+)p', quality.lower())
            if match:
                height = int(match.group(1))
                if with_audio:
                    format_selector = f"best[height<={height}]"
                else:
                    format_selector = f"bestvideo[height<={height}]+bestaudio"
            else:
                format_selector = "best[height<=720]"
        else:
            format_selector = "best[height<=720]"
        
        cmd = [
            YTDLP_PATH,
            "--no-warnings",
            "--no-check-certificates",
            "--force-ipv4",
            "--geo-bypass",
            "-f", format_selector,
            "-g",
            "--no-playlist",
            f"https://www.youtube.com/watch?v={video_id}"
        ]
        
        if os.path.exists(COOKIES_PATH):
            cmd.extend(["--cookies", COOKIES_PATH])
        
        result = await run_command(cmd, timeout=60)
        
        if result["returncode"] != 0:
            raise HTTPException(status_code=500, detail=result["stderr"])
        
        stream_url = result["stdout"].strip()
        if not stream_url:
            raise HTTPException(status_code=404, detail="No video stream found")
        
        # Check if multiple URLs (video+audio separate)
        urls = stream_url.split('\n')
        
        return {
            "status": "success",
            "video_id": video_id,
            "quality": quality,
            "with_audio": with_audio,
            "stream_url": urls[0] if len(urls) == 1 else urls,
            "is_combined": len(urls) == 1,
            "url_count": len(urls),
            "notes": "For separate audio/video URLs, you need to mux them together with FFmpeg"
        }
    
    except Exception as e:
        logger.error(f"Video stream extraction failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to get video stream")

# -------------------------
# ERROR HANDLERS
# -------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Handle HTTP exceptions"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "error": exc.detail,
            "path": request.url.path
        }
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request, exc):
    """Handle generic exceptions"""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "status": "error",
            "error": "Internal server error",
            "path": request.url.path
        }
    )

# Run the application
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api:app",
        host="0.0.0.0",
        port=PORT,
        workers=WORKERS,
        log_level="info",
        access_log=True
    )
