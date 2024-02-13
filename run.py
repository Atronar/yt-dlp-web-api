"""

"""
from typing import Any, Generic, Iterable, Literal, NotRequired, Required, TypeVar, TypedDict
import json
import asyncio
import hashlib
import os
import random
import uuid
import zipfile
import datetime
from yt_dlp import YoutubeDL
import tornado.web
import tornado.escape
import requests
from moviepy.video.io.ffmpeg_tools import ffmpeg_extract_subclip
from moviepy.editor import VideoFileClip
from pygifsicle import optimize
from mutagen.easyid3 import EasyID3
import sentry_sdk

# TODO: auto-reload/reload on webhook using gitpython

# README: functionality is described once per documentation in order to leave as
# little clutter as possible

class _Conf(TypedDict):
    maxLength: int
    maxPlaylistLength: int
    maxGifLength: int
    maxGifResolution: int
    maxLengthPlaylistVideo: int
    proxyListURL: str|Literal[False]
    url: str
    listeningPort: int
    bugcatcher: bool
    bugcatcherdsn: str
    allowedorigins: str|list[str]
    downloadsPath: str

confpath = ".conf.json"
"""Local path to json config"""

if "docs" in os.getcwd():
    # Check if building docs, if so, change conf path
    confpath = "../.conf.json"

# Load configuration at runtime
with open(confpath, "r", encoding="utf-8") as conffile:
    conf: _Conf = json.loads(conffile.read())
    """ Global configuration variable """

# If using bugcatcher such as Glitchtip/Sentry set it up
if conf["bugcatcher"]:
    sentry_sdk.init(conf["bugcatcherdsn"])

def dlProxies(proxy_list_url: str|bytes = "", path: str|os.PathLike = "proxies.txt"):
    """
    Function to download proxies from plain url to a given path.
    this is useful for me, but if other people need to utilize a more complex method
    of downloading proxies I recommend implementing it and doing a merge request
    """
    response = requests.get(proxy_list_url, timeout=30)
    rlist = response.text.split("\n")
    rlistfixed: list[str] = []
    for p in rlist[:-1]:
        pl = p.replace("\n", "").replace("\r", "").split(":")
        proxy = f"{pl[2]}:{pl[3]}@{pl[0]}:{pl[1]}"
        rlistfixed.append(proxy)

    with open(path, "w", encoding="utf-8") as file:
        file.write("\n".join(rlistfixed))

    print("Proxies refreshed!")

# If using proxy list url and there's no proxies file, download proxies at runtime
if conf["proxyListURL"] is not False:
    if not os.path.exists("proxies.txt"):
        dlProxies(proxy_list_url=conf["proxyListURL"])

class _ResponseDict(TypedDict, total=False):
    method: Required[str]
    error: Required[bool]
    spinnerid: Required[str|None]

def resInit(method: str, spinnerid: str|None) -> _ResponseDict:
    """
    Function to initialize response to client
    Takes method and spinnerid
    spinnerid is the id of the spinner object to remove on the ui, none is fine here
    """
    res: _ResponseDict = {
        "method": method,
        "error": True,
        "spinnerid": spinnerid
    }
    return res

class _ToMP3RequestData(TypedDict):
    spinnerid: str|None
    url: str
    id3: dict[str, str|None]

class _ToMP3ResponseData(_ResponseDict, total=False):
    link: str
    title: str
    details: str

