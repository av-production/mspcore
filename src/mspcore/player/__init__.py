from __future__ import annotations

import html
import logging
import random
import time
from typing import Any, Callable, TYPE_CHECKING

from mspcore import errors
from mspcore.player.enums import Mode, State, TrackType
from mspcore.player.sound_device import SoundDevice
from mspcore.player.track import Track

import mpv

if TYPE_CHECKING:
    from mspcore import MSPCore


class Player:
    def __init__(self, core: MSPCore):
        self.events = core.events
        self.config = core.config.player
        mpv_options = {
            "demuxer_lavf_o": "http_persistent=false",
            "demuxer_max_back_bytes": 1048576,
            "demuxer_max_bytes": 2097152,
            "video": False,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36", # noqa
            "ytdl": False,
        }
        mpv_options.update(self.config.player_options)
        try:
            self._player = mpv.MPV(**mpv_options, log_handler=self.log_handler)
        except AttributeError:
            del mpv_options["demuxer_max_back_bytes"]
            self._player = mpv.MPV(**mpv_options, log_handler=self.log_handler)
        self._log_level = 5
        self.track_list: list[Track] = []
        self.track: Track = Track()
        self.track_index: int = -1
        self._state = State.Stopped
        self.mode = Mode.TrackList
        self.volume = self.config.default_volume

    def initialize(self) -> None:
        logging.debug("Initializing player")
        logging.debug("Player initialized")

    def run(self) -> None:
        logging.debug("Registering player callbacks")
        self.register_event_callback("end-file", self.on_end_file)
        self._player.observe_property("metadata", self.on_metadata_update)
        self._player.observe_property("media-title", self.on_metadata_update)
        logging.debug("Player callbacks registered")

    def close(self) -> None:
        logging.debug("Closing player")
        if self.state != State.Stopped:
            self.stop()
        self._player.terminate()
        logging.debug("Player closed")

    def play(
        self,
        tracks: list[Track] | None = None,
        start_track_index: int | None = None,
    ) -> None:
        if tracks is not None:
            self.track_list = tracks
            if not start_track_index and self.mode == Mode.Random:
                self.shuffle(True)
                self.track_index = self._index_list[0]
                self.track = self.track_list[self.track_index]
            else:
                self.track_index = start_track_index if start_track_index else 0
                self.track = tracks[self.track_index]
            self._play(self.track.url)
        else:
            self._player.pause = False
        self._player.volume = self.volume
        self.state = State.Playing

    def pause(self) -> None:
        self.state = State.Paused
        self._player.pause = True

    def stop(self) -> None:
        self.state = State.Stopped
        self._player.stop()
        self.track_list = []
        self.track = Track()
        self.track_index = -1

    def _play(self, arg: str) -> None:
        self._player.pause = False
        self._player.play(arg)

    def next(self) -> None:
        track_index = self.track_index
        if len(self.track_list) > 0:
            if self.mode == Mode.Random:
                try:
                    track_index = self._index_list[
                        self._index_list.index(self.track_index) + 1
                    ]
                except IndexError:
                    track_index = 0
            else:
                track_index += 1
        else:
            track_index = 0
        try:
            self.play_by_index(track_index)
        except errors.IncorrectTrackIndexError:
            if self.mode == Mode.RepeatTrackList:
                self.play_by_index(0)
            else:
                raise errors.NoNextTrackError()

    def previous(self) -> None:
        track_index = self.track_index
        if len(self.track_list) > 0:
            if self.mode == Mode.Random:
                try:
                    track_index = self._index_list[
                        self._index_list.index(self.track_index) - 1
                    ]
                except IndexError:
                    track_index = len(self.track_list) - 1
            else:
                if track_index == 0 and self.mode != Mode.RepeatTrackList:
                    raise errors.NoPreviousTrackError
                else:
                    track_index -= 1
        else:
            track_index = 0
        try:
            self.play_by_index(track_index)
        except errors.IncorrectTrackIndexError:
            if self.mode == Mode.RepeatTrackList:
                self.play_by_index(len(self.track_list) - 1)
            else:
                raise errors.NoPreviousTrackError

    def play_by_index(self, index: int) -> None:
        if index < len(self.track_list) and index >= (0 - len(self.track_list)):
            self.track = self.track_list[index]
            self.track_index = self.track_list.index(self.track)
            self._play(self.track.url)
            self.state = State.Playing
        else:
            raise errors.IncorrectTrackIndexError()

    def set_volume(self, volume: int) -> None:
        volume = volume if volume <= self.config.max_volume else self.config.max_volume
        self.volume = volume
        if self.config.volume_fading:
            n = 1 if self._player.volume < volume else -1
            for i in range(int(self._player.volume), volume, n):
                self._player.volume = i
                time.sleep(self.config.volume_fading_interval)
        else:
            self._player.volume = volume

    def get_speed(self) -> float:
        return self._player.speed

    def set_speed(self, arg: float) -> None:
        if arg < 0.25 or arg > 4:
            raise ValueError()
        self._player.speed = arg

    def seek_back(self, step: float | None = None) -> None:
        step = step if step else self.config.seek_step
        if step <= 0:
            raise ValueError()
        try:
            self._player.seek(-step, reference="relative")
        except SystemError:
            self.stop()

    def seek_forward(self, step: float | None = None) -> None:
        step = step if step else self.config.seek_step
        if step <= 0:
            raise ValueError()
        try:
            self._player.seek(step, reference="relative")
        except SystemError:
            self.stop()

    def get_duration(self) -> float:
        return self._player.duration

    """def get_position(self) -> float:
        return self._player.time_pos

    def set_position(self, arg: float) -> None:
        if arg < 0:
            raise errors.IncorrectPositionError()
        self._player.seek(arg, reference="absolute")"""

    def get_output_devices(self) -> list[SoundDevice]:
        devices: list[SoundDevice] = []
        for device in self._player.audio_device_list:
            devices.append(SoundDevice(device["description"], device["name"]))
        return devices

    def set_output_device(self, sound_device: SoundDevice) -> None:
        self._player.audio_device = sound_device.id

    def shuffle(self, enable: bool) -> None:
        if enable:
            self._index_list = [i for i in range(0, len(self.track_list))]
            random.shuffle(self._index_list)
        else:
            del self._index_list

    def register_event_callback(
        self, callback_name: str, callback_func: Callable[[mpv.MpvEvent], None],
    ) -> None:
        self._player.event_callback(callback_name)(callback_func)

    def log_handler(self, level: str, component: str, message: str) -> None:
        logging.log(self._log_level, "{}: {}: {}".format(level, component, message))

    def _parse_metadata(self, metadata: dict[str, Any]) -> str:
        stream_names = ["icy-name"]
        stream_name = None
        title = None
        artist = None
        for i in metadata:
            if i in stream_names:
                stream_name = html.unescape(metadata[i])
            if "title" in i:
                title = html.unescape(metadata[i])
            if "artist" in i:
                artist = html.unescape(metadata[i])
        chunks: list[str] = []
        chunks.append(artist) if artist else ...
        chunks.append(title) if title else ...
        chunks.append(stream_name) if stream_name else ...
        return " - ".join(chunks)

    def on_end_file(self, event: mpv.MpvEvent) -> None:
        if self.state == State.Playing and self._player.idle_active:
            if self.mode == Mode.SingleTrack or self.track.type == TrackType.Direct:
                self.stop()
            elif self.mode == Mode.RepeatTrack:
                self.play_by_index(self.track_index)
            else:
                try:
                    self.next()
                except errors.NoNextTrackError:
                    self.stop()

    def on_metadata_update(self, name: str, value: Any) -> None:
        if self.state == State.Playing and (
            self.track.type == TrackType.Direct or self.track.type == TrackType.Local
        ):
            metadata = self._player.metadata
            try:
                new_name = self._parse_metadata(metadata)
                if not new_name:
                    new_name = html.unescape(self._player.media_title)
            except TypeError:
                new_name = html.unescape(self._player.media_title)
            if self.track.name != new_name and new_name:
                self.track.name = new_name
        self.events.player_track_update(self.track)

    @property
    def state(self) -> State:
        return self._state

    @state.setter
    def state(self, state: State):
        self._state = state
        self.events.player_state_update(state)
