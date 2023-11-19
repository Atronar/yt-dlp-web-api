![yt-dlp-web logo](logo.png)

# yt-dlp-web-api

## This is a socketio based web api for using [yt-dlp](https://github.com/yt-dlp/yt-dlp). The ultimate goal of this library is to provide simple access to yt-dlp's functionality in order to create downloader websites

### See also: [https://gitlab.com/wizdevgirl1/yt-dlp-web-ui](https://gitlab.com/wizdevgirl1/yt-dlp-web-ui) for a premade front end

Requirements:

Either python3 installed locally or docker/podman with compose

First clone this repo

Next, copy .conf.json.example to .conf.json and modify the paremeters to your liking

Parameters:

maxLength: maximum length of videos allowed to download in seconds

maxPlaylistLength: maximum number of videos allowed on playlist to download

maxGifLength: maximum length of gifs in seconds

maxGifResolution: maximum resolution of gifs in pixels

maxLengthPlaylistVideo: maximum length of individual videos on playlists

proxyListURL: url to download proxies from, if not leave as false

url: base url of server

bugcatcher: whether to use a bug catching service

bugcatcherdsn: dsn of bug catching service 

allowedorigins: allowed urls of clients


Python:

run:

`pip3 install -r requirement.txt`

`pip3 install --upgrade sentry-sdk`

`bash start.sh`

/

make a downloads folder in the yt-dlp-web directory

`python3 run.py`

Docker/podman compose:

run:

`bash start-docker.sh`

or 

`bash start-podman.sh`

depending on whether you have docker compose or podman compose installed

For an example configuration for Apache please refer to [apache.example.conf](/apache.example.conf) or [apache.example.mt.conf](/apache.example.mt.conf) for multithreading

For more details please read the [docs](/docs/_build/markdown/index.md) or the inline comments

Coming soon:

Multi-node functionality

# License
This code is distributed under [GPLv3](https://www.gnu.org/licenses/gpl-3.0.en.html)