async def toMP3(sid: str|None, data: _ToMP3RequestData, loop: int=0) -> _ToMP3ResponseData:
    """
    Socketio event, takes the client id, a json payload and a loop count for retries
    Converts link to mp3 file
    """
    # Initialize response, if spinnerid data doesn't exist it will just set it to none
    res: _ToMP3ResponseData = resInit("toMP3", data.get("spinnerid")) # type: ignore
    # Try/catch loop will send error message to client on error
    try:
        # Get video url from data
        url = data["url"]
        if "list" in url:
            raise ValueError("Method is for singular videos")
        # Get information about the video via yt-dlp to make future decisions
        info = getInfo(url)
        # Return an error if the video is longer than the configured maximum video length
        if info["duration"] > conf["maxLength"]:
            raise ValueError("Video is longer than configured maximum length")

        # Get file system safe title for video
        title = makeSafe(info["title"])
        # Download video as MP3 from given url and get the final title of the video
        ftitle = download(url, True, title, "mp3")
        # Tell the client there is no error
        res["error"] = False
        # Give the client the download link
        res["link"] = f'{conf["url"]}/downloads/{ftitle}.mp3'
        # Give the client the initial safe title just for display on the ui
        res["title"] = title
        # If there is id3 metadata apply this metadata to the file
        if data["id3"] is not None:
            # We use EasyID3 here as, well, it's easy, if you need to add more fields
            # please read the mutagen documentation for this here:
            # https://mutagen.readthedocs.io/en/latest/user/id3.html
            audio = EasyID3(os.path.join(conf["downloadsPath"], f"{ftitle}.mp3"))
            for key, value in data["id3"].items():
                if value not in ("", None):
                    audio[key] = value
            audio.save()
        # Emit result to client
        return res
    except OSError as exc:
        if loop > 0:
            # Get text of error
            res["details"] = str(exc)
            return res
        return await toMP3(sid, data, loop=1)
    except Exception as exc:
        # Get text of error
        res["details"] = str(exc)
        return res

class _PlaylistRequestData(TypedDict):
    spinnerid: str|None
    url: str

class _PlaylistResponseData(_ResponseDict, total=False):
    link: str
    title: str
    details: str

async def playlist(sid: str|None, data: _PlaylistRequestData, loop: int=0) -> _PlaylistResponseData:
    """
    Downloads playlist as a zip of MP3s
    """
    res: _PlaylistResponseData = resInit("playlist", data.get("spinnerid")) # type: ignore
    try:
        purl = data["url"]
        # Get playlist info
        info = getInfo(purl)
        # Create playlist title from the file system safe title and a random uuid
        # The uuid is to prevent two users from accidentally overwriting each other's files
        # (very unlikely due to cleanup but still possible)
        ptitle = makeSafe(info["title"]) + str(uuid.uuid4())
        # If the number of entries is larger than the configured maximum
        # playlist length throw an error
        if len(info["entries"]) > conf["maxPlaylistLength"]:
            raise ValueError("Playlist is longer than configured maximum length")

        # Check the length of all videos in the playlist,
        # if any are longer than the configured maximum
        # length for playlist videos throw an error
        for v in info["entries"]:
            if v["duration"] > conf["maxLengthPlaylistVideo"]:
                raise ValueError("Video in playlist is longer than configured maximum length")
        # Iterate through all videos on the playlist,
        # download each one as an MP3 and then write it to the playlist zip file
        title = ""
        for v in info["entries"]:
            #TODO: make generic
            vid = v["id"]
            vurl = "https://www.youtube.com/watch?v=" + vid
            title = makeSafe(v["title"])
            ftitle = download(vurl, True, title, "mp3")
            with zipfile.ZipFile(
                os.path.join(conf["downloadsPath"], f'{ptitle}.zip'),
                'a'
            ) as myzip:
                myzip.write(os.path.join(conf["downloadsPath"], f"{ftitle}.mp3"))
        res["error"] = False
        res["link"] = f'{conf["url"]}/downloads/{ptitle}.zip'
        res["title"] = title
        return res
    except OSError as exc:
        if loop > 0:
            # Get text of error
            res["details"] = str(exc)
            return res
        return await playlist(sid, data, loop=1)
    except Exception as exc:
        res["details"] = str(exc)
        return res

class _SubtitlesRequestData(TypedDict):
    spinnerid: str|None
    url: str
    step: str|int
    languageCode: str|None
    autoSub: bool

