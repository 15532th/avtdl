## avtdl

Tool to monitor Youtube and some other streaming platforms for new streams and uploads and execute user-defined commands when it happens. It aims to provide a highly configurable environment for setting up automated archiving of new content with filtering and notifications support. It does not try to provide downloading streams itself and instead relies on executing commonly used well-known solutions for the task, such as `yt-dlp` or `streamlink`.

### Features overview

Some of supported features include:

- monitoring Youtube channels using RSS feed
- monitoring Youtube channels, `Videos` and `Streams` tabs specifically, playlists. With authorization cookies from Youtube account it's possible to get notifications for member-only and in any other way restricted streams and uploads, as well as to monitor entire subscriptions feed
- monitoring Youtube channel community tab for new posts (including member-only with authorization cookies)
- monitoring other streaming platforms, such as twitch and twitcasting, for event of channel going live
- filtering new videos and streams by channel name, presence or absense of pre-defined keywords in video title or description, picking only upcoming streams or only member-only content, deduplication of the same stream or video url coming from multiple sources
- sending notifications to Discord channel and as Jabber message


For full list and description of features see [Description and configuration of available plugins](...)

### Installation

Python version 3.9 or higher is required.

Installing from git repository:

- clone or download and unpack repository
- (optionally) initialize and activate virtual environment
- run `pip3 install -r requirements.txt`

Installing from pypi: # not published yet

- (optionally) initialize and activate virtual environment
- run `pip3 install avtdl`

Prebuilt executable:

    Hopefully will be available on Releases page eventually  

### Configuration

#### Configuration file syntax

