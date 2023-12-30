"""

"""
from typing import Any, Literal
from yt_dlp import YoutubeDL
import json
import asyncio
import tornado.web, tornado.escape
import requests
import os
import random
import uuid
import zipfile
import datetime
from moviepy.video.io.ffmpeg_tools import ffmpeg_extract_subclip
from moviepy.editor import VideoFileClip
from pygifsicle import optimize
from mutagen.easyid3 import EasyID3
import sentry_sdk

# TODO: auto-reload/reload on webhook using gitpython

# README: functionality is described once per documentation in order to leave as
# little clutter as possible

conf: dict[str, Any] = {}
""" Global configuration variable """

confpath = ".conf.json"
"""Local path to json config"""

if "docs" in os.getcwd():
    """Check if building docs, if so, change conf path"""
    confpath = "../.conf.json"

# Load configuration at runtime
with open(confpath, "r", encoding="utf-8") as f:
    conf = json.loads(f.read())

# If using bugcatcher such as Glitchtip/Sentry set it up
if conf["bugcatcher"]:
    sentry_sdk.init(conf["bugcatcherdsn"])

def dlProxies(path="proxies.txt"):
    """
    Function to download proxies from plain url to a given path. this is useful for me, but if other people need to utilize a more complex method of downloading proxies I recommend implementing it and doing a merge request
    """
    r = requests.get(conf["proxyListURL"], timeout=30)
    with open(path, "w", encoding="utf-8") as f:
        rlist = r.text.split("\n")
        rlistfixed = []
        for p in rlist[:-1]:
            pl = p.replace("\n", "").replace("\r", "").split(":")
            proxy = "{0}:{1}@{2}:{3}".format(pl[2], pl[3], pl[0], pl[1])
            rlistfixed.append(proxy)
        f.write("\n".join(rlistfixed))
    print("Proxies refreshed!")

# If using proxy list url and there's no proxies file, download proxies at runtime
if conf["proxyListURL"] != False:
    if not os.path.exists("proxies.txt"):
        dlProxies()

def resInit(method, spinnerid) -> dict[str, Any]:
    """
    Function to initialize response to client
    Takes method and spinnerid
    spinnerid is the id of the spinner object to remove on the ui, none is fine here
    """
    res = {
        "method": method,
        "error": True,
        "spinnerid": spinnerid
    }
    return res


#@sio.event
async def toMP3(sid, data: dict[str, Any], loop: int=0):
    """
    Socketio event, takes the client id, a json payload and a loop count for retries
    Converts link to mp3 file
    """
    # Initialize response, if spinnerid data doesn't exist it will just set it to none
    res = resInit("toMP3", data.get("spinnerid"))
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
        else:
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
            if data["id3"] != None:
                # We use EasyID3 here as, well, it's easy, if you need to add more fields
                # please read the mutagen documentation for this here:
                # https://mutagen.readthedocs.io/en/latest/user/id3.html
                audio = EasyID3(os.path.join(conf["downloadsPath"], f"{ftitle}.mp3"))
                for key, value in data["id3"].items():
                    if value != "" and value != None:
                        audio[key] = value
                audio.save()
            # Emit result to client
            #await sio.emit("done", res, sid)
    except OSError as e:
        if loop > 0:
            # Get text of error
            res["details"] = str(e)
            #await sio.emit("done", res, sid)
        else:
            await toMP3(sid, data, loop=1)
    except Exception as e:
        # Get text of error
        res["details"] = str(e)
        #await sio.emit("done", res, sid)

#@sio.event
async def playlist(sid, data: dict[str, Any], loop: int=0):
    """
    Downloads playlist as a zip of MP3s
    """
    res = resInit("playlist", data.get("spinnerid"))
    try:
        purl = data["url"]
        # Get playlist info
        info = getInfo(purl)
        # Create playlist title from the file system safe title and a random uuid
        # The uuid is to prevent two users from accidentally overwriting each other's files (very unlikely due to cleanup but still possible)
        ptitle = makeSafe(info["title"]) + str(uuid.uuid4())
        # If the number of entries is larger than the configured maximum playlist length throw an error
        if len(info["entries"]) > conf["maxPlaylistLength"]:
            raise ValueError("Playlist is longer than configured maximum length")
        else:
            # Check the length of all videos in the playlist, if any are longer than the configured maximum
            # length for playlist videos throw an error
            for v in info["entries"]:
                if v["duration"] > conf["maxLengthPlaylistVideo"]:
                    raise ValueError("Video in playlist is longer than configured maximum length")
            # Iterate through all videos on the playlist, download each one as an MP3 and then write it to the playlist zip file
            for v in info["entries"]:
                #TODO: make generic
                vid = v["id"]
                vurl = "https://www.youtube.com/watch?v=" + vid
                title = makeSafe(v["title"])
                ftitle = download(vurl, True, title, "mp3")
                with zipfile.ZipFile(os.path.join(conf["downloadsPath"], f'{ptitle}.zip'), 'a') as myzip:
                    myzip.write(os.path.join(conf["downloadsPath"], f"{ftitle}.mp3"))
            res["error"] = False
            res["link"] = f'{conf["url"]}/downloads/{ptitle}.zip'
            res["title"] = title
            #await sio.emit("done", res, sid)
    except OSError as e:
        if loop > 0:
            # Get text of error
            res["details"] = str(e)
            #await sio.emit("done", res, sid)
        else:
            await playlist(sid, data, loop=1)
    except Exception as e:
        res["details"] = str(e)
        #await sio.emit("done", res, sid)