class _SubtitlesResponseData(_ResponseDict, total=False):
    step: int
    link: str
    title: str
    select: list
    details: str

async def subtitles(
    sid: str|None,
    data: _SubtitlesRequestData,
    loop: int=0
) -> _SubtitlesResponseData:
    """
    Two step event
    1. Get list of subtitles
    2. Download chosen subtitle file
    """
    res: _SubtitlesResponseData = resInit("subtitles", data.get("spinnerid")) # type: ignore
    try:
        step = int(data["step"])
        url = data["url"]
        if "list" in url:
            raise ValueError("Method is for singular videos")
        # Step 1 of subtitles is to get the list of subtitles available and return them
        if step == 1:
            info = getInfo(url, getSubtitles=True)
            title = makeSafe(info["title"])
            res["error"] = False
            res["title"] = title
            # List of subtitle keys for picking subtitles
            res["select"] = list(info["subtitles"].keys())
            # Step for front end use, the value here doesn't really matter,
            # the variable just has to exist to tell the ui to move to step 2
            # when the method is called again
            res["step"] = 0
            # Again details doesn't need a value it just needs to exist to let
            # the front end know to populate the details column with a select
            # defined by the list provided by select
            res["details"] = ""
            return res
        # Step 2 of subtitles is to download the subtitles to the server
        # and provide that link to the user
        if step == 2:
            # Get the selected subtitles by language code
            languageCode = data["languageCode"]
            # Check if the user wants to download autosubs
            autoSub = data["autoSub"]
            info = getInfo(url)
            title = makeSafe(info["title"])
            # Download the subtitles
            # Unfortunately at the moment this requires downloading the lowest quality stream
            # as well, in the future some modification to yt-dlp might be necessary to avoid this
            ftitle = download(
                url,
                False,
                title,
                "subtitles",
                languageCode=languageCode,
                autoSub=autoSub
            )
            res["error"] = False
            res["link"] = f'{conf["url"]}/downloads/{ftitle}.{languageCode}.vtt'
            res["title"] = title
            return res
    except OSError as exc:
        if loop > 0:
            # Get text of error
            res["details"] = str(exc)
            return res
        return await subtitles(sid, data, loop=1)
    except Exception as exc:
        res["details"] = str(exc)
        return res
    return res

class _ClipRequestData(TypedDict):
    spinnerid: str|None
    url: str
    directURL: NotRequired[str|bytes]
    gif: NotRequired[bool]
    format_id: NotRequired[str]
    timeA: str|int
    timeB: str|int

class _ClipResponseData(_ResponseDict, total=False):
    link: str
    title: str
    details: str

