from typing import List, Union
from plexapi.video import Episode
from plexapi.media import AudioStream, SubtitleStream, MediaPart

from plex_auto_languages.utils.logger import get_logger
from plex_auto_languages.constants import EventType


logger = get_logger()


class TrackChanges():

    def __init__(self, username: str, reference: Episode, event_type: EventType):
        self._reference = reference
        self._username = username
        self._event_type = event_type
        self._audio_stream, self._subtitle_stream = self._get_selected_streams(reference)
        self._changes = []
        self._description = ""
        self._title = ""
        self._computed = False

    @property
    def computed(self):
        return self._computed

    @property
    def event_type(self):
        return self._event_type

    @property
    def description(self):
        return self._description

    @property
    def inline_description(self):
        return self._description.replace("\n", " | ")

    @property
    def title(self):
        return self._title

    @property
    def reference_name(self):
        return f"{self._reference.show().title} (S{self._reference.seasonNumber:02}E{self._reference.episodeNumber:02})"

    @property
    def has_changes(self):
        return len(self._changes) > 0

    @property
    def username(self):
        return self._username

    @property
    def change_count(self):
        return len(self._changes)

    def get_episodes_to_update(self, update_level: str, update_strategy: str):
        show_or_season = None
        if update_level == "show":
            show_or_season = self._reference.show()
        elif update_level == "season":
            show_or_season = self._reference.season()
        episodes = show_or_season.episodes()
        if update_strategy == "next":
            episodes = [e for e in episodes if self._is_episode_after(e)]
        return episodes

    def compute(self, episodes: List[Episode]):
        logger.debug(f"[Language Update] Checking language update for show "
                     f"{self._reference.show()} and user '{self._username}' based on episode {self._reference}")
        self._changes = []
        for episode in episodes:
            try:
                episode.reload()
                for part in episode.iterParts():
                    current_audio_stream, current_subtitle_stream = self._get_selected_streams(part)
                    # Audio stream handling
                    matching_audio_stream = self._match_audio_stream(part.audioStreams())
                    if (current_audio_stream is not None and matching_audio_stream is not None and
                            matching_audio_stream.id != current_audio_stream.id):
                        self._changes.append((episode, part, AudioStream.STREAMTYPE, matching_audio_stream))
                    # Subtitle stream handling
                    try:
                        matching_subtitle_stream = self._match_subtitle_stream(part.subtitleStreams())
                        # Handle subtitle removal
                        if current_subtitle_stream is not None and matching_subtitle_stream is None:
                            self._changes.append((episode, part, SubtitleStream.STREAMTYPE, None))
                        # Handle subtitle changes
                        if matching_subtitle_stream is not None:
                            if current_subtitle_stream is None or matching_subtitle_stream.id != current_subtitle_stream.id:
                                # Check for commentary audio
                                if (current_audio_stream and
                                    getattr(current_audio_stream, 'title', '') and
                                    "commentary" in current_audio_stream.title.lower() and
                                    matching_audio_stream is None):
                                    logger.debug(f"[Language Update] Skipping subtitle changes for "
                                               f"episode {self._reference} and user '{self.username}' "
                                               f"due to commentary track")
                                else:
                                    self._changes.append((episode, part, SubtitleStream.STREAMTYPE, matching_subtitle_stream))
                    except Exception as e:
                        logger.warning(f"Error matching subtitle stream for episode {episode}: {str(e)}")
                        continue
            except Exception as e:
                logger.error(f"Error processing episode {episode}: {str(e)}")
                continue

        self._update_description(episodes)
        self._computed = True

    def apply(self):
        if not self.has_changes:
            logger.debug(f"[Language Update] No changes to perform for show "
                         f"{self._reference.show()} and user '{self.username}'")
            return
        logger.debug(f"[Language Update] Performing {len(self._changes)} change(s) for show {self._reference.show()}")
        for episode, part, stream_type, new_stream in self._changes:
            stream_type_name = "audio" if stream_type == AudioStream.STREAMTYPE else "subtitle"
            logger.debug(f"[Language Update] Updating {stream_type_name} stream of episode {episode} to {new_stream}")
            if stream_type == AudioStream.STREAMTYPE:
                part.setSelectedAudioStream(new_stream)
            elif stream_type == SubtitleStream.STREAMTYPE and new_stream is None:
                part.resetSelectedSubtitleStream()
            elif stream_type == SubtitleStream.STREAMTYPE:
                part.setSelectedSubtitleStream(new_stream)

    def _is_episode_after(self, episode: Episode):
        return self._reference.seasonNumber < episode.seasonNumber or \
            (self._reference.seasonNumber == episode.seasonNumber and self._reference.episodeNumber < episode.episodeNumber)

    def _update_description(self, episodes: List[Episode]):
        if len(episodes) == 0:
            self._title = ""
            self._description = ""
            return
        season_numbers = [e.seasonNumber for e in episodes]
        min_season_number, max_season_number = min(season_numbers), max(season_numbers)
        min_episode_number = min([e.episodeNumber for e in episodes if e.seasonNumber == min_season_number])
        max_episode_number = max([e.episodeNumber for e in episodes if e.seasonNumber == max_season_number])
        from_str = f"S{min_season_number:02}E{min_episode_number:02}"
        to_str = f"S{max_season_number:02}E{max_episode_number:02}"
        range_str = f"{from_str} - {to_str}" if from_str != to_str else from_str
        nb_updated = len({e.key for e, _, _, _ in self._changes})
        nb_total = len(episodes)
        self._title = self._reference.show().title
        self._description = (
            f"Show: {self._reference.show().title}\n"
            f"User: {self._username}\n"
            f"Audio: {self._audio_stream.displayTitle if self._audio_stream is not None else 'None'}\n"
            f"Subtitles: {self._subtitle_stream.displayTitle if self._subtitle_stream is not None else 'None'}\n"
            f"Updated episodes: {nb_updated}/{nb_total} ({range_str})"
        )

    def _match_audio_stream(self, audio_streams: List[AudioStream]):
        # The reference stream can be 'None'
        if self._audio_stream is None:
            return None
        # We only want stream with the same language code
        streams = [s for s in audio_streams if s.languageCode == self._audio_stream.languageCode]
        # if streams aren't differentiated, set ambiguous flag
        ambiguous = all(s.title == audio_streams[0].title for s in audio_streams)
        # attempt to filter commentary tracks
        if self._audio_stream.title is not None and "commentary" in self._audio_stream.title.lower():
            streams = [s for s in streams if s.title is not None and "commentary" in s.title.lower()]
        else:
            streams = [s for s in streams if s.title is not None and "commentary" not in s.title.lower()]
        if len(streams) == 0:
            return None
        if len(streams) == 1:
            return streams[0]
        # If multiple streams match, order them based on a score
        scores = [0] * len(streams)
        for index, stream in enumerate(streams):
            if self._audio_stream.codec == stream.codec:
                scores[index] += 5
            if self._audio_stream.audioChannelLayout == stream.audioChannelLayout:
                scores[index] += 3
            if ambiguous:
                if self._audio_stream.channels < 3:
                    if self._audio_stream.channels < stream.channels:
                        # if streams are ambiguous, prefer more channels as a safe choice to avoid commentary (likely 2.0)
                        # or we could default to first match...
                        scores[index] += 8
                else:
                    if self._audio_stream.channels <= stream.channels:
                        scores[index] += 1
            if self._audio_stream.title is not None and stream.title is not None and self._audio_stream.title == stream.title:
                scores[index] += 5
        return streams[scores.index(max(scores))]

    def _match_subtitle_stream(self, subtitle_streams: List[SubtitleStream]):
        """
        Match the most appropriate subtitle stream based on the reference stream.
        Args:
            subtitle_streams: List of available subtitle streams
        Returns:
            Best matching subtitle stream or None if no match found
        """
        # If no subtitle is selected, handle based on audio stream
        if self._subtitle_stream is None:
            if self._audio_stream is None:
                return None
            # Try to find forced subtitles in the audio language
            language_code = self._audio_stream.languageCode
            forced_streams = [s for s in subtitle_streams
                             if s.languageCode == language_code and s.forced]
            return forced_streams[0] if forced_streams else None

        # Get the reference properties
        language_code = self._subtitle_stream.languageCode
        ref_forced = getattr(self._subtitle_stream, 'forced', False)
        ref_hearing_impaired = getattr(self._subtitle_stream, 'hearingImpaired', False)
        ref_codec = getattr(self._subtitle_stream, 'codec', None)
        ref_title = getattr(self._subtitle_stream, 'title', None)

        # Filter streams by language
        streams = [s for s in subtitle_streams if s.languageCode == language_code]
        if not streams:
            return None

        # If only one stream, return it
        if len(streams) == 1:
            return streams[0]

        # Score the remaining streams based on attributes
        stream_scores = []
        for stream in streams:
            score = 0
            # Basic attribute matching
            if getattr(stream, 'forced', False) == ref_forced:
                score += 3
            if getattr(stream, 'hearingImpaired', False) == ref_hearing_impaired:
                score += 3
            # Codec matching (if available)
            stream_codec = getattr(stream, 'codec', None)
            if ref_codec and stream_codec and ref_codec == stream_codec:
                score += 1
            # Title matching (if available)
            stream_title = getattr(stream, 'title', None)
            if ref_title and stream_title and ref_title == stream_title:
                score += 5

            stream_scores.append((score, stream))

        # Sort by score (highest first) and return the best match
        stream_scores.sort(reverse=True, key=lambda x: x[0])
        logger.debug(f"Subtitle stream scores: {[(score, getattr(stream, 'title', 'No title')) for score, stream in stream_scores]}")
        return stream_scores[0][1] if stream_scores else None

    @staticmethod
    def _get_selected_streams(episode: Union[Episode, MediaPart]):
        audio_stream = ([a for a in episode.audioStreams() if a.selected] + [None])[0]
        subtitle_stream = ([s for s in episode.subtitleStreams() if s.selected] + [None])[0]
        return audio_stream, subtitle_stream