#@sio.event
async def subtitles(sid, data: dict[str, Any], loop: int=0):
    """
    Two step event
    1. Get list of subtitles
    2. Download chosen subtitle file
    """
    res = resInit("subtitles", data.get("spinnerid"))
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
            # Step for front end use, the value here doesn't really matter, the variable just has to exist to tell the ui to move to step 2 when the method is called again
            res["step"] = 0
            # Again details doesn't need a value it just needs to exist to let the front end know to populate the details column with a select defined by the list provided by select
            res["details"] = ""
            #await sio.emit("done", res, sid)
        # Step 2 of subtitles is to download the subtitles to the server and provide that link to the user
        elif step == 2:
            # Get the selected subtitles by language code
            languageCode = data["languageCode"]
            # Check if the user wants to download autosubs
            autoSub = data["autoSub"]
            info = getInfo(url)
            title = makeSafe(info["title"])
            # Download the subtitles
            # Unfortunately at the moment this requires downloading the lowest quality stream as well, in the future some modification to yt-dlp might be necessary to avoid this
            ftitle = download(url, False, title, "subtitles", languageCode=languageCode, autoSub=autoSub)
            res["error"] = False
            res["link"] = f'{conf["url"]}/downloads/{ftitle}.{languageCode}.vtt'
            res["title"] = title
            #await sio.emit("done", res, sid)
    except OSError as e:
        if loop > 0:
            # Get text of error
            res["details"] = str(e)
            #await sio.emit("done", res, sid)
        else:
            await subtitles(sid, data, loop=1)
    except Exception as e:
        res["details"] = str(e)
        #await sio.emit("done", res, sid)

#@sio.event
async def clip(sid, data: dict[str, Any], loop: int=0):
    """
    Event to clip a given stream and return the clip to the user, the user can optionally convert this clip into a gif
    """
    res = resInit("clip", data.get("spinnerid"))
    try:
        url = data["url"]
        if "list" in url:
            raise ValueError("Method is for singular videos")
        info = getInfo(url)
        # Check if directURL is in the data from the client
        # directURL defines a video url to download from directly instead of through yt-dlp
        directURL: Literal[False]|str|bytes = False
        if "directURL" in data.keys():
            directURL = data["directURL"]
        # Check if user wants to create a gif
        gif = False
        if "gif" in data.keys():
            gif = True
        # Get the format id the user wants for downloading a given stream from a given video
        format_id: str|Literal[False] = False
        if "format_id" in data.keys():
            format_id = data["format_id"]
        if info["duration"] > conf["maxLength"]:
            raise ValueError("Video is longer than configured maximum length")
        # Get the start and end time for the clip
        timeA = int(data["timeA"])
        timeB = int(data["timeB"])
        # If we're making a gif make sure the clip is not longer than the maximum gif length
        # Please be careful with gif lengths, if you set this too high you may end up with huge gifs hogging the server
        if gif and ((timeB - timeA) > conf["maxGifLength"]):
            raise ValueError("Range is too large for gif")
        title = makeSafe(info["title"])
        # If the directURL is set download directly
        if directURL != False:
            ititle = f'{title}.{info["ext"]}'
            downloadDirect(directURL, os.path.join(conf["downloadsPath"], ititle))
        # Otherwise download the video through yt-dlp
        # If there's no format id just get the default video
        else:
            if format_id != False:
                ititle = download(url, False, title, "mp4", extension=info["ext"], format_id=format_id)
            else:
                ititle = download(url, False, title, "mp4", extension=info["ext"])
        cuuid = uuid.uuid4()
        if gif:
            # Clip video and then convert it to a gif
            (VideoFileClip(os.path.join(conf["downloadsPath"], ititle))).subclip(timeA, timeB).write_gif(os.path.join(conf["downloadsPath"], f"{title}.{cuuid}.clipped.gif"))
            # Optimize the gif
            optimize(os.path.join(conf["downloadsPath"], f"{title}.clipped.gif"))
        else:
            # Clip the video and return the mp4 of the clip
            ffmpeg_extract_subclip(os.path.join(conf["downloadsPath"], ititle), timeA, timeB, targetname=os.path.join(conf["downloadsPath"], f"{title}.{cuuid}.clipped.mp4"))
        res["error"] = False
        # Set the extension to use either to mp4 or gif depending on whether the user wanted a gif
        # The extension is just for creating the url for the clip
        extension = "mp4"
        if gif:
            extension = "gif"
        res["link"] = f'{conf["url"]}/downloads/{title}.{cuuid}.clipped.{extension}'
        res["title"] = title
        #await sio.emit("done", res, sid)
    except OSError as e:
        if loop > 0:
            # Get text of error
            res["details"] = str(e)
            #await sio.emit("done", res, sid)
        else:
            await clip(sid, data, loop=1)
    except Exception as e:
        res["details"] = str(e)
        #await sio.emit("done", res, sid)