async def clip(sid: str|None, data: _ClipRequestData, loop: int=0) -> _ClipResponseData:
    """
    Event to clip a given stream and return the clip to the user,
    the user can optionally convert this clip into a gif
    """
    res: _ClipResponseData = resInit("clip", data.get("spinnerid")) # type: ignore
    try:
        url = data["url"]
        if "list" in url:
            raise ValueError("Method is for singular videos")
        info = getInfo(url)
        # Check if directURL is in the data from the client
        # directURL defines a video url to download from directly instead of through yt-dlp
        directURL = None
        if "directURL" in data.keys():
            directURL = data["directURL"]
        # Check if user wants to create a gif
        gif = False
        if "gif" in data.keys():
            gif = bool(data["gif"])
        # Get the format id the user wants for downloading a given stream from a given video
        format_id = None
        if "format_id" in data.keys():
            format_id = data["format_id"]
        if info["duration"] > conf["maxLength"]:
            raise ValueError("Video is longer than configured maximum length")
        # Get the start and end time for the clip
        timeA = int(data["timeA"])
        timeB = int(data["timeB"])
        # If we're making a gif make sure the clip is not longer than the maximum gif length
        # Please be careful with gif lengths,
        # if you set this too high you may end up with huge gifs hogging the server
        if gif and ((timeB - timeA) > conf["maxGifLength"]):
            raise ValueError("Range is too large for gif")
        title = makeSafe(info["title"])
        # If the directURL is set download directly
        if directURL is not None:
            ititle = f'{title}.{info["ext"]}'
            downloadDirect(directURL, os.path.join(conf["downloadsPath"], ititle))
        # Otherwise download the video through yt-dlp
        # If there's no format id just get the default video
        else:
            if format_id is not None:
                ititle = download(
                    url,
                    False,
                    title,
                    "mp4",
                    extension=info["ext"],
                    format_id=format_id
                )
            else:
                ititle = download(
                    url,
                    False,
                    title,
                    "mp4",
                    extension=info["ext"]
                )
        cuuid = uuid.uuid4()
        if gif:
            # Clip video and then convert it to a gif
            ((VideoFileClip(os.path.join(conf["downloadsPath"], ititle)))
                .subclip(timeA, timeB)
                .write_gif(os.path.join(conf["downloadsPath"], f"{title}.{cuuid}.clipped.gif")))
            # Optimize the gif
            optimize(os.path.join(conf["downloadsPath"], f"{title}.clipped.gif"))
        else:
            # Clip the video and return the mp4 of the clip
            ffmpeg_extract_subclip(
                os.path.join(conf["downloadsPath"], ititle),
                timeA,
                timeB,
                targetname=os.path.join(conf["downloadsPath"], f"{title}.{cuuid}.clipped.mp4")
            )
        res["error"] = False
        # Set the extension to use either to mp4 or gif depending on whether the user wanted a gif
        # The extension is just for creating the url for the clip
        extension = "mp4"
        if gif:
            extension = "gif"
        res["link"] = f'{conf["url"]}/downloads/{title}.{cuuid}.clipped.{extension}'
        res["title"] = title
        return res
    except OSError as exc:
        if loop > 0:
            # Get text of error
            res["details"] = str(exc)
            return res
        return await clip(sid, data, loop=1)
    except Exception as exc:
        res["details"] = str(exc)
        return res

class _CombineRequestData(TypedDict):
    spinnerid: str|None
    url: str
    format_id: NotRequired[str]
    format_id_audio: NotRequired[str]

class _CombineResponseData(_ResponseDict, total=False):
    link: str
    title: str
    details: str

async def combine(sid: str|None, data: _CombineRequestData, loop: int=0) -> _CombineResponseData:
    """
    Combine audio and video streams
    """
    res: _CombineResponseData = resInit("combine", data.get("spinnerid")) # type: ignore
    try:
        curl = data["url"]
        # Get video info
        info = getInfo(curl)
        # Create the video title from the file system safe title and a random uuid
        # The uuid is to prevent two users from accidentally overwriting each other's files
        # (very unlikely due to cleanup but still possible)
        ptitle = f'{makeSafe(info["title"])}{uuid.uuid4()}'
        # If the number of entries is larger than the configured maximum playlist length
        # throw an error
        if "list" in curl:
            raise ValueError("This method is for a single video")

        # Check the length of the video, if it's too long throw an error
        if info["duration"] > conf["maxLength"]:
            raise ValueError("Video is longer than configured maximum length")
        title = download(
            curl,
            False,
            ptitle,
            None,
            extension="mp4",
            format_id=data["format_id"],
            format_id_audio=data["format_id_audio"]
        )
        res["error"] = False
        res["link"] = f'{conf["url"]}/downloads/{title}'
        res["title"] = ptitle
        return res
    except OSError as exc:
        if loop > 0:
            # Get text of error
            res["details"] = str(exc)
            return res
        return await playlist(sid, data, loop=1)
    except Exception as exc:
        res["details"] = str(exc)
        return res

class _GetInfoEventRequestData(TypedDict):
    spinnerid: str|None
    method: str
    url: str

