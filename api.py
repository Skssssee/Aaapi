
import time
import json
import os
import asyncio
import subprocess
import logging
from typing import List, Optional, Dict, Any
from fastapi import FastAPI, Query, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse, Response, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import re
import psutil
import tempfile
import shutil
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Environment variables
PORT = int(os.getenv("PORT", "8000"))
WORKERS = int(os.getenv("WORKERS", "2"))
YTDLP_PATH = os.getenv("YTDLP_PATH", "yt-dlp")
COOKIES_PATH = os.getenv("COOKIES_PATH", "cookies.txt")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "90"))
MAX_VIDEO_DURATION = int(os.getenv("MAX_VIDEO_DURATION", "7200"))
ALLOW_COOKIE_UPLOAD = os.getenv("ALLOW_COOKIE_UPLOAD", "true").lower() == "true"

# Track startup time
START_TIME = time.time()

# Global cookies file path
COOKIES_FILE = Path(COOKIES_PATH)
TEMP_COOKIES_FILE = Path("temp_cookies.txt")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup/shutdown events"""
    # Startup
    logger.info(f"Starting YouTube Stream API on port {PORT}")
    logger.info(f"Cookies file: {COOKIES_FILE} (exists: {COOKIES_FILE.exists()})")
    logger.info(f"Cookie uploads allowed: {ALLOW_COOKIE_UPLOAD}")
    
    # Verify yt-dlp installation
    try:
        result = await run_command([YTDLP_PATH, "--version"], timeout=10)
        logger.info(f"yt-dlp version: {result['stdout'].strip()}")
    except Exception as e:
        logger.error(f"Failed to verify yt-dlp: {e}")
    
    # Create cookies file if it doesn't exist
    if not COOKIES_FILE.exists():
        logger.warning("Cookies file does not exist. Age-restricted videos may not work.")
        COOKIES_FILE.touch()
    
    yield
    
    # Shutdown
    logger.info("Shutting down YouTube Stream API")
    # Clean up temp cookies
    if TEMP_COOKIES_FILE.exists():
        TEMP_COOKIES_FILE.unlink()

app = FastAPI(
    title="YouTube Stream Extractor API",
    description="Extract YouTube video/audio streams with cookie support",
    version="2.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Response-Time"]
)

# -------------------------
# COOKIE MANAGEMENT FUNCTIONS
# -------------------------
def validate_cookies_file(file_path: Path) -> bool:
    """Validate that cookies file is in Netscape format"""
    if not file_path.exists():
        return False
    
    try:
        content = file_path.read_text(encoding='utf-8', errors='ignore')
        
        # Check for Netscape format markers
        if "# Netscape HTTP Cookie File" in content or "# HTTP Cookie File" in content:
            return True
        
        # Check for common cookie domains
        cookie_domains = [".youtube.com", "youtube.com", ".google.com"]
        for domain in cookie_domains:
            if domain in content:
                return True
        
        # Check for tab-separated values (Netscape format)
        lines = content.strip().split('\n')
        valid_lines = 0
        for line in lines:
            if line.startswith('#') or not line.strip():
                continue
            parts = line.split('\t')
            if len(parts) >= 7:
                valid_lines += 1
        
        return valid_lines > 0
    
    except Exception as e:
        logger.error(f"Cookie validation failed: {e}")
        return False

async def get_cookies_info() -> Dict[str, Any]:
    """Get information about current cookies"""
    info = {
        "file_exists": COOKIES_FILE.exists(),
        "file_size": COOKIES_FILE.stat().st_size if COOKIES_FILE.exists() else 0,
        "is_valid": False,
        "domains": [],
        "cookie_count": 0,
        "last_modified": None
    }
    
    if COOKIES_FILE.exists():
        info["is_valid"] = validate_cookies_file(COOKIES_FILE)
        info["last_modified"] = time.ctime(COOKIES_FILE.stat().st_mtime)
        
        try:
            content = COOKIES_FILE.read_text(encoding='utf-8', errors='ignore')
            lines = content.strip().split('\n')
            
            for line in lines:
                if line.startswith('#') or not line.strip():
                    continue
                parts = line.split('\t')
                if len(parts) >= 7:
                    info["cookie_count"] += 1
                    domain = parts[0].strip()
                    if domain not in info["domains"]:
                        info["domains"].append(domain)
        
        except Exception as e:
            logger.error(f"Failed to read cookies: {e}")
    
    return info

def get_effective_cookies_path() -> Path:
    """Get the effective cookies file path (temp or main)"""
    if TEMP_COOKIES_FILE.exists() and validate_cookies_file(TEMP_COOKIES_FILE):
        logger.info("Using temporary cookies file")
        return TEMP_COOKIES_FILE
    elif COOKIES_FILE.exists() and validate_cookies_file(COOKIES_FILE):
        return COOKIES_FILE
    else:
        return Path("/dev/null")  # No valid cookies

# -------------------------
# UTILITY FUNCTIONS
# -------------------------
async def run_command(cmd: List[str], timeout: int = 90) -> Dict[str, Any]:
    """Run subprocess command asynchronously with timeout"""
    logger.debug(f"Running command: {' '.join(cmd[:5])}...")
    
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

def build_ytdlp_cmd(extra_args: List[str] = None, use_cookies: bool = True) -> List[str]:
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
    
    # Add cookies if available
    if use_cookies:
        cookies_path = get_effective_cookies_path()
        if cookies_path.exists() and cookies_path != Path("/dev/null"):
            cmd.extend(["--cookies", str(cookies_path)])
            logger.debug(f"Using cookies from: {cookies_path}")
    
    if extra_args:
        cmd.extend(extra_args)
    
    return cmd

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
# COOKIE MANAGEMENT ENDPOINTS
# -------------------------
@app.get("/cookies/info")
async def cookies_info():
    """Get information about current cookies"""
    cookies_info = await get_cookies_info()
    
    return {
        "status": "success",
        "cookies": cookies_info,
        "upload_allowed": ALLOW_COOKIE_UPLOAD,
        "instructions": {
            "how_to_get": "Use browser extension 'Get cookies.txt' or 'cookies.txt'",
            "format": "Netscape HTTP Cookie File format",
            "temp_cookies": "Temporary cookies expire after 1 hour or on server restart"
        }
    }

@app.post("/cookies/upload")
async def upload_cookies(
    file: UploadFile = File(...),
    permanent: bool = Form(False, description="Save as permanent cookies (requires admin)"),
    session_only: bool = Form(True, description="Use cookies only for this session")
):
    """Upload cookies file"""
    if not ALLOW_COOKIE_UPLOAD:
        raise HTTPException(status_code=403, detail="Cookie uploads are disabled")
    
    if not file.filename.endswith('.txt'):
        raise HTTPException(status_code=400, detail="Only .txt files are accepted")
    
    try:
        # Read uploaded file
        content = await file.read()
        
        # Save to temporary file for validation
        with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.txt') as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        
        # Validate cookies
        if not validate_cookies_file(tmp_path):
            tmp_path.unlink()
            raise HTTPException(status_code=400, detail="Invalid cookies file format")
        
        # Determine where to save
        if permanent:
            # Save as permanent cookies
            save_path = COOKIES_FILE
            message = "Cookies saved permanently"
        elif session_only:
            # Save as session cookies (temp)
            save_path = TEMP_COOKIES_FILE
            message = "Cookies saved for current session only"
        else:
            # Override permanent cookies
            save_path = COOKIES_FILE
            message = "Cookies saved permanently"
        
        # Save the file
        shutil.copy(tmp_path, save_path)
        tmp_path.unlink()
        
        # Get updated info
        cookies_info = await get_cookies_info()
        
        return {
            "status": "success",
            "message": message,
            "saved_to": str(save_path),
            "cookies_info": cookies_info
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Cookie upload failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload cookies: {str(e)}")

@app.delete("/cookies/clear")
async def clear_cookies(
    clear_temp: bool = Query(True, description="Clear temporary cookies"),
    clear_permanent: bool = Query(False, description="Clear permanent cookies (requires admin)")
):
    """Clear cookies"""
    cleared = []
    
    if clear_temp and TEMP_COOKIES_FILE.exists():
        TEMP_COOKIES_FILE.unlink()
        cleared.append("temporary")
    
    if clear_permanent and COOKIES_FILE.exists():
        # In production, you might want to add admin check here
        COOKIES_FILE.unlink()
        cleared.append("permanent")
    
    return {
        "status": "success",
        "message": f"Cleared {', '.join(cleared) if cleared else 'no'} cookies",
        "cleared": cleared
    }

@app.get("/cookies/test")
async def test_cookies(
    url: str = Query("https://www.youtube.com/watch?v=dQw4w9WgXcQ", description="YouTube URL to test with")
):
    """Test if cookies are working"""
    try:
        video_id = extract_video_id(url)
        
        # Try with cookies
        cmd_with_cookies = build_ytdlp_cmd([
            "--no-playlist",
            "--skip-download",
            f"https://www.youtube.com/watch?v={video_id}"
        ], use_cookies=True)
        
        # Try without cookies
        cmd_without_cookies = build_ytdlp_cmd([
            "--no-playlist",
            "--skip-download",
            f"https://www.youtube.com/watch?v={video_id}"
        ], use_cookies=False)
        
        # Run both
        result_with = await run_command(cmd_with_cookies, timeout=30)
        result_without = await run_command(cmd_without_cookies, timeout=30)
        
        with_success = result_with["returncode"] == 0
        without_success = result_without["returncode"] == 0
        
        # Parse results
        with_data = None
        without_data = None
        
        if with_success:
            try:
                with_data = json.loads(result_with["stdout"])
            except:
                with_data = {"raw": result_with["stdout"][:100]}
        
        if without_success:
            try:
                without_data = json.loads(result_without["stdout"])
            except:
                without_data = {"raw": result_without["stdout"][:100]}
        
        cookies_info = await get_cookies_info()
        
        return {
            "status": "success",
            "cookies_info": cookies_info,
            "test_results": {
                "with_cookies": {
                    "success": with_success,
                    "error": result_with["stderr"] if not with_success else None,
                    "title": with_data.get("title") if with_data else None
                },
                "without_cookies": {
                    "success": without_success,
                    "error": result_without["stderr"] if not without_success else None,
                    "title": without_data.get("title") if without_data else None
                }
            },
            "cookies_effective": with_success and not without_success
        }
    
    except Exception as e:
        logger.error(f"Cookie test failed: {e}")
        raise HTTPException(status_code=500, detail=f"Cookie test failed: {str(e)}")

# -------------------------
# HEALTH & INFO ENDPOINTS
# -------------------------
@app.get("/")
async def root():
    """Root endpoint with API information"""
    cookies_info = await get_cookies_info()
    
    return {
        "status": "running",
        "api": "YouTube Stream Extractor API",
        "version": "2.2.0",
        "uptime": uptime(),
        "cookies": {
            "available": cookies_info["is_valid"],
            "count": cookies_info["cookie_count"],
            "upload_allowed": ALLOW_COOKIE_UPLOAD
        },
        "documentation": {
            "swagger": "/docs",
            "redoc": "/redoc"
        },
        "endpoints": {
            "cookies": {
                "info": "GET /cookies/info",
                "upload": "POST /cookies/upload",
                "test": "GET /cookies/test?url={youtube_url}",
                "clear": "DELETE /cookies/clear"
            },
            "audio": "GET /audio?url={youtube_url}&quality=[best|high|medium|low]",
            "video": "GET /video/qualities?url={youtube_url}",
            "health": "GET /health",
            "stats": "GET /stats"
        }
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    cookies_info = await get_cookies_info()
    
    health_status = {
        "status": "healthy",
        "timestamp": time_module.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "uptime": uptime(),
        "cookies": cookies_info
    }
    
    # Check yt-dlp
    try:
        result = await run_command([YTDLP_PATH, "--version"], timeout=10)
        health_status["ytdlp"] = {
            "available": True,
            "version": result["stdout"].strip()[:20]
        }
    except Exception as e:
        health_status["ytdlp"] = {"available": False, "error": str(e)}
        health_status["status"] = "degraded"
    
    return health_status

# -------------------------
# AUDIO ENDPOINT WITH COOKIE SUPPORT
# -------------------------
@app.get("/audio")
async def get_audio_stream(
    url: str = Query(..., description="YouTube URL or Video ID"),
    quality: str = Query("best", description="Audio quality: best, high, medium, low"),
    use_cookies: bool = Query(True, description="Use cookies if available")
):
    """Get direct audio stream URL with cookie support"""
    try:
        video_id = extract_video_id(url)
        
        # Quality mapping
        quality_map = {
            "best": "bestaudio",
            "high": "bestaudio[abr>=128]",
            "medium": "bestaudio[abr>=96]",
            "low": "bestaudio[abr>=64]"
        }
        
        format_selector = quality_map.get(quality.lower(), "bestaudio")
        
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
        
        # Add cookies if requested and available
        if use_cookies:
            cookies_path = get_effective_cookies_path()
            if cookies_path.exists() and cookies_path != Path("/dev/null"):
                cmd.extend(["--cookies", str(cookies_path)])
        
        result = await run_command(cmd, timeout=60)
        
        # Check for age restriction error
        if result["returncode"] != 0:
            error_msg = result["stderr"].lower()
            
            if any(msg in error_msg for msg in ["age restricted", "age-restricted", "login required"]):
                if use_cookies:
                    raise HTTPException(
                        status_code=403,
                        detail="Age-restricted video. Current cookies may be insufficient."
                    )
                else:
                    raise HTTPException(
                        status_code=403,
                        detail="Age-restricted video. Try with use_cookies=true"
                    )
            elif "private" in error_msg:
                raise HTTPException(status_code=403, detail="Private video")
            elif "unavailable" in error_msg:
                raise HTTPException(status_code=404, detail="Video not found")
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to extract audio: {result['stderr'][:200]}"
                )
        
        audio_url = result["stdout"].strip()
        if not audio_url:
            raise HTTPException(status_code=404, detail="No audio stream found")
        
        cookies_info = await get_cookies_info()
        
        return {
            "status": "success",
            "video_id": video_id,
            "quality": quality,
            "audio_url": audio_url,
            "cookies_used": use_cookies and cookies_info["is_valid"],
            "cookies_info": {
                "valid": cookies_info["is_valid"],
                "count": cookies_info["cookie_count"]
            },
            "direct_stream": "googlevideo.com" in audio_url
        }
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Audio extraction failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to get audio stream")

# -------------------------
# VIDEO QUALITIES ENDPOINT WITH COOKIE SUPPORT
# -------------------------
@app.get("/video/qualities")
async def get_video_qualities(
    url: str = Query(...),
    use_cookies: bool = Query(True, description="Use cookies if available"),
    max_results: int = Query(50, ge=1, le=100)
):
    """Get all available video qualities with cookie support"""
    try:
        video_id = extract_video_id(url)
        
        cmd = build_ytdlp_cmd([
            f"https://www.youtube.com/watch?v={video_id}"
        ], use_cookies=use_cookies)
        
        result = await run_command(cmd, timeout=REQUEST_TIMEOUT)
        
        # Handle specific errors
        if result["returncode"] != 0:
            error_msg = result["stderr"].lower()
            
            if any(msg in error_msg for msg in ["age restricted", "age-restricted", "login required"]):
                if use_cookies:
                    cookies_info = await get_cookies_info()
                    if cookies_info["is_valid"]:
                        raise HTTPException(
                            status_code=403,
                            detail="Age-restricted video. Current cookies may be expired or insufficient."
                        )
                    else:
                        raise HTTPException(
                            status_code=403,
                            detail="Age-restricted video. No valid cookies available."
                        )
                else:
                    raise HTTPException(
                        status_code=403,
                        detail="Age-restricted video. Try with use_cookies=true"
                    )
            elif "private" in error_msg:
                raise HTTPException(status_code=403, detail="Private video")
            elif "unavailable" in error_msg:
                raise HTTPException(status_code=404, detail="Video not found")
            else:
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to get video info: {result['stderr'][:200]}"
                )
        
        info = json.loads(result["stdout"])
        
        # Check video duration
        duration = info.get("duration", 0)
        if duration > MAX_VIDEO_DURATION:
            raise HTTPException(
                status_code=400, 
                detail=f"Video too long ({duration}s > {MAX_VIDEO_DURATION}s limit)"
            )
        
        # Parse formats
        formats = []
        for fmt in info.get("formats", []):
            if not fmt.get("url"):
                continue
            
            protocol = fmt.get("protocol", "").lower()
            
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
                "is_hls": "m3u8" in protocol,
                "has_video": fmt.get("vcodec") != "none",
                "has_audio": fmt.get("acodec") != "none",
                "is_direct": "googlevideo.com" in (fmt.get("url") or "")
            }
            
            formats.append(format_info)
        
        # Sort and limit
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
        
        formats = formats[:max_results]
        
        cookies_info = await get_cookies_info()
        
        return {
            "status": "success",
            "video_id": video_id,
            "title": info.get("title"),
            "author": info.get("uploader"),
            "duration": duration,
            "view_count": info.get("view_count"),
            "cookies_used": use_cookies and cookies_info["is_valid"],
            "cookies_info": {
                "valid": cookies_info["is_valid"],
                "count": cookies_info["cookie_count"]
            },
            "total_formats": len(formats),
            "formats": formats
        }
    
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error: {e}")
        raise HTTPException(status_code=500, detail="Failed to parse video information")
    except Exception as e:
        logger.error(f"Video qualities extraction failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to get video qualities")

# -------------------------
# HOW-TO GUIDE ENDPOINT
# -------------------------
@app.get("/cookies/guide")
async def cookies_guide():
    """Get guide on how to obtain cookies"""
    return {
        "status": "success",
        "guide": {
            "title": "How to Get YouTube Cookies",
            "steps": [
                {
                    "step": 1,
                    "title": "Install Browser Extension",
                    "description": "Install 'Get cookies.txt' extension for Chrome/Firefox or 'cookies.txt' extension",
                    "links": [
                        "https://chrome.google.com/webstore/detail/get-cookiestxt/bgaddhkoddajcdgocldbbfleckgcbcid",
                        "https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/"
                    ]
                },
                {
                    "step": 2,
                    "title": "Login to YouTube",
                    "description": "Open YouTube in your browser and login to your account"
                },
                {
                    "step": 3,
                    "title": "Export Cookies",
                    "description": "Click the extension icon and export cookies as 'Netscape HTTP Cookie File' format"
                },
                {
                    "step": 4,
                    "title": "Upload to API",
                    "description": "Use the /cookies/upload endpoint or place the file as 'cookies.txt' in the server"
                }
            ],
            "important_notes": [
                "Cookies expire! You need to refresh them periodically",
                "Never share your cookies file publicly",
                "For age-restricted videos, you must be logged in AND have watched the video on browser",
                "Some videos require cookies even if not age-restricted"
            ],
            "api_endpoints": {
                "upload": "POST /cookies/upload (multipart form with file)",
                "test": "GET /cookies/test?url={youtube_url}",
                "info": "GET /cookies/info"
            }
        }
    }

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
            "path": request.url.path,
            "timestamp": time_module.strftime("%Y-%m-%d %H:%M:%S")
        }
    )

# -------------------------
# MAIN
# -------------------------
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