#@sio.event
async def combine(sid, data: dict[str, Any], loop: int=0):
    """
    Combine audio and video streams
    """
    res = resInit("combine", data.get("spinnerid"))
    try:
        curl = data["url"]
        # Get video info
        info = getInfo(curl)
        # Create the video title from the file system safe title and a random uuid
        # The uuid is to prevent two users from accidentally overwriting each other's files (very unlikely due to cleanup but still possible)
        ptitle = f'{makeSafe(info["title"])}{uuid.uuid4()}'
        # If the number of entries is larger than the configured maximum playlist length throw an error
        if "list" in curl:
            raise ValueError("This method is for a single video")
        else:
            # Check the length of the video, if it's too long throw an error
            if info["duration"] > conf["maxLength"]:
                raise ValueError("Video is longer than configured maximum length")
            title = download(curl, False, ptitle, False, extension="mp4", format_id=data["format_id"], format_id_audio=data["format_id_audio"])
            res["error"] = False
            res["link"] = f'{conf["url"]}/downloads/{title}'
            res["title"] = ptitle
            #await sio.emit("done", res, sid)
    except OSError as e:
        if loop > 0:
            # Get text of error
            res["details"] = str(e)
            #await sio.emit("done", res, sid)
        else:
            await playlist(sid, data, loop=1)
    except Exception as e:
        res["details"] = str(e)
        #await sio.emit("done", res, sid)

#@sio.event
async def getInfoEvent(sid, data: dict[str, Any]):
    """
        Generic event to get all the information provided by yt-dlp for a given url
    """
    # Unlike other events we set the method here from the passed method in order to make this generic and flexible
    res = resInit(data["method"], data.get("spinnerid"))
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
        #await sio.emit("done", res, sid)
    except Exception as e:
        res["details"] = str(e)
        #await sio.emit("done", res, sid)

#@sio.event
async def limits(sid, data: dict[str, Any]):
    """
    Get set limits of server for display in UI
    """
    res = resInit("limits", data.get("spinnerid"))
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
        #await sio.emit("done", res, sid)
    except Exception as e:
        res["details"] = str(e)
        #await sio.emit("done", res, sid)

def download(
        url,
        isAudio: bool,
        title: str,
        codec: str|Literal[False],
        languageCode: str|None = None,
        autoSub: bool = False,
        extension: str|Literal[False] = False,
        format_id: str|Literal[False] = False,
        format_id_audio: str|Literal[False] = False
    ) -> str:
    """
    Generic download method
    """
    # Used to avoid filename conflicts
    ukey = str(uuid.uuid4())
    # Set the location/name of the output file
    ydl_opts: dict[str, Any] = {
        'outtmpl': os.path.join(conf["downloadsPath"], f"{title}.{ukey}")
    }
    # Add extension to filepath if set
    if extension != False:
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
        if format_id != False:
            ydl_opts['format'] = format_id
            if format_id_audio != False:
                ydl_opts['format'] += f"+{format_id_audio}"
                print(ydl_opts['format'])
        # Otherwise if we're downloading subtitles...
        elif codec == "subtitles":
            # Set up to write the subtitles to disk
            ydl_opts["writesubtitles"] = True
            # Further settings to write subtitles
            ydl_opts['subtitle'] = f'--write-sub --sub-lang {languageCode}'
            # If the user wants to download auto subtitles set the subtitle field to do so
            if autoSub:
                ydl_opts['subtitle'] = f'--write-auto-sub {ydl_opts["subtitle"]}'
            ydl_opts['format'] = "worst"
        # Otherwise just download the best video+audio
        else:
            ydl_opts['format'] = None
    # If there is a proxy list url set up, set yt-dlp to use a random proxy
    if conf["proxyListURL"] != False:
        ydl_opts['proxy'] = getProxy()
    # Finally, actually download the file/s
    with YoutubeDL(ydl_opts) as ydl:
        if codec == "subtitles":
            ydl.extract_info(url, download=True)
        else:
            ydl.download([url])
    # Construct and return the filepath for the downloaded file
    res = f"{title}.{ukey}"
    if extension != False:
        res += f".{extension}"
    return res