class _GetInfoEventResponseData(_ResponseDict, total=False):
    link: str
    title: str
    select: Any
    ext: str
    info: Any
    details: str

async def getInfoEvent(sid: str|None, data: _GetInfoEventRequestData) -> _GetInfoEventResponseData:
    """
        Generic event to get all the information provided by yt-dlp for a given url
    """
    # Unlike other events we set the method here from the passed method
    # in order to make this generic and flexible
    res: _GetInfoEventResponseData = resInit(data["method"], data.get("spinnerid")) # type: ignore
    try:
        url = data["url"]
        if "list" in url:
            raise ValueError("Method is for singular videos")
        info = getInfo(url)
        if data["method"] == "streams":
            res["details"] = ""
            res["select"] = ""
        title = makeSafe(info["title"])
        res["ext"] = info.get("ext", "")
        res["error"] = False
        res["title"] = title
        res["info"] = info
        return res
    except Exception as exc:
        res["details"] = str(exc)
        return res

class _LimitsRequestData(TypedDict):
    spinnerid: str|None

class _Limit(TypedDict):
    limitid: str
    limitvalue: int

class _LimitsResponseData(_ResponseDict, total=False):
    limits: list[_Limit]
    details: str

async def limits(sid: str|None, data: _LimitsRequestData) -> _LimitsResponseData:
    """
    Get set limits of server for display in UI
    """
    res: _LimitsResponseData = resInit("limits", data.get("spinnerid")) # type: ignore
    try:
        _limits = [
            "maxLength",
            "maxPlaylistLength",
            "maxGifLength",
            "maxGifResolution",
            "maxLengthPlaylistVideo"
        ]
        res["limits"] = [{"limitid": limit, "limitvalue": conf[limit]} for limit in _limits]
        res["error"] = False
        return res
    except Exception as exc:
        res["details"] = str(exc)
        return res

_PostprocessorName = TypeVar("_PostprocessorName", bound=str)

class _BasePostprocessorData(Generic[_PostprocessorName], TypedDict):
    key: _PostprocessorName

class _FFmpegExtractAudioPostprocessorData(
    _BasePostprocessorData[Literal['FFmpegExtractAudio']],
    total=False
):
    preferredcodec: str|None
    preferredquality: str|int|float|None

_PostprocessorData = _BasePostprocessorData | _FFmpegExtractAudioPostprocessorData

class _YdlOptsData(TypedDict, total=False):
    format: str|None
    outtmpl: str
    writesubtitles: bool
    writeautomaticsub: bool
    subtitleslangs: Iterable[str]|None
    proxy: str|None
    postprocessors: list[_PostprocessorData]

def download(
        url: str,
        isAudio: bool,
        title: str,
        codec: str|None,
        languageCode: str|None = None,
        autoSub: bool = False,
        extension: str|None = None,
        format_id: str|None = None,
        format_id_audio: str|None = None
    ) -> str:
    """
    Generic download method
    """
    # Used to avoid filename conflicts
    ukey = hashlib.md5(url.encode()).hexdigest()
    # Set the location/name of the output file
    ydl_opts: _YdlOptsData = {
        'outtmpl': os.path.join(conf["downloadsPath"], f"{title}.{ukey}")
    }
    # Add extension to filepath if set
    if extension is not None:
        ydl_opts["outtmpl"] += f".{extension}"
    # If this is audio setup for getting the best audio with the given codec
    if isAudio:
        ydl_opts['format'] = "bestaudio/best"
        ydl_opts['postprocessors'] = [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': codec,
            'preferredquality': '192',
        }]
    # Otherwise...
    else:
        # Check if there's a format id, if so set the download format to that format id
        if format_id is not None:
            ydl_opts['format'] = format_id
            if format_id_audio is not None:
                ydl_opts['format'] += f"+{format_id_audio}"
                print(ydl_opts['format'])
        # Otherwise if we're downloading subtitles...
        elif codec == "subtitles":
            # Set up to write the subtitles to disk
            ydl_opts["writesubtitles"] = True
            # Further settings to write subtitles
            if languageCode:
                ydl_opts['subtitleslangs'] = languageCode.split(',')
            # If the user wants to download auto subtitles set the subtitle field to do so
            ydl_opts['writeautomaticsub'] = autoSub
            ydl_opts['format'] = "worst"
        # Otherwise just download the best video+audio
        else:
            ydl_opts['format'] = None
    # If there is a proxy list url set up, set yt-dlp to use a random proxy
    if conf["proxyListURL"] is not False:
        ydl_opts['proxy'] = getProxy()
    # Finally, actually download the file/s
    with YoutubeDL(ydl_opts) as ydl:
        if codec == "subtitles":
            ydl.extract_info(url, download=True)
        else:
            ydl.download([url])
    # Construct and return the filepath for the downloaded file
    res = f"{title}.{ukey}"
    if extension is not None:
        res += f".{extension}"
    return res

