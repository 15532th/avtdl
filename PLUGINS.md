
<!-- This file is manually crafted as part of avtdl -->

## Description and configuration of available plugins

---
### Table of content:

<!-- [TOC] -->

* [`discord.hook` - Send record to Discord using webhook](#discordhook---send-record-to-discord-using-webhook)
* [`execute` - Run pre-defined shell command](#execute---run-pre-defined-shell-command)
* [`fc2` - Monitor for live.fc2.com](#fc2---monitor-for-livefc2com)
* [`from_file` - Monitor content of a text file](#fromfile---monitor-content-of-a-text-file)
* [`to_file` - Write record to a text file](#tofile---write-record-to-a-text-file)
* [`filter.noop` - Pass everything through](#filternoop---pass-everything-through)
* [`filter.void` - Drop everything](#filtervoid---drop-everything)
* [`filter.match` - Keep records with specific words](#filtermatch---keep-records-with-specific-words)
* [`filter.exclude` - Drop records with specific words](#filterexclude---drop-records-with-specific-words)
* [`filter.event` - Filter for records with "Event" type](#filterevent---filter-for-records-with-event-type)
* [`filter.type` - Filter for records of specific type](#filtertype---filter-for-records-of-specific-type)
* [`filter.json` - Format record as JSON](#filterjson---format-record-as-json)
* [`filter.format` - Format record as text](#filterformat---format-record-as-text)
* [`filter.deduplicate` - Drop already seen records](#filterdeduplicate---drop-already-seen-records)
* [`nitter` - Monitor for Nitter instances](#nitter---monitor-for-nitter-instances)
* [`filter.nitter.pick` - Pick `NitterRecord` with specified properties](#filternitterpick---pick-nitterrecord-with-specified-properties)
* [`filter.nitter.drop` - Drop `NitterRecord` without specified properties.](#filternitterdrop---drop-nitterrecord-without-specified-properties)
* [`generic_rss` - RSS feed monitor](#genericrss---rss-feed-monitor)
* [`twitcast` - Monitor for twitcasting.tv](#twitcast---monitor-for-twitcastingtv)
* [`twitch` - Monitor for twitch.tv](#twitch---monitor-for-twitchtv)
* [`get_url` - Monitor web page text](#geturl---monitor-web-page-text)
* [`xmpp` - Send record as a Jabber message](#xmpp---send-record-as-a-jabber-message)
* [`rss` - Youtube channel RSS feed monitor](#rss---youtube-channel-rss-feed-monitor)
* [`community` - Youtube community page monitor](#community---youtube-community-page-monitor)
* [`channel` - Youtube channel monitor](#channel---youtube-channel-monitor)
* [`filter.channel` - Pick `YoutubeVideoRecord` with specified properties](#filterchannel---pick-youtubevideorecord-with-specified-properties)
* [`prechat` - Youtube livechat monitor](#prechat---youtube-livechat-monitor)

---

### `discord.hook` - Send record to Discord using webhook

To generate webhook url follow instructions in "Making a Webhook" section of
<https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks>

Some record types support rich formatting when sent to Discord, such as
showing author's avatar and links to attached images. Youtube videos will
show thumbnail, however embedding video itself is not supported.

Records coming within six seconds one after another will be batched together into a single message.
When too many records are received at once, they will be sent with delays to conform Discord
rate limits. Records deemed to be too long to fit in Discord message
[length limits](https://discord.com/developers/docs/resources/channel#create-message-jsonform-params)
will be dropped with a warning.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `url`: webhook url. Required.
##### 
* `timezone`: takes timezone name from <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones> (or OS settings if omitted), converts record fields containing date and time to this timezone. Not required.

---

### `execute` - Run pre-defined shell command

Take `command` string, replace keywords provided in `placeholders` with corresponding fields
of currently processed record. For example, if `command` is set to

    "yt-dlp {url}"`

and currently processed record comes from Youtube RSS feed and has `url` field value
`https://www.youtube.com/watch?v=L692Sxz3thw`, then with default `placeholders`
resulting command will be

    yt-dlp https://www.youtube.com/watch?v=L692Sxz3thw

Note that placeholders do not have to be wrapped in `{}` and can, in fact, be any
arbitrary text strings. However, placeholders are replaced by corresponding values
one after another, so using piece of text that might come up in record field might
produce unexpected results.

Make sure the executable the command uses (`yt-dlp` in this case) is installed and
can be run from working directory by current user. It is advised to confirm command
can be executed manually and finishes without errors before automating it.

For each entity separate working directory can be configured. Output is shown
in the same window by default, but can be redirected in file, with either static
or autogenerated name.

Produces Events on beginning, successful end and failure of executed command
if corresponding entity settings are enabled. They can be used to send Discord
or Jabber notifications or execute another command when it happens.

Processed record itself can also be passed down the chain if command failed,
providing a way to try a different one as a fallback. For example, record with
Youtube url could be first handled by `ytarchive` and passed to `yt-dlp` if
it happens to fail due to video link not being a livestream.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `command`: shell command to be executed on every received record. Supports placeholders that will be replaced with currently processed record fields values. Required.
##### 
* `working_dir`: path to directory where command will be executed. If not set current working directory is used. Not required.
* `log_dir`: write executed process output to a file in this directory if set. If it is not set, output will not be redirected to file. Not required.
* `log_filename`: filename to write executed process output to. If not defined is generated automatically based on command and entity name. Not required.
* `placeholders`: parts of `command` string that should be replaced with processed record fields, defined  as mapping `'placeholder': 'record field name'`. Default value is `"{url}": "url", "{title}": "title", "{text}": "text"`.
* `static_placeholders`: parts of `command` string that will be replaced with provided values, defined as mapping `'placeholder': 'replacement string'`. Intended to allow reusing same `command` template for multiple entities. Not required.
* `forward_failed`: emit currently processed record down the chain if subprocess returned non-zero exit code. Can be used to define fallback command in case this one fails. Default value is `false`.
* `report_failed`: emit Event with type "error" if subprocess returned non-zero exit code or raised exception. Default value is `true`.
* `report_finished`: emit Event with type "finished" if subprocess returned zero as exit code. Default value is `false`.
* `report_started`: emit Event with type "started" before starting subprocess. Default value is `false`.


#### Produced records types:


<details markdown="block">
  <summary>Event</summary>

Record produced by internal event (usually error) inside the plugin


* `event_type`: text describing the nature of event, can be used to filter classes of events, such as errors. 
* `text`: text describing specific even details. 

</details>

---

### `fc2` - Monitor for live.fc2.com

Monitors fc2.com user with given id, produces record when it goes live.
For user `https://live.fc2.com/24374512/` user id would be `24374512`.

Since endpoint used for monitoring does not provide user nickname,
the name of configuration entity is used instead.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `user_id`: user id, numeric part at the end of livestream url. Required.
##### 
* `update_interval`: how often monitored channel will be checked, in seconds. Default value is `120`.
* `cookies_file`: path to text file containing cookies in Netscape format. Not required.
* `headers`: custom HTTP headers as pairs "key": value". "Set-Cookie" header will be ignored, use `cookies_file` option instead. Default value is `"Accept-Language": "en-US,en;q=0.9"`.


#### Produced records types:


<details markdown="block">
  <summary>FC2Record</summary>

Represents event of a stream going live on FC2


* `name`: name of the config entity for this user. 
* `url`: url of the user stream. 
* `user_id`: unique for given user/channel part of the stream url. 
* `title`: stream title. 
* `info`: stream description. 
* `start`: timestamp of the stream start. 
* `avatar_url`: link to user's avatar. 
* `login_only`: Whether logging in is required to view current livestream. 

</details>

---

### `from_file` - Monitor content of a text file

On specified intervals check existence and last modification time
of target file, and if it changed read file content
either line by line or as a whole and emit it as a text record(s).

Records are not checked for uniqueness, so appending content to the end
of the existing file will produce duplicates of already sent records.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `path`: path to monitored file. Required.
##### 
* `update_interval`: how often monitored file should be checked, in seconds. Default value is `60`.
* `encoding`: encoding used to open monitored file. If not specified default system-wide encoding is used. Not required.
* `split_lines`: if true, each line of the file will create a separate record. Otherwise, a single record will be generated with entire file content. Default value is `false`.


#### Produced records types:


<details markdown="block">
  <summary>TextRecord</summary>

Simplest record, containing only a single text field


* `text`: content of the record. 

</details>

---

### `to_file` - Write record to a text file

Takes record coming from a Chain, converts it to text representation,
and write to a file in given directory. When file already exists,
new records can be appended to the end of the file or overwrite it.

Output file name can be static or generated dynamically based on template
filled with values from the record fields: every occurrence of `{text}`
in filename will be replaced with value of the `text` field of processed
record, if the record has one.

Allows writing record as human-readable text representation or as names and
values of the record fields in json format. For custom format template pass record
through `filter.format` plugin prior to this one.

Produces `Event` with `error` type if writing to target file fails.

Note discrepancy between default value of `encoding` setting between `from_file`
and `to_file` plugins. Former is expected to be able to read files produced by
different software and therefore relies on system-wide settings. It would make
sense to do the same in latter, but it would introduce possibility of failing
to write records containing text with Unicode codepoints that cannot be represented
using system-wide encoding.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `filename`: name of the output file. Supports templating with `{...}`. Required.
##### 
* `path`: directory where output file should be created. Default is current directory. Not required.
* `encoding`: output file encoding. Default value is `utf8`.
* `output_format`: one of `str`, `repr`, `json`, `pretty_json`, `hash`. Default value is `text`.
* `overwrite`: whether file should be overwritten in if it already exists. Default value is `true`.
* `append`: if true, new record will be written in the end of the file without overwriting already present lines. Default value is `true`.
* `prefix`: string that will be appended before record text. Can be used to separate records from each other or for simple templating. Not required.
* `postfix`: string that will be appended after record text. Not required.


#### Produced records types:


<details markdown="block">
  <summary>Event</summary>

Record produced by internal event (usually error) inside the plugin


* `event_type`: text describing the nature of event, can be used to filter classes of events, such as errors. 
* `text`: text describing specific even details. 

</details>

---

### `filter.noop` - Pass everything through

Lets all coming records pass through unchanged, effectively
doing nothing with them. As any other filter it has entities,
so it can be used as a merging point to gather records from
multiple chains and process then in a single place.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.

---

### `filter.void` - Drop everything

Does not produce anything, dropping any incoming records.
Can be used to stuff multiple chains in one if the need ever arise.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.

---

### `filter.match` - Keep records with specific words

This filter lets through records, that has one of values
defined by `patterns` list found in any (or specified) field of the record.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `patterns`: list of strings to search in the record. Required.
##### 
* `fields`: field names to search patterns in. If not specified all fields are checked. Not required.

---

### `filter.exclude` - Drop records with specific words

This filter lets through records, that has none of values
defined by `patterns` list found in any (or specified) field of the record.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `patterns`: list of strings to search in the record. Required.
##### 
* `fields`: field names to search patterns in. If not specified all fields are checked. Not required.

---

### `filter.event` - Filter for records with "Event" type

Only lets through Events and not normal Records. Can be used to
set up notifications on events (such as errors) from, for example,
`execute` plugin within the same chain that uses it, by separating
them from regular records.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
##### 
* `event_types`: list of event types. See descriptions of plugins producing events for possible values. Not required.


#### Produced records types:


<details markdown="block">
  <summary>Event</summary>

Record produced by internal event (usually error) inside the plugin


* `event_type`: text describing the nature of event, can be used to filter classes of events, such as errors. 
* `text`: text describing specific even details. 

</details>

---

### `filter.type` - Filter for records of specific type

Only lets through records of specified types, such as `Event` or `YoutubeVideoRecord`.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `types`: list of records class names, such as "Record" and "Event" . Required.
##### 
* `exact_match`: whether match should check for exact record type or look in entire records hierarchy up to Record. Default value is `false`.

---

### `filter.json` - Format record as JSON

Takes record and produces a new `TextRecord` rendering fields of the
original record in JSON format, with option for pretty-print.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
##### 
* `prettify`: whether output should be multiline and indented or a single line. Default value is `false`.


#### Produced records types:


<details markdown="block">
  <summary>TextRecord</summary>

Simplest record, containing only a single text field


* `text`: content of the record. 

</details>

---

### `filter.format` - Format record as text

Takes record and produces a new `TextRecord` by taking `template` string
and replacing "{placeholder}" with value of `placeholder` field of the
current record, where `placeholder` is any field the record might have.
If one of placeholders is not a field of specific record, it will be
replaced with value defined in `missing` parameter if it is specified,
otherwise it will be left intact.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `template`: template string with placeholders that will be filled with corresponding values from current record. Required.
##### 
* `missing`: if specified, will be used to fill template placeholders that do not have corresponding fields in current record. Not required.


#### Produced records types:


<details markdown="block">
  <summary>TextRecord</summary>

Simplest record, containing only a single text field


* `text`: content of the record. 

</details>

---

### `filter.deduplicate` - Drop already seen records

Checks if the `field` field value of the current record has already been
present in one of the previous records and only let it through otherwise.

`field` might be either a record field name or one of `hash` or `as_json`
for sha1 and fulltext comparison. If `field` is not present in the current
record, it will be passed through as if it's new.

This filter will work with records of any type, as long as they have defined
field (all records have `hash` and `as_json`). For example, it is possible
to ensure no multiple records for a single video will be produced
in a chain, that gather records from Youtube channel and Youtube RSS monitors,
by passing them to an entity of this filter with `field` set to `video_id`.

Note, that history is kept in memory, so it will not be persisted between
restarts.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
##### 
* `field`: field name to use for comparison. Default value is `hash`.
* `history_size`: how many old records should be kept in memory. Default value is `10000`.

---

### `nitter` - Monitor for Nitter instances

Monitors recent tweets, retweets and replies of Twitter user
by scraping and parsing data from a Nitter instance.

Examples of supported url:

- `https://nitter.net/username`
- `https://nitter.net/username/with_replies`

Some instances might not be happy getting automated scraping. Make sure
to use reasonable `update_interval` and keep eyes on 4XX and 5XX responses
in log, as they might indicate server is under high load or refuses to
communicate.

Nitter has built in RSS feed, though not all instances enable it, so it
can also be monitored with `generic_rss` plugin instead of this one.

Twitter Spaces appears on user feed as normal tweets with text only
containing a single link similar to `https://x.com/i/spaces/2FsjOybqEbnzR`.
It therefore can be picked up by using a regular full-text `match` filter.


#### Plugin configuration options:
* `db_path`: path to sqlite database file keeping history of old records of this monitor.
Might specify a path to a directory containing the file (with trailing slash)
or direct path to the file itself (without a slash). If special value `:memory:` is used,
database is kept in memory and not stored on disk at all, providing a clean database on every startup. Default value is `:memory:`.



#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `url`: url that should be monitored. Required.
##### 
* `update_interval`: How often the monitored url will be checked, in seconds. Default value is `1800`.
* `cookies_file`: path to text file containing cookies in Netscape format. Not required.
* `headers`: custom HTTP headers as pairs "key": value". "Set-Cookie" header will be ignored, use `cookies_file` option instead. Default value is `"Accept-Language": "en-US,en;q=0.9"`.
* `adjust_update_interval`: change delay before next update based on response headers. This setting doesn't affect timeouts after failed requests. Default value is `true`.
* `quiet_start`: throw away new records on the first update after application startup. Default value is `false`.
* `quiet_first_time`: throw away new records produced on first update of given url. Default value is `true`.
* `max_continuation_depth`: when updating feed with pagination support, only continue for this many pages. Default value is `10`.
* `next_page_delay`: when updating feed with pagination support, wait this much before loading next page. Default value is `1`.
* `allow_discontinuity`: when updating feed with pagination support, if this setting is enabled and error happens when loading a page, records from already parsed pages will not be dropped. It will allow update of the feed to finish, but older records from deeper pages will then never be parsed on consecutive updates. Default value is `false`.
* `fetch_until_the_end_of_feed_mode`: when updating feed with pagination support, enables special mode, which makes monitor try loading and parsing all pages until the end, even if they have been already parsed. Designed for purpose of archiving entire feed content. Default value is `false`.


#### Produced records types:


<details markdown="block">
  <summary>NitterRecord</summary>

Single post as parsed from Nitter instance

Depending on the tweet type (regular, retweet, reply, quote) some fields might be empty


* `retweet_header`: text line saying this is a retweet. 
* `reply_header`: text line saying this tweet is a reply. 
* `url`: tweet url. 
* `author`: user's visible name. 
* `username`: user's handle. 
* `avatar_url`: link to the picture used as user's avatar. 
* `published`: tweet timestamp. 
* `text`: tweet text with stripped formatting. 
* `html`: tweet text as raw html. 
* `attachments`: list of links to attached images or video thumbnails. 
* `quote`: Nested NitterRecord containing tweet being quited. 

</details>

---

### `filter.nitter.pick` - Pick `NitterRecord` with specified properties

Lets through `NitterRecord` if it matches any of specified criteria.
All records from other sources pass through without filtering.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
##### 
* `retweet`: match retweets. Default value is `false`.
* `reply`: match replies. Default value is `false`.
* `quote`: match quotes. Default value is `false`.
* `regular_tweet`: match regular tweets, that are not a retweet, reply or quote. Default value is `false`.
* `author`: match if given string is a part of the name of the author of the tweet. Not required.
* `username`: match if given string is a part of tweet author's username (without the "@" symbol). Not required.


#### Produced records types:


<details markdown="block">
  <summary>NitterRecord</summary>

Single post as parsed from Nitter instance

Depending on the tweet type (regular, retweet, reply, quote) some fields might be empty


* `retweet_header`: text line saying this is a retweet. 
* `reply_header`: text line saying this tweet is a reply. 
* `url`: tweet url. 
* `author`: user's visible name. 
* `username`: user's handle. 
* `avatar_url`: link to the picture used as user's avatar. 
* `published`: tweet timestamp. 
* `text`: tweet text with stripped formatting. 
* `html`: tweet text as raw html. 
* `attachments`: list of links to attached images or video thumbnails. 
* `quote`: Nested NitterRecord containing tweet being quited. 

</details>

---

### `filter.nitter.drop` - Drop `NitterRecord` without specified properties.

Lets through `NitterRecord` if it doesn't match all of the specified criteria.
All records from other sources pass through without filtering.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
##### 
* `retweet`: match retweets. Default value is `false`.
* `reply`: match replies. Default value is `false`.
* `quote`: match quotes. Default value is `false`.
* `regular_tweet`: match regular tweets, that are not a retweet, reply or quote. Default value is `false`.
* `author`: match if given string is a part of the name of the author of the tweet. Not required.
* `username`: match if given string is a part of tweet author's username (without the "@" symbol). Not required.


#### Produced records types:


<details markdown="block">
  <summary>NitterRecord</summary>

Single post as parsed from Nitter instance

Depending on the tweet type (regular, retweet, reply, quote) some fields might be empty


* `retweet_header`: text line saying this is a retweet. 
* `reply_header`: text line saying this tweet is a reply. 
* `url`: tweet url. 
* `author`: user's visible name. 
* `username`: user's handle. 
* `avatar_url`: link to the picture used as user's avatar. 
* `published`: tweet timestamp. 
* `text`: tweet text with stripped formatting. 
* `html`: tweet text as raw html. 
* `attachments`: list of links to attached images or video thumbnails. 
* `quote`: Nested NitterRecord containing tweet being quited. 

</details>

---

### `generic_rss` - RSS feed monitor

Monitors RSS feed for new entries. Will attempt to adjust
update interval based on HTTP response headers.

Depending on specific feed format, fields names and content
might vary greatly. Commonly present standardized fields are
`url`, `title` and `author`, though they might be empty
in some feeds.

Before defining a command to be executed for records of newly
set feed it is recommended to inspect feed entity content by
forwarding records in a file in JSON format using `to_file` plugin.

Normally feeds have some kind of value to unique identify
feed entries, but in case there is none parser will attempt
to create one by combining `link` and `title` or `summary` fields.


#### Plugin configuration options:
* `db_path`: path to sqlite database file keeping history of old records of this monitor.
Might specify a path to a directory containing the file (with trailing slash)
or direct path to the file itself (without a slash). If special value `:memory:` is used,
database is kept in memory and not stored on disk at all, providing a clean database on every startup. Default value is `:memory:`.



#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `update_interval`: how often the monitored source should be checked for new content, in seconds. Required.
* `url`: url that should be monitored. Required.
##### 
* `cookies_file`: path to text file containing cookies in Netscape format. Not required.
* `headers`: custom HTTP headers as pairs "key": value". "Set-Cookie" header will be ignored, use `cookies_file` option instead. Default value is `"Accept-Language": "en-US,en;q=0.9"`.
* `adjust_update_interval`: change delay before next update based on response headers. This setting doesn't affect timeouts after failed requests. Default value is `true`.
* `quiet_start`: throw away new records on the first update after application startup. Default value is `false`.
* `quiet_first_time`: throw away new records produced on first update of given url. Default value is `true`.


#### Produced records types:


<details markdown="block">
  <summary>GenericRSSRecord</summary>

Represents RSS feed entry

Might contain additional fields if they are present in the feed.


* `uid`: value that is unique for this entry of RSS feed. 
* `url`: "href" or "link" field value of this entry. 
* `summary`: "summary" or "description" field value of this entry. 
* `author`: "author" field value. Might be empty. 
* `title`: "title" field value. Might be empty. 
* `published`: "published" or "issued" field value of this entry. 

</details>

---

### `twitcast` - Monitor for twitcasting.tv

Monitors twitcasting.tv user with given id, produces record when it goes live.
For user `https://twitcasting.tv/c:username` user id would be `c:username`.

Streams on Twitcasting might be set to be visible only for members of specific group.
For monitoring such streams it is necessarily to provide login cookies of
account being member of the group. Password-protected and age-restricted streams
do not require that.

Rate limits for endpoint used to check if user is live are likely relatively high,
but it is better to keep `update_interval` big enough for combined amount of updates
for all monitored users to not exceed one request per second.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `user_id`: user id that should be monitored. Required.
##### 
* `update_interval`: how often user will be checked for being live, in seconds. Default value is `60`.
* `cookies_file`: path to text file containing cookies in Netscape format. Not required.
* `headers`: custom HTTP headers as pairs "key": value". "Set-Cookie" header will be ignored, use `cookies_file` option instead. Default value is `"Accept-Language": "en-US,en;q=0.9"`.


#### Produced records types:


<details markdown="block">
  <summary>TwitcastRecord</summary>

Represents even of user going live on Twitcasting


* `user_id`: unique part of channel url. 
* `movie_id`: unique id for current livestream. 
* `url`: user (channel) url. 
* `movie_url`: current livestream url. 
* `title`: livestream title. 

</details>

---

### `twitch` - Monitor for twitch.tv

Monitors twitch.tv user with given username, produces record when it goes live.
For user `https://www.twitch.tv/username` username would be `username`.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `username`: Twitch username of monitored channel. Required.
##### 
* `update_interval`: how often user will be checked for being live, in seconds. Default value is `300`.
* `cookies_file`: path to text file containing cookies in Netscape format. Not required.
* `headers`: custom HTTP headers as pairs "key": value". "Set-Cookie" header will be ignored, use `cookies_file` option instead. Default value is `"Accept-Language": "en-US,en;q=0.9"`.
* `adjust_update_interval`: change delay before next update based on response headers. This setting doesn't affect timeouts after failed requests. Default value is `true`.


#### Produced records types:


<details markdown="block">
  <summary>TwitchRecord</summary>

Represents even of user going live on Twitch


* `url`: channel url. 
* `username`: username value from configuration entity. 
* `title`: stream title. 

</details>

---

### `get_url` - Monitor web page text

Download content of web page at `url` and emit it as a `TextRecord`
if it has changed since last update. Intended for working with simple
text endpoints.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `update_interval`: how often the monitored source should be checked for new content, in seconds. Required.
* `url`: url to monitor. Required.
##### 
* `cookies_file`: path to text file containing cookies in Netscape format. Not required.
* `headers`: custom HTTP headers as pairs "key": value". "Set-Cookie" header will be ignored, use `cookies_file` option instead. Default value is `"Accept-Language": "en-US,en;q=0.9"`.
* `adjust_update_interval`: change delay before next update based on response headers. This setting doesn't affect timeouts after failed requests. Default value is `true`.


#### Produced records types:


<details markdown="block">
  <summary>TextRecord</summary>

Simplest record, containing only a single text field


* `text`: content of the record. 

</details>

---

### `xmpp` - Send record as a Jabber message

Converts records to text representation and sends them as messages
to specified recipients. Sends each record in separate message,
does not impose any limits on frequency or size of messages, leaving
it to server side.


#### Plugin configuration options:
* `xmpp_username`: JID of the account to be used to send messages, resource included. Required.
* `xmpp_pass`: password of the account to be used to send messages. Required.



#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `jid`: JID to send message to. Required.
##### 
* `timezone`: takes timezone name from <https://en.wikipedia.org/wiki/List_of_tz_database_time_zones> or OS settings if omitted, converts record fields containing date and time to this timezone. Not required.

---

### `rss` - Youtube channel RSS feed monitor

Monitors channel for new uploads and livestreams using RSS feed
generated by Youtube. Requires old channel id format. In order to obtain
RSS feed url for a given channel, use "View Source" on a channel page
and search for "rss".

Example of supported url:

- `https://www.youtube.com/feeds/videos.xml?channel_id=UCK0V3b23uJyU4N8eR_BR0QA`

RSS feed is smaller and faster to parse compared to HTML channel page,
but by design only shows updates of a single channel and doesn't support
authentication and therefore unable to show member-only streams.

Scheduled date for upcoming streams is not present in feed itself, so it
is obtained by fetching and parsing video page first time it appears in
the feed. It then gets updated until stream goes live, unless `track_reschedule`
option is disabled.

No matter how often the url gets fetched, content of the feed only gets
changed once every 15 minutes, so setting `update_interval` lower than that
value is not recommended. This monitor will attempt to calculate time of the
next update from HTTP headers and schedule next request right after it. Use
`adjust_update_interval` to disable this behavior.


#### Plugin configuration options:
* `db_path`: path to sqlite database file keeping history of old records of this monitor.
Might specify a path to a directory containing the file (with trailing slash)
or direct path to the file itself (without a slash). If special value `:memory:` is used,
database is kept in memory and not stored on disk at all, providing a clean database on every startup. Default value is `:memory:`.



#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `url`: url that should be monitored. Required.
##### 
* `update_interval`: How often the feed should be updated, in seconds. Default value is `900`.
* `cookies_file`: path to text file containing cookies in Netscape format. Not required.
* `headers`: custom HTTP headers as pairs "key": value". "Set-Cookie" header will be ignored, use `cookies_file` option instead. Default value is `"Accept-Language": "en-US,en;q=0.9"`.
* `adjust_update_interval`: change delay before next update based on response headers. This setting doesn't affect timeouts after failed requests. Default value is `true`.
* `quiet_start`: throw away new records on the first update after application startup. Default value is `false`.
* `quiet_first_time`: throw away new records produced on first update of given url. Default value is `true`.
* `track_reschedule`: Keep track of scheduled time of upcoming streams, emit record again if it changed to earlier date. Default value is `true`.


#### Produced records types:


<details markdown="block">
  <summary>YoutubeFeedRecord</summary>

Youtube video or livestream parsed from channel RSS feed


* `url`: link to the video. 
* `title`: title of the video at time of parsing. 
* `published`: published value of the feed item, usually the time when video was uploaded or livestream frame was set up. 
* `updated`: updated value of the feed item. If different from `published` might indicate either a change to video title, thumbnail or description, or change in video status, for example livestream ending. 
* `author`: author name, as shown on channel icon. 
* `video_id`: short string identifying video on Youtube. Part of video url. 
* `summary`: video description. 
* `views`: current number of views. Is zero for upcoming and ongoing livestreams. 
* `scheduled`: for upcoming livestream is a time it is scheduled to go live at, otherwise absent. 

</details>

---

### `community` - Youtube community page monitor

Monitors posts on community page of a channel, supports
member-only posts if login cookies are provided. Some features,
such as polls, are not supported.

Examples of supported url:

- `https://www.youtube.com/@ChannelName/community`
- `https://www.youtube.com/channel/UCK0V3b23uJyU4N8eR_BR0QA/community`


#### Plugin configuration options:
* `db_path`: path to sqlite database file keeping history of old records of this monitor.
Might specify a path to a directory containing the file (with trailing slash)
or direct path to the file itself (without a slash). If special value `:memory:` is used,
database is kept in memory and not stored on disk at all, providing a clean database on every startup. Default value is `:memory:`.



#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `url`: url of community page of the channel. Required.
##### 
* `update_interval`: how often community page will be checked for new posts. Default value is `1800`.
* `cookies_file`: path to text file containing cookies in Netscape format. Not required.
* `headers`: custom HTTP headers as pairs "key": value". "Set-Cookie" header will be ignored, use `cookies_file` option instead. Default value is `"Accept-Language": "en-US,en;q=0.9"`.
* `adjust_update_interval`: change delay before next update based on response headers. This setting doesn't affect timeouts after failed requests. Default value is `true`.
* `quiet_start`: throw away new records on the first update after application startup. Default value is `false`.
* `quiet_first_time`: throw away new records produced on first update of given url. Default value is `true`.
* `max_continuation_depth`: when updating feed with pagination support, only continue for this many pages. Default value is `10`.
* `next_page_delay`: when updating feed with pagination support, wait this much before loading next page. Default value is `1`.
* `allow_discontinuity`: when updating feed with pagination support, if this setting is enabled and error happens when loading a page, records from already parsed pages will not be dropped. It will allow update of the feed to finish, but older records from deeper pages will then never be parsed on consecutive updates. Default value is `false`.
* `fetch_until_the_end_of_feed_mode`: when updating feed with pagination support, enables special mode, which makes monitor try loading and parsing all pages until the end, even if they have been already parsed. Designed for purpose of archiving entire feed content. Default value is `false`.


#### Produced records types:


<details markdown="block">
  <summary>CommunityPostRecord</summary>

Youtube community post content


* `channel_id`: channel ID in old format. 
* `post_id`: unique id of the post. 
* `author`: author channel name. 
* `avatar_url`: link to avatar of the channel. 
* `vote_count`: current number of upvotes. 
* `sponsor_only`: indicates weather the post is member-only. 
* `published_text`: localized text saying how long ago the video was uploaded. 
* `full_text`: post content as plaintext. 
* `attachments`: list of links to attached images or video thumbnails. 
* `video_id`: if post links to youtube video will have video id, otherwise absent. 
* `original_post`: for reposts contains original post content, otherwise absent. 

</details>


<details markdown="block">
  <summary>SharedCommunityPostRecord</summary>

Youtube community post that is itself a repost of another post


* `channel_id`: channel ID in old format. 
* `post_id`: unique id of the post. 
* `author`: author channel name. 
* `avatar_url`: link to avatar of the channel. 
* `published_text`: localized text saying how long ago the video was uploaded. 
* `full_text`: post content. 
* `original_post`: not present in shared post. 

</details>

---

### `channel` - Youtube channel monitor

Monitors Youtube url listing videos, such as channels main page,
videos and streams tab of a channel, as well as playlists, and,
with login cookies, subscriptions feed or the main page.

Due to small differences in presentation aforementioned sources
have, same video might have slightly different appearance when
parsed from different urls. For example, video parsed from main
page or subscriptions feed will not have full description text.

Examples of supported url:

- `https://www.youtube.com/@ChannelName`
- `https://www.youtube.com/@ChannelName/videos`
- `https://www.youtube.com/@ChannelName/streams`
- `https://www.youtube.com/channel/UCK0V3b23uJyU4N8eR_BR0QA/`
- `https://www.youtube.com/playlist?list=PLWGY3fcU-ZeQmBfoJ6SmT8v2zV8NEhrB2`
- `https://www.youtube.com/feed/subscriptions` (providing cookies is necessarily)

Unlike `rss` monitor, with login cookies it can see videos and streams
with limited access (such as member-only).

While monitoring a single channel is less efficient, both
bandwidth- and computational-wise, using this monitor with
subscriptions feed url on a dedicated account is a recommended way
to monitor a high amount (hundreds) of channels, as it only requires
loading a single page to check all of them for updates.


#### Plugin configuration options:
* `db_path`: path to sqlite database file keeping history of old records of this monitor.
Might specify a path to a directory containing the file (with trailing slash)
or direct path to the file itself (without a slash). If special value `:memory:` is used,
database is kept in memory and not stored on disk at all, providing a clean database on every startup. Default value is `:memory:`.



#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `url`: url that should be monitored. Required.
##### 
* `update_interval`: . Default value is `1800`.
* `cookies_file`: path to text file containing cookies in Netscape format. Not required.
* `headers`: custom HTTP headers as pairs "key": value". "Set-Cookie" header will be ignored, use `cookies_file` option instead. Default value is `"Accept-Language": "en-US,en;q=0.9"`.
* `adjust_update_interval`: change delay before next update based on response headers. This setting doesn't affect timeouts after failed requests. Default value is `true`.
* `quiet_start`: throw away new records on the first update after application startup. Default value is `false`.
* `quiet_first_time`: throw away new records produced on first update of given url. Default value is `true`.
* `max_continuation_depth`: when updating feed with pagination support, only continue for this many pages. Default value is `10`.
* `next_page_delay`: when updating feed with pagination support, wait this much before loading next page. Default value is `1`.
* `allow_discontinuity`: when updating feed with pagination support, if this setting is enabled and error happens when loading a page, records from already parsed pages will not be dropped. It will allow update of the feed to finish, but older records from deeper pages will then never be parsed on consecutive updates. Default value is `false`.
* `fetch_until_the_end_of_feed_mode`: when updating feed with pagination support, enables special mode, which makes monitor try loading and parsing all pages until the end, even if they have been already parsed. Designed for purpose of archiving entire feed content. Default value is `false`.


#### Produced records types:


<details markdown="block">
  <summary>YoutubeVideoRecord</summary>

Youtube video or livestream listed among others on Youtube page

Produced by parsing channels main page, videos and streams tab,
as well as playlists, and, with login cookies, subscriptions feed.


* `video_id`: short string identifying video on Youtube. Part of video url. 
* `url`: link to video, uses `https://www.youtube.com/watch?v=<video_id>` format. 
* `title`: title of the video at time of parsing. 
* `summary`: snippet of video description. Not always available. 
* `scheduled`: scheduled date for upcoming stream or premiere. 
* `author`: channel name. 
* `avatar_url`: link to avatar of the channel. Not always available. 
* `channel_link`: link to the channel uploading the video. 
* `channel_id`: channel ID in old format (such as `UCK0V3b23uJyU4N8eR_BR0QA`). 
* `published_text`: localized text saying how long ago the video was uploaded. 
* `length`: text showing the video duration (hh:mm:ss). 
* `is_upcoming`: indicates that video is an upcoming livestream or premiere. 
* `is_live`: indicates that the video is a livestream or premiere that is currently live. 
* `is_member_only`: indicated that the video is limited to members of the channel. Note that video status might be changed at any time. 

</details>

---

### `filter.channel` - Pick `YoutubeVideoRecord` with specified properties

Filter that only lets `YoutubeVideoRecord` through if it has certain properties.
All records from other sources pass through without filtering.

If multiple settings are set to `true`, they all should match. Use multiple
entities if picking records with any of multiple properties is required.


#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
##### 
* `upcoming`: to pass filter record should be either upcoming livestream or scheduled premiere. Default value is `true`.
* `live`: to pass filter record should be an ongoing livestream. Default value is `false`.
* `member_only`: to pass filter record should be marked as member-only. Default value is `false`.


#### Produced records types:


<details markdown="block">
  <summary>YoutubeVideoRecord</summary>

Youtube video or livestream listed among others on Youtube page

Produced by parsing channels main page, videos and streams tab,
as well as playlists, and, with login cookies, subscriptions feed.


* `video_id`: short string identifying video on Youtube. Part of video url. 
* `url`: link to video, uses `https://www.youtube.com/watch?v=<video_id>` format. 
* `title`: title of the video at time of parsing. 
* `summary`: snippet of video description. Not always available. 
* `scheduled`: scheduled date for upcoming stream or premiere. 
* `author`: channel name. 
* `avatar_url`: link to avatar of the channel. Not always available. 
* `channel_link`: link to the channel uploading the video. 
* `channel_id`: channel ID in old format (such as `UCK0V3b23uJyU4N8eR_BR0QA`). 
* `published_text`: localized text saying how long ago the video was uploaded. 
* `length`: text showing the video duration (hh:mm:ss). 
* `is_upcoming`: indicates that video is an upcoming livestream or premiere. 
* `is_live`: indicates that the video is a livestream or premiere that is currently live. 
* `is_member_only`: indicated that the video is limited to members of the channel. Note that video status might be changed at any time. 

</details>

---

### `prechat` - Youtube livechat monitor

Monitor chat of Youtube livestream and produce a record
for each chat message. Though it is capable of processing
a chat on ongoing stream and chat replay on a stream VOD,
the main purpose is to monitor and preserve chat of upcoming
livestreams.

Some features, such as polls, are not supported.


#### Plugin configuration options:
* `db_path`: path to sqlite database file keeping history of old records of this monitor.
Might specify a path to a directory containing the file (with trailing slash)
or direct path to the file itself (without a slash). If special value `:memory:` is used,
database is kept in memory and not stored on disk at all, providing a clean database on every startup. Default value is `:memory:`.



#### Entity configuration options:
* `name`: name of specific entity. Used to reference it in `Chains` section. Must be unique within a plugin. Required.
* `url`: . Required.
##### 
* `update_interval`: . Default value is `20`.
* `cookies_file`: path to text file containing cookies in Netscape format. Not required.
* `headers`: custom HTTP headers as pairs "key": value". "Set-Cookie" header will be ignored, use `cookies_file` option instead. Default value is `"Accept-Language": "en-US,en;q=0.9"`.
* `quiet_start`: throw away new records on the first update after application startup. Default value is `false`.
* `quiet_first_time`: throw away new records produced on first update of given url. Default value is `true`.


#### Produced records types:


<details markdown="block">
  <summary>YoutubeChatRecord</summary>

Youtube chat message


* `author`: name of the message author. 
* `channel`: message author channel url. 
* `badges`: localized list of message author badges (owner, moderator, member, verified and so on). 
* `timestamp`: time when message was sent. 
* `text`: message content as plaintext. 
* `amount`: for superchats, string specifying amount and currency, otherwise empty. 
* `banner_header`: used for special objects in chat, such as pinned messages. 
* `message_header`: . 
* `sticker`: supersticker name if message is a supersticker, otherwise empty. 
* `uid`: unique id of the message. 
* `action`: internal name of message type. Used for debug purposes. 
* `renderer`: internal name of message format. Used for debug purposes. 

</details>

---