# Download file directly, with random proxy if set up
def downloadDirect(url: str|bytes, filename: str|bytes|os.PathLike):
    """
    Download file directly, with random proxy if set up
    """
    if conf["proxyListURL"] != False:
        proxies = {'https': f'https://{getProxy()}'}
        with requests.get(url, proxies=proxies, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
    else:
        with requests.get(url, stream=True, timeout=30) as r:
            r.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)

# Generic method to get sanitized information about the given url, with a random proxy if set up
# Try to write subtitles if requested
def getInfo(url, getSubtitles: bool=False) -> dict[str, Any]:
    """
    Generic method to get sanitized information about the given url, with a random proxy if set up
    Try to write subtitles if requested
    """
    info: dict[str, Any] = {
        "writesubtitles": getSubtitles
    }
    if conf["proxyListURL"] != False:
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
    illegal_unprintable = (chr(c) for c in (*range(31), 127))
    reserved_words = 'CON, CONIN$, CONOUT$, PRN, AUX, CLOCK$, NUL, \
COM0, COM1, COM2, COM3, COM4, COM5, COM6, COM7, COM8, COM9, \
LPT0, LPT1, LPT2, LPT3, LPT4, LPT5, LPT6, LPT7, LPT8, LPT9, \
LST, KEYBD$, SCREEN$, $IDLE$, CONFIG$\
'.split(', ')
    if os.path.splitext(filename)[0].upper() in reserved_words: return f"__{filename}"
    if set(filename)=={'.'}: return filename.replace('.','\uff0e')
    return "".join(chr(ord(c)+65248) if c in illegal_chars else c for c in filename if c not in illegal_unprintable).rstrip()

# Get random proxy from proxy list
def getProxy() -> str:
    """
    Get random proxy from proxy list
    """
    proxy = ""
    with open("proxies.txt", "r", encoding="utg-8") as f:
        proxy = random.choice(f.read().split("\n"))
    return proxy

async def refreshProxies():
    """
    Refresh proxies every hour
    """
    while True:
        dlProxies()
        await asyncio.sleep(3600)

async def clean():
    """
    Clean all files that are older than an hour out of downloads every hour
    """
    while True:
        try:
            for f in os.listdir(conf["downloadsPath"]):
                fmt = datetime.datetime.fromtimestamp(os.path.getmtime(os.path.join(conf["downloadsPath"], f)))
                if (datetime.datetime.now() - fmt).total_seconds() > 7200:
                    os.remove(os.path.join(conf["downloadsPath"], f))
        except FileNotFoundError:
            os.makedirs(conf["downloadsPath"])
        print("Cleaned!")
        await asyncio.sleep(3600)

class RootPage(tornado.web.RequestHandler):
    def get(self):
        self.write(f'test {self.get_argument("arg")}')

class YtDlp(tornado.web.RequestHandler):
    def get(self):
        if url := self.get_argument("url", None):
            info = self.getInfoEvent({"url": url})
            self.write(info)
        elif url := self.get_argument("download", None):
            info = self.getInfoEvent({"url": url})

            audio = False
            if self.get_argument("audioonly", 0)=="1":
                audio = True
            local_file_name = download(url, audio, '', None, extension=info.get("ext"))

            visible_file_name = os.path.extsep.join([makeSafe(info.get("title")), info.get("ext"), ])
            self.set_header('Content-Type', 'application/octet-stream')
            self.set_header('Content-Disposition', f'attachment; filename*=UTF-8\'\'{tornado.escape.url_escape(visible_file_name, False)}')
            chunk_size = 8192
            with open(os.path.join(conf["downloadsPath"], local_file_name), 'rb') as f:
                while (data := f.read(chunk_size)):
                    self.write(data)
            self.finish()
        else:
            self.send_error(404)

    def getInfoEvent(self, data: dict[str, Any]) -> dict[str, Any]:
        res = {"error": True, "details": ""}
        try:
            url = data["url"]
            info = getInfo(url)
            res["title"] = makeSafe(info["title"])
            res["ext"] = info.get("ext", "")
            res["error"] = False
            res["info"] = info
            return res
        except Exception as e:
            res["details"] = str(e)
            return res

    def post(self):
        try:
            data = json.loads(self.request.body)
            print(data)
        except Exception as e:
            print(str(e))
            self.send_error(400)

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
    if conf["proxyListURL"] != False:
        task = asyncio.create_task(refreshProxies())
        # This is needed to get the async task running
        await asyncio.sleep(0)
    # Set up cleaning task
    task2 = asyncio.create_task(clean())
    await asyncio.sleep(0)
    # Generic tornado setup
    app = make_app()
    app.listen(conf["listeningPort"])
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())