# Download file directly, with random proxy if set up
def downloadDirect(url: str|bytes, filename: str|bytes|os.PathLike):
    """
    Download file directly, with random proxy if set up
    """
    if conf["proxyListURL"] is not False:
        proxies = {'https': f'https://{getProxy()}'}
        with requests.get(url, proxies=proxies, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            with open(filename, 'wb') as file:
                for chunk in resp.iter_content(chunk_size=8192):
                    file.write(chunk)
    else:
        with requests.get(url, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            with open(filename, 'wb') as file:
                for chunk in resp.iter_content(chunk_size=8192):
                    file.write(chunk)

# Generic method to get sanitized information about the given url, with a random proxy if set up
# Try to write subtitles if requested
def getInfo(url: str, getSubtitles: bool=False) -> dict[str, Any]:
    """
    Generic method to get sanitized information about the given url, with a random proxy if set up
    Try to write subtitles if requested
    """
    info: dict[str, Any] = {
        "writesubtitles": getSubtitles
    }
    if conf["proxyListURL"] is not False:
        info['proxy'] = getProxy()
    with YoutubeDL({}) as ydl:
        info = ydl.extract_info(url, download=False)
        info = ydl.sanitize_info(info)
    return info

def makeSafe(filename: str) -> str:
    """
    # Make title file system safe
# https://stackoverflow.com/questions/7406102/create-sane-safe-filename-from-any-unsafe-string
    """
    illegal_chars = "/\\?%*:|\"<>"
    illegal_unprintable = {chr(c) for c in (*range(31), 127)}
    reserved_words = {
        'CON', 'CONIN$', 'CONOUT$', 'PRN', 'AUX', 'CLOCK$', 'NUL',
        'COM0', 'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
        'LPT0', 'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9',
        'LST', 'KEYBD$', 'SCREEN$', '$IDLE$', 'CONFIG$'
    }
    if os.path.splitext(filename)[0].upper() in reserved_words: return f"__{filename}"
    if set(filename)=={'.'}: return filename.replace('.', '\uff0e')
    return "".join(
        chr(ord(c)+65248) if c in illegal_chars else c
        for c in filename
        if c not in illegal_unprintable
    ).rstrip().rstrip('.')

# Get random proxy from proxy list
def getProxy() -> str:
    """
    Get random proxy from proxy list
    """
    with open("proxies.txt", "r", encoding="utf-8") as file:
        return random.choice(file.readlines()).strip()

async def refreshProxies(proxy_list_url: str|bytes = ""):
    """
    Refresh proxies every hour
    """
    while True:
        dlProxies(proxy_list_url=proxy_list_url)
        await asyncio.sleep(3600)

async def clean(downloads_path: str|os.PathLike):
    """
    Clean all files that are older than 2 hours out of downloads every hour
    """
    while True:
        cleaned = False
        current_time = datetime.datetime.now()
        try:
            for filename in os.listdir(downloads_path):
                file_path = os.path.join(downloads_path, filename)
                file_mtime = datetime.datetime.fromtimestamp(
                    os.path.getmtime(file_path)
                )
                if (current_time - file_mtime).total_seconds() > 7200:
                    os.remove(file_path)
                    cleaned = True
        except FileNotFoundError:
            os.makedirs(downloads_path)
        if cleaned:
            print("Cleaned!")
        await asyncio.sleep(3600)

class RootPage(tornado.web.RequestHandler):
    async def get(self):
        self.write(f'test {self.get_argument("arg")}')

    def data_received(self, chunk: bytes):
        pass

class _InfoDataDict(TypedDict):
    url: str

class _InfoEventDict(TypedDict):
    error: bool
    details: str
    title: NotRequired[str]
    ext: NotRequired[str]
    info: NotRequired[dict[str, Any]]

class YtDlp(tornado.web.RequestHandler):
    async def get(self):
        if url := self.get_argument("url", None):
            info = await self.getInfoEvent({"url": url})
            self.write(dict(info))
        elif url := self.get_argument("download", None):
            info = await self.getInfoEvent({"url": url})

            if not info.get('error'):
                audio = False
                if self.get_argument("audioonly", "0")=="1":
                    audio = True
                local_file_name = download(url, audio, '', None, extension=info.get("ext"))

                visible_file_name = os.path.extsep.join([
                    makeSafe(info.get("title", "")),
                    info.get("ext", ""),
                ])
                self.set_header(
                    'Content-Type',
                    'application/octet-stream'
                )
                self.set_header(
                    'Content-Disposition',
                    'attachment; '
                        f'filename*=UTF-8\'\'{tornado.escape.url_escape(visible_file_name, False)}'
                )
                chunk_size = 8192
                with open(os.path.join(conf["downloadsPath"], local_file_name), 'rb') as file:
                    while (data := file.read(chunk_size)):
                        self.write(data)
            else:
                self.write(info.get('details'))
            self.finish()
        else:
            self.send_error(404)

    async def getInfoEvent(self, data: _InfoDataDict):
        res: _InfoEventDict = {"error": True, "details": ""}
        try:
            res["error"] = False
            url = data["url"]
            info = getInfo(url)
            res["title"] = makeSafe(info["title"])
            res["ext"] = info.get("ext", "")
            res["info"] = info
            return res
        except Exception as exc:
            res["details"] = str(exc)
            return res

    async def post(self):
        try:
            data: dict = json.loads(self.request.body).get('data', {})

            url = data.get('url', "")
            title = data.get('title', "")
            ukey = uuid.uuid4()

            ydl_opts = {
                'outtmpl': os.path.join(conf["downloadsPath"], f"{title}.{ukey}")
            }
            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            print(data)
        except Exception as exc:
            print(str(exc))
            self.send_error(400)

    def data_received(self, chunk: bytes):
        pass

def make_app():
    return tornado.web.Application([
        (r'/downloads/(.*)', tornado.web.StaticFileHandler, {'path': conf["downloadsPath"]}),
        (r"/", RootPage,),
        (r"/yt-dlp", YtDlp,),
    ])

async def main():
    """
    Main method
    """
    # If proxies are configured set up the refresh proxies task
    if conf["proxyListURL"] is not False:
        asyncio.create_task(refreshProxies(proxy_list_url=conf["proxyListURL"]))
        # This is needed to get the async task running
        await asyncio.sleep(0)
    # Set up cleaning task
    asyncio.create_task(clean(conf["downloadsPath"]))
    await asyncio.sleep(0)
    # Generic tornado setup
    app = make_app()
    app.listen(conf["listeningPort"])
    await asyncio.Event().wait()

if __name__ == "__main__":
    print(f"Started on {conf['url']}")
    asyncio.run(main())
