#!/usr/bin/env python3
import datetime
import json
import logging
import urllib.parse
import urllib.request
from html.parser import HTMLParser

'''
    Gets timestamp of date video scheduled to from video id.
    Uses parts of code from ytarchive v0.2.1
    https://github.com/Kethsar/ytarchive
'''

WATCH_URL = "https://www.youtube.com/watch?v={0}"
INITIAL_PLAYER_RESPONSE_DECL = "var ytInitialPlayerResponse ="

PLAYABLE_OK = "OK"
PLAYABLE_OFFLINE = "LIVE_STREAM_OFFLINE"
PLAYABLE_UNPLAYABLE = "UNPLAYABLE"
PLAYABLE_ERROR = "ERROR"

def logwarn(msg):
    logging.debug('[yt_info]: {}'.format(msg))

# Download data from the given URL and return it as unicode text
def download_as_text(url):
    data = b""
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = resp.read()
    except Exception as err:
        logwarn("Failed to retrieve data from {0}: {1}".format(url, err))
        return None
    return data.decode("utf-8")


class WatchPageParser(HTMLParser):
    player_response_text = ""

    def handle_data(self, data):
        """
            Check tag data for INITIAL_PLAYER_RESPONSE_DECL at the start.
            Turns out members videos have more than just the player_response
            object delcaration. Should probably do a find instead of startswith
            for the variable declaration as well, but whatever.
        """
        if not data.startswith(INITIAL_PLAYER_RESPONSE_DECL):
            return

        obj_start = data.find("{")
        obj_end = data.find("};", obj_start) + 1

        if obj_end > obj_start:
            self.player_response_text = data[obj_start:obj_end]


# Get the base player response object for the given video id
def get_player_response(video_id):
    player_response = None

    watch_html = download_as_text(WATCH_URL.format(video_id))
    if watch_html is None or len(watch_html) == 0:
        logwarn(f'Watch page {video_id} did not return any data')
        return None

    watch_parser = WatchPageParser()
    watch_parser.feed(watch_html)

    if len(watch_parser.player_response_text) == 0:
        logwarn(f'Player response not found in the watch page of {video_id}')
        return None

    try:
        player_response = json.loads(watch_parser.player_response_text)
    except Exception as e:
        logwarn(f'Failed to parse player response json from {video_id}')
        return None

    return player_response

# return timestamp of stream scheduled start time if possible, otherwise None
def get_sched_time(video_id):
    if not video_id:
        logwarn("bad video_id: {0}".format(video_id))
        return None

    player_response = get_player_response(video_id)

    if not player_response:
        return None
    if not player_response["videoDetails"]["isLiveContent"]:
        logwarn("{0} is not a livestream.".format(video_id))
        return None

    playability = player_response["playabilityStatus"]
    playability_status = playability["status"]

    if playability_status == PLAYABLE_OK:
        logwarn("{0} Playability status: OK. Stream not scheduled anymore.".format(video_id))
        return None
    elif playability_status == PLAYABLE_ERROR:
        logwarn("{0} Playability status: ERROR. Reason: {1}".format(video_id, playability["reason"]))
        return None
    elif playability_status == PLAYABLE_UNPLAYABLE:
        logwarn("{0} Playability status: Unplayable.".format(video_id))
        return None
    elif playability_status == PLAYABLE_OFFLINE:
        sched_time = int(playability["liveStreamability"]["liveStreamabilityRenderer"]["offlineSlate"]["liveStreamOfflineSlateRenderer"]["scheduledStartTime"])
        return sched_time

def get_sched_isoformat(video_id):
    scheduled_timestamp = get_sched_time(video_id)
    if scheduled_timestamp:
        return datetime.datetime.fromtimestamp(scheduled_timestamp, tz=datetime.timezone.utc).isoformat(timespec='seconds')

    else:
        return None
