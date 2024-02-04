## avtdl

Tool to monitor Youtube and some other streaming platforms for new streams and uploads and execute user-defined commands when it happens. It aims to provide a highly configurable environment for setting up automated archiving of new content with filtering and notification support. It does not try to provide downloading streams itself and instead relies on executing commonly used well-known solutions for the task, such as `yt-dlp`.

Refer to [documentation](https://github.com/15532th/avtdl?tab=readme-ov-file#avtdl) for full list of available features and description of configuration process.

### Features overview

Some of the supported features include:

- monitoring Youtube channels using RSS feed
- monitoring Youtube channels, individual tabs of a channel or playlists by parsing html. With authorization cookies from Youtube account it's possible to get notifications for member-only and in any other way restricted streams and uploads, as well as to monitor the entire subscriptions feed
- monitoring Youtube channel community tab for new posts (including member-only with authorization cookies)
- monitoring other streaming platforms, such as Twitch and Twitcasting, for events of a channel going live
- filtering new videos and streams by channel name, presence or absense of pre-defined keywords in video title or description, picking up only upcoming streams or only member-only content, deduplication of the same stream or video url coming from multiple sources
- sending notifications to a Discord channel and/or as a Jabber message