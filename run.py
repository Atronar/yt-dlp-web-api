import socketio
from yt_dlp import YoutubeDL
import json
import asyncio
import tornado
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

# Global configuration variable
conf = {}

# Load configuratin at runtime
with open(".conf.json", "r") as f:
    conf = json.loads(f.read())

# If using bugcatcher such as Glitchtip/Sentry set it up
if conf["bugcatcher"]:
    sentry_sdk.init(conf["bugcatcherdsn"])

# Function to download proxies from plain url, this is useful for me, but
# if other people need to utilize a more complex method of downloading proxies
# I recommend implementing it and doing a merge request
def dlProxies():
    r = requests.get(conf["proxyListURL"])
    with open("proxies.txt", "w") as f:
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

# Function to initialize response to client
# Takes method and spinnerid
# spinnerid is the id of the spinner object to remove on the ui, none is fine here
def resInit(method, spinnerid):
    res = {
        "method": method,
        "error": True,
        "spinnerid": spinnerid
    }
    return res

# create a Socket.IO server
sio = socketio.AsyncServer(cors_allowed_origins=conf["allowedorigins"], async_mode="tornado")

# Socketio event, takes the client id and a json payload
# Converts link to mp3 file
@sio.event
async def toMP3(sid, data):
    # Initialize response, if spinnerid data doesn't exist it will just set it to none
    res = resInit("toMP3", data.get("spinnerid"))
    # Try/catch loop will send error message to client on error
    try:
        # Get video url from data
        url = data["url"]
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
            res["link"] = conf["url"] + "/downloads/" + ftitle + ".mp3"
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
            await sio.emit("done", res, sid)
    except Exception as e:
        # Get text of error
        res["details"] = str(e)
        await sio.emit("done", res, sid)

# Downloads playlist as a zip of MP3s
@sio.event
async def playlist(sid, data):
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
            res["link"] = conf["url"] + "/downloads/" + ptitle + ".zip"
            res["title"] = title
            await sio.emit("done", res, sid)

    except Exception as e:
        res["details"] = str(e)
        await sio.emit("done", res, sid)

# Two step event
# 1. Get list of subtitles
# 2. Download chosen subtitle file
@sio.event
async def subtitles(sid, data):
    res = resInit("subtitles", data.get("spinnerid"))
    try:
        step = int(data["step"])
        url = data["url"]
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
            await sio.emit("done", res, sid)
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
            res["link"] = conf["url"] + "/downloads/" + ftitle + "." + languageCode + ".vtt"
            res["title"] = title
            await sio.emit("done", res, sid)
    except Exception as e:
        res["details"] = str(e)
        await sio.emit("done", res, sid)

# Event to clip a given stream and return the clip to the user, the user can optionally convert this clip into a gif
@sio.event
async def clip(sid, data):
    res = resInit("clip", data.get("spinnerid"))
    try:
        url = data["url"]
        info = getInfo(url)
        # Check if directURL is in the data from the client
        # directURL defines a video url to download from directly instead of through yt-dlp
        directURL = False
        if "directURL" in data.keys():
            directURL = data["directURL"]
        # Check if user wants to create a gif
        gif = False
        if "gif" in data.keys():
            gif = True
        # Get the format id the user wants for downloading a given stream from a given video
        format_id = False
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
            ititle = title + "." + info["ext"]
            downloadDirect(directURL, os.path.join(conf["downloadsPath"], ititle))      
        # Otherwise download the video through yt-dlp
        # If there's no format id just get the default video
        else:
            if format_id != False:
                ititle = download(url, False, title, "mp4", extension=info["ext"], format_id=format_id)
            else:
                ititle = download(url, False, title, "mp4", extension=info["ext"])
        if gif:
            # Clip video and then convert it to a gif
            (VideoFileClip(os.path.join(conf["downloadsPath"], ititle))).subclip(timeA, timeB).write_gif(os.path.join(conf["downloadsPath"], f"{title}.{uuid.uuid4()}.clipped.gif"))
            # Optimize the gif
            optimize(os.path.join(conf["downloadsPath"], f"{title}.clipped.gif"))
        else:
            # Clip the video and return the mp4 of the clip
            ffmpeg_extract_subclip(os.path.join(conf["downloadsPath"], ititle), timeA, timeB, targetname=os.path.join(conf["downloadsPath"], f"{title}.{uuid.uuid4()}.clipped.mp4"))
        res["error"] = False
        # Set the extension to use either to mp4 or gif depending on whether the user wanted a gif
        # The extension is just for creating the url for the clip
        extension = "mp4"
        if gif:
            extension = "gif"
        res["link"] = conf["url"] + "/downloads/" + title + ".clipped." + extension
        res["title"] = title
        await sio.emit("done", res, sid)
    except Exception as e:
        res["details"] = str(e)
        await sio.emit("done", res, sid)