class NewOrUpdatedTrackChanges():

    def __init__(self, event_type: EventType, new: bool):
        self._episode = None
        self._event_type = event_type
        self._new = new
        self._track_changes = []
        self._description = ""
        self._title = ""

    @property
    def episode_name(self):
        if self._episode is None:
            return ""
        return f"{self._episode.show().title} (S{self._episode.seasonNumber:02}E{self._episode.episodeNumber:02})"

    @property
    def event_type(self):
        return self._event_type

    @property
    def description(self):
        return self._description

    @property
    def inline_description(self):
        return self._description.replace("\n", " | ")

    @property
    def title(self):
        return self._title

    @property
    def has_changes(self):
        return sum([1 for tc in self._track_changes if tc.has_changes]) > 0

    def change_track_for_user(self, username: str, reference: Episode, episode: Episode):
        self._episode = episode
        track_changes = TrackChanges(username, reference, self._event_type)
        track_changes.compute([episode])
        track_changes.apply()
        self._track_changes.append(track_changes)
        self._update_description()

    def _update_description(self):
        if len(self._track_changes) == 0:
            self._title = ""
            self._description = ""
            self._episode = None
            return
        event_str = "New" if self._new else "Updated"
        self._title = f"{event_str}: {self.episode_name}"
        self._description = (
            f"Episode: {self.episode_name}\n"
            f"Status: {event_str} episode\n"
            f"Updated for all users"
        )