Currently configuration is performed with configuration file that uses [YAML](https://yaml.org) format. It only uses basic features, but anything PyYAML can parse should work.

Just like JSON, YAML provides means to define a structure of nested sequences and `key: value` mappings, but is easier to read and requires less effort writing (especially if your text editor adds indentation automatically). It provides some complicated features, but basic syntax is simple enough to pick up just by reading examples. See section 2.1 of chapter 2 of [YAML specification](https://yaml.org/spec/1.2.0/#Preview) for introduction and basic examples.

The basics of YAML syntax are sequences and mappings of key-value pairs. Each item in a sequence is preceded with `-`, mappings have format `key: value`. Both can be nested inside each other by using indentation levels. Each level of indentation uses exactly 2 space characters, tabs are not allowed. This is not always required, but is strongly recommended to enclose every value in a sequence or mapping with single or double quotes to avoid ambiguity. Syntax validation is part of config file parsing, and error message will be produced in case the file syntax is wrong. If message is unclear, try pasting configuration file text in any online YAML validator.

#### Configuration file terminology

`Record` is an entity that represents a certain event, such as a new video getting published or a channel going live. It is internally represented as a set of `key: value` pairs, but also typically has a predefined human-friendly string representation used to send it over notification channel. What specific fields record has depends on where it was produced. For example, `record` about a new Youtube video will contain video title and url among other fields.

Features, such as monitoring RSS feeds or sending XMPP messages, are contained inside `plugins`. Plugins are grouped into three types:

- `monitors` - periodically check something for new content and produce `records` for each new entity
- `filters` - take `record` as input and then either produce same `record` as output or drop it, based on some condition, such as presence of certain keywords
- `actions` - take `record` as input and act on it. Examples of `action` would be sending Discord or Jabber notification or running `yt-dlp` with url field of the `record` as argument.

For each of the plugins, `entities` are unified sets of settings describing a single element plugin works with. For RSS feeds monitor, one entity would be defined for every feed it supposed to check, providing feed url and how often it should be checked for updates.

`Chains` combine `entities` of different plugins in sequences, where `records` produced by `monitors` flow through zero or more `filters` to `actors`.

#### Configuration file format

Configuration file contains three top level sections:

```yaml
Settings:
    # <application-wide setting>

Actors:
    # <plugins with entities they contain>

Chains:
    # <sequences of entities names>
```

Each section is explained in details below.

##### Settings

This section contains some application settings and can be fully omitted if default values of options are acceptable.

These options mostly regulate logging to file. To set a log details level for console output use command line options instead.

- `log_directory` - path to directory where application will write log file
- `logfile_size` - size of a single log file in bytes. After reaching this size file will be replaced by new one. Only last 10 files are kept inside log directory
- `logfile_level` - how detailed output to log file is. Can be "DEBUG", "INFO", "WARNING" or "ERROR". It is generally recommended to keep log file loglevel set to "DEBUG" and only set console output to higher level.
- `loglevel_override` - allows to overwrite loglevel of specific logger. Used to prevent a single talkative logger from filling up the log file. Each log line is preceded by log level and logger name. For example, line `[DEBUG  ] [actor.channel.db] successfully connected to sqlite database at ":memory:"` is produced by logger `actor.channel.db` on `DEBUG` level

Example of `Settings` section with all default values:

```yaml
Settings:
  log_directory: "logs"
  logfile_size: "1000000"
  logfile_level: "DEBUG"
  loglevel_override: 
    bus: "INFO"
    chain: "INFO"
    actor.request: "INFO"
```

##### Actors

This section must contain plugin names from [Description and configuration of available plugins](...). Each of them has following structure:

```yaml
Actors:
  <plugin_name>:
    config:
      # <plugin-specific configuration>
    defaults:
      # <options whose values are the same for all entities>
    entities:
      # <list of key-value pairs defining this plugin entities>
```

Each plugin section contains three sub-sections: `config`, `defaults` and `entities`. Specific format is different for each plugin, see  [Description and configuration of available plugins](...) for details. Many plugins doesn't have `config` section, and `defaults` sections is not mandatory and can be omitted. If field description mention default value it means the field could be omitted from config section and default value would be used instead. Fields without defaults are mandatory. If section end up having no values (common for `config` section), it should be omitted.

Here is an example of `Actors` configuration section with a few plugins. Refer to sections in  [Description and configuration of available plugins](...) corresponding to plugin names for detailed explanations on options.

```yaml
Actors:

  rss:  # Youtube channel monitor
    defaults:
        update_interval: 3600 # how often the feed will be checked for updates, in seconds
    entities:
      - name: "One Example Channel"
        url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCK0V3b23uJyU4N8eR_BR0QA"
      - name: "Another Example Channel"
        url: "https://www.youtube.com/feeds/videos.xml?channel_id=UC3In1x9H3jC4JzsIaocebWg"

  community:  # Youtube community tab monitor
    config:
      db_path: "db/" # path to directory where local database for storing old records should be located
    entities:
        - name: "Another Example Channel community tab"
          url: "https://www.youtube.com/channel/UC3In1x9H3jC4JzsIaocebWg/community"

  filter.match:  # filter by keyword
    entities:
      - name: "karaoke"
        patterns:
          - "karaoke"
          - "sing"

  discord.hook:  # send Discord message using webhook
    - name: "notifications"
      url: "https://discord.com/api/webhooks/1176072251045217473/N-tDdv_iIZnUl6I67GcWqmO0GlNDgCBRYTYf2Z-lfUsLk0HcvvK-i0thuPXiigXcB6h6"
```

It features total of four plugins: two for monitoring data sources (`rss` and `community`), one for matching against pattern (`filter.match`) and one more for sending notifications (`discord.hook`). Note how each entry in `entities` list has a `name` parameter: it will be later used in `Chains` section to refer to specific entity of a plugin.
`rss` plugin `entities` section sets it to monitor two Youtube channel RSS feeds, with default `update_interval` value for both of them overwritten in `defaults`. `community` plugin shows example of plugin-wide option `db_path` in `config` section, explicitly setting persistent storage location.

##### Chains

A chain groups entities from plugins in the `Actors` section in a sequence, where `records` from one or more `monitors` get through `filters` and trigger `actions`. Each entity is identified by combination of plugin name and the entity `name` property. General `Chains` section structure:

```yaml
Chains:
  <arbitrary chain name>:
    - <plugin name>:
      - <entity name>
      - <another entity name>
    - <another plugin name>:
      - <entity name>
```

Example, following this structure:

```yaml
Chains:
  "new streams notifications":
    - rss:
      - "One Example Channel"
      - "Another Example Channel"
    - discord.hook:
       - "notifications"
```

In this example a single `chain` named "new streams notifications" declares that all records produced by two entities of `rss` monitor are forwarded into `discord.hook` entity to be sent as messages into Discord channel.
According to configuration in `Actors` section, `rss` plugin will check RSS feeds of the two Youtube channels every 3600 seconds. When new video is uploaded, new entry appears in the RSS feed, leading to new `record` being generated on the next update. Due to `rss` plugin entities being listed in the `chain`, it then gets fed into `discord.hook` "notifications" entity, which in turn will convert the `record` in fitting representation and send it to Discord by making request to webhook url.

#### Examples # are yet to be added


- download livestreams from youtube channel (plus member-only)
- save community posts to files
- send nitter posts to discord

#### Common options

Main description of plugins configuration is provided in [Description and configuration of available plugins](...), this section aims to explain some nuances of several options used in multiple plugins without overloading each plugin description.

##### `cookies_file`

Path to text file containing authorization cookies in Netscape format.

After user logs in on a website, so called authorization or login cookies are set by the server and are then used to ensure consecutive requests for resources with limited access come from authorized user. Therefore in order to allow monitoring pages with limited access, such as subscriptions feed on Youtube, they should be send along with every update request.

Text file with cookies is typically obtained by using a specialized browser extension. Such an extension would need to have access to all cookies across entire browser profile, so it should be chosen with care. Authorization cookies might expire or get updated after certain period of time or after user logs out in browser, so it is recommended to use the following procedure:

- open a site in private window or clean browser profile
- log in and export cookies for specific site using extension
- close browser without logging out

Python standard library functionality used for parsing cookies file is considerable strict to file format, so if loading cookies fails, it might be caused by extension not conforming to expected format, in which case using different one might fix the error. Common format discrepancies include not having mandatory text `# Netscape HTTP Cookie File` on the first line of the file and using a lowercase for `TRUE` and `FALSE`.

##### `headers`

Allows to specify HTTP headers that will be sent with every update request. Example of intended use would be specifying preferred language with [Accept-Language](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Accept-Language) header. Headers sent to specific page by browser can be found by inspecting request in Network tab of Developer tools of the browser.

Headers are specified in `"key": "value"` format. This example ensures locale-dependant elements of Youtube community post, such as published date, are presented in English regardless of IP address geolocation:

```yaml
Actors:

  comminuty:
    defaults:
      headers:
        "Accept-Language": "en-US,en;q=0.9"
      entities:
        ...
```

##### `timezone`

Used in notification plugins to specify timezone in which date and time in message should be presented in, when possible. Timezone is identified by name, as specified in "TZ identifier" column of https://en.wikipedia.org/wiki/List_of_tz_database_time_zones#List table.

This is useful when the application is running on remote machine or when message recipients have different time settings. When the option is omitted, local computer timezone is used for conversion.

Not all date and time values are presented in a form that can be easily parsed. Record fields, referred to as "localized" in field description, for example `published_text` of `YoutubeVideoRecord`, are not affected by this option. They can be manipulated at source plugin by setting HTTP headers with `headers` plugin.

##### Formatting templates

Allows to populate a predefined text string with value of current record fields. Used for making dynamic, for example, output file name, or to change text representation of the record with help of `filter.format` plugin.

Formatting is performed by taking any text enclosed in `{}` and, if it contains name of field of the currently processed record, replacing it with value of the field.

##### `fetch_until_the_end_of_feed_mode`

Intended for one time use, to allow loading, for example, entire playlist or all available posts on community tab on Youtube for archiving purposes. 

Normally, when updating community tab or a user page on Nitter instance, plugin will stop after encountering posts that have already been seen on previous updates or when maximum depth is reached. If this option is enabled, the plugin will try to load all pages available on first update and will continue trying until it succeeds at least once. After that it is recommended to delete this option from the config to avoid unnecessarily load on server on the app restart.

#### Tools commonly used for downloading livestreams

Before automating download process it is a good idea to try doing it manually and ensure everything is working properly. This section provides overview on some tools that can be used for archiving livestreams, including solutions that offer monitoring in addition to downloading and can be used as a single-purpose alternative to avtdl.

Old versions of these tools (as well as avtdl itself) can sometimes not be able to work with streaming sites they support due to breaking changes on the site side, so in case of problems it worth checking that most recent version is used.

Only a brief description is offered here. Refer to each tool documentation for full list of available options and adjust suggested command lines to fit specific use case.

##### Youtube

[ytarchive](https://github.com/Kethsar/ytarchive) is a tool to download upcoming and ongoing livestreams. Checks scheduled date and waits for upcoming livestream, can monitor channel for livestreams and download them as they start. Typical command would be

    ytarchive --threads 4 --add-metadata --thumbnail --wait {url} best

[yt-dlp](https://github.com/yt-dlp/yt-dlp) can be used to download Youtube videos, playlists or entire channel content. Might not work well with livestreams.

    yt-dlp --add-metadata --embed-thumbnail --embed-chapters --embed-subs {url}

Both tools support customization of output name format and can download member-only streams if authorization cookies file in Netscape format is provided.

Youtube livestreams are often encoded with `AVC1` codec, but stream archive would usually also have `VP9` codec available, providing similar quality with much lower size after a certain time, usually a few hours after the stream end. 
To keep long term archive size small while ensuring recording will still be present if stream archive is not available, it is possible to use combination of ytarchive (controlled by avtdl or standalone) to obtain stream recording immediately and yt-dlp running by scheduler on daily basic to collect processed versions. To ensure yt-dlp won't try to download livestream before it gets converted to `VP9`, exact quality code can be specified as video format:

    yt-dlp --add-metadata --embed-thumbnail --embed-chapters --embed-subs --write-subs --sub-langs "live_chat, en" --merge-output-format mkv --download-archive archive.txt --format 303+251/248+251 {url}

To archive entire channel, both uploads and livestreams, run yt-dlp with channel url instead of specific video or playlist:

    yt-dlp --add-metadata --embed-thumbnail --embed-chapters --embed-subs --write-subs --sub-langs "live_chat, en" --merge-output-format mkv --download-archive archive.txt --format 303+251/248+251/bestvideo*+bestaudio/best -o "[%(upload_date)s] %(title)s - %(id)s.%(ext)s" https://www.youtube.com/@ChannelName

##### Twitcasting

To download archive use [yt-dlp](https://github.com/yt-dlp/yt-dlp).

Livestreams on Twitcasting are particularly sensitive to network connection latency, and recording file might often end up missing fragments if connection is not good enough or server is under high load. Using lower quality might help.

Ongoing livestreams also can be downloaded with [yt-dlp](https://github.com/yt-dlp/yt-dlp). When specifying quality other than `best`, note that not every quality code is available on every stream and it is better to always specify `best` as a fallback option. 

Another tool for downloading livestreams only is [TwcLazer](https://github.com/HoloArchivists/TwcLazer). It uses different download method compared to yt-dlp, so one might serve as alternative to another when something breaks due to changes on server side.

##### FC2

[fc2_live_dl](https://github.com/HoloArchivists/fc2-live-dl) allows downloading FC2 livestreams. Default options are good for most cases:

    fc2-live-dl {url}

Comes with [autofc2](https://github.com/HoloArchivists/fc2-live-dl#autofc2) script, that allows to continuously monitor a channel and download a stream as it goes live. Uses configuration file in `json` format, but file structure is simple and example is provided. Paste config file content in any online json validator to check it for possible formatting errors.

Note, that FC2 only allows a single window with particular livestream, and opening channel that is currently being downloaded in a browser will result in error and is likely to interrupt download.

##### Youtube community posts

avtdl supports saving community post text in file natively, but as alternative, this fork of [youtube-community-tab](https://github.com/HoloArchivists/youtube-community-tab) might be used. It comes with `ytct.py` script that allows to download either a specific post by direct link or all new posts on a channel. Posts are stored in `json` format, which can be rendered to human readable text files with third party [ytct-convert.py](https://gist.github.com/15532th/111c8b32e5d82112379703f3eab51e49) script.

