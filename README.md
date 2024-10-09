## avtdl

Tool to monitor Youtube and some other streaming platforms for new streams and uploads and execute user-defined commands when it happens. It aims to provide a highly configurable environment for setting up automated archiving of new content with filtering and notification support. It does not try to provide downloading streams itself and instead relies on executing commonly used well-known solutions for the task, such as `yt-dlp`.

---

<!-- TOC -->
  * [avtdl](#avtdl)
    * [Features overview](#features-overview)
    * [Installation](#installation)
    * [Running](#running)
    * [Configuration](#configuration)
      * [Configuration file syntax](#configuration-file-syntax)
      * [Configuration file terminology](#configuration-file-terminology)
      * [Configuration file format](#configuration-file-format)
        * [settings](#settings)
        * [actors](#actors)
        * [chains](#chains)
      * [Examples](#examples)
      * [Common options](#common-options)
        * [`update_interval`](#update_interval)
        * [`cookies_file`](#cookies_file)
        * [`headers`](#headers)
        * [`timezone`](#timezone)
        * [`fetch_until_the_end_of_feed_mode`](#fetch_until_the_end_of_feed_mode)
        * [`quiet_first_time` and `quiet_start`](#quiet_first_time-and-quiet_start)
        * [`reset_origin`](#resetorigin)
        * [Formatting templates](#formatting-templates)
        * [Providing path to a file or a directory](#providing-path-to-a-file-or-a-directory)
      * [Troubleshooting](#troubleshooting)
    * [Tools commonly used for downloading livestreams](#tools-commonly-used-for-downloading-livestreams)
      * [Youtube](#youtube)
      * [Twitcasting](#twitcasting)
      * [FC2](#fc2)
      * [Youtube community posts](#youtube-community-posts)
<!-- TOC -->

---

### Features overview

Some of the supported features include:

- monitoring Youtube channels using RSS feed
- monitoring Youtube channels, individual tabs of a channel or playlists by parsing html. With authorization cookies from Youtube account it's possible to get notifications for member-only and in any other way restricted streams and uploads, as well as to monitor the entire subscriptions feed
- monitoring Youtube channel community tab for new posts (including member-only with authorization cookies) and Twitter timelines for new tweets and Spaces (account required)
- monitoring other streaming platforms, such as Twitch and Twitcasting, for events of a channel going live
- filtering new videos and streams by channel name, presence or absense of pre-defined keywords in video title or description, picking up only upcoming streams or only member-only content, deduplication of the same stream or video url coming from multiple sources
- sending notifications to a Discord channel and/or as a Jabber message, storing events in plaintext or json files, downloading images and other files from direct links
- executing pre-defined command template filled with current event's data, such as stream url and title

For the full list and descriptions of features see [Description and configuration of available plugins](PLUGINS.md)

The goal of avtdl is to provide means for setting up customisable workflows for archiving and sending notifications about events on a number of supported platforms in a uniform way by combining building blocks (monitors, filters and actions) into sequences, representing specific workflows (chains). Skip to [Configuration](#configuration) section for more details.


### Installation

Python version 3.9 or higher is required.

Installing from git repository:

```bash
git clone https://github.com/15532th/avtdl.git
cd avtdl
pip3 install -r requirements.txt
```

Installing from PyPI:

```bash
pip3 install avtdl
```

Prebuilt executable:

Available on [Releases](https://github.com/15532th/avtdl/releases) page ([latest](https://github.com/15532th/avtdl/releases/latest)) as a single executable or as a zip-archive of the app directory (recommended).


### Running

Depending on the installation method and OS, application might be invoked as `avtdl [options]`, `python3 avtdl.py [options]` or `py avtdl.py [options]`

After installing, proceed with writing configuration file, as described in [Configuration](#configuration) section. [example.config.yml](example.config.yml) can be used as a starting point.

By default, configuration file is named `config.yml` and located in current working directory. Current directory is also used as default location to create folders for persistent storage and logs, unless redefined in configuration file.

To specify a different config file, use `avtdl --config path/to/config.yml` option. Run `avtdl --help` for full list of options.

### Configuration

#### Configuration file syntax

Currently, configuration is performed with a configuration file that uses [YAML](https://yaml.org) format. It only uses basic features, but anything PyYAML can parse should work.

Just like JSON, YAML provides means to define a structure of nested sequences and `key: value` mappings, but is easier to read and requires less effort writing (especially if your text editor adds indentation automatically). It provides some complicated features, but basic syntax is simple enough to pick up just by reading examples. See section 2.1 of chapter 2 of [YAML specification](https://yaml.org/spec/1.2.0/#Preview) for introduction and basic examples.

The basics of YAML syntax are sequences and mappings of key-value pairs. Each item in a sequence is preceded with `-`, mappings have format `key: value`. Both can be nested inside each other by using indentation levels. Each level of indentation uses exactly 2 space characters, tabs are not allowed.

It is not always required, but is strongly recommended to enclose every value in a sequence or mapping with single or double quotes to avoid ambiguity. Syntax validation is part of config file parsing, and error message will be produced if the file syntax is wrong. If the message is unclear, try pasting configuration file text in any online YAML validator.

#### Configuration file terminology

`Record` is an entity that represents a certain event, such as a new video getting published or a channel going live. It is internally represented as a set of `key: value` pairs, but also typically has a predefined human-friendly string representation used to send it over notification channel. What specific fields record has depends on where it was produced. For example, `record` about a new Youtube video will contain video title and url among other fields.

There is also a special kind of records, `events`, that are generated by plugins themselves, not by data from external source.

Features, such as monitoring RSS feeds or sending XMPP messages, are contained inside `plugins`. Plugins are grouped into three types:

- `monitors` - periodically check something for new content and produce `records` for each new entity
- `filters` - take `record` as input and then either produce the same `record` as output or drop it, based on a condition, such as presence of certain keywords
- `actions` - take `record` as input and act on it. Examples of `action` would be sending Discord or Jabber notification or running `yt-dlp` with the url field of the `record` as argument.

For each of the plugins, `entities` are unified sets of settings describing a single element a plugin works with. For RSS feeds monitor, one entity would be defined for every feed it is supposed to check, providing feed url and how often it should be checked for updates.

`chains` combine `entities` of different plugins in sequences, where `records` produced by `monitors` flow through zero or more `filters` to `actors`.

#### Configuration file format

Configuration file contains three top level sections:

```yaml
settings:
    # <application-wide setting>

actors:
    # <plugins with entities they contain>

chains:
    # <sequences of entities names>
```

Each section is explained in details below.

##### settings

This section contains some application settings and can be fully omitted if default values of options are acceptable.

These options mostly regulate logging to a file. To set a log level for console output use command line options instead.

- `log_directory` - path to a directory where application will write log file
- `logfile_size` - size of a single log file in bytes. After reaching this size the file will be replaced by a new one. Only last 10 files are kept inside the log directory
- `logfile_level` - how detailed the output to log file is. Can be "DEBUG", "INFO", "WARNING" or "ERROR". It is recommended to keep log file loglevel set to "DEBUG" and only set console output to higher level.
- `loglevel_override` - allows to overwrite loglevel of a specific logger. Used to prevent a single talkative logger from filling up the log file. Each log line is preceded by log level and logger name. For example, line `[DEBUG  ] [actor.channel.db] successfully connected to sqlite database at ":memory:"` is produced by logger `actor.channel.db` on `DEBUG` level

Example of `settings` section with all default values:

```yaml
settings:
  log_directory: "logs"
  logfile_size: "1000000"
  logfile_level: "DEBUG"
  loglevel_override: 
    bus: "INFO"
    chain: "INFO"
    actor.request: "INFO"
```

##### actors

This section must contain plugin names from [Description and configuration of available plugins](PLUGINS.md). Each of them has the following structure:

```yaml
actors:
  <plugin_name>:
    config:
      # <plugin-specific configuration>
    defaults:
      # <options whose values are the same for all entities>
    entities:
      # <list of key-value pairs defining this plugin entities>
```

Each plugin section contains three subsections: `config`, `defaults` and `entities`. Specific format is different for each plugin, see [Description and configuration of available plugins](PLUGINS.md) for details. Many plugins don't have `config` section, and `defaults` sections is not mandatory and can be omitted. If field description mentions a default value, it means the field could be omitted from the config section and the default value would be used instead. Fields without defaults are mandatory. If the section ends up not having any values (common for `config` section), it must be omitted.

Here is an example of `actors` configuration section with a few plugins. Refer to sections in [Description and configuration of available plugins](PLUGINS.md) corresponding to plugin names for detailed explanations on the options.

```yaml
actors:

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
    entities:
      - name: "notifications"
        url: "https://discord.com/api/webhooks/1176072251045217473/N-tDdv_iIZnUl6I67GcWqmO0GlNDgCBRYTYf2Z-lfUsLk0HcvvK-i0thuPXiigXcB6h6"
```

It features four plugins: two for monitoring data sources (`rss` and `community`), one for matching against patterns (`filter.match`), and one more for sending notifications (`discord.hook`). Note how each entry in `entities` list has a `name` parameter: it will be later used in `chains` section to refer to a specific entity of a plugin.
`rss` plugin's `entities` section sets it to monitor two Youtube channels RSS feeds, with default `update_interval` value for both of them overwritten in `defaults`. `community` plugin shows an example of plugin-wide option `db_path` in `config` section, explicitly setting persistent storage location.

##### chains

As explained in the [Configuration file terminology](#configuration-file-terminology) section, plugins can be divided into three types: `monitors`, `filters` and `actions`. A `monitor` can only produce new records. A `filter` consumes records and decides to either output them or not based on its settings. An `action` consumes `records` and can produce `events`, for example in case of an error.

A chain groups entities from plugins in the `actors` section in a sequence, where `records` from `monitors` get through `filters` and trigger `actions`. Each entity is identified by a combination of a plugin name and the entity `name` property. General `chains` section structure is:

```yaml
chains:
  <arbitrary chain name>:
    - <plugin name>:
      - <entity name>
      - <another entity name>
    - <another plugin name>:
      - <entity name>
```

Example, following this structure:

```yaml
chains:
  "new streams notifications":
    - rss:
      - "One Example Channel"
      - "Another Example Channel"
    - discord.hook:
       - "notifications"
```

In this example a single `chain` named "new streams notifications" declares that all records produced by two entities of the `rss` monitor are forwarded into the `discord.hook` entity to be sent as a messages into a Discord channel.
Assuming the `actors` section defined [before](#actors) is used, `rss` plugin will check RSS feeds of the two Youtube channels every 3600 seconds. When a new video is uploaded, a new entry appears in the RSS feed, leading to a new `record` being generated on the next update. Due to the `rss` plugin entities being listed in the `chain`, it then gets fed into the `discord.hook` "notifications" entity, which in turn will perform an action of converting the `record` into a fitting representation and sending it to Discord.

***

Even though `actions` do not forward `records` they received down the chain, they might produce `events` when a certain event happens while processing a record. For example, `execute` plugin might produce an `event` with "error" type if the shell command it was set to execute failed. `Events` are treated as normal `records` and can be passed through `filters` to other `actors`.

```yaml
chains:
  "download and notify":
    - rss:
        - "Example Channel"
    - execute:
        - "run ytarchive"
    - xmpp:
        - "notifications"
```

Records from the `rss` feed will be consumed by `execute` plugin entity and won't make it to `xmpp`, but if the command defined in "run ytarchive" failed, a message about it is passed to "notifications" as `event`.

***

Before version 1.0 all plugin entities were stateful, in a sense that if a certain entity of specific plugin is used in multiple chains, all instances would produce all records consumed by any of them. Old behavior can be enabled on per-entity basis by enabling `reset_origin` option.

In its simplest form, a `chain` includes one monitor, zero or more `filters` and ends with an `action`. It is possible to list multiple `monitors` sequentially in one `chain`:

```yaml
chains:
  "multiplatform streams notifications":
    - rss:
      - "Example Channel 1"
    - twitch:
      - "Example Channel 2"
    - discord.hook:
        - "notifications"
```

When a `monitor` located in the middle of a chain receives a `record` as input, it will be passed down the chain unchanged. In the example above records from the `rss` monitor will fall through `twitch`, ending in `discord.hook`, along with records from `twitch` itself.

A copy of a `record` produced by a `monitor` entity spawns in every `chain` this entity is listed in. Each copy then gets tied to the `chain` and only gets passed through within its bounds.

For example, if aforementioned entity "Example Channel 1" of the `twitch` plugin is also used in a second chain to run a command, next entity of that chain will only receive records from "Example Channel 2" on `twitch`, but anything produced by "Example Channel 1" of the `rss` plugin will stay in the first chain and only get into the `discord.hook`, along with records from the `twitch` entity.

<details>
  <summary>Like this (click to expand):</summary>

```yaml
chains:
  "multiplatform streams notifications":
    - rss:
        - "Example Channel 1"
    - twitch:
        - "Example Channel 2"
    - discord.hook:
        # receives records from both twitch and rss feed monitors
        - "notifications"

  "twitch download":
    - twitch:
      - "Example Channel 2"
    - execute:
      # receives records only from twitch monitor
      - "streamlink for Example Channel 2"
```

</details>

***

It might be useful to disable this behavior when using a `filter.noop` (or any other filter's) entity as a way to merge records produced by multiple monitors into a single processing chain.

In the following example records from Youtube and Twitch monitors are fed into "multiplexor" entity of `noop` filter, which simply passes all records through unchanged. Output of the "multiplexor" then passed to `discord.hook` "notifications" entity after some (possible complex) filtering and preprocessing.

```yaml
actors:
  filter.noop:
    entities:
      - name: "multiplexor"
        reset_origin: true
  # rest of the "actors" section is omitted for brevity

chains:

  "from youtube":
    - rss:
        - "Example Channel"
        - "Another Example Channel"
    - filter.noop:
        - "multiplexor"

  "from twitch":
    - twitch:
        - "Example Channel"
    - filter.noop:
        - "multiplexor"

  "to discord":
    - filter.noop:
        # records from both other chains are forwarded here
        - "multiplexor"
    - filter.exclude:
        - "notifications blacklist"
    - filter.channel:
        - "livestreams notifications"
    - discord.hook:
        - "notifications"
```

***

If a plugin in a `chain` lists multiple entities, all of them will receive records from previous one, and all of them will generate records based on it.

```yaml
chains:
  "interesting streams notifications":
    - rss:
      - "Example Channel"
    - filter.match:
      - "sing"
      - "game"
      - "talk"
    # if a record matches two of three filters above, it will be duplicated at this point
    - xmpp:
      - "notifications"
```

When two entities of such plugin let the same record through, it will effectively be duplicated, coming down the chain twice. To mitigate it, either give each entity its own `chain`, or use `filter.deduplicate` plugin to drop repeating `records` before passing it to the `action`:

```yaml
chains:
  "interesting streams notifications":
    - rss:
      - "Example Channel"
    - filter.match:
      - "sing"
      - "game"
      - "talk"
    - filter.deduplicate:
        - "interesting streams in Example Channel"
    - xmpp:
      - "notifications"
```


***

The same is also true for monitors: 

```yaml
chains:
  "youtube notifications":
    - rss:
        - "Example Channel 1"
    - channel:
        - "Example Channel 2"
        - "Example Channel 3"
    # records from rss are duplicated at this point
    - discord.hook:
        - "notifications"
```

Records from the "Example Channel 1" pass through both "Example Channel 2" and "Example Channel 3", getting duplicated. To avoid this, split monitor listing two entities in two sequential copies listing a single entity each (variant 1), or only use multiple entities at the beginning of the chain (variant 2):

```yaml
chains:

  "youtube notifications, variant 1":
    - rss:
        - "Example Channel 1"
    - channel:
        - "Example Channel 2"
    - channel:
        - "Example Channel 3"
    - discord.hook:
        - "notifications"

  "youtube notifications, variant 2":
    - channel:
        - "Example Channel 2"
        - "Example Channel 3"
    - rss:
        - "Example Channel 1"
    - discord.hook:
        - "notifications"
```

***

It is also possible for multiple entities to produce duplicate records simply because they monitor the same source. In the example below records from "subscriptions feed" might be already seen by "Example Channel". Then one of them will be dropped on `filter.deduplicate`.

```yaml
chains:
  "streams notifications":
    - channel:
        - "Example Channel"
        - "subscriptions feed"
    - filter.deduplicate:
        - "youtube notifications"
    - xmpp:
        - "notifications"
```

#### Examples

Example configuration file [example.config.yml](example.config.yml) contains a combination of a few common workflows and can be used as starting point.
[EXAMPLES.md](EXAMPLES.md) lists a few independent configuration files, highlighting usage of specific plugin or plugins combination.

#### Common options

Main description of plugins configuration is provided in [Description and configuration of available plugins](PLUGINS.md). This section aims to explain some nuances of several options used in multiple plugins without overloading each plugin description.

##### `update_interval`

Interval between two consecutive updates of a monitored url in seconds.

It is generally advised to set it reasonably high to avoid triggering server rate limits. One sign of update rate being too high is presence of 503 and 429, as well as other error response codes in the app log and console output.

Since requests to different urls on the same server are likely to be counted together, many entities with reasonable update interval each might also cause rate limit errors.

Monitoring plugins will try to avoid doing too many requests in a short interval of time by spacing update requests with the same `update_interval` evenly. For example, with three entities with `update_interval` set to 60 seconds, updates will be separated from each other by 20 seconds. Note that it means that the first update after startup might be delayed.

##### `cookies_file`

Path to the text file containing authorization cookies in Netscape format.

After user logs in on a website, so-called authorization or login cookies are set by the server and are then used to ensure consecutive requests for resources with limited access come from an authorized user. Therefore, in order to allow monitoring pages with limited access, such as subscriptions feed on Youtube, they should be sent along with every update request.

Text file with cookies is typically obtained by using a specialized browser extension. Such an extension would need to have access to all cookies across the entire browser profile, so it should be chosen with care. Authorization cookies might expire or get updated after a certain period of time or after a user logs out in browser, so it is recommended to use the following procedure:

- open a site in private window or clean browser profile
- log in and export cookies for a specific site using an extension
- close the browser without logging out

Python standard library functionality used for parsing cookies file is considerably strict to the file format, so if loading the cookies fails, it might be caused by the extension not conforming to the expected format, in which case using a different one might fix the error. Common format discrepancies include not having mandatory text `# Netscape HTTP Cookie File` on the first line of the file and using lowercase for `TRUE` and `FALSE`.

##### `headers`

Allows to specify HTTP headers that will be sent with every update request. Example of the intended use would be specifying preferred language with the [Accept-Language](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Accept-Language) header. Headers sent to a specific page by browser can be found by inspecting request in Network tab of Developer tools of the browser.

Headers are specified in `"key": "value"` format. This example ensures locale-dependant elements of Youtube community post, such as published date, are presented in English regardless of IP address geolocation:

```yaml
actors:

  community:
    defaults:
      headers:
        "Accept-Language": "en-US,en;q=0.9"
      entities:
        ...
```

##### `timezone`

Used in notification plugins to specify timezone in which date and time in the message should be presented in, where possible. Timezone is identified by name, as specified in "TZ identifier" column of [this](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones#List) table.

This is useful when the application is running on a remote machine or when message recipients have different time settings. When the option is omitted, local computer timezone is used for conversion.

Not all date and time values are presented in a form that can be easily parsed. Record fields, referred to as "localized" in field description, for example `published_text` of `YoutubeVideoRecord`, are not affected by this option. They can be manipulated at source plugin by setting HTTP headers with `headers` plugin.

##### `fetch_until_the_end_of_feed_mode`

Intended for one time use, to allow loading, for example, an entire playlist or all available posts on a community tab on Youtube channel for archiving purposes. 

Normally, when updating a community tab or a user page on Nitter instance, plugin will stop after encountering posts that have already been seen on previous updates or when maximum depth is reached. If this option is enabled, the plugin will try to load all pages available on first update and will continue trying until it succeeds at least once. After that it is recommended to delete this option from the config to avoid unnecessarily load on server on the app restart.

##### `quiet_first_time` and `quiet_start`

When content of a newly added feed is loaded and parsed for the first time, it might contain hundreds of new entries. Option `quiet_first_time` tells a monitor to throw them away to avoid causing a torrent of notifications on initial update of the feed. On consequent updates these entries will be considered already processed.

It means that after a feed url is added to the monitor, it will only generate records when feed gets new entries that came in after the moment of first update on startup.

The `quiet_start` option does the same on every startup, discarding entities that were added to the feed while the application wasn't running.


##### `reset_origin`

Normally, once produced, record is bound to its chain, meaning that any filter or action entity listed in multiple chains will only pass each processed record to the chain it has come from. However it might sometimes be useful to remove this limitation in order to merge output of multiple chains in one. See the [chains](#chains) sections above for detailed explanation and usage example.

##### Formatting templates

Allows populating a predefined text string with values of current record fields. Used for making dynamic strings, i.e. output file name, or to change the text representation of the record by the `filter.format` plugin.

Formatting is performed by taking any text enclosed in `{}` and, if it contains a name of field of the currently processed record, replacing it with the value of the field.

Additionally, any use of %-encoded format codes specified in https://docs.python.org/3/library/datetime.html#strftime-and-strptime-format-codes in the template string will be replaced with current time formatted accordingly.

For example, the following config snippet uses a template to output each processed record to a separate file, with the record's `post_id` field preceded by current date used as the file name.

```yaml
actors:
  to_file:
    entities:
      - name: "ChannelName"
        path: "archive/community/channelname/"
        filename: "%Y%m%d_{post_id}.txt"
```

Note how this makes this plugin entity only suitable for processing records coming from the `community` plugin, since only that plugin uses this field. If currently processed record does not have this field, it will not be replaced with anything, and the resulting file name will be quite literally `{post_id}.txt`. If this happens, debug message is produced in log. Some field names are used by multiple plugins, one notable example being the `url` field, which usually contains the url of a new livestream, video or post.

When a template is used as a file name or as a path to a directory, field values will have characters that are not allowed to be used in filenames replaced with underscore. Final string length and characters are not checked for compliance with OS restrictions, however.

##### Providing path to a file or a directory

Both absolute and relative paths can be used, with relative being resolved in the current working directory `avtdl` is run from. It is possible to use either `/` and `\\` as directory separator regardless of OS, but note that latter must be used twice. Shell variables and aliases, such as `~`, are not expanded, so full path must be provided instead.

Shell commands used in `execute` plugin entities are also run from the current working directory. Therefore executables, such as yt-dlp, must be either referenced by absolute path or present in `PATH` environment variable. Alternatively, they might be placed in the working directory itself.



#### Troubleshooting

When loading and parsing configuration file, `avtdl` will check the structure and parameters and report everything it finds wrong. Location of the error is presented as a semicolon-separated list of sections from the top one to most nested. For example, running it with unedited `example.config.yml` will produce the following output:

    [ERROR  ] [avtdl] Failed to process configuration file, following errors occurred: 
        error parsing "cookies.txt" in config section actors: channel: entities: 0: cookies_file: Path does not point to a file
        error parsing "cookies.txt" in config section actors: community: entities: 0: cookies_file: Path does not point to a file

It means that `cookies_file` parameters of first entity of both `channel` and `community` plugins specify path to a file that doesn't exist and therefore cannot be loaded and parsed as cookies file.

Even if configuration file is valid and loads successfully, it still might be configured in a way causing application to fail or not produce desired results. Some obviously wrong settings, as well as any runtime errors deemed serious enough, such as network connection problems, will be reported with `[WARNING ]` or `[ERROR  ]` loglevels. Issues of lower severity are reported on `[DEBUG  ]` level along with debug messages providing context of what was happening around the moment. By default, they are not shown in console unless application is run with `--debug` argument, but are written in log file, as defined in `settings` section of the configuration file.

### Tools commonly used for downloading livestreams

Before automating the download process it is a good idea to try doing it manually first and ensure everything is working properly. This section provides an overview of some tools that can be used for archiving livestreams, including those offering monitoring in addition to downloading that can be used as single-purpose alternatives to avtdl.

Old versions of these tools (as well as avtdl itself) might sometimes not be able to work due to breaking changes on the site side, so in case of problems it is worth checking that the most recent version is used.

Only a brief description is offered here. Refer to each tool's documentation for a full list of available options and adjust suggested command lines to fit specific use cases.

All mentioned tools support customization of the output name format and can download limited access streams if an authorization cookies file is provided.

#### Youtube

[ytarchive](https://github.com/Kethsar/ytarchive) is a tool for downloading upcoming and ongoing livestreams. Checks scheduled date and waits for upcoming livestream, can monitor channel for livestreams and download them as they start. Typical command would be

    ytarchive --threads 4 --add-metadata --thumbnail --wait --merge {url} best

Running `ytarchive` without an url, specifying the `--wait` key or selecting the livestream quality will cause it to prompt for user input asking to provide missing details, which works in interactive environment but would wait forever if happened in automated job, so caution should be applied to provide all necessary info in command string. Quality can be specified as a slash-delimited list, and it is generally advised to always add `best` at the end: `1080p/1080p60/best`.

<details>
  <summary>Commonly used options (click to expand):</summary>

- `--add-metadata` - writes stream info, such as title, description, channel name and date to output file metadata
- `--cookies "path/to/file.txt"` - file with Youtube login cookies, allows downloading member-only streams
- `--threads "number"` - run multiple download threads in parallel. Use if download is falling behind the live edge. Usually 2 or 3 threads is enough for a livestream
- `--thumbnail` - embed the stream thumbnail as a video preview
- `--wait` and `--merge` are used to avoid prompting for user input

</details>

`ytarchive` depends on [ffmpeg](https://www.ffmpeg.org/download.html) for merging video and audio in output file and for embedding thumbnail and metadata, so it should be installed or provided as an executable.

[yt-dlp](https://github.com/yt-dlp/yt-dlp) can be used to download Youtube videos, playlists or entire channel content. Might not work well with livestreams.

    yt-dlp --add-metadata --embed-thumbnail --embed-chapters --embed-subs {url}

`yt-dlp` also requires [ffmpeg](https://www.ffmpeg.org/download.html) for many functions, but might work without it for simple download.

<details>
  <summary>Commonly used options (click to expand):</summary>

- `--add-metadata` - writes stream info, such as title, description, channel name and date to output file metadata
- `--cookies "path/to/file.txt"` - file with Youtube login cookies, allows downloading member-only streams
- `--download-archive archive.txt` - writes video id of successfully downloaded streams into `archive.txt` file, marking them as processed to skip on consequential runs. Commonly used to download only new videos from a playlist or a channel even if already downloaded ones were moved
- `--embed-thumbnail` - embed the stream thumbnail as a video preview
- `--embed-subs --write-subs --sub-langs "live_chat, en"` - deals with subtitles and livechat. `--write-subs` stores them as a separate files (`vtt` for subtitles and `json` for livechat), `--embed-subs` will additionally put subtitles (but not a chat) in output video file
- `--format` - allows to select quality and codecs. See [docs](https://github.com/yt-dlp/yt-dlp#format-selection) for examples
- `-o` - format of the output file name ([docs](https://github.com/yt-dlp/yt-dlp#output-template)). One particularly useful feature is ability to limit maximum value of specific placeholder in order to prevent total filename length exceeding filesystem limit (typically 255 symbols or bytes). For example, format template `-o "[%(upload_date)s] %(title).200B - %(id)s.%(ext)s"` will ensure video title gets trimmed to 200 bytes regardles of how many characters it takes
</details>

Youtube livestreams are often encoded with `AVC1` codec, but stream archive would usually also have format encoded in `VP9` available, providing similar quality with much lower size after a certain time, usually a few hours after the stream end. 

To keep long-term archive size small while ensuring a recording will still be present if the stream archive is not available, it is possible to use a combination of ytarchive (controlled by avtdl or standalone) to obtain the stream recording immediately and yt-dlp managed by a scheduler on daily basis to collect processed versions. To ensure yt-dlp won't try to download a livestream before it gets converted to `VP9`, exact quality code can be specified as the video format:

    yt-dlp --add-metadata --embed-thumbnail --embed-chapters --embed-subs --write-subs --sub-langs "live_chat, en" --merge-output-format mkv --download-archive archive.txt --format 303+251/248+251 {url}

This way yt-dlp will skip ongoing and newly finished livestreams, leaving them to ytarchive, and download `VP9` format when it becomes available on the next day.

To archive an entire channel, both uploads and livestreams, run yt-dlp with a channel url instead of a specific video or playlist:

    yt-dlp --add-metadata --embed-thumbnail --embed-chapters --embed-subs --write-subs --sub-langs "live_chat, en" --merge-output-format mkv --download-archive archive.txt --format 303+251/248+251/bestvideo*+bestaudio/best -o "[%(upload_date)s] %(title).200B - %(id)s.%(ext)s" https://www.youtube.com/@ChannelName

#### Twitcasting

To download an archive use [yt-dlp](https://github.com/yt-dlp/yt-dlp).

Livestreams on Twitcasting are particularly sensitive to network connection latency, and the recording file might often end up missing fragments if connection is not good enough or the server is under high load. Using lower quality might help.

Ongoing livestreams also can be downloaded with [yt-dlp](https://github.com/yt-dlp/yt-dlp). When specifying quality other than `best`, note that not every quality code is available on every stream, and it is better to always add `best` as a fallback option. 

    yt-dlp -f 220k/best https://twitcasting.tv/c:username

Another tool for downloading livestreams is [TwcLazer](https://github.com/HoloArchivists/TwcLazer). It uses different download method compared to yt-dlp, so one might serve as alternative to another when something breaks due to changes on server side.

#### FC2

[fc2-live-dl](https://github.com/HoloArchivists/fc2-live-dl) can be used for downloading ongoing FC2 streams. Default options are good for most cases:

    fc2-live-dl {url}

Comes with [autofc2](https://github.com/HoloArchivists/fc2-live-dl#autofc2) script, that allows to continuously monitor a channel and download a stream as it goes live. Uses configuration file in JSON format, but file structure is simple and an example config is provided. Paste config file content in any online JSON validator to check it for possible formatting errors.

Note, that FC2 only allows a single window with particular livestream, and opening channel that is currently being downloaded in a browser will result in error and is likely to interrupt download.

#### Youtube community posts

avtdl supports saving community post text in a file natively ([example config](https://github.com/15532th/avtdl/blob/master/EXAMPLES.md#save-community-posts-to-files)), but as an alternative, this fork of [youtube-community-tab](https://github.com/HoloArchivists/youtube-community-tab) might be used. It comes with `ytct.py` script that allows to download either a specific post by direct link or all new posts on a channel. Posts are stored in JSON format, which can be rendered to human-readable text files with third party [ytct-convert.py](https://gist.github.com/15532th/111c8b32e5d82112379703f3eab51e49) script.

