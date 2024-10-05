## Examples

This file provides examples of configurations file that are meant to achieve a certain common task or to illustrate intended usage of specific plugins in context.

Every example is meant to be a valid configuration file, that can be used standalone or as part of a bigger config. See also [configuration file example](example.config.yml), that can be used as a starting point.

---

<!-- TOC -->
  * [Examples](#examples)
      * [Download livestreams from Youtube channel](#download-livestreams-from-youtube-channel)
      * [Download member-only livestreams from subscription feed](#download-member-only-livestreams-from-subscription-feed)
      * [Monitor and download streams from both subscriptions feed and channels RSS feeds](#monitor-and-download-streams-from-both-subscriptions-feed-and-channels-rss-feeds)
      * [Download Twitcasting streams using yt-dlp](#download-twitcasting-streams-using-yt-dlp)
      * [Download FC2 streams using fc2-live-dl](#download-fc2-streams-using-fc2-live-dl)
      * [Send Jabber message about Youtube videos with specific words in the title](#send-jabber-message-about-youtube-videos-with-specific-words-in-the-title)
      * [Send Discord notification about Twitcasting and FC2 livestreams](#send-discord-notification-about-twitcasting-and-fc2-livestreams)
      * [Save community posts to files](#save-community-posts-to-files)
        * [Archive community tab](#archive-community-tab)
      * [Save Youtube video chat to a text file](#save-youtube-video-chat-to-a-text-file)
      * [Send Jabber message when channel owner comments in the chat](#send-jabber-message-when-channel-owner-comments-in-the-chat)
      * [Store tweets and images posted by a Twitter account](#store-tweets-and-images-posted-by-a-twitter-account)
      * [Monitor Twitter timeline for tweets by specific users, send the tweets to Discord](#monitor-twitter-timeline-for-tweets-by-specific-users-send-the-tweets-to-discord)
      * [Send notifications and download Twitter Spaces](#send-notifications-and-download-twitter-spaces)
      * [Monitor and download RPLAY livestreams](#monitor-and-download-rplay-livestreams)
<!-- TOC -->

---

#### Download livestreams from Youtube channel

Monitor two Youtube channels (`@ChannelName` and `@AnotherChannelName`) with default update interval of 15 minutes and send new publications urls to `ytarchive`, executed in dedicated directories (specified by template in `working_dir`) for each channel. Every new upload, be it a video or a scheduled livestream, is sent to `ytarchive`, relying on it only processing livestreams.

```yaml
actors:

  rss:
    entities:
      - name: "ChannelName"
        url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCK0V3b23uJyU4N8eR_BR0QA"
      - name: "AnotherChannelName"
        url: "https://www.youtube.com/feeds/videos.xml?channel_id=UC3In1x9H3jC4JzsIaocebWg"

  execute:
    entities:
      - name: "archive"
        command: "ytarchive --threads 3 --wait {url} best"
        working_dir: "archive/livestreams/{author}/"


chains:

  "archive channels":
    - rss:
      - "ChannelName"
      - "AnotherChannelName"
    - execute:
      - "archive"
```

#### Download member-only livestreams from subscription feed

Check subscription feed of Youtube account using cookies from `cookies.txt` and send all uploads marked as "Member Only" to `execute` plugin running ytarchive. Again, template in `working_dir` is used to ensure files from different channels gets stored in different directories.

```yaml
actors:

  channel:
    entities:
      - name: "subscriptions"
        url: "https://www.youtube.com/feed/subscriptions"
        cookies_file: "cookies.txt"
        update_interval: 900

  filter.channel:
    entities:
      - name: "subscriptions-member-only"
        member_only: true

  execute:
    entities:
      - name: "archive"
        command: "ytarchive --threads 3 --wait --cookies cookies.txt {url} best"
        working_dir: "archive/livestreams/{author} (member-only)/"


chains:

  "download-member-only":
    - channel:
      - "subscriptions"
    - filter.channel:
      - "subscriptions-member-only"
    - execute:
      - "archive"
```

#### Monitor and download streams from both subscriptions feed and channels RSS feeds

Both RSS feeds and channel pages are monitored for new uploads. All new records are then fed into the same `filter.deduplicate` entity, so that only one record (from the monitor that noticed it first) is generated for a new video. These records are then passed to `execute` plugin entity that runs `ytarchive` on them.

```yaml
actors:

  rss:
    entities:
      - name: "ChannelName"
        url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCK0V3b23uJyU4N8eR_BR0QA"
      - name: "AnotherChannelName"
        url: "https://www.youtube.com/feeds/videos.xml?channel_id=UC3In1x9H3jC4JzsIaocebWg"

  channel:
    default:
      cookies_file: "cookies.txt"
    entities:
      - name: "ChannelName"
        url: "https://www.youtube.com/@ChannelName"
      - name: "AnotherChannelName"
        url: "https://www.youtube.com/@AnotherChannelName"

  filter.deduplicate:
    entities:
      - name: "youtube channels"
        field: "video_id"
        reset_origin: true

  execute:
    entities:
      - name: "archive"
        command: "ytarchive --threads 3 --wait {url} best"
        working_dir: "archive/livestreams/{author}/"


chains:

  "monitor channels RSS":
    - rss:
      - "ChannelName"
      - "AnotherChannelName"
    - filter.deduplicate:
      - "youtube channels"

  "monitor channels pages":
    - channel:
      - "ChannelName"
      - "AnotherChannelName"
    - filter.deduplicate:
      - "youtube channels"

  "archive channels":
    - filter.deduplicate:
      - "youtube channels"
    - execute:
      - "archive"
```

#### Download Twitcasting streams using yt-dlp

Channels of users `c:user` and `c:another-user` are checked for being live every 60 seconds. When a channel goes live, the url gets passed to `execute` plugin entity that will start yt-dlp. Path to output directory uses template to ensure download process for each user runs in dedicated subdirectory.

Some streams might have limited visibility. In order to download them, a cookies file from an account that has appropriate permissions should be provided to the monitor (with `cookies_file` setting) and to yt-dlp in the command line.

```yaml
actors:

  twitcast:
    defaults:
      update_interval: 60
      cookies_file: "cookies.txt"
    entities:
      - name: "user"
        user_id: "c:user"
      - name: "another-user"
        user_id: "c:another-user"

  execute:
    entities:
      - name: "twitcasting"
        command: "yt-dlp --cookies cookies.txt -f 220k/best {url}"
        working_dir: "archive/twitcasting/{user_id}/"


chains:

  "twitcast-dl":
    - twitcast:
      - "user"
      - "another-user"
    - execute:
      - "twitcasting"
```

#### Download FC2 streams using fc2-live-dl

Monitor the channel with user id `41021654` and run fc2-live-dl when it goes live. Note that while monitoring does not require login cookies, downloading certain stream might do. Providing them only requires adjusting the download command in the `execute` plugin section.

```yaml
actors:

  fc2:
    entities:
      - name: "fc2user"
        user_id: "41021654"

  execute:
    entities:
      - name: "fc2"
        command: "fc2-live-dl --log-level debug {url}"
        working_dir: "archive/fc2/{name}/"


chains:

  "fc2-dl":
    - fc2:
      - "fc2user"
    - execute:
      - "fc2"
```

#### Send Jabber message about Youtube videos with specific words in the title

Look through subscription feed, picking records with title containing either "karaoke" or "sing" in any position. Send them to `user@example.com` on Jabber from account, specified in the `config` section of `xmpp` plugin.

```yaml
actors:

  channel:
    entities:
      - name: "subscriptions"
        url: "https://www.youtube.com/feed/subscriptions"
        cookies_file: "cookies.txt"

  filter.match:
    entities:
      - name: "karaoke"
        fields:
          - "title"
        patterns:
          - "karaoke"
          - "sing"

  xmpp:
    config:
        xmpp_username: "bot@example.com/avtdl"
        xmpp_pass: "bot's password"
    entities:
      - name: "user"
        jid: "user@example.com"
        timezone: "UTC"


chains:

  "notify-karaoke":
    - channel:
      - "subscriptions"
    - filter.match:
      - "karaoke"
    - xmpp:
      - "user"
```

#### Send Discord notification about Twitcasting and FC2 livestreams

Check two Twitcast channels and an FC2 user with the specified update intervals, send message into Discord channel when livestream is detected on any of them.

```yaml
actors:

  twitcast:
    defaults:
        update_interval: 120
    entities:
      - name: "user"
        user_id: "c:user"
      - name: "another-user"
        user_id: "c:another-user"
  
  fc2:
    entities:
      - name: "fc2user"
        user_id: "41021654"
        update_interval: 60

  discord.hook:
    entities:
      - name: "my-server#livestream_announcements"
        url: "https://discord.com/api/webhooks/..."


chains:

  "from_twitcast":
    - twitcast:
      - "user"
      - "another-user"
    - discord.hook:
        - "my-server#livestream_announcements"
 
  "from_fc2": 
    - fc2:
      - "fc2user"
    - discord.hook:
        - "my-server#livestream_announcements"

```

#### Save community posts to files

Monitor community page of `@ChannelName` and save each new post as a text file, using post id as a name. Uses cookies to access member-only posts.

Images attached to the posts are downloaded stored alongside the text files.

```yaml
actors:
  community:
    entities:
      - name: "ChannelName"
        url: "https://www.youtube.com/@ChannelName/community"
        cookies_file: cookies.txt
      - name: "AnotherChannelName"
        url: "https://www.youtube.com/@AnotherChannelName/community"
        cookies_file: cookies.txt

  to_file:
    entities:
      - name: "community posts"
        path: "archive/community/{author}/"
        filename: "{post_id}.txt"
        append: false

  download:
    entities:
      - name: "community files"
        path: "archive/community/{author}/"
        filename: "{post_id}"
        rename_suffix: "_{i}"
        url_field: "attachments"


chains:
  "community posts text":
    - community:
        - "ChannelName"
        - "AnotherChannelName"
    - to_file:
        - "community posts"

  "community posts files":
    - community:
        - "ChannelName"
        - "AnotherChannelName"
    - download:
        - "community files"
```

##### Archive community tab

In order to store not only new but every post available on a channel, `fetch_until_the_end_of_feed_mode` must be set to `true` and `quiet_first_time` set to `false`. The rest of configuration is essentially the same:

```yaml
actors:
  community:
    entities:
      - name: "ChannelName"
        url: "https://www.youtube.com/@ChannelName/community"
        cookies_file: cookies.txt
        # delete the following two lines after archiving of old records is completed
        fetch_until_the_end_of_feed_mode: true
        quiet_first_time: false

  to_file:
    entities:
      - name: "community posts"
        path: "archive/community/{author}/"
        filename: "{post_id}.txt"
        append: false

  download:
    entities:
      - name: "community files"
        path: "archive/community/{author}/"
        filename: "{post_id}"
        rename_suffix: "_{i}"
        url_field: "attachments"


chains:
  "community posts text":
    - community:
        - "ChannelName"
    - to_file:
        - "community posts"

  "community posts files":
    - community:
        - "ChannelName"
    - download:
        - "community files"
```

#### Save Youtube video chat to a text file

Monitor livechat for new messages and store them into a text file, with each message formatted into JSOM format according to YoutubeChatRecord structure. To make output file itself a valid JSON its entire content would need to be enclosed in `[...]`.

```yaml
actors:

  prechat:
    entities:
      - name: "L692Sxz3thw"
        url: "https://www.youtube.com/watch?v=L692Sxz3thw"
  
  to_file:
    entities:
      - name: "youtube-chat"
        output_format: "pretty_json"
        postfix: ",\n"
        path: "archive/chat/{video_author}/"
        filename: "{video_title} - {video_id}.live_chat.txt"
 

chains:
  freechat:
    - prechat:
      - "L692Sxz3thw"
    - to_file:
      - "youtube-chat"
```

#### Send Jabber message when channel owner comments in the chat

Message author badges, such as owner or moderator, are translated by Youtube according to browser language settings. To ensure it's always the same regardless of geolocation, Accept-Language header is specified. Messages are filtered by regular text filter (`filter.match`) and then formatted by `filter.format` according to template for better context and sent to a Jabber account. Note that order of filters is important, since `filter.format` outputs a TextRecord, that doesn't contain field (`badges`) used to distinguish messages by channel's owner from others.

```yaml
actors:

  prechat:
    entities:
      - name: "L692Sxz3thw"
        url: "https://www.youtube.com/watch?v=L692Sxz3thw"
        headers: 
          "Accept-Language": "en-US,en;q=0.9"
  
  filter.match:
    entities:
      - name: "prechat-owner"
        fields:
          - "badges"
        patterns:
          - "Owner"

  filter.format:
    entities:
      - name: "prechat-owner"
        missing: ""
        template: "{author} commented on {video_title} (https://www.youtube.com/watch?v={video_id}): {amount}\n{sticker}{text}"

  xmpp:
    config:
      xmpp_username: "bot@example.com/avtdl"
      xmpp_pass: "bot's password"
    entities:
      - name: "user"
        jid: "user@example.com"
        timezone: "UTC"
 

chains:
  freechat:
    - prechat:
      - "L692Sxz3thw"
    - filter.match:
      - "prechat-owner"
    - filter.format:
      - "prechat-owner"
    - xmpp:
      - "user"
```


#### Store tweets and images posted by a Twitter account

Tweets posted by `@specificuser` and `@anotheruser` are monitored with a default update interval of 30 minutes. All tweets, along with retweets, replies and quotes, are stored into text files, but only regular tweets by the user themself are picked by the filter for storing images.
Placeholders are used in the names of the output directories and files to put each user's tweets in dedicated folder.

```yaml
actors:

  twitter.user:
    defaults:
      cookies_file: "cookies.txt"
    entities:
      - name: "user"
        user: "specificuser"
      - name: "another user"
        user: "anotheruser"
  
  filter.twitter:
    entities:
      - name: "regular tweets"
        regular_tweet: true

  to_file:
    defaults:
      postfix: "\n---------------------------------------------------\n"
      path: "archive/twitter/{author}"
    entities:
      - name: "tweets"
        filename: "twitter_{username}_%Y.txt"

  download:
    entities:
      - name: "twitter images"
        url_field: 'images'
        path: "archive/twitter/{author}/images/"
        filename: '{username}_{uid}_{source_name}'

chains:

  "store tweets":
    - twitter.user:
        - "user"
        - "another user"
    - to_file:
        - "tweets"
  
  "store images":
    - twitter.user:
        - "user"
        - "another user"
    - filter.twitter:
        - "regular tweets"
    - download:
        - "twitter images"
```


#### Monitor Twitter timeline for tweets by specific users, send the tweets to Discord

Monitor Home Timeline (Following tab) of the account with cookies from `cookies.txt`, use filter to pick tweets made by `@specificuser` and `@anotheruser`, and send them to Discord channel using webhook.

```yaml
actors:

  twitter.home:
    entities:
      - name: "home timeline"
        following: true
        cookies_file: "cookies.txt"
  
  filter.twitter:
    entities:
      - name: "tweets by user"
        username: "specificuser"
      - name: "tweets by another user"
        username: "anotheruser"

  discord.hook:
    entities:
      - name: "my-server#tweets"
        url: "https://discord.com/api/webhooks/..."


chains:

  "send tweets":
    - twitter.home:
        - "home timeline"
    - filter.twitter:
        - "tweets by user"
        - "tweets by another user"
    - discord.hook:
        - "my-server#tweets"
```

#### Send notifications and download Twitter Spaces

Monitor tweets on the home timeline looking for Twitter Spaces, deduplicate cross-posts and feed the spaces to `twitter.spaces` plugin, that will monitor them and emit notifications when spaces start and end.

Notifications are sent when a space gets scheduled or started, and when it ends. Here `emit_on_live` can be disabled because `emit_immediately` will take care of sending initial notification, and `emit_on_end` must be enabled to ensure a download will be initiated even for spaces without replay.

`TwitterSpaceRecord`s with `state` field being "Ended", which means a corresponding Space has ended, are passed to `execute` plugin entity that uses [tslazer](https://github.com/HoloArchivists/tslazer) to download a recording. 

```yaml
actors:

  twitter.home:
    entities:
      - name: "home timeline"
        following: true
        cookies_file: "cookies.txt"
  
  filter.deduplicate:
    entities:
      - name: "spaces"
        field: "space_id"

  filter.match:
    entities:
      - name: "ended spaces"
        fields:
          - "state"
        patterns:
          - "Ended"

  twitter.space:
    entities:
      - name: "home timeline spaces"
        cookies_file: "cookies.txt"
        emit_immediately: true
        emit_on_live: false
        emit_on_archive: true
        emit_on_end: true

  discord.hook:
    entities:
      - name: "my-server#twitter_spaces"
        url: "https://discord.com/api/webhooks/..."

  execute:
    entities:
      - name: "tslazer"
        command: "tslazer --dyn_url {media_url} --filename '[{username}] {title} [{uid}]'"
        placeholders:
          "{media_url}": "media_url"
          "{username}": "username"
          "{title}": "title"
          "{uid}": "uid"
        working_dir: "archive/spaces/{username}/"


chains:

  "monitor spaces":
    - twitter.home:
      - "home timeline"
    - filter.deduplicate:
      - "spaces"
    - twitter.space:
      - "home timeline spaces" 
  
  "send notifications":
    - twitter.space:
      - "home timeline spaces" 
    - discord.hook:
      - "my-server#twitter_spaces"

  "download ended spaces":
    - twitter.space:
      - "home timeline spaces" 
    - filter.match:
      - "ended spaces"
    - execute:
      - "tslazer"
```

#### Monitor and download RPLAY livestreams

Monitor creators channels for livestreams, attempt to generate an HLS playlist url when a stream goes live, and pass it to yt-dlp for downloading.

Streams hosted on other platforms (Youtube or Twitch) are filtered out by the "rplay restream" `exclude` filter by checking the "restream_platform" field on the record.

Streams on RPLAY do not get a unique ID, so the starting time is used instead.

By providing login credentials it is possible to download subscriber-only livestreams accessible to a given account.

```yaml
actors:

  rplay.user:
    config:
      login: "username@example.com"
      password: "the password"
    defaults:
      update_interval: 320
      quiet_first_time: false
    entities:
      - name: "creator1"
        creator_oid: "665afa669da3d5cd36c18401"
      - name: "creator2"
        creator_oid: "665afa669da3d5cd36c18402"
      - name: "creator3"
        creator_oid: "665afa669da3d5cd36c18403"

  filter.exclude:
    entities:
      - name: "rplay restream"
        fields:
          - "restream_platform"
        patterns:
          - "twitch"
          - "youtube"

  execute:
    entities:
      - name: "rplay"
        command: "yt-dlp --windows-filenames --add-header Referer:'https://rplay.live' {playlist_url} --output '{start} [{name}] {title}.%(ext)s'"
        placeholders:
          "{playlist_url}": "playlist_url"
          "{name}": "name"
          "{title}": "title"
          "{start}": "start"
        working_dir: "archive/rplay/{name} [{creator_id}]/"
        log_dir: "archive/rplay/logs/"
        log_filename: "{start} {name} [{creator_id}].log"

chains:
  
  "rplay-dl":
    - rplay.user:
      - "creator1"
      - "creator2"
      - "creator3"
    - filter.exclude:
      - "rplay restream"
    - execute:
      - "rplay"

```
