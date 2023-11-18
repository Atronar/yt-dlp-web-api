# run

### Module Attributes

| `conf`     | Global configuration variable   |
|------------|---------------------------------|
| `confpath` | Local path to json config       |

### Functions

| `clean`()                                     | Clean all files that are older than an hour out of downloads every hour                                                                               |
|-----------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------|
| `clip`(sid, data[, loop])                     | Event to clip a given stream and return the clip to the user, the user can optionally convert this clip into a gif                                    |
| `dlProxies`([path])                           | Function to download proxies from plain url to a given path.                                                                                          |
| `download`(url, isAudio, title, codec[, ...]) | Generic download method                                                                                                                               |
| `downloadDirect`(url, filename)               | Download file directly, with random proxy if set up                                                                                                   |
| `getInfo`(url[, getSubtitles])                | Generic method to get sanitized information about the given url, with a random proxy if set up Try to write subtitles if requested                    |
| `getInfoEvent`(sid, data)                     | Generic event to get all the information provided by yt-dlp for a given url                                                                           |
| `getProxy`()                                  | Get random proxy from proxy list                                                                                                                      |
| `limits`(sid, data)                           | Get set limits of server for display in UI                                                                                                            |
| `main`()                                      | Main method                                                                                                                                           |
| `makeSafe`(filename)                          | # Make title file system safe                                                                                                                         |
| `make_app`()                                  |                                                                                                                                                       |
| `playlist`(sid, data[, loop])                 | Downloads playlist as a zip of MP3s                                                                                                                   |
| `refreshProxies`()                            | Refresh proxies every hour                                                                                                                            |
| `resInit`(method, spinnerid)                  | Function to initialize response to client Takes method and spinnerid spinnerid is the id of the spinner object to remove on the ui, none is fine here |
| `subtitles`(sid, data[, loop])                | Two step event 1.                                                                                                                                     |
| `toMP3`(sid, data[, loop])                    | Socketio event, takes the client id, a json payload and a loop count for retries Converts link to mp3 file                                            |