# Generic event to get all the information provided by yt-dlp for a given url
@sio.event
async def getInfoEvent(sid, data):
    # Unlike other events we set the method here from the passed method in order to make this generic and flexible
    res = resInit(data["method"], data.get("spinnerid"))
    try:
        url = data["url"]
        info = getInfo(url)
        if data["method"] == "streams":
            res["details"] = ""
            res["select"] = ""
        title = makeSafe(info["title"])
        res["error"] = False
        res["title"] = title
        res["info"] = info
        await sio.emit("done", res, sid)
    except Exception as e:
        res["details"] = str(e)
        await sio.emit("done", res, sid)

# Get limits of server for display in UI
@sio.event
async def limits(sid, data):
    res = resInit("limits", data.get("spinnerid"))
    try:
        limits = [
            "maxLength",
            "maxPlaylistLength",
            "maxGifLength",
            "maxGifResolution",
            "maxLengthPlaylistVideo"
        ]
        res["limits"] = [{"limitid": limit, "limitvalue": conf[limit]} for limit in limits]
        res["error"] = False
        await sio.emit("done", res, sid)
    except Exception as e:
        res["details"] = str(e)
        await sio.emit("done", res, sid)

# Generic download method
def download(url, isAudio, title, codec, languageCode=None, autoSub=False, extension=False, format_id=False):
    # Used to avoid filename conflicts
    ukey = str(uuid.uuid4())
    # Set the location/name of the output file
    ydl_opts = {
        'outtmpl': os.path.join(conf["downloadsPath"], f"{title}.{ukey}")
    }
    # Add extension to filepath if set
    if extension != False:
        ydl_opts["outtmpl"] += "." + extension
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
        # Otherwise if we're downloading subtitles...
        elif codec == "subtitles":
            # Set up to write the subtitles to disk
            ydl_opts["writesubtitles"] = True
            # Further settings to write subtitles
            ydl_opts['subtitle'] = '--write-sub --sub-lang ' + languageCode
            # If the user wants to download auto subtitles set the subtitle field to do so
            if autoSub:
                ydl_opts['subtitle'] = "--write-auto-sub " + ydl_opts["subtitle"]
            ydl_opts['format'] = "worst"
        # Otherwise just download the best video
        else:
            ydl_opts['format'] = "bestvideo/best"
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
    res = title + "." + ukey
    if extension != False:
        res += "." + extension
    return res

# Download file directly, with random proxy if set up
def downloadDirect(url, filename):
    if conf["proxyListURL"] != False:
        proxies = {'https': 'https://' + getProxy()}
        with requests.get(url, proxies=proxies, stream=True) as r:
            r.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): 
                    f.write(chunk)
    else:
        with requests.get(url, stream=True) as r:
            r.raise_for_status()
            with open(filename, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): 
                    f.write(chunk)

# Generic method to get sanitized information about the given url, with a random proxy if set up
# Try to write subtitles if requested
def getInfo(url, getSubtitles=False):
    info = {
        "writesubtitles": getSubtitles
    }
    if conf["proxyListURL"] != False:
        info['proxy'] = getProxy()
    with YoutubeDL({}) as ydl:
        info = ydl.extract_info(url, download=False)
        info = ydl.sanitize_info(info)
    return info

# Make title file system safe
# https://stackoverflow.com/questions/7406102/create-sane-safe-filename-from-any-unsafe-string
def makeSafe(filename):
    return "".join([c for c in filename if c.isalpha() or c.isdigit() or c==' ']).rstrip()

# Get random proxy from proxy list
def getProxy():
    proxy = ""
    with open("proxies.txt", "r") as f:
        proxy = random.choice(f.read().split("\n"))
    return proxy

# Refresh proxies every hour
async def refreshProxies():
    while True:
        dlProxies()
        await asyncio.sleep(3600)

# Clean all files that are older than an hour out of downloads every hour
async def clean():
    while True:
        for f in os.listdir(conf["downloadsPath"]):
            fmt = datetime.datetime.fromtimestamp(os.path.getmtime(os.path.join(conf["downloadsPath"], f)))
            if (datetime.datetime.now() - fmt).total_seconds() > 7200:
                os.remove(os.path.join(conf["downloadsPath"], f))
        print("Cleaned!")
        await asyncio.sleep(3600)

def make_app():
    return tornado.web.Application([
        (r'/downloads/(.*)', tornado.web.StaticFileHandler, {'path': conf["downloadsPath"]}),
        (r"/socket.io/", socketio.get_tornado_handler(sio))
    ])

# Main method
async def main():